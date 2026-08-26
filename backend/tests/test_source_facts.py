"""SourceFact 事实层测试（V2.3 Block E1；AC-KI1/3/4 对应部分）。

覆盖：
- 七类 fact_type 合法创建 + FSM 合法/非法转换矩阵（revision 递增 + 领域事件）
- parent 环检测：直环、长链成环、32 层深度上限边界
- direct_sibling 父母未知独立成立；再婚多条 spouse 并存与 revoked 重建
- partial unique：同元组冲突 / 全局与空间并存 / revoked 后重建
- raw 原文不可变（DB 触发器）+ 原文关联与 .revised 内容变更
- v1 结构边映射工厂 seed_structural_edge_to_fact 与图内可查询
- social_relations 存储与 CHECK 兜底
"""

from __future__ import annotations

import pytest
from conftest import create_user_with_pin, create_v1_relation, seed_structural_edge_to_fact
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.errors import extract_api_error
from app.models.relationship_facts import (
    PARENT_FACT_TYPES,
    SOURCE_FACT_TYPES,
    SocialRelation,
    SourceFact,
)
from app.models.v2_foundation import DomainEvent
from app.services import source_facts as sf
from app.utils.timeutil import utcnow


def _make_pair(session, prefix: str):
    a = create_user_with_pin(session, f"{prefix}-父", "123456")
    b = create_user_with_pin(session, f"{prefix}-子", "123456")
    return a, b


def _create_parent_fact(session, subject_id: int, object_id: int) -> SourceFact:
    fact = sf.create_source_fact(
        session,
        fact_type="biological_parent",
        subject_user_id=subject_id,
        object_user_id=object_id,
        provenance="manual_entry",
    )
    sf.transition_source_fact(session, fact, "confirm")
    return fact


def _fact_events(session, fact_id: int) -> list[DomainEvent]:
    session.flush()  # SessionLocal autoflush=False：先落盘未决事件再查询
    stmt = (
        select(DomainEvent)
        .where(
            DomainEvent.aggregate_type == "source_fact",
            DomainEvent.aggregate_id == fact_id,
        )
        .order_by(DomainEvent.id)
    )
    return list(session.scalars(stmt))


def _assert_event(payload_event: DomainEvent, event_type: str, fact: SourceFact) -> None:
    assert payload_event.type == event_type
    assert payload_event.space_id == fact.space_id
    assert payload_event.payload == {
        "fact_id": fact.id,
        "fact_type": fact.fact_type,
        "subject_user_id": fact.subject_user_id,
        "object_user_id": fact.object_user_id,
        "space_id": fact.space_id,
        "revision": fact.revision,
    }


# ---- 创建 ----


@pytest.mark.parametrize("fact_type", SOURCE_FACT_TYPES)
def test_all_fact_types_create(db_session, fact_type: str) -> None:
    """七类事实全部可创建（proposed 初始态，不产生状态类事件）。"""
    a, b = _make_pair(db_session, fact_type[:6])
    fact = sf.create_source_fact(
        db_session,
        fact_type=fact_type,
        subject_user_id=a.id,
        object_user_id=b.id,
        provenance="profile_form",
        space_id=None,
    )
    assert fact.state == "proposed"
    assert fact.revision == 1
    assert _fact_events(db_session, fact.id) == []


def test_create_validates_type_provenance_and_state(db_session) -> None:
    a, b = _make_pair(db_session, "校验")
    for kwargs in (
        {"fact_type": "best_friend", "provenance": "manual_entry"},
        {"fact_type": "spouse", "provenance": "gossip"},
    ):
        with pytest.raises(HTTPException) as exc_info:
            sf.create_source_fact(db_session, subject_user_id=a.id, object_user_id=b.id, **kwargs)  # type: ignore[arg-type]
        error = extract_api_error(exc_info.value.detail)
        assert exc_info.value.status_code == 422
        assert error is not None and error["code"] == "VALIDATION_ERROR"

    with pytest.raises(HTTPException) as exc_info:
        sf.create_source_fact(
            db_session,
            fact_type="spouse",
            subject_user_id=a.id,
            object_user_id=b.id,
            provenance="manual_entry",
            state="disputed",
        )
    error = extract_api_error(exc_info.value.detail)
    assert error is not None and error["code"] == "VALIDATION_ERROR"

    with pytest.raises(HTTPException) as exc_info:
        sf.create_source_fact(
            db_session,
            fact_type="spouse",
            subject_user_id=a.id,
            object_user_id=a.id,
            provenance="manual_entry",
        )
    error = extract_api_error(exc_info.value.detail)
    assert exc_info.value.status_code == 422
    assert error is not None and error["code"] == "SOURCE_FACT_SELF_FORBIDDEN"


