"""PFV 一致性合同测试（09-11 projection-consistency AC-1..AC-5）。

覆盖：逐事件影响矩阵（AC-1）、无浏览器 GET 的后台初始化收敛（AC-2）、
隐藏中间人路径撤销后的主/替代路径封锁（AC-3）、个人称谓隔离与版本/ETag
联动（AC-4）、旧 If-None-Match 在各类失效后绝不 304 与跨会话持久物化（AC-5）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import config
from app.commands import spaces as space_commands
from app.commands.registration import register_user
from app.db import SessionLocal
from app.models.personal_family_view import PersonalFamilyView, PersonalFamilyViewEdge
from app.models.space import FamilySpace, SpaceProfileRef
from app.services import (
    domain_events,
    family_recommendations,
    invite_codes,
    personal_family_view,
    terms,
)
from app.services import source_facts as sf
from app.utils.timeutil import utcnow
from conftest import (
    auth_header,
    create_agent_fixture,
    create_space_member,
    create_user_with_pin,
    login,
)

# ---- 公共辅助 ----


def _make_space(session, owner, name: str, *, kind: str = "household") -> FamilySpace:
    space = FamilySpace(name=name, kind=kind, owner_id=owner.id, created_at=utcnow())
    session.add(space)
    session.flush()
    create_space_member(session, space.id, owner.id)
    return space


def _confirm(session, fact_type: str, subject_id: int, object_id: int, space_id: int | None):
    fact = sf.create_source_fact(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        provenance="manual_entry",
        space_id=space_id,
    )
    sf.transition_source_fact(session, fact, "confirm")
    return fact


def _materialize(session, account, space_id) -> PersonalFamilyView:
    view = personal_family_view.rebuild_view(session, account=account, space_id=space_id)
    session.commit()
    return view


def _view(session, account_id: int, space_id: int) -> PersonalFamilyView:
    return session.scalar(
        select(PersonalFamilyView).where(
            PersonalFamilyView.viewer_account_id == account_id,
            PersonalFamilyView.space_id == space_id,
        )
    )


# ---- 称谓闭环黄金用例 ----


def test_pfv_persists_daughter_in_law_term_for_dm_sf(db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="kinship-term")
    son = create_user_with_pin(db_session, "kinship-son", "123456", gender="m")
    wife = create_user_with_pin(db_session, "kinship-wife", "123456", gender="f")
    create_space_member(db_session, space.id, son.id)
    create_space_member(db_session, space.id, wife.id)

    _confirm(db_session, "biological_parent", viewer.id, son.id, space.id)
    _confirm(db_session, "spouse", son.id, wife.id, space.id)
    terms.seed_builtin_packs(db_session)
    db_session.commit()

    view = _materialize(db_session, viewer.account, space.id)
    edge = db_session.scalar(
        select(PersonalFamilyViewEdge).where(
            PersonalFamilyViewEdge.view_id == view.id,
            PersonalFamilyViewEdge.to_user_id == wife.id,
        )
    )

    assert edge is not None
    assert edge.concept_code == "Dm-Sf"
    assert edge.term == "儿媳"
    assert edge.authorization_basis_json["term_source_level"] == "locale"


def test_legacy_pfv_version_cannot_serve_structural_snapshot(db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="kinship-legacy")
    son = create_user_with_pin(db_session, "kinship-legacy-son", "123456", gender="m")
    wife = create_user_with_pin(db_session, "kinship-legacy-wife", "123456", gender="f")
    create_space_member(db_session, space.id, son.id)
    create_space_member(db_session, space.id, wife.id)
    _confirm(db_session, "biological_parent", viewer.id, son.id, space.id)
    _confirm(db_session, "spouse", son.id, wife.id, space.id)
    terms.seed_builtin_packs(db_session)
    db_session.commit()

    view = _materialize(db_session, viewer.account, space.id)
    edge = db_session.scalar(
        select(PersonalFamilyViewEdge).where(
            PersonalFamilyViewEdge.view_id == view.id,
            PersonalFamilyViewEdge.to_user_id == wife.id,
        )
    )
    assert edge is not None and edge.term == "儿媳"

    view.computation_version = "pfv-v1"
    view.policy_version = "graph"
    db_session.commit()

    payload = personal_family_view.view_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    assert payload["nodes"] == []
    assert payload["edges"] == []
    assert payload["stale_reason"] == "version_drift"


def test_rebuild_space_views_repairs_current_version_drift(db_session) -> None:
    """周期重建必须发现 current 行的代码版本漂移，即使没有领域事件。"""
    viewer, space = create_agent_fixture(db_session, name="version-drift-rebuild")
    view = _materialize(db_session, viewer.account, space.id)
    view.policy_version = "graph"
    view.computation_version = "pfv-v1"
    db_session.commit()

    assert personal_family_view.rebuild_space_views(db_session, space_id=space.id) == 1
    db_session.commit()
    repaired = _view(db_session, viewer.account.id, space.id)
    assert repaired.status == "current"
    assert repaired.policy_version == personal_family_view.POLICY_VERSION
    assert repaired.computation_version == personal_family_view.COMPUTATION_VERSION


# ---- AC-1：逐事件影响矩阵 ----


def test_membership_change_invalidates_only_that_space(db_session) -> None:
    owner_a, space_a = create_agent_fixture(db_session, name="imp-a")
    owner_b, space_b = create_agent_fixture(db_session, name="imp-b")
    _materialize(db_session, owner_a.account, space_a.id)
    _materialize(db_session, owner_b.account, space_b.id)

    domain_events.emit(
        db_session,
        event_type="space.membership.changed",
        aggregate_type="space",
        aggregate_id=space_a.id,
        payload={"action": "remove", "user_id": owner_a.id},
        space_id=space_a.id,
    )
    db_session.commit()

    assert _view(db_session, owner_a.account.id, space_a.id).status == "stale"
    assert _view(db_session, owner_b.account.id, space_b.id).status == "current"


def test_term_personal_updated_only_hits_own_account(db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="imp-term")
    other = create_user_with_pin(db_session, "imp-term-other", "123456")
    create_space_member(db_session, space.id, other.id)
    _materialize(db_session, owner.account, space.id)
    _materialize(db_session, other.account, space.id)

    terms.set_personal_term(
        db_session,
        account_id=owner.account.id,
        space_id=space.id,
        concept_code="Um",
        term="老爷子",
    )
    db_session.commit()

    assert _view(db_session, owner.account.id, space.id).status == "stale"
    assert _view(db_session, other.account.id, space.id).status == "current"


def test_global_source_fact_revoked_scopes_to_member_spaces(db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="imp-global")
    stranger_owner, other_space = create_agent_fixture(db_session, name="imp-global-b")
    parent = create_user_with_pin(db_session, "imp-global-parent", "123456", gender="m")
    create_space_member(db_session, space.id, parent.id)
    fact = _confirm(db_session, "biological_parent", parent.id, owner.id, None)
    _materialize(db_session, owner.account, space.id)
    _materialize(db_session, stranger_owner.account, other_space.id)

    sf.transition_source_fact(db_session, fact, "revoke")
    db_session.commit()

    assert _view(db_session, owner.account.id, space.id).status == "stale"
    # 无关空间不被全局人物事件波及
    assert _view(db_session, stranger_owner.account.id, other_space.id).status == "current"


def test_disclosure_updated_scopes_to_authorized_spaces(db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="imp-disclosure")
    stranger_owner, other_space = create_agent_fixture(db_session, name="imp-disclosure-b")
    _materialize(db_session, owner.account, space.id)
    _materialize(db_session, stranger_owner.account, other_space.id)

    # 真实 producer（commands/members.py）事件形状：aggregate=profile、payload.scope
    domain_events.emit(
        db_session,
        event_type="disclosure.updated",
        aggregate_type="profile",
        aggregate_id=owner.id,
        payload={"scope": "global"},
    )
    db_session.commit()

    assert _view(db_session, owner.account.id, space.id).status == "stale"
    assert _view(db_session, stranger_owner.account.id, other_space.id).status == "current"


def test_private_memory_event_never_triggers_space_work(db_session) -> None:
    from app.models.steward import StewardJob

    owner, space = create_agent_fixture(db_session, name="imp-memory")
    _materialize(db_session, owner.account, space.id)

    jobs_before = db_session.query(StewardJob).count()
    domain_events.emit(
        db_session,
        event_type="memory.confirmed",
        aggregate_type="memory",
        aggregate_id=1,
        payload={"memory_id": 1},
    )
    db_session.commit()

    assert _view(db_session, owner.account.id, space.id).status == "current"
    assert db_session.query(StewardJob).count() == jobs_before


def test_scoped_memory_and_rag_events_never_trigger_steward_work(db_session) -> None:
    """带空间维度的 memory/RAG 事件也不应登记 Steward 作业。"""
    from app.models.steward import StewardJob

    owner, space = create_agent_fixture(db_session, name="imp-scoped-memory")
    jobs_before = db_session.query(StewardJob).count()
    domain_events.emit(
        db_session,
        event_type="memory.confirmed",
        aggregate_type="memory",
        aggregate_id=1,
        payload={"scope": "space", "space_id": space.id},
        space_id=space.id,
        actor_account_id=owner.account.id,
    )
    domain_events.emit(
        db_session,
        event_type="rag.document.ingested",
        aggregate_type="rag_document",
        aggregate_id=1,
        payload={"scope": "space", "space_id": space.id},
        space_id=space.id,
        actor_account_id=owner.account.id,
    )
    db_session.commit()

    assert db_session.query(StewardJob).count() == jobs_before


# ---- AC-2：无浏览器 GET 的初始化收敛 ----


def test_registration_without_code_creates_no_view_rows(db_session) -> None:
    result = register_user(db_session, username="init-nospace", pin="123456")
    db_session.commit()
    assert (
        db_session.scalar(
            select(PersonalFamilyView.id).where(
                PersonalFamilyView.viewer_account_id == result.user.account.id
            )
        )
        is None
    )


def test_registration_with_stranger_code_initializes_queued_view(db_session) -> None:
    creator = create_user_with_pin(db_session, "init-stranger-creator", "123456")
    code = invite_codes.create_code(db_session, creator=creator, kind="stranger")
    db_session.commit()

    result = register_user(
        db_session, username="init-stranger", pin="123456", invite_code=code.code
    )
    db_session.commit()
    row = db_session.scalar(
        select(PersonalFamilyView).where(
            PersonalFamilyView.viewer_account_id == result.user.account.id
        )
    )
    assert row is not None
    assert row.status == "queued"


def test_registration_with_household_code_initializes_queued_view(db_session) -> None:
    creator, space = create_agent_fixture(db_session, name="init-household")
    code = invite_codes.create_code(
        db_session, creator=creator, kind="household", space_id=space.id
    )
    db_session.commit()

    result = register_user(db_session, username="init-joiner", pin="123456", invite_code=code.code)
    db_session.commit()
    row = _view(db_session, result.user.account.id, space.id)
    assert row is not None and row.status == "queued"


def test_later_member_add_initializes_queued_view(db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="init-add")
    joiner = create_user_with_pin(db_session, "init-add-joiner", "123456")
    db_session.commit()

    from app.commands.context import ActorContext

    ctx = ActorContext(
        user_id=owner.id,
        account_id=owner.account.id,
        account_status=owner.account.status,
    )
    member, _created = space_commands.invite_member(db_session, ctx, space.id, user_id=joiner.id)
    db_session.commit()
    joiner_ctx = ActorContext(
        user_id=joiner.id,
        account_id=joiner.account.id,
        account_status=joiner.account.status,
    )
    space_commands.respond_invitation(db_session, joiner_ctx, member.id, accept=True)
    db_session.commit()

    row = _view(db_session, joiner.account.id, space.id)
    assert row is not None and row.status == "queued"


def test_first_path_formation_reaches_safe_current_without_browser_get(db_session) -> None:
    creator, space = create_agent_fixture(db_session, name="init-path")
    code = invite_codes.create_code(
        db_session, creator=creator, kind="household", space_id=space.id
    )
    db_session.commit()
    parent = create_user_with_pin(db_session, "init-path-parent", "123456", gender="m")
    create_space_member(db_session, space.id, parent.id)
    result = register_user(
        db_session, username="init-path-joiner", pin="123456", invite_code=code.code
    )
    db_session.commit()
    # 注册初始化的 queued 行（无 GET）
    assert _view(db_session, result.user.account.id, space.id).status == "queued"

    _confirm(db_session, "biological_parent", parent.id, result.user.id, space.id)
    db_session.commit()
    personal_family_view.rebuild_space_views(db_session, space_id=space.id)
    db_session.commit()

    view = _view(db_session, result.user.account.id, space.id)
    assert view.status == "current"
    payload = personal_family_view.view_payload(
        db_session, account=result.user.account, space_id=space.id
    )
    assert {node["user_id"] for node in payload["nodes"]} == {result.user.id, parent.id}


def test_provisional_person_without_account_gets_no_view(db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="init-prov")
    from app.models.user import User

    provisional = User(
        name="init-prov-person",
        created_at=utcnow(),
        profile_status="provisional",
    )
    db_session.add(provisional)
    db_session.flush()
    # 只有引用、没有自己的 Account 的人物：不伪造视图行
    from app.models.account import Account

    assert db_session.scalar(select(Account.id).where(Account.user_id == provisional.id)) is None
    db_session.add(
        SpaceProfileRef(
            space_id=space.id,
            user_id=provisional.id,
            added_by=owner.id,
            status="active",
            created_at=utcnow(),
        )
    )
    db_session.commit()

    domain_events.emit(
        db_session,
        event_type="space.membership.changed",
        aggregate_type="space",
        aggregate_id=space.id,
        payload={"action": "accepted", "user_id": owner.id},
        space_id=space.id,
    )
    db_session.commit()
    views = db_session.scalars(select(PersonalFamilyView)).all()
    assert {view.viewer_account_id for view in views} == {owner.account.id}


# ---- AC-3：隐藏中间人路径撤销 ----


def _two_middle_paths(session, viewer, middles, grandparent, space_id):
    """viewer→m_i→grandparent 两条替代路径（隐藏中间人为 m_i）。"""
    for middle in middles:
        create_space_member(session, space_id, middle.id)
        _confirm(session, "biological_parent", middle.id, viewer.id, space_id)
        _confirm(session, "biological_parent", grandparent.id, middle.id, space_id)
    create_space_member(session, space_id, grandparent.id)
    session.commit()


def test_hidden_middle_revocation_blocks_main_and_alternative_paths(db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="hid-viewer")
    m1 = create_user_with_pin(db_session, "hid-m1", "123456", gender="f")
    m2 = create_user_with_pin(db_session, "hid-m2", "123456", gender="f")
    grandparent = create_user_with_pin(db_session, "hid-grand", "123456", gender="m")
    _two_middle_paths(db_session, viewer, [m1, m2], grandparent, space.id)

    _materialize(db_session, viewer.account, space.id)
    payload = personal_family_view.view_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    grand_edges = [e for e in payload["edges"] if e["to_user_id"] == grandparent.id]
    assert len(grand_edges) == 1
    assert len(grand_edges[0]["alternative_paths"]) == 1

    # 撤销中间事实（m2→viewer 的孩子边与 m2→grandparent 边之一）：主路径证据失效，
    # 事件事务内同步标 stale；端点仍可见但中间证据已断。
    revoked = db_session.scalars(
        select(sf.SourceFact).where(
            sf.SourceFact.subject_user_id == m1.id,
            sf.SourceFact.object_user_id == viewer.id,
            sf.SourceFact.state == "confirmed",
        )
    ).all()
    for fact in revoked:
        sf.transition_source_fact(db_session, fact, "revoke")
    db_session.commit()

    view = _view(db_session, viewer.account.id, space.id)
    assert view.status == "stale"
    payload = personal_family_view.view_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    assert payload["edges"] == []
    assert payload["nodes"] == []

    # 模拟失效遗漏（行仍标 current）：新鲜度判定仍拒绝旧路径
    view.status = "current"
    db_session.commit()
    payload = personal_family_view.view_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    assert payload["edges"] == []
    assert payload["stale_reason"] is not None
    # 推荐不使用旧 current
    recommendations = family_recommendations.recommendations_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    assert all(item["category"] != "confirmed_kinship" for item in recommendations["items"])

    # 重算后 viewer→grandparent 只剩经 m2 的路径，被撤销事实不再出现
    # （行被上文强制 current，直接走单视图重算命令）
    personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
    db_session.commit()
    payload = personal_family_view.view_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    grand_edges = [e for e in payload["edges"] if e["to_user_id"] == grandparent.id]
    assert len(grand_edges) == 1
    edge = grand_edges[0]
    revoked_ids = {fact.id for fact in revoked}
    all_paths = [edge["path"], *edge["alternative_paths"]]
    all_fact_ids = {step["fact_id"] for path in all_paths for step in path if step["fact_id"] > 0}
    assert all_fact_ids & revoked_ids == set()
    assert any(step["to"] == m2.id for path in all_paths for step in path)


def test_path_evidence_validation_rejects_revoked_middle_fact(db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="hid-unit")
    middle = create_user_with_pin(db_session, "hid-unit-m", "123456", gender="f")
    create_space_member(db_session, space.id, middle.id)
    fact = _confirm(db_session, "biological_parent", middle.id, viewer.id, space.id)
    db_session.commit()
    visible = {viewer.id, middle.id}
    path = [
        {
            "from": viewer.id,
            "to": middle.id,
            "edge_type": "parent",
            "subtype": "biological",
            "direction": "up",
            "fact_id": fact.id,
        }
    ]
    assert personal_family_view._path_evidence_valid(
        db_session, path=path, space_id=space.id, visible_ids=visible
    )
    sf.transition_source_fact(db_session, fact, "revoke")
    db_session.commit()
    assert not personal_family_view._path_evidence_valid(
        db_session, path=path, space_id=space.id, visible_ids=visible
    )
    # 中间人不可见时，即使事实仍 confirmed，整条路径也无效
    assert not personal_family_view._path_evidence_valid(
        db_session, path=path, space_id=space.id, visible_ids={viewer.id}
    )


# ---- AC-4：个人称谓隔离与版本联动 ----


def test_personal_terms_isolated_between_accounts(db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="term-viewer")
    other = create_user_with_pin(db_session, "term-other", "123456")
    parent = create_user_with_pin(db_session, "term-parent", "123456", gender="m")
    for user in (other, parent):
        create_space_member(db_session, space.id, user.id)
    _confirm(db_session, "biological_parent", parent.id, viewer.id, space.id)
    _confirm(db_session, "biological_parent", parent.id, other.id, space.id)
    db_session.commit()

    _materialize(db_session, viewer.account, space.id)
    _materialize(db_session, other.account, space.id)

    terms.set_personal_term(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um",
        term="老爷子",
    )
    db_session.commit()
    personal_family_view.rebuild_space_views(db_session, space_id=space.id)
    db_session.commit()

    def _edge_term(account):
        view = _view(db_session, account.id, space.id)
        edge = db_session.scalar(
            select(PersonalFamilyViewEdge).where(
                PersonalFamilyViewEdge.view_id == view.id,
                PersonalFamilyViewEdge.to_user_id == parent.id,
            )
        )
        return edge.term

    assert _edge_term(viewer.account) == "老爷子"
    # 另一用户的偏好不被覆盖（仍走词典/结构回退）
    assert _edge_term(other.account) != "老爷子"

    terms.set_personal_term(
        db_session,
        account_id=other.account.id,
        space_id=space.id,
        concept_code="Um",
        term="老爸",
    )
    db_session.commit()
    personal_family_view.rebuild_space_views(db_session, space_id=space.id)
    db_session.commit()
    assert _edge_term(viewer.account) == "老爷子"
    assert _edge_term(other.account) == "老爸"


def test_term_change_bumps_version_and_etag(db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="term-etag")
    parent = create_user_with_pin(db_session, "term-etag-parent", "123456", gender="m")
    create_space_member(db_session, space.id, parent.id)
    _confirm(db_session, "biological_parent", parent.id, viewer.id, space.id)
    db_session.commit()
    view = _materialize(db_session, viewer.account, space.id)
    version_before = int(view.view_version)
    hash_before = str(view.input_hash)
    etag_before = personal_family_view.etag_for(view, account=viewer.account)

    terms.set_personal_term(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um",
        term="老爷子",
    )
    db_session.commit()
    stale_view = _view(db_session, viewer.account.id, space.id)
    assert stale_view.status == "stale"
    assert personal_family_view.etag_for(stale_view, account=viewer.account) != etag_before

    personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
    db_session.commit()
    fresh_view = _view(db_session, viewer.account.id, space.id)
    assert fresh_view.view_version == version_before + 1
    assert fresh_view.input_hash != hash_before


# ---- AC-5：条件缓存与持久物化 ----


@pytest.fixture()
def _pfv_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)


def _client_get(client, headers, space_id, etag=None):
    return client.get(
        "/api/personal-family-view",
        params={"space_id": space_id},
        headers={**headers, **({"If-None-Match": etag} if etag else {})},
    )


def _api_setup(db_session):
    viewer, space = create_agent_fixture(db_session, name="cache-viewer")
    parent = create_user_with_pin(db_session, "cache-parent", "123456", gender="m")
    create_space_member(db_session, space.id, parent.id)
    fact = _confirm(db_session, "biological_parent", parent.id, viewer.id, space.id)
    db_session.commit()
    return viewer, space, fact


def test_stale_etag_never_serves_304_after_revocation(db_session, client, _pfv_enabled) -> None:
    viewer, space, fact = _api_setup(db_session)
    _materialize(db_session, viewer.account, space.id)

    headers = auth_header(login(client, "cache-viewer", "123456").json())
    first = _client_get(client, headers, space.id)
    assert first.status_code == 200
    etag = first.headers["ETag"]
    assert _client_get(client, headers, space.id, etag).status_code == 304

    sf.transition_source_fact(db_session, fact, "revoke")
    db_session.commit()
    response = _client_get(client, headers, space.id, etag)
    assert response.status_code == 200
    assert response.json()["status"] == "stale"
    assert response.json()["edges"] == []


def test_stale_etag_never_serves_304_after_policy_update(
    db_session, client, _pfv_enabled, monkeypatch: pytest.MonkeyPatch
) -> None:
    viewer, space, _fact = _api_setup(db_session)
    _materialize(db_session, viewer.account, space.id)
    headers = auth_header(login(client, "cache-viewer", "123456").json())
    etag = _client_get(client, headers, space.id).headers["ETag"]

    monkeypatch.setattr(personal_family_view, "POLICY_VERSION", "v-test-policy-2")
    response = _client_get(client, headers, space.id, etag)
    assert response.status_code == 200
    assert response.json()["status"] == "stale"


def test_stale_etag_never_serves_304_after_term_change(db_session, client, _pfv_enabled) -> None:
    viewer, space, _fact = _api_setup(db_session)
    _materialize(db_session, viewer.account, space.id)
    headers = auth_header(login(client, "cache-viewer", "123456").json())
    etag = _client_get(client, headers, space.id).headers["ETag"]

    terms.set_personal_term(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um",
        term="老爷子",
    )
    db_session.commit()
    response = _client_get(client, headers, space.id, etag)
    assert response.status_code == 200
    assert response.json()["status"] == "stale"


def test_stale_etag_never_serves_304_after_bridge_expiry(db_session, client, _pfv_enabled) -> None:
    from app.services import personal_family_bridge

    left = create_user_with_pin(db_session, "cache-bridge-a", "123456")
    right = create_user_with_pin(db_session, "cache-bridge-b", "123456")
    left_space = _make_space(db_session, left, "cache-bridge-left", kind="lineage")
    right_space = _make_space(db_session, right, "cache-bridge-right", kind="lineage")
    _confirm(db_session, "spouse", left.id, right.id, left_space.id)
    db_session.commit()
    bridge = personal_family_bridge.create_bridge(
        db_session,
        account=left.account,
        anchor_user_id=left.id,
        other_space_id=right_space.id,
        other_anchor_user_id=right.id,
        scope={"mode": "anchor_paths"},
    )
    personal_family_bridge.consent_bridge(
        db_session, bridge_id=bridge.id, account=right.account, revision=bridge.revision
    )
    _materialize(db_session, left.account, left_space.id)

    headers = auth_header(login(client, "cache-bridge-a", "123456").json())
    first = _client_get(client, headers, left_space.id)
    assert first.status_code == 200
    etag = first.headers["ETag"]
    assert right.id in {node["user_id"] for node in first.json()["nodes"]}
    assert _client_get(client, headers, left_space.id, etag).status_code == 304

    bridge.status = "expired"
    db_session.commit()
    response = _client_get(client, headers, left_space.id, etag)
    assert response.status_code == 200
    assert response.json()["status"] == "stale"
    assert right.id not in {node["user_id"] for node in response.json()["nodes"]}


def test_first_materialization_persists_across_independent_sessions(db_session) -> None:
    viewer, space, _fact = _api_setup(db_session)
    _materialize(db_session, viewer.account, space.id)

    with SessionLocal() as independent:
        row = _view(independent, viewer.account.id, space.id)
        assert row is not None and row.status == "current"
        payload = personal_family_view.view_payload(
            independent, account=viewer.account, space_id=space.id
        )
        assert payload["status"] == "current"
        assert len(payload["edges"]) == 1
