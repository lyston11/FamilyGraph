"""Steward 作业与推荐矩阵集成测试（V2.4 Block S1，纯服务层，不测 API）。

覆盖 PRD AC-ST1/ST2/ST3/ST4/ST5/ST7：
1. Steward Job 幂等：同 trigger 重复 enqueue 同一 job；同 cursor 再执行零副作用；
   worker crash after card insert 重跑不重复（AC-ST2）。
2. 跨空间对抗：Steward Job bound space A，他空间 B 的 confirmed 事实不进入 A 的
   finding/card；operator 身份不放宽可见性（AC-ST1 底）。
3. dedupe/evidence_version/supersede：相同 evidence_version 不重复出卡；
   evidence_version 变更出新卡且旧卡 superseded；membership revoke 触发未执行卡
   supersede（AC-ST3）。
4. 推荐矩阵逐行（AC-ST5，recommendation_matrix 纯函数 + create_card 端到端）：
   friend/colleague 永不出家族卡（AC-ST4）；partner 仅 create_household（需双方披露）；
   spouse→household_link + lineage_request 两张；parent-child(bio/adopt/step)/
   confirmed sibling 按 household/lineage 选择；guardian 默认 household；
   未确档 Profile/proposed/disputed Fact 零卡（AC-ST4）。
5. DomainEvent：card viewed/dismissed/accepted/executed/expired/superseded 各落事件；
   steward.conflict_detected/gap_detected 在冲突/缺失时落事件（AC-ST7）。
6. BehaviorProjection：put_projection/set_kind_cooldown/kind_in_cooldown 往返；
   cooldown 阻止短期内重复出卡。
7. 冲突/缺失 detector：direct_sibling 无共同父母只报 gap 不虚构父母；两条矛盾
   biological_parent 落 conflict_detected。
"""

from __future__ import annotations

import fastapi
import pytest
from conftest import (
    create_space_member,
    create_user_with_pin,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models.derived_fact import DerivedFact
from app.models.relationship_facts import SourceFact
from app.models.space import FamilySpace, SpaceProfileRef
from app.models.steward import ActionCard, BehaviorProjection, StewardJob
from app.models.user import User
from app.models.v2_foundation import DomainEvent, PlatformRoleAssignment
from app.services import action_cards, steward
from app.services import disclosure as disclosure_service
from app.services import recommendation_matrix as rm
from app.services import source_facts as sf
from app.services.domain_events import emit as emit_event
from app.services.platform_roles import ROLE_PLATFORM_OPERATOR
from app.utils import timeutil

# ---- 造数辅助 ----


def _err_code(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    assert isinstance(detail, dict) and "__api_error__" in detail, exc
    return str(detail["__api_error__"]["code"])


def _space(session: Session, name: str, *, kind: str = "household") -> FamilySpace:
    owner = create_user_with_pin(session, f"{name}-own", "123456")
    space = FamilySpace(name=name, kind=kind, owner_id=owner.id, created_at=owner.created_at)
    session.add(space)
    session.commit()
    return space


def _person(
    session: Session,
    space_id: int | None,
    name: str,
    *,
    gender: str = "m",
    member: bool = True,
    ref: bool = True,
    confirmed: bool = True,
) -> User:
    user = create_user_with_pin(
        session,
        name,
        "123456",
        gender=gender,
        profile_status="identity_confirmed" if confirmed else "provisional",
    )
    if space_id is not None and member:
        create_space_member(session, space_id, user.id)
    if space_id is not None and ref and not member:
        session.add(
            SpaceProfileRef(
                space_id=space_id, user_id=user.id, status="active", created_at=timeutil.utcnow()
            )
        )
    session.commit()
    return user


def _confirm(
    session: Session,
    fact_type: str,
    subject_id: int,
    object_id: int,
    *,
    space_id: int | None = None,
) -> SourceFact:
    fact = sf.create_source_fact(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        provenance="manual_entry",
        space_id=space_id,
    )
    sf.transition_source_fact(session, fact, "confirm")
    session.commit()
    return fact


def _fact(
    session: Session,
    fact_type: str,
    subject_id: int,
    object_id: int,
    *,
    space_id: int | None = None,
    state: str = "confirmed",
) -> SourceFact:
    """直建任意 state 的 SourceFact（绕过 FSM/环检测，用于构造冲突场景）。"""
    now = timeutil.utcnow()
    row = SourceFact(
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        space_id=space_id,
        provenance="import",
        state=state,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def _ref(session: Session, space_id: int, uid: int) -> None:
    session.add(
        SpaceProfileRef(
            space_id=space_id, user_id=uid, status="active", created_at=timeutil.utcnow()
        )
    )
    session.commit()


def _emit_fact_event(
    session: Session, fact: SourceFact, *, event_type: str = "source_fact.confirmed"
) -> DomainEvent:
    return emit_event(
        session,
        event_type=event_type,
        aggregate_type="source_fact",
        aggregate_id=fact.id,
        payload={"subject_user_id": fact.subject_user_id, "object_user_id": fact.object_user_id},
        space_id=fact.space_id,
    )


def test_domain_event_automatically_enqueues_scoped_job(db_session) -> None:
    """领域事件提交前即登记同空间 Job，重复事件只推进同一水位。"""
    space = _space(db_session, "auto-job", kind="lineage")
    first = emit_event(
        db_session,
        event_type="term.corrected",
        aggregate_type="term_entry",
        aggregate_id=11,
        payload={"term": "示例"},
        space_id=space.id,
    )
    job = db_session.scalar(select(StewardJob).where(StewardJob.space_id == space.id))
    assert job is not None and job.status == "queued" and job.trigger_cursor == first.id

    second = emit_event(
        db_session,
        event_type="disclosure.updated",
        aggregate_type="profile",
        aggregate_id=12,
        payload={},
        space_id=space.id,
    )
    db_session.flush()
    jobs = list(db_session.scalars(select(StewardJob).where(StewardJob.space_id == space.id)))
    assert len(jobs) == 1 and jobs[0].trigger_cursor == second.id
    db_session.rollback()


def _run_job(session: Session, space: FamilySpace, cursor: int, *, cause: str = "source_fact"):
    job, _ = steward.enqueue_steward_job(
        session, space_id=space.id, cause=cause, trigger_cursor=cursor
    )
    granted = steward.lease_next_steward_job(session, leased_by="test-worker", space_id=space.id)
    assert granted is not None and granted.id == job.id
    return steward.run_steward_job(session, granted), job


def _cards(session: Session, space_id: int) -> list[ActionCard]:
    return list(session.scalars(select(ActionCard).where(ActionCard.space_id == space_id)))


def _events(session: Session, type_prefix: str) -> list[DomainEvent]:
    return list(
        session.scalars(select(DomainEvent).where(DomainEvent.type.like(f"{type_prefix}%")))
    )


def _derived_count(session: Session) -> int:
    return len(list(session.scalars(select(DerivedFact))))


# ---- 1. Steward Job 幂等（AC-ST2）----


def test_enqueue_idempotent_same_trigger_returns_same_job(db_session) -> None:
    """同 trigger_cursor 重复 enqueue 同一空间 → 返回同一 job（created=False）。"""
    space = _space(db_session, "idem")
    job1, created1 = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=5
    )
    assert created1 is True and job1.status == "queued"
    job2, created2 = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=5
    )
    assert created2 is False and job2.id == job1.id


def test_enqueue_older_cursor_when_active_busy_returns_409(db_session) -> None:
    """活跃作业存在且其 trigger_cursor 低于新请求水位（非 admin_rerun）→ 409。"""
    space = _space(db_session, "busy")
    # 先入队一个水位=5 的作业
    steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=5
    )
    # 再请求更高水位 10：活跃作业水位 5 < 10 → 409
    with pytest.raises(fastapi.HTTPException) as exc_info:
        steward.enqueue_steward_job(
            db_session, space_id=space.id, cause="source_fact", trigger_cursor=10
        )
    assert _err_code(exc_info.value) == "STEWARD_JOB_SPACE_BUSY"