# ---- FSM 矩阵 ----


def test_fsm_legal_transition_matrix(db_session) -> None:
    """五条合法转换逐一验证：状态、revision 递增、对应领域事件。"""
    # proposed --confirm--> confirmed
    a, b = _make_pair(db_session, "甲")
    fact = sf.create_source_fact(
        db_session,
        fact_type="biological_parent",
        subject_user_id=a.id,
        object_user_id=b.id,
        provenance="manual_entry",
    )
    sf.transition_source_fact(db_session, fact, "confirm", actor_account_id=a.account.id)
    assert (fact.state, fact.revision) == ("confirmed", 2)
    events = _fact_events(db_session, fact.id)
    assert [e.type for e in events] == ["source_fact.confirmed"]
    _assert_event(events[0], "source_fact.confirmed", fact)

    # confirmed --revoke--> revoked
    sf.transition_source_fact(db_session, fact, "revoke", actor_account_id=a.account.id)
    assert (fact.state, fact.revision) == ("revoked", 3)
    events = _fact_events(db_session, fact.id)
    assert [e.type for e in events] == ["source_fact.confirmed", "source_fact.revoked"]
    _assert_event(events[1], "source_fact.revoked", fact)

    # proposed --dispute--> disputed --confirm--> confirmed
    c, d = _make_pair(db_session, "乙")
    disputed = sf.create_source_fact(
        db_session,
        fact_type="guardian",
        subject_user_id=c.id,
        object_user_id=d.id,
        provenance="agent_proposal",
    )
    sf.transition_source_fact(db_session, disputed, "dispute")
    assert (disputed.state, disputed.revision) == ("disputed", 2)
    sf.transition_source_fact(db_session, disputed, "confirm")
    assert (disputed.state, disputed.revision) == ("confirmed", 3)
    assert [e.type for e in _fact_events(db_session, disputed.id)] == [
        "source_fact.disputed",
        "source_fact.confirmed",
    ]

    # disputed --revoke--> revoked
    e, f = _make_pair(db_session, "丙")
    from_disputed = sf.create_source_fact(
        db_session,
        fact_type="partner",
        subject_user_id=e.id,
        object_user_id=f.id,
        provenance="import",
    )
    sf.transition_source_fact(db_session, from_disputed, "dispute")
    sf.transition_source_fact(db_session, from_disputed, "revoke")
    assert (from_disputed.state, from_disputed.revision) == ("revoked", 3)
    assert [ev.type for ev in _fact_events(db_session, from_disputed.id)] == [
        "source_fact.disputed",
        "source_fact.revoked",
    ]


def test_fsm_illegal_transitions_rejected(db_session) -> None:
    """非法转换一律 409 SOURCE_FACT_INVALID_TRANSITION 且状态不变。"""
    cases: list[tuple[str, str]] = [
        ("proposed", "revoke"),
        ("confirmed", "confirm"),
        ("confirmed", "dispute"),
        ("disputed", "dispute"),
        ("revoked", "confirm"),
        ("revoked", "dispute"),
        ("revoked", "revoke"),
    ]
    a, b = _make_pair(db_session, "非法")

    def fresh(state: str):
        fact = sf.create_source_fact(
            db_session,
            fact_type="direct_sibling",
            subject_user_id=a.id,
            object_user_id=b.id,
            provenance="manual_entry",
        )
        while fact.state != state:
            action = {
                ("proposed", "confirmed"): "confirm",
                ("proposed", "disputed"): "dispute",
                ("proposed", "revoked"): "confirm",  # 两跳：先 confirm 再 revoke
                ("confirmed", "revoked"): "revoke",
                ("disputed", "revoked"): "revoke",
            }[(fact.state, state)]
            sf.transition_source_fact(db_session, fact, action)
        revision_before = fact.revision
        events_before = len(_fact_events(db_session, fact.id))
        return fact, revision_before, events_before

    for start_state, action in cases:
        fact, revision_before, events_before = fresh(start_state)
        with pytest.raises(HTTPException) as exc_info:
            sf.transition_source_fact(db_session, fact, action)
        error = extract_api_error(exc_info.value.detail)
        assert exc_info.value.status_code == 409
        assert error is not None and error["code"] == "SOURCE_FACT_INVALID_TRANSITION"
        assert fact.state == start_state
        assert fact.revision == revision_before
        assert len(_fact_events(db_session, fact.id)) == events_before
        if fact.state == "proposed":  # 走合法路径释放同对唯一坑位，供下一用例新建
            sf.transition_source_fact(db_session, fact, "confirm")
        if fact.state != "revoked":
            sf.transition_source_fact(db_session, fact, "revoke")