def test_execute_same_cursor_zero_side_effects(db_session) -> None:
    """execute 后同 cursor 再次（经 admin_rerun）重跑：DerivedFact 行数与卡片不变。"""
    space = _space(db_session, "rerun", kind="lineage")
    a = _person(db_session, space.id, "A", member=True)
    b = _person(db_session, space.id, "B", gender="f", member=False, ref=True)
    fact = _confirm(db_session, "spouse", a.id, b.id, space_id=space.id)
    ev = _emit_fact_event(db_session, fact)
    db_session.commit()
    summary, job = _run_job(db_session, space, ev.id)
    assert summary["stats"]["cards_created"] == 2  # spouse: household_link + lineage_request
    derived_after_first = _derived_count(db_session)
    cards_after_first = len(_cards(db_session, space.id))

    # admin_rerun 强制新建作业并重跑同一 cursor
    job2, created = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="admin_rerun", trigger_cursor=ev.id
    )
    assert created is True
    grant2 = steward.lease_next_steward_job(db_session, leased_by="test-worker")
    assert grant2 is not None and grant2.id == job2.id
    summary2 = steward.run_steward_job(db_session, grant2)
    assert summary2["stats"]["cards_created"] == 0  # 去重：同证据不再出卡
    assert summary2["stats"]["cards_superseded"] == 0
    assert _derived_count(db_session) == derived_after_first
    assert len(_cards(db_session, space.id)) == cards_after_first


def test_worker_crash_after_card_insert_rerun_no_duplicate(db_session) -> None:
    """模拟 worker 在插入卡片后、提交 job checkpoint 前崩溃：
    run_steward_job 的整体立即事务回滚卡片插入；重跑不重复出卡（AC-ST2）。"""
    space = _space(db_session, "crash", kind="lineage")
    a = _person(db_session, space.id, "A", member=True)
    b = _person(db_session, space.id, "B", gender="f", member=False, ref=True)
    fact = _confirm(db_session, "spouse", a.id, b.id, space_id=space.id)
    ev = _emit_fact_event(db_session, fact)
    db_session.commit()

    # 入队 + lease
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=ev.id
    )
    grant = steward.lease_next_steward_job(db_session, leased_by="test-worker")
    assert grant is not None and grant.id == job.id

    # 模拟执行中途异常（事务整体回滚）：直接 raise，run_steward_job 的
    # _immediate_tx 会回滚，job 状态回到 leased（未结算）。
    with pytest.raises(RuntimeError):
        # 通过在执行体内抛错模拟崩溃；用 monkeypatch 替换 _recommend_cards
        import app.services.steward as st_mod

        original = st_mod._recommend_cards

        def boom(s, sp, vis, *, now):
            raise RuntimeError("simulated crash after card insert")

        st_mod._recommend_cards = boom  # type: ignore[assignment]
        try:
            steward.run_steward_job(db_session, grant)
        finally:
            st_mod._recommend_cards = original  # type: ignore[assignment]

    # 崩溃回滚后：无卡片、job 仍非 succeeded
    assert _cards(db_session, space.id) == []
    db_session.refresh(job)
    assert job.status != "succeeded"

    # 把 lease 失效后 reaper 回队，重跑正常路径
    job.lease_expires_at = timeutil.utcnow()
    db_session.commit()
    steward.reaper_pass(db_session)
    db_session.refresh(job)
    assert job.status == "queued"  # attempt 未耗尽 → 回队

    grant2 = steward.lease_next_steward_job(db_session, leased_by="test-worker")
    assert grant2 is not None and grant2.id == job.id
    summary = steward.run_steward_job(db_session, grant2)
    assert summary["stats"]["cards_created"] == 2
    cards = _cards(db_session, space.id)
    assert len(cards) == 2
    # 同空间同证据只有这两张；dedupe key 各一
    assert {c.dedupe_key for c in cards} == {
        action_cards.dedupe_key_for("household_link", a.id, b.id),
        action_cards.dedupe_key_for("lineage_request", a.id, b.id),
    }


# ---- 2. 跨空间对抗（AC-ST1 底）----


def test_cross_space_fact_not_consumed_by_other_space_job(db_session) -> None:
    """空间 B 的 confirmed 事实不进入空间 A 的 finding/card（AC-ST1 底）。"""
    space_a = _space(db_session, "A", kind="lineage")
    space_b = _space(db_session, "B", kind="lineage")
    a = _person(db_session, space_a.id, "A", member=True)
    b = _person(db_session, space_b.id, "B", gender="f", member=True)
    # 空间 B 的 spouse 事实（subject/object 均仅 B 空间可见）
    fact_b = _confirm(db_session, "spouse", a.id, b.id, space_id=space_b.id)
    # a 不在空间 B 可见集合里（不是 B 成员、无 B 引用），故该事实也不应被 A 消费
    ev = _emit_fact_event(db_session, fact_b)
    db_session.commit()

    summary, _ = _run_job(db_session, space_a, ev.id)
    # A 的可见集合不含 b，事实被过滤；A 不出卡、不出 finding
    assert summary["stats"]["cards_created"] == 0
    assert _cards(db_session, space_a.id) == []


def test_operator_does_not_widen_steward_visibility(db_session) -> None:
    """platform_operator 角色不放宽 Steward 可见性：他空间 confirmed 事实仍不进入本空间。"""
    space_a = _space(db_session, "opA", kind="lineage")
    space_b = _space(db_session, "opB", kind="lineage")
    # 给 space_a 的 owner 追加 platform_operator 角色
    operator = db_session.get(User, space_a.owner_id)
    db_session.add(
        PlatformRoleAssignment(
            account_id=operator.account.id,
            role=ROLE_PLATFORM_OPERATOR,
            created_by=None,
            created_at=timeutil.utcnow(),
        )
    )
    db_session.commit()
    a = _person(db_session, space_a.id, "A", member=True)
    b = _person(db_session, space_b.id, "B", gender="f", member=True)
    fact_b = _confirm(db_session, "spouse", a.id, b.id, space_id=space_b.id)
    ev = _emit_fact_event(db_session, fact_b)
    db_session.commit()

    summary, _ = _run_job(db_session, space_a, ev.id)
    # operator 身份未让 A 看到空间 B 的事实
    assert summary["stats"]["cards_created"] == 0
    assert _cards(db_session, space_a.id) == []


# ---- 3. dedupe / evidence_version / supersede（AC-ST3）----


def test_same_evidence_version_no_duplicate_card(db_session) -> None:
    """相同 (kind,subject,object) 相同 evidence_version 不重复出卡。"""
    space = _space(db_session, "dup", kind="household")
    a = _person(db_session, space.id, "A", member=False, ref=True)
    b = _person(db_session, space.id, "B", gender="f", member=False, ref=True)
    fact = _confirm(db_session, "spouse", a.id, b.id, space_id=space.id)
    ev = _emit_fact_event(db_session, fact)
    db_session.commit()
    summary, _ = _run_job(db_session, space, ev.id)
    assert summary["stats"]["cards_created"] == 1
    # 同 cursor admin_rerun 重跑：零新增
    job2, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="admin_rerun", trigger_cursor=ev.id
    )
    grant2 = steward.lease_next_steward_job(db_session, leased_by="w")
    summary2 = steward.run_steward_job(db_session, grant2)
    assert summary2["stats"]["cards_created"] == 0
    assert len(_cards(db_session, space.id)) == 1


def test_evidence_version_change_supersedes_old_card(db_session) -> None:
    """事实 revision 变化 → evidence_hash 变化 → 新卡插入、旧活动卡 superseded。"""
    space = _space(db_session, "evver", kind="household")
    a = _person(db_session, space.id, "A", member=False, ref=True)
    b = _person(db_session, space.id, "B", gender="f", member=False, ref=True)
    fact = _confirm(db_session, "spouse", a.id, b.id, space_id=space.id)
    ev1 = _emit_fact_event(db_session, fact)
    db_session.commit()
    _run_job(db_session, space, ev1.id)
    old = db_session.scalar(
        select(ActionCard).where(
            ActionCard.space_id == space.id, ActionCard.kind == "household_link"
        )
    )
    assert old is not None and old.state == "pending"
    old_version = old.evidence_version

    # 触发事实内容变化（revised 事件）→ revision+1，evidence_hash 改变
    # 服务层 revise_source_fact 需要 raw_text_id；直接改 revision 模拟证据版本变化
    fact.revision += 1
    db_session.flush()
    ev2 = emit_event(
        db_session,
        event_type="source_fact.revised",
        aggregate_type="source_fact",
        aggregate_id=fact.id,
        payload={"subject_user_id": fact.subject_user_id, "object_user_id": fact.object_user_id},
        space_id=space.id,
    )
    db_session.commit()
    summary, _ = _run_job(db_session, space, ev2.id)
    db_session.refresh(old)
    assert old.state == "superseded"
    new = db_session.scalar(
        select(ActionCard).where(
            ActionCard.space_id == space.id,
            ActionCard.kind == "household_link",
            ActionCard.state == "pending",
        )
    )
    assert new is not None and new.evidence_version == old_version + 1
    assert old.superseded_by_id == new.id