# ---- 环检测 ----


def test_parent_cycle_direct_rejected(db_session) -> None:
    a, b = _make_pair(db_session, "直环")
    _create_parent_fact(db_session, subject_id=a.id, object_id=b.id)
    with pytest.raises(HTTPException) as exc_info:
        sf.create_source_fact(
            db_session,
            fact_type="biological_parent",
            subject_user_id=b.id,
            object_user_id=a.id,
            provenance="manual_entry",
        )
    error = extract_api_error(exc_info.value.detail)
    assert exc_info.value.status_code == 422
    assert error is not None and error["code"] == "SOURCE_FACT_CYCLE_FORBIDDEN"


def test_parent_cycle_long_chain_rejected(db_session) -> None:
    """A←B←C←D 已确认链上，再把最底层孩子立为顶层祖先的家长 → 拒。"""
    users = [create_user_with_pin(db_session, f"链{i}", "123456") for i in range(5)]
    for child, parent in zip(users, users[1:], strict=False):
        _create_parent_fact(db_session, subject_id=parent.id, object_id=child.id)
    with pytest.raises(HTTPException) as exc_info:
        sf.create_source_fact(
            db_session,
            fact_type="adoptive_parent",
            subject_user_id=users[0].id,
            object_user_id=users[-1].id,
            provenance="manual_entry",
        )
    error = extract_api_error(exc_info.value.detail)
    assert error is not None and error["code"] == "SOURCE_FACT_CYCLE_FORBIDDEN"


def test_parent_chain_depth_limit_boundary(db_session) -> None:
    """合同边界：≤32 层内的环被拒；恰好超出深度窗口的环不再上溯（放行）。"""
    depth = sf.PARENT_CHAIN_DEPTH_LIMIT  # 32
    users = [create_user_with_pin(db_session, f"深{i}", "123456") for i in range(depth + 2)]
    for child, parent in zip(users, users[1 : depth + 1], strict=False):
        _create_parent_fact(db_session, subject_id=parent.id, object_id=child.id)
    # 距离恰为 32 的祖先 → 命中并拒绝
    with pytest.raises(HTTPException) as exc_info:
        sf.create_source_fact(
            db_session,
            fact_type="step_parent",
            subject_user_id=users[depth].id,
            object_user_id=users[0].id,
            provenance="manual_entry",
        )
    error = extract_api_error(exc_info.value.detail)
    assert error is not None and error["code"] == "SOURCE_FACT_CYCLE_FORBIDDEN"
    # 距离 33（超出窗口）→ 当前合同不再上溯，允许写入
    escaped = sf.create_source_fact(
        db_session,
        fact_type="step_parent",
        subject_user_id=users[depth + 1].id,
        object_user_id=users[0].id,
        provenance="manual_entry",
    )
    assert escaped.state == "proposed"


# ---- sibling / 再婚 / partial unique ----


def test_direct_sibling_stands_without_parents(db_session) -> None:
    a, b = _make_pair(db_session, "兄妹")
    fact = sf.create_source_fact(
        db_session,
        fact_type="direct_sibling",
        subject_user_id=a.id,
        object_user_id=b.id,
        provenance="manual_entry",
    )
    sf.transition_source_fact(db_session, fact, "confirm")
    assert fact.state == "confirmed"
    parents = db_session.scalar(
        select(SourceFact).where(SourceFact.fact_type.in_(PARENT_FACT_TYPES))
    )
    assert parents is None  # 不反推任何父母