def test_membership_revoke_supersedes_unexecuted_card(db_session) -> None:
    """成员资格/事实撤销触发资格丧失 → 未执行卡 superseded（不静默写/发）。"""
    space = _space(db_session, "rev", kind="household")
    a = _person(db_session, space.id, "A", member=False, ref=True)
    b = _person(db_session, space.id, "B", gender="f", member=False, ref=True)
    fact = _confirm(db_session, "spouse", a.id, b.id, space_id=space.id)
    ev1 = _emit_fact_event(db_session, fact)
    db_session.commit()
    _run_job(db_session, space, ev1.id)
    card = db_session.scalar(
        select(ActionCard).where(
            ActionCard.space_id == space.id, ActionCard.kind == "household_link"
        )
    )
    assert card is not None and card.state == "pending"

    # 撤销事实 → 资格丧失 → revalidate 把卡置 superseded（evidence_invalidated）
    sf.transition_source_fact(db_session, fact, "revoke")
    ev2 = emit_event(
        db_session,
        event_type="source_fact.revoked",
        aggregate_type="source_fact",
        aggregate_id=fact.id,
        payload={"subject_user_id": fact.subject_user_id, "object_user_id": fact.object_user_id},
        space_id=space.id,
    )
    db_session.commit()
    summary, _ = _run_job(db_session, space, ev2.id)
    assert summary["stats"]["cards_superseded"] >= 1
    db_session.refresh(card)
    assert card.state == "superseded"
    assert card.failed_reason == "evidence_invalidated"
    # 无新卡生成（资格已丧失）
    pending = [c for c in _cards(db_session, space.id) if c.state == "pending"]
    assert pending == []


# ---- 4. 推荐矩阵逐行（AC-ST4/ST5）----


def _matrix_outcome(**overrides) -> rm.RecommendationOutcome:
    base = dict(
        fact_type="spouse",
        fact_state="confirmed",
        subject_identity_confirmed=True,
        object_identity_confirmed=True,
        creation_choices=frozenset({"household"}),
        mutual_disclosure_allowed=False,
        share_household_membership=False,
        lineage_request_possible=False,
        in_cooldown=False,
    )
    base.update(overrides)
    return rm.evaluate_recommendation(rm.RecommendationInput(**base))  # type: ignore[arg-type]


def test_matrix_friend_colleague_never_eligible() -> None:
    """friend/colleague 等未知 fact_type 一律 fail-closed（AC-ST4）。"""
    for ft in ("friend", "colleague", "acquaintance", "other", "bogus"):
        out = _matrix_outcome(fact_type=ft)
        assert out.eligible is False
        assert out.reason == rm.REASON_UNKNOWN_FACT_TYPE
        assert out.actions == ()


def test_matrix_unconfirmed_profile_or_fact_ineligible() -> None:
    """任一端未 identity_confirmed 或事实非 confirmed → 不出卡（AC-ST4）。"""
    assert (
        _matrix_outcome(subject_identity_confirmed=False).reason == rm.REASON_PROFILE_NOT_CONFIRMED
    )
    assert (
        _matrix_outcome(object_identity_confirmed=False).reason == rm.REASON_PROFILE_NOT_CONFIRMED
    )
    assert _matrix_outcome(fact_state="proposed").reason == rm.REASON_FACT_NOT_CONFIRMED
    assert _matrix_outcome(fact_state="disputed").reason == rm.REASON_FACT_NOT_CONFIRMED


def test_matrix_partner_requires_disclosure_only_household() -> None:
    """partner：双方确认且允许披露 → 仅 create_household；未披露拒绝；绝不 lineage。"""
    assert (
        _matrix_outcome(fact_type="partner", mutual_disclosure_allowed=False).reason
        == rm.REASON_DISCLOSURE_NOT_ALLOWED
    )
    out = _matrix_outcome(fact_type="partner", mutual_disclosure_allowed=True)
    assert out.eligible is True and out.actions == (rm.ACTION_CREATE_HOUSEHOLD,)
    # 即使选择 lineage 也不出 lineage 卡
    out2 = _matrix_outcome(
        fact_type="partner",
        mutual_disclosure_allowed=True,
        creation_choices=frozenset({"household", "lineage"}),
        lineage_request_possible=True,
    )
    assert out2.actions == (rm.ACTION_CREATE_HOUSEHOLD,)


def test_matrix_spouse_household_and_lineage() -> None:
    """spouse：create_household + 可申请 lineage（不自动通过）。"""
    out = _matrix_outcome(fact_type="spouse", lineage_request_possible=True)
    assert out.eligible is True
    assert out.actions == (rm.ACTION_CREATE_HOUSEHOLD, rm.ACTION_REQUEST_LINEAGE)
    # 已共享 household 时不再出 household 卡，仅 lineage
    out2 = _matrix_outcome(
        fact_type="spouse", lineage_request_possible=True, share_household_membership=True
    )
    assert out2.actions == (rm.ACTION_REQUEST_LINEAGE,)


@pytest.mark.parametrize("fact_type", ["biological_parent", "adoptive_parent", "step_parent"])
def test_matrix_parent_child_by_creation_choice(fact_type: str) -> None:
    """parent-child(bio/adopt/step) 按 household/lineage 选择出对应卡。"""
    # household 选择
    out = _matrix_outcome(fact_type=fact_type, creation_choices=frozenset({"household"}))
    assert out.eligible is True and out.actions == (rm.ACTION_CREATE_HOUSEHOLD,)
    # lineage 选择
    out = _matrix_outcome(
        fact_type=fact_type,
        creation_choices=frozenset({"lineage"}),
        lineage_request_possible=True,
    )
    assert out.actions == (rm.ACTION_REQUEST_LINEAGE,)
    # 两者
    out = _matrix_outcome(
        fact_type=fact_type,
        creation_choices=frozenset({"household", "lineage"}),
        lineage_request_possible=True,
    )
    assert out.actions == (rm.ACTION_CREATE_HOUSEHOLD, rm.ACTION_REQUEST_LINEAGE)
    # no-space → 不出卡
    assert _matrix_outcome(
        fact_type=fact_type, creation_choices=frozenset({"no-space"})
    ).reason == (rm.REASON_CREATION_NO_SPACE)


def test_matrix_confirmed_sibling_by_creation_choice() -> None:
    """confirmed sibling 按 household/lineage 选择。"""
    out = _matrix_outcome(fact_type="direct_sibling", creation_choices=frozenset({"household"}))
    assert out.eligible is True and out.actions == (rm.ACTION_CREATE_HOUSEHOLD,)
    out = _matrix_outcome(
        fact_type="direct_sibling",
        creation_choices=frozenset({"lineage"}),
        lineage_request_possible=True,
    )
    assert out.actions == (rm.ACTION_REQUEST_LINEAGE,)


def test_matrix_guardian_defaults_household_only() -> None:
    """guardian 默认 household；即使选择含 lineage 也只出 household 卡。"""
    out = _matrix_outcome(fact_type="guardian", creation_choices=frozenset({"household"}))
    assert out.eligible is True and out.actions == (rm.ACTION_CREATE_HOUSEHOLD,)
    # 含 lineage 仍只 household（guardian 绝不 lineage）
    out = _matrix_outcome(
        fact_type="guardian",
        creation_choices=frozenset({"household", "lineage"}),
        lineage_request_possible=True,
    )
    assert out.actions == (rm.ACTION_CREATE_HOUSEHOLD,)
    # 仅 lineage → guardian 不出 lineage 卡，无任何动作 → 不可用
    assert not _matrix_outcome(
        fact_type="guardian",
        creation_choices=frozenset({"lineage"}),
        lineage_request_possible=True,
    ).eligible


def test_matrix_cooldown_blocks() -> None:
    """in_cooldown=True → 不出卡。"""
    assert _matrix_outcome(in_cooldown=True).reason == rm.REASON_COOLDOWN_ACTIVE


def test_matrix_already_connected_when_nothing_to_do() -> None:
    """已共享 household 且无 lineage 可申请 → no_eligible_action。"""
    out = _matrix_outcome(share_household_membership=True, lineage_request_possible=False)
    assert out.eligible is False and out.reason == rm.REASON_ALREADY_CONNECTED


# ---- 端到端出卡（recommendation_matrix + create_card 经 Steward 作业）----


def test_e2e_spouse_lineage_produces_two_cards(db_session) -> None:
    """spouse 在 lineage 空间（一端成员一端引用）→ household_link + lineage_request 两张。"""
    space = _space(db_session, "sp", kind="lineage")
    a = _person(db_session, space.id, "A", member=True)
    b = _person(db_session, space.id, "B", gender="f", member=False, ref=True)
    fact = _confirm(db_session, "spouse", a.id, b.id, space_id=space.id)
    ev = _emit_fact_event(db_session, fact)
    db_session.commit()
    summary, _ = _run_job(db_session, space, ev.id)
    cards = _cards(db_session, space.id)
    kinds = sorted(c.kind for c in cards)
    assert kinds == ["household_link", "lineage_request"]
    assert all(c.state == "pending" for c in cards)
    # recipient 是 subject 的 account
    for c in cards:
        assert c.subject_user_id == a.id and c.object_user_id == b.id
        assert c.reason_text  # 模板文案非空
        assert c.privacy_effect  # 隐私影响非空
        assert c.expires_at is not None  # 有效期


def test_e2e_spouse_household_shared_no_card(db_session) -> None:
    """household 空间双方已是 active 成员（共享）→ spouse 无 card（already_connected）。"""
    space = _space(db_session, "sp-hh", kind="household")
    a = _person(db_session, space.id, "A", member=True)
    b = _person(db_session, space.id, "B", gender="f", member=True)
    fact = _confirm(db_session, "spouse", a.id, b.id, space_id=space.id)
    ev = _emit_fact_event(db_session, fact)
    db_session.commit()
    summary, _ = _run_job(db_session, space, ev.id)
    assert summary["stats"]["cards_created"] == 0
    assert _cards(db_session, space.id) == []


def test_e2e_partner_no_disclosure_no_card(db_session) -> None:
    """partner 未披露（当前空间双方默认没有 disclosure grant）→ 不出卡。"""
    space = _space(db_session, "part", kind="household")
    a = _person(db_session, space.id, "A", member=False, ref=True)
    b = _person(db_session, space.id, "B", gender="f", member=False, ref=True)
    fact = _confirm(db_session, "partner", a.id, b.id, space_id=space.id)
    ev = _emit_fact_event(db_session, fact)
    db_session.commit()
    summary, _ = _run_job(db_session, space, ev.id)
    assert summary["stats"]["cards_created"] == 0