def test_remariage_spouses_coexist_and_rebuild(db_session) -> None:
    a = create_user_with_pin(db_session, "再婚者", "123456")
    b = create_user_with_pin(db_session, "前偶", "123456")
    c = create_user_with_pin(db_session, "现偶", "123456")
    first = sf.create_source_fact(
        db_session,
        fact_type="spouse",
        subject_user_id=a.id,
        object_user_id=b.id,
        provenance="connection_accept",
    )
    second = sf.create_source_fact(
        db_session,
        fact_type="spouse",
        subject_user_id=a.id,
        object_user_id=c.id,
        provenance="connection_accept",
    )
    sf.transition_source_fact(db_session, first, "confirm")
    sf.transition_source_fact(db_session, second, "confirm")
    rows = list(
        db_session.scalars(
            select(SourceFact).where(
                SourceFact.fact_type == "spouse", SourceFact.subject_user_id == a.id
            )
        )
    )
    assert {r.object_user_id for r in rows} == {b.id, c.id}  # 再婚两条并存合法

    sf.transition_source_fact(db_session, first, "revoke")
    rebuilt = sf.create_source_fact(
        db_session,
        fact_type="spouse",
        subject_user_id=a.id,
        object_user_id=b.id,
        provenance="manual_entry",
    )
    assert rebuilt.state == "proposed"  # revoked 后同元组可重建


def test_partial_unique_scope_and_revoked_recreate(db_session) -> None:
    from conftest import create_agent_fixture

    _, space = create_agent_fixture(db_session, name="空间")
    a, b = _make_pair(db_session, "唯一")
    kwargs = dict(fact_type="guardian", subject_user_id=a.id, object_user_id=b.id)

    global_fact = sf.create_source_fact(db_session, provenance="manual_entry", **kwargs)
    with pytest.raises(HTTPException) as exc_info:  # 同元组全局重复 → 409
        sf.create_source_fact(db_session, provenance="manual_entry", **kwargs)
    error = extract_api_error(exc_info.value.detail)
    assert error is not None and error["code"] == "SOURCE_FACT_DUPLICATE"

    space_fact = sf.create_source_fact(  # 不同空间可并存
        db_session, provenance="manual_entry", space_id=space.id, **kwargs
    )
    assert space_fact.space_id == space.id

    sf.transition_source_fact(db_session, global_fact, "confirm")
    sf.transition_source_fact(db_session, global_fact, "revoke")
    recreated = sf.create_source_fact(db_session, provenance="manual_entry", **kwargs)
    assert recreated.state == "proposed"  # revoked 不占坑


# ---- raw 原文不可变与关联 ----


def test_raw_input_immutable_by_trigger_and_linkage(db_session) -> None:
    a, b = _make_pair(db_session, "原文")
    raw = sf.create_raw_relation_input(
        db_session,
        author_account_id=a.account.id,
        text="老妈",
        context={"page": "relation_input", "space_id": None},
    )
    assert raw.text == "老妈"
    db_session.commit()  # 固化原文与事实，后续触发器回滚不至连带撤销建行

    fact = sf.create_source_fact(
        db_session,
        fact_type="biological_parent",
        subject_user_id=a.id,
        object_user_id=b.id,
        provenance="manual_entry",
        raw_text_id=raw.id,
    )
    assert fact.raw_text_id == raw.id  # 关联保存

    # append-only：任何 UPDATE 路径由触发器 ABORT（KI-3：词典/Agent 不得覆盖原文）
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE raw_relation_inputs SET text = :t WHERE id = :id"),
            {"t": "母亲", "id": raw.id},
        )
    db_session.rollback()
    db_session.expire_all()
    assert db_session.get(type(raw), raw.id).text == "老妈"  # type: ignore[union-attr]

    # 内容变更走 revise：revision+1 + .revised 事件；无变化拒绝
    other = sf.create_raw_relation_input(
        db_session,
        author_account_id=a.account.id,
        text="娘亲",
        context={"page": "relation_input"},
    )
    sf.revise_source_fact(db_session, fact, raw_text_id=other.id, actor_account_id=a.account.id)
    assert (fact.raw_text_id, fact.revision) == (other.id, 2)
    events = _fact_events(db_session, fact.id)
    assert len(events) == 1 and events[0].type == "source_fact.revised"

    with pytest.raises(HTTPException) as exc_info:
        sf.revise_source_fact(db_session, fact, raw_text_id=other.id)
    error = extract_api_error(exc_info.value.detail)
    assert error is not None and error["code"] == "VALIDATION_ERROR"