def test_e2e_partner_with_mutual_disclosure_household_card(db_session) -> None:
    """partner 双方在当前空间各自开放基础披露后，仅生成 household 卡。"""
    space = _space(db_session, "part-open", kind="household")
    a = _person(db_session, space.id, "A", member=False, ref=True)
    b = _person(db_session, space.id, "B", gender="f", member=False, ref=True)
    disclosure_service.set_basic_disclosure(db_session, a, {"bio": True})
    disclosure_service.set_basic_disclosure(db_session, b, {"bio": True})
    db_session.commit()
    fact = _confirm(db_session, "partner", a.id, b.id, space_id=space.id)
    ev = _emit_fact_event(db_session, fact)
    db_session.commit()
    summary, _ = _run_job(db_session, space, ev.id)
    assert summary["stats"]["cards_created"] == 1
    assert [card.kind for card in _cards(db_session, space.id)] == ["household_link"]


def test_e2e_parent_child_household_card(db_session) -> None:
    """parent-child 在 household 空间（双方可见且非共享成员）→ household_link 一张。"""
    space = _space(db_session, "pc", kind="household")
    parent = _person(db_session, space.id, "P", member=False, ref=True)
    child = _person(db_session, space.id, "C", gender="f", member=False, ref=True)
    fact = _confirm(db_session, "biological_parent", parent.id, child.id, space_id=space.id)
    ev = _emit_fact_event(db_session, fact)
    db_session.commit()
    summary, _ = _run_job(db_session, space, ev.id)
    cards = _cards(db_session, space.id)
    assert [c.kind for c in cards] == ["household_link"]
    assert cards[0].subject_user_id == parent.id and cards[0].object_user_id == child.id


def test_e2e_unconfirmed_profile_no_card(db_session) -> None:
    """未确档 Profile（provisional）→ 零卡（AC-ST4）。"""
    space = _space(db_session, "unc", kind="household")
    a = _person(db_session, space.id, "A", member=False, ref=True, confirmed=False)
    b = _person(db_session, space.id, "B", gender="f", member=False, ref=True)
    fact = _confirm(db_session, "spouse", a.id, b.id, space_id=space.id)
    ev = _emit_fact_event(db_session, fact)
    db_session.commit()
    summary, _ = _run_job(db_session, space, ev.id)
    assert summary["stats"]["cards_created"] == 0
    assert _cards(db_session, space.id) == []


def test_e2e_proposed_or_disputed_fact_no_card(db_session) -> None:
    """proposed/disputed Fact 不进入推荐（AC-ST4）。"""
    space = _space(db_session, "pd", kind="household")
    a = _person(db_session, space.id, "A", member=False, ref=True)
    b = _person(db_session, space.id, "B", gender="f", member=False, ref=True)
    # proposed（不 confirm）
    fact = sf.create_source_fact(
        db_session,
        fact_type="spouse",
        subject_user_id=a.id,
        object_user_id=b.id,
        provenance="manual_entry",
        space_id=space.id,
    )
    db_session.commit()
    ev = emit_event(
        db_session,
        event_type="source_fact.proposed",
        aggregate_type="source_fact",
        aggregate_id=fact.id,
        payload={"subject_user_id": a.id, "object_user_id": b.id},
        space_id=space.id,
    )
    db_session.commit()
    summary, _ = _run_job(db_session, space, ev.id)
    assert summary["stats"]["cards_created"] == 0

    # disputed：从 proposed 直接 dispute（FSM：proposed→disputed）
    sf.transition_source_fact(db_session, fact, "dispute")
    ev2 = emit_event(
        db_session,
        event_type="source_fact.disputed",
        aggregate_type="source_fact",
        aggregate_id=fact.id,
        payload={"subject_user_id": a.id, "object_user_id": b.id},
        space_id=space.id,
    )
    db_session.commit()
    job2, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="admin_rerun", trigger_cursor=ev2.id
    )
    grant2 = steward.lease_next_steward_job(db_session, leased_by="w")
    summary2 = steward.run_steward_job(db_session, grant2)
    assert summary2["stats"]["cards_created"] == 0


# ---- 5. DomainEvent（AC-ST7）----