def test_raw_input_length_bounds(db_session) -> None:
    a, _ = _make_pair(db_session, "限长")
    from app.errors import VALIDATION_ERROR

    with pytest.raises(HTTPException) as exc_info:
        sf.create_raw_relation_input(
            db_session, author_account_id=a.account.id, text="", context={}
        )
    assert exc_info.value.status_code == 422

    with pytest.raises(HTTPException) as exc_info:
        sf.create_raw_relation_input(
            db_session, author_account_id=a.account.id, text="亲" * 201, context={}
        )
    error = extract_api_error(exc_info.value.detail)
    assert error is not None and error["code"] == VALIDATION_ERROR


# ---- v1 边映射工厂 ----


def test_seed_structural_edge_mapping_queryable(db_session) -> None:
    a, b = _make_pair(db_session, "映射")
    c, d = _make_pair(db_session, "映射偶")  # 配偶边用独立人对，避开 v1 同对唯一索引
    elder = create_v1_relation(db_session, from_user_id=a.id, to_user_id=b.id, dir_class="elder")
    fact = seed_structural_edge_to_fact(db_session, elder)
    assert (fact.fact_type, fact.subject_user_id, fact.object_user_id) == (
        "biological_parent",
        b.id,
        a.id,
    )
    row = db_session.scalar(
        select(SourceFact).where(
            SourceFact.fact_type == "biological_parent",
            SourceFact.subject_user_id == b.id,
            SourceFact.object_user_id == a.id,
        )
    )
    assert row is not None and row.state == "confirmed" and row.provenance == "connection_accept"
    # 直接落 confirmed 也写 source_fact.confirmed 事件（E2 失效链完整）
    events = _fact_events(db_session, fact.id)
    assert len(events) == 1 and events[0].type == "source_fact.confirmed"

    spouse_edge = create_v1_relation(
        db_session, from_user_id=c.id, to_user_id=d.id, dir_class="spouse"
    )
    spouse_fact = seed_structural_edge_to_fact(db_session, spouse_edge)
    assert (spouse_fact.fact_type, spouse_fact.subject_user_id) == ("spouse", c.id)


def test_seed_rejects_peer_and_non_active(db_session) -> None:
    a, b = _make_pair(db_session, "拒映1")
    peer = create_v1_relation(db_session, from_user_id=a.id, to_user_id=b.id, dir_class="peer")
    with pytest.raises(ValueError, match="dir_class"):
        seed_structural_edge_to_fact(db_session, peer)
    c, d = _make_pair(db_session, "拒映2")  # 独立人对，避开 v1 同对唯一索引
    pending = create_v1_relation(
        db_session, from_user_id=c.id, to_user_id=d.id, dir_class="younger", status="pending"
    )
    with pytest.raises(ValueError, match="active"):
        seed_structural_edge_to_fact(db_session, pending)
    e, f = _make_pair(db_session, "拒映3")
    younger = create_v1_relation(
        db_session, from_user_id=e.id, to_user_id=f.id, dir_class="younger"
    )
    fact = seed_structural_edge_to_fact(db_session, younger)
    assert (fact.subject_user_id, fact.object_user_id) == (e.id, f.id)  # f 是 e 的长辈


# ---- social_relations 存储（不参加血缘路径）----


def test_social_relations_storage_and_check(db_session) -> None:
    a, b = _make_pair(db_session, "朋友")
    row = SocialRelation(
        relation_kind="friend",
        user_a_id=a.id,
        user_b_id=b.id,
        note="球友",
        created_at=utcnow(),
    )
    db_session.add(row)
    db_session.flush()
    fetched = db_session.scalar(
        select(SocialRelation).where(SocialRelation.relation_kind == "friend")
    )
    assert fetched is not None and (fetched.user_a_id, fetched.user_b_id) == (a.id, b.id)

    bad = SocialRelation(
        relation_kind="nemesis", user_a_id=a.id, user_b_id=b.id, created_at=row.created_at
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    self_row = SocialRelation(
        relation_kind="other", user_a_id=a.id, user_b_id=a.id, created_at=row.created_at
    )
    db_session.add(self_row)
    with pytest.raises(IntegrityError):  # 自环兜底拒绝
        db_session.flush()
    db_session.rollback()


# ---- 配置 flag 默认关闭 ----


def test_relationship_intelligence_flag_default_off() -> None:
    from app import config

    assert config.RELATIONSHIP_INTELLIGENCE_ENABLED is False