def test_card_lifecycle_events_emitted(db_session) -> None:
    """card viewed/accepted/executed/expired/superseded/dismissed 各落对应 type 事件。"""
    space = _space(db_session, "evt", kind="household")
    a = _person(db_session, space.id, "A", member=False, ref=True)
    b = _person(db_session, space.id, "B", gender="f", member=False, ref=True)
    fact = _confirm(db_session, "spouse", a.id, b.id, space_id=space.id)
    ev = _emit_fact_event(db_session, fact)
    db_session.commit()
    _run_job(db_session, space, ev.id)
    card = db_session.scalar(
        select(ActionCard).where(
            ActionCard.space_id == space.id, ActionCard.kind == "household_link"
        )
    )
    assert card is not None

    # viewed → card.viewed
    action_cards.transition_card(
        db_session, card, "view", expected_revision=card.revision, actor_account_id=a.account.id
    )
    # accepted → card.accepted
    action_cards.transition_card(
        db_session, card, "accept", expected_revision=card.revision, actor_account_id=a.account.id
    )
    # executed → card.executed
    action_cards.transition_card(
        db_session,
        card,
        "execute",
        expected_revision=card.revision,
        actor_account_id=a.account.id,
        executed_event_id=77,
    )
    db_session.commit()

    types_seen = {e.type for e in _events(db_session, "card.")}
    assert "card.viewed" in types_seen
    assert "card.accepted" in types_seen
    assert "card.executed" in types_seen

    # dismissed 路径（新卡，用独立 object 构造）
    c = _person(db_session, space.id, "C2", gender="m", member=False, ref=True)
    fact_c = _confirm(db_session, "spouse", a.id, c.id, space_id=space.id)
    ev_c = _emit_fact_event(db_session, fact_c)
    db_session.commit()
    _run_job(db_session, space, ev_c.id)
    card_c = db_session.scalar(
        select(ActionCard).where(
            ActionCard.space_id == space.id,
            ActionCard.kind == "household_link",
            ActionCard.state == "pending",
        )
    )
    assert card_c is not None
    action_cards.transition_card(db_session, card_c, "dismiss", expected_revision=card_c.revision)
    db_session.commit()
    assert "card.dismissed" in {e.type for e in _events(db_session, "card.")}

    # expired 路径
    d = _person(db_session, space.id, "D2", gender="m", member=False, ref=True)
    fact_d = _confirm(db_session, "spouse", a.id, d.id, space_id=space.id)
    ev_d = _emit_fact_event(db_session, fact_d)
    db_session.commit()
    _run_job(db_session, space, ev_d.id)
    card_d = db_session.scalar(
        select(ActionCard).where(
            ActionCard.space_id == space.id,
            ActionCard.kind == "household_link",
            ActionCard.state == "pending",
        )
    )
    assert card_d is not None
    action_cards.transition_card(db_session, card_d, "expire", expected_revision=card_d.revision)
    db_session.commit()
    assert "card.expired" in {e.type for e in _events(db_session, "card.")}

    # superseded 路径（手动调用）
    e = _person(db_session, space.id, "E2", gender="m", member=False, ref=True)
    fact_e = _confirm(db_session, "spouse", a.id, e.id, space_id=space.id)
    ev_e = _emit_fact_event(db_session, fact_e)
    db_session.commit()
    _run_job(db_session, space, ev_e.id)
    card_e = db_session.scalar(
        select(ActionCard).where(
            ActionCard.space_id == space.id,
            ActionCard.kind == "household_link",
            ActionCard.state == "pending",
        )
    )
    assert card_e is not None
    action_cards.supersede_card(db_session, card_e, reason="eligibility_lost", superseded_by_id=999)
    db_session.commit()
    assert "card.superseded" in {e.type for e in _events(db_session, "card.")}


def test_steward_job_completed_event_emitted(db_session) -> None:
    """Steward Job 成功执行落 steward.job_completed 事件。"""
    space = _space(db_session, "jc", kind="household")
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="integrity_scan", trigger_cursor=1
    )
    grant = steward.lease_next_steward_job(db_session, leased_by="w")
    assert grant is not None
    steward.run_steward_job(db_session, grant)
    db_session.commit()
    events = [e for e in _events(db_session, "steward.") if e.type == "steward.job_completed"]
    assert len(events) == 1
    assert events[0].payload["space_id"] == space.id


def test_steward_job_failed_event_on_settle(db_session) -> None:
    """execute_steward_job 异常时把作业标 failed 并落 steward.job_failed。"""
    space = _space(db_session, "jf", kind="household")
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="integrity_scan", trigger_cursor=1
    )
    grant = steward.lease_next_steward_job(db_session, leased_by="w")
    assert grant is not None
    # 让 run_steward_job 内部抛错
    import app.services.steward as st_mod

    original = st_mod._execute_locked

    def boom(db, job, *, now):
        raise RuntimeError("boom")

    st_mod._execute_locked = boom  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError):
            steward.execute_steward_job(db_session, grant)
    finally:
        st_mod._execute_locked = original  # type: ignore[assignment]
    db_session.commit()
    db_session.refresh(grant)
    assert grant.status == "failed"
    failed = [e for e in _events(db_session, "steward.") if e.type == "steward.job_failed"]
    assert len(failed) == 1


# ---- 6. BehaviorProjection 往返 + cooldown 阻止出卡 ----


def test_projection_roundtrip_and_whitelist(db_session) -> None:
    """put_projection upsert / kind_in_cooldown 往返；非白名单键被拒。"""
    space = _space(db_session, "proj")
    owner = db_session.get(User, space.owner_id)
    account_id = owner.account.id
    row = steward.put_projection(
        db_session,
        space_id=space.id,
        account_id=account_id,
        projection_key="card_cooldown:household_link",
        value={"until": "2099-01-01T00:00:00"},
    )
    db_session.commit()
    assert row.value_json == {"until": "2099-01-01T00:00:00"}
    # upsert 同键更新
    row2 = steward.put_projection(
        db_session,
        space_id=space.id,
        account_id=account_id,
        projection_key="card_cooldown:household_link",
        value={"until": "2099-02-02T00:00:00"},
    )
    db_session.commit()
    assert row2.id == row.id
    assert row2.value_json == {"until": "2099-02-02T00:00:00"}
    # 非白名单前缀被拒
    with pytest.raises(fastapi.HTTPException) as exc_info:
        steward.put_projection(
            db_session,
            space_id=space.id,
            account_id=account_id,
            projection_key="mouse_dwell:foo",
            value={"ms": 12},
        )
    assert _err_code(exc_info.value) == "CARD_INVALID_TRANSITION"


def test_kind_cooldown_roundtrip_and_blocks_card(db_session) -> None:
    """set_kind_cooldown/kind_in_cooldown 往返；cooldown 阻止 Steward 出该 kind 卡。"""
    space = _space(db_session, "cool", kind="household")
    a = _person(db_session, space.id, "A", member=False, ref=True)
    b = _person(db_session, space.id, "B", gender="f", member=False, ref=True)
    # 预置 a 的 household_link 冷却
    steward.set_kind_cooldown(
        db_session, space_id=space.id, account_id=a.account.id, kind="household_link"
    )
    db_session.commit()
    assert (
        steward.kind_in_cooldown(
            db_session, space_id=space.id, account_id=a.account.id, kind="household_link"
        )
        is True
    )
    # 过期后不再冷却
    row = db_session.scalar(
        select(BehaviorProjection).where(
            BehaviorProjection.space_id == space.id,
            BehaviorProjection.account_id == a.account.id,
            BehaviorProjection.projection_key == "card_cooldown:household_link",
        )
    )
    assert row is not None
    row.value_json = {"until": "2000-01-01T00:00:00"}  # 已过期
    db_session.commit()
    assert (
        steward.kind_in_cooldown(
            db_session, space_id=space.id, account_id=a.account.id, kind="household_link"
        )
        is False
    )

    # 重新置冷却 → Steward 跑作业不出 household_link 卡
    steward.set_kind_cooldown(
        db_session, space_id=space.id, account_id=a.account.id, kind="household_link"
    )
    db_session.commit()
    fact = _confirm(db_session, "spouse", a.id, b.id, space_id=space.id)
    ev = _emit_fact_event(db_session, fact)
    db_session.commit()
    summary, _ = _run_job(db_session, space, ev.id)
    assert summary["stats"]["cards_created"] == 0
    cards = _cards(db_session, space.id)
    assert all(c.kind != "household_link" for c in cards)


# ---- 7. 冲突/缺失 detector ----


def test_gap_sibling_missing_parents_reports_gap_no_fabrication(db_session) -> None:
    """direct_sibling 无共同父母 → gap_detected，不虚构父母。"""
    space = _space(db_session, "gap")
    a = _person(db_session, space.id, "A")
    b = _person(db_session, space.id, "B", gender="f")
    fact = _confirm(db_session, "direct_sibling", a.id, b.id, space_id=space.id)
    ev = _emit_fact_event(db_session, fact)
    db_session.commit()
    summary, _ = _run_job(db_session, space, ev.id)
    assert summary["stats"]["findings_emitted"] == 1
    gaps = [e for e in _events(db_session, "steward.") if e.type == "steward.gap_detected"]
    assert len(gaps) == 1
    assert gaps[0].payload["detail"]["code"] == "sibling_missing_parents"
    # 未虚构父母
    parents = list(
        db_session.scalars(select(SourceFact).where(SourceFact.fact_type.in_(sf.PARENT_FACT_TYPES)))
    )
    assert parents == []


def test_conflict_parent_cycle_emitted(db_session) -> None:
    """两条矛盾 biological_parent（A→B 且 B→A）→ conflict_detected。"""
    space = _space(db_session, "conf")
    a = _person(db_session, space.id, "A")
    b = _person(db_session, space.id, "B", gender="f")
    # 直插成环事实（绕过服务层环检测，模拟导入脏数据）
    for subj, obj in ((a.id, b.id), (b.id, a.id)):
        _fact(
            db_session,
            "biological_parent",
            subj,
            obj,
            space_id=space.id,
            state="confirmed",
        )
    db_session.commit()
    ev = emit_event(
        db_session,
        event_type="source_fact.confirmed",
        aggregate_type="source_fact",
        aggregate_id=999,
        payload={"subject_user_id": a.id, "object_user_id": b.id},
        space_id=space.id,
    )
    db_session.commit()
    summary, _ = _run_job(db_session, space, ev.id)
    conflicts = [
        e for e in _events(db_session, "steward.") if e.type == "steward.conflict_detected"
    ]
    assert len(conflicts) >= 1
    assert conflicts[0].payload["detail"]["code"] == "parent_cycle"


def test_findings_idempotent_on_rerun(db_session) -> None:
    """同 finding 签名重跑不再重复落事件（checkpoint 签名幂等）。"""
    space = _space(db_session, "gap2")
    a = _person(db_session, space.id, "A")
    b = _person(db_session, space.id, "B", gender="f")
    fact = _confirm(db_session, "direct_sibling", a.id, b.id, space_id=space.id)
    ev = _emit_fact_event(db_session, fact)
    db_session.commit()
    _run_job(db_session, space, ev.id)
    gaps_after_first = len(
        [e for e in _events(db_session, "steward.") if e.type == "steward.gap_detected"]
    )
    # admin_rerun 重跑
    job2, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="admin_rerun", trigger_cursor=ev.id
    )
    grant2 = steward.lease_next_steward_job(db_session, leased_by="w")
    steward.run_steward_job(db_session, grant2)
    gaps_after_rerun = len(
        [e for e in _events(db_session, "steward.") if e.type == "steward.gap_detected"]
    )
    assert gaps_after_rerun == gaps_after_first  # 不重复发


# ---- Steward feature flag ----


def test_steward_disabled_rejects_enqueue(monkeypatch: pytest.MonkeyPatch, db_session) -> None:
    """STEWARD_ENABLED 关闭时 enqueue 入口 503 STEWARD_DISABLED。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", False)
    space = _space(db_session, "off")
    with pytest.raises(fastapi.HTTPException) as exc_info:
        steward.enqueue_steward_job(
            db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
        )
    assert _err_code(exc_info.value) == "STEWARD_DISABLED"
    assert exc_info.value.status_code == 503
