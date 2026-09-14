"""Steward 推测层投影与读取（任务 09-13-steward-inferred-tree-layer）。

职责边界（design.md）：

- **投影**（管家 core 作业事务内，SAVEPOINT 隔离）：把 LLM 候选池的
  proposed 候选投影为 ``StewardInferredEdge``（单跳原子关系）。幂等键 =
  ``source_candidate_id`` 反连接；去重 = 三元组活跃唯一索引；证据变化 →
  同三元组旧行 superseded + 新行；同证据 rejected → 冷却跳过；活跃数超
  ``STEWARD_INFERRED_MAX_ACTIVE_PER_SPACE`` → 停止。
- **读取**：``active_edges`` 供 PFV 增广图消费（created_at 升序截断）。
- **红线**：推测边是显示层投影，任何路径都不写 SourceFact；确认转正走
  ``relationship_proposals`` 命令层（api/steward_inferred.py）。全程确定性，
  零模型调用。

有效开关 = ``STEWARD_INFERRED_TREE_ENABLED``（平台）AND 空间级
``agent_space_provider_settings.inferred_tree``（steward 维度行）；任一关闭
投影与读取均返回空，行为与推测层上线前逐字节等价。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.commands.context import ActorContext, command_transaction
from app.errors import (
    INFERRED_EDGE_NOT_FOUND,
    INFERRED_EDGE_REVISION_CONFLICT,
    INFERRED_EDGE_STATE_CONFLICT,
    raise_api_error,
)
from app.models.agent_provider import AgentSpaceProviderSetting
from app.models.space import SpaceMember
from app.models.steward import StewardJob, StewardLlmCandidate
from app.models.steward_inferred import (
    INFERRED_ACTIVE_STATE,
    INFERRED_RELATION_KINDS,
    StewardInferredEdge,
)
from app.services import steward_events
from app.services.domain_events import emit as emit_domain_event
from app.utils.timeutil import utcnow

logger = logging.getLogger(__name__)

# 对称关系：端点交换视为同一三元组（去重/冷却/confirmed 覆盖判定均适用）
_SYMMETRIC_KINDS = frozenset({"spouse", "partner", "direct_sibling"})


def space_flag(db: Session, space_id: int) -> bool:
    """空间级推测层开关（steward 维度设置行；缺行 = 关）。"""
    row = db.scalar(
        select(AgentSpaceProviderSetting).where(
            AgentSpaceProviderSetting.space_id == space_id,
            AgentSpaceProviderSetting.agent_kind == "steward",
        )
    )
    return bool(row is not None and row.inferred_tree)


def effective_enabled(db: Session, space_id: int) -> bool:
    """有效开关 = 平台级 AND 空间级（fail-closed：任一缺省即关）。"""
    return bool(config.STEWARD_INFERRED_TREE_ENABLED and space_flag(db, space_id))


def _triple_keys(subject: int, object_: int, kind: str) -> set[tuple[int, int, str]]:
    """三元组的去重键集合：对称关系两个方向都归一，方向性关系仅本方向。"""
    forward = (subject, object_, kind)
    if kind in _SYMMETRIC_KINDS:
        return {forward, (object_, subject, kind)}
    return {forward}


def _confirmed_triples(db: Session, facts: list[Any]) -> set[tuple[int, int, str]]:
    """既有 confirmed 事实的三元组键集合（投影去重：候选不重复已有事实）。"""
    keys: set[tuple[int, int, str]] = set()
    for fact in facts:
        kind = getattr(fact, "fact_type", None)
        subject = getattr(fact, "subject_user_id", None)
        object_ = getattr(fact, "object_user_id", None)
        if isinstance(kind, str) and isinstance(subject, int) and isinstance(object_, int):
            keys |= _triple_keys(subject, object_, kind)
    return keys


def project_for_job(
    db: Session,
    job: StewardJob,
    *,
    facts: list[Any],
    visible: set[int],
    now: datetime | None = None,
    candidate_ids: list[int] | None = None,
) -> int:
    """把未投影的 proposed LLM 候选投影为推测边；返回新建行数。

    由 ``steward._execute_locked`` 在 SAVEPOINT 内调用：任何异常由调用方
    回滚本投影且不拖垮确定性 core（与建议投影同一隔离纪律）。
    """
    if not effective_enabled(db, job.space_id):
        return 0
    moment = now or utcnow()

    evidence = _evidence_snapshot(db, job.space_id)
    confirmed_keys = _confirmed_triples(db, facts)

    projected = (
        select(StewardInferredEdge.id)
        .where(
            StewardInferredEdge.source_candidate_id == StewardLlmCandidate.id,
        )
        .exists()
    )
    candidates = list(
        db.scalars(
            select(StewardLlmCandidate).where(
                StewardLlmCandidate.space_id == job.space_id,
                StewardLlmCandidate.status == "proposed",
                ~projected,
                *([StewardLlmCandidate.id.in_(candidate_ids)] if candidate_ids is not None else []),
            )
        )
    )

    active_count = int(
        db.scalar(
            select(func.count())
            .select_from(StewardInferredEdge)
            .where(
                StewardInferredEdge.space_id == job.space_id,
                StewardInferredEdge.status == INFERRED_ACTIVE_STATE,
            )
        )
        or 0
    )
    cap = config.STEWARD_INFERRED_MAX_ACTIVE_PER_SPACE

    created = 0
    for candidate in candidates:
        if active_count >= cap:
            break
        edge = _edge_from_candidate(db, job, candidate, visible, confirmed_keys, evidence, moment)
        if edge is None:
            continue
        db.add(edge)
        db.flush()
        emit_domain_event(
            db,
            event_type=steward_events.EVENT_STEWARD_INFERRED_PROJECTED,
            aggregate_type=steward_events.AGGREGATE_STEWARD_INFERRED_EDGE,
            aggregate_id=edge.id,
            payload={
                "edge_id": edge.id,
                "space_id": job.space_id,
                "job_id": job.id,
                "subject_user_id": edge.subject_user_id,
                "object_user_id": edge.object_user_id,
                "relation_kind": edge.relation_kind,
                "origin": edge.origin,
            },
            space_id=job.space_id,
            actor_account_id=None,
        )
        active_count += 1
        created += 1
    return created


def _evidence_snapshot(db: Session, space_id: int) -> dict[str, Any]:
    """证据指纹快照：facts brief 白名单 + revision（与辅助批次同口径）。"""
    from app.services.steward_assist import _canonical_hash, _facts_evidence

    snapshot = _facts_evidence(db, space_id)
    return {
        "facts": snapshot.get("revisions", []),
        "brief": snapshot.get("brief", []),
        "evidence_hash": _canonical_hash(snapshot),
    }


def _edge_from_candidate(
    db: Session,
    job: StewardJob,
    candidate: StewardLlmCandidate,
    visible: set[int],
    confirmed_keys: set[tuple[int, int, str]],
    evidence: dict[str, Any],
    moment: datetime,
) -> StewardInferredEdge | None:
    """候选 → 推测边；结构不合法/不可见/重复/冷却/超限返回 None（逐条跳过）。"""
    payload = candidate.payload_json if isinstance(candidate.payload_json, dict) else {}
    raw_kind = payload.get("kind")
    raw_subject = payload.get("subject_user_id")
    raw_object = payload.get("object_user_id")
    if not isinstance(raw_kind, str) or raw_kind not in INFERRED_RELATION_KINDS:
        return None
    if isinstance(raw_subject, bool) or not isinstance(raw_subject, int):
        return None
    if isinstance(raw_object, bool) or not isinstance(raw_object, int):
        return None
    if raw_subject == raw_object:
        return None
    if raw_subject not in visible or raw_object not in visible:
        return None
    if _triple_keys(raw_subject, raw_object, raw_kind) & confirmed_keys:
        return None  # 已有同结构 confirmed 事实

    evidence_hash = str(evidence["evidence_hash"])
    candidate_keys = _triple_keys(raw_subject, raw_object, raw_kind)
    existing = list(
        db.scalars(
            select(StewardInferredEdge).where(
                StewardInferredEdge.space_id == job.space_id,
                StewardInferredEdge.relation_kind == raw_kind,
            )
        )
    )
    for row in existing:
        # 对称关系两方向归一（design §2.1）：反方向候选与既有行视为同三元组
        if not (
            _triple_keys(row.subject_user_id, row.object_user_id, row.relation_kind)
            & candidate_keys
        ):
            continue
        if row.status == INFERRED_ACTIVE_STATE:
            return None  # 活跃唯一索引兜底；正常流程不可达
        if row.status == "rejected" and row.evidence_hash == evidence_hash:
            return None  # 驳回冷却：同证据不重复上树
        if row.status == "confirmed":
            return None  # 已转正（防御：confirmed_keys 理论已覆盖）
    # 同三元组历史行（superseded 等）不阻塞新证据投影；活跃上限由调用方计数。
    return StewardInferredEdge(
        space_id=job.space_id,
        subject_user_id=raw_subject,
        object_user_id=raw_object,
        relation_kind=raw_kind,
        status=INFERRED_ACTIVE_STATE,
        origin="llm",
        source_candidate_id=candidate.id,
        evidence_hash=evidence_hash,
        evidence_json={"facts": evidence["facts"]},
        confidence=None,
        revision=1,
        created_at=moment,
        updated_at=moment,
        resolved_at=None,
    )


def active_edges(
    db: Session, space_id: int, *, limit: int | None = None
) -> list[StewardInferredEdge]:
    """空间活跃推测边（created_at 升序；上限截断）。供 PFV 增广图消费。"""
    cap = limit if limit is not None else config.STEWARD_INFERRED_MAX_ACTIVE_PER_SPACE
    return list(
        db.scalars(
            select(StewardInferredEdge)
            .where(
                StewardInferredEdge.space_id == space_id,
                StewardInferredEdge.status == INFERRED_ACTIVE_STATE,
            )
            .order_by(StewardInferredEdge.created_at.asc(), StewardInferredEdge.id.asc())
            .limit(cap)
        )
    )


def supersede_evidence_changed(
    db: Session, space_id: int, *, now: datetime | None = None, edge_ids: list[int] | None = None
) -> int:
    """证据变化失效：活跃行的 evidence_hash 与当前 facts 摘要不符 → superseded。

    由管家 core 作业在投影前调用（重算时证据口径已刷新）。返回失效行数。
    """
    if not effective_enabled(db, space_id):
        return 0
    moment = now or utcnow()
    evidence = _evidence_snapshot(db, space_id)
    evidence_hash = str(evidence["evidence_hash"])
    stale = list(
        db.scalars(
            select(StewardInferredEdge).where(
                StewardInferredEdge.space_id == space_id,
                StewardInferredEdge.status == INFERRED_ACTIVE_STATE,
                StewardInferredEdge.evidence_hash != evidence_hash,
                *([StewardInferredEdge.id.in_(edge_ids)] if edge_ids is not None else []),
            )
        )
    )
    for row in stale:
        row.status = "superseded"
        row.resolved_at = moment
        row.updated_at = moment
        emit_domain_event(
            db,
            event_type=steward_events.EVENT_STEWARD_INFERRED_SUPERSEDED,
            aggregate_type=steward_events.AGGREGATE_STEWARD_INFERRED_EDGE,
            aggregate_id=row.id,
            payload={
                "edge_id": row.id,
                "space_id": space_id,
                "old_evidence_hash": row.evidence_hash,
                "new_evidence_hash": evidence_hash,
            },
            space_id=space_id,
            actor_account_id=None,
        )
    return len(stale)


# ---- 操作（确认/驳回/撤销驳回；api/steward_inferred.py 消费）----


def _edge_or_404(
    session: Session, *, space_id: int, edge_id: int, viewer_user_id: int
) -> StewardInferredEdge:
    from app.models.user import User
    from app.services import visibility

    edge = session.get(StewardInferredEdge, edge_id)
    if (
        edge is None
        or edge.space_id != space_id
        or edge.status not in ("proposed", "rejected", "confirmed")
    ):
        # 不存在 / 不可见 / superseded 终态：统一 404 防枚举
        raise_api_error(404, INFERRED_EDGE_NOT_FOUND, "推测关系不存在")
    viewer = session.get(User, viewer_user_id)
    for user_id in (edge.subject_user_id, edge.object_user_id):
        endpoint = session.get(User, user_id)
        if (
            viewer is None
            or endpoint is None
            or not visibility.evaluate(
                session,
                viewer,
                endpoint,
                space_context=space_id,
                purpose=visibility.PURPOSE_PROFILE,
            ).visible
        ):
            raise_api_error(404, INFERRED_EDGE_NOT_FOUND, "推测关系不存在")
    return edge


def _require_member(session: Session, *, space_id: int, user_id: int) -> None:
    member = session.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == space_id,
            SpaceMember.user_id == user_id,
            SpaceMember.status == "active",
        )
    )
    if member is None:
        raise_api_error(404, INFERRED_EDGE_NOT_FOUND, "推测关系不存在")


def _bump(edge: StewardInferredEdge, *, status: str, now: datetime) -> None:
    edge.status = status
    edge.revision += 1
    edge.updated_at = now
    edge.resolved_at = now


def _emit_edge_event(
    session: Session,
    *,
    event_type: str,
    edge: StewardInferredEdge,
    actor_account_id: int | None,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    emit_domain_event(
        session,
        event_type=event_type,
        aggregate_type=steward_events.AGGREGATE_STEWARD_INFERRED_EDGE,
        aggregate_id=edge.id,
        payload={
            "edge_id": edge.id,
            "space_id": edge.space_id,
            "status": edge.status,
            "revision": edge.revision,
            **(extra_payload or {}),
        },
        space_id=edge.space_id,
        actor_account_id=actor_account_id,
    )


def confirm_edge(
    session: Session,
    ctx: ActorContext,
    *,
    account: Any,
    space_id: int,
    edge_id: int,
    expected_revision: int,
    now: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    """确认推测边（design §5）：

    - 有权当事人（端点本人 ∪ 合法代管人）：创建提案并同事务确认 → 推测边
      confirmed，confirmed SourceFact 入图（经 relationship_proposals 现行
      consent 合同，绝不绕过资格校验）；
    - 非当事人：仅创建提案（202 语义，speculo 边保持 proposed），待对方确认；
      同元组已有活跃提案 → 返回既有提案（状态幂等，不产生第二提案）。
    revision CAS；同推测边重复确认返回既有结果（状态幂等）。
    """
    from app.commands import relationship_proposals
    from app.services import steward_suggestions

    moment = now or utcnow()
    with command_transaction(session, immediate=True):
        _require_member(session, space_id=space_id, user_id=account.user_id)
        edge = _edge_or_404(
            session, space_id=space_id, edge_id=edge_id, viewer_user_id=account.user_id
        )
        current_confirmed = steward_suggestions.find_confirmed_relation(
            session,
            space_id=space_id,
            subject_user_id=edge.subject_user_id,
            object_user_id=edge.object_user_id,
            fact_type=edge.relation_kind,
        )
        if edge.status == "confirmed":
            # 状态幂等：已确认（重复确认）返回既有结果形状
            payload: dict[str, Any] = {
                "edge": {"id": edge.id, "status": edge.status, "revision": edge.revision},
                "linked_proposal": {
                    "source_fact_id": current_confirmed.id,
                    "revision": current_confirmed.revision,
                    "state": current_confirmed.state,
                    "fact_type": current_confirmed.fact_type,
                }
                if current_confirmed is not None
                else None,
                "pending_confirmations": [],
            }
            return 200, payload
        if edge.status != "proposed":
            raise_api_error(409, INFERRED_EDGE_STATE_CONFLICT, "推测关系已驳回，请先撤销驳回")
        if edge.revision != expected_revision:
            raise_api_error(409, INFERRED_EDGE_REVISION_CONFLICT, "推测关系已被其他操作更新")

        if current_confirmed is not None:
            # Another authorized flow already confirmed the exact relation.
            # Acknowledge that fact; do not create or confirm another proposal.
            _bump(edge, status="confirmed", now=moment)
            steward_suggestions.resolve_for_linked_fact(
                session,
                fact_id=current_confirmed.id,
                space_id=space_id,
                now=moment,
            )
            _emit_edge_event(
                session,
                event_type=steward_events.EVENT_STEWARD_INFERRED_CONFIRMED,
                edge=edge,
                actor_account_id=ctx.account_id,
                extra_payload={"source_fact_id": current_confirmed.id},
            )
            return 200, {
                "edge": {"id": edge.id, "status": edge.status, "revision": edge.revision},
                "linked_proposal": {
                    "source_fact_id": current_confirmed.id,
                    "revision": current_confirmed.revision,
                    "state": current_confirmed.state,
                    "fact_type": current_confirmed.fact_type,
                },
                "pending_confirmations": [],
            }

        existing_proposal = _find_active_proposal(
            session,
            space_id=space_id,
            subject_user_id=edge.subject_user_id,
            object_user_id=edge.object_user_id,
            fact_type=edge.relation_kind,
        )
        confirmer_ids = relationship_proposals.eligible_confirmer_account_ids(
            session,
            subject_user_id=edge.subject_user_id,
            object_user_id=edge.object_user_id,
        )
        entitled = ctx.account_id in confirmer_ids
        proposal: Any | None = existing_proposal
        if entitled and proposal is None:
            proposal = relationship_proposals.create_relationship_proposal(
                session,
                ctx,
                space_id=space_id,
                fact_type=edge.relation_kind,
                subject_user_id=edge.subject_user_id,
                object_user_id=edge.object_user_id,
                evidence_json=dict(edge.evidence_json),
                commit=False,
            )

        if entitled and proposal is not None:
            steward_suggestions.link_source_proposal(
                session,
                space_id=space_id,
                source_candidate_id=edge.source_candidate_id,
                proposal=proposal,
                now=moment,
            )
            confirmed_fact = relationship_proposals.confirm_relationship_proposal(
                session, ctx, proposal.id, expected_revision=proposal.revision, commit=False
            )
            _bump(edge, status="confirmed", now=moment)
            steward_suggestions.link_source_proposal(
                session,
                space_id=space_id,
                source_candidate_id=edge.source_candidate_id,
                proposal=confirmed_fact,
                now=moment,
            )
            _emit_edge_event(
                session,
                event_type=steward_events.EVENT_STEWARD_INFERRED_CONFIRMED,
                edge=edge,
                actor_account_id=ctx.account_id,
                extra_payload={"source_fact_id": confirmed_fact.id},
            )
            payload = {
                "edge": {"id": edge.id, "status": edge.status, "revision": edge.revision},
                "linked_proposal": {
                    "source_fact_id": confirmed_fact.id,
                    "revision": confirmed_fact.revision,
                    "state": confirmed_fact.state,
                    "fact_type": confirmed_fact.fact_type,
                },
                "pending_confirmations": [],
            }
            return 200, payload

        if proposal is None:
            # 非当事人且无既有提案：代为创建提案（绝不直接确认）
            proposal = relationship_proposals.create_relationship_proposal(
                session,
                ctx,
                space_id=space_id,
                fact_type=edge.relation_kind,
                subject_user_id=edge.subject_user_id,
                object_user_id=edge.object_user_id,
                evidence_json=dict(edge.evidence_json),
                commit=False,
            )
        steward_suggestions.link_source_proposal(
            session,
            space_id=space_id,
            source_candidate_id=edge.source_candidate_id,
            proposal=proposal,
            now=moment,
        )
        pending: list[dict[str, int]] = (
            [{"account_id": a} for a in confirmer_ids] if proposal.state == "proposed" else []
        )
        payload = {
            "edge": {"id": edge.id, "status": edge.status, "revision": edge.revision},
            "linked_proposal": {
                "source_fact_id": proposal.id,
                "revision": proposal.revision,
                "state": proposal.state,
                "fact_type": proposal.fact_type,
            },
            "pending_confirmations": pending,
        }
        return 202, payload


def _find_active_proposal(
    session: Session, *, space_id: int, subject_user_id: int, object_user_id: int, fact_type: str
) -> Any | None:
    from app.services.steward_suggestions import find_related_proposal

    return find_related_proposal(
        session,
        space_id=space_id,
        subject_user_id=subject_user_id,
        object_user_id=object_user_id,
        fact_type=fact_type,
    )


def dismiss_edge(
    session: Session,
    *,
    account: Any,
    space_id: int,
    edge_id: int,
    expected_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """驳回推测边：proposed → rejected（同证据冷却由投影侧判定）。"""
    moment = now or utcnow()
    with command_transaction(session, immediate=True):
        _require_member(session, space_id=space_id, user_id=account.user_id)
        edge = _edge_or_404(
            session, space_id=space_id, edge_id=edge_id, viewer_user_id=account.user_id
        )
        if edge.status == "rejected":
            return {"id": edge.id, "status": edge.status, "revision": edge.revision}
        if edge.status != "proposed":
            raise_api_error(409, INFERRED_EDGE_STATE_CONFLICT, "该推测关系当前不可驳回")
        if edge.revision != expected_revision:
            raise_api_error(409, INFERRED_EDGE_REVISION_CONFLICT, "推测关系已被其他操作更新")
        _bump(edge, status="rejected", now=moment)
        _emit_edge_event(
            session,
            event_type=steward_events.EVENT_STEWARD_INFERRED_DISMISSED,
            edge=edge,
            actor_account_id=account.id,
        )
        return {"id": edge.id, "status": edge.status, "revision": edge.revision}


def reinstate_edge(
    session: Session,
    *,
    account: Any,
    space_id: int,
    edge_id: int,
    expected_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """撤销驳回：rejected → proposed（活跃唯一索引兜底同三元组冲突）。"""
    moment = now or utcnow()
    with command_transaction(session, immediate=True):
        _require_member(session, space_id=space_id, user_id=account.user_id)
        edge = _edge_or_404(
            session, space_id=space_id, edge_id=edge_id, viewer_user_id=account.user_id
        )
        if edge.status == "proposed":
            return {"id": edge.id, "status": edge.status, "revision": edge.revision}
        if edge.status != "rejected":
            raise_api_error(409, INFERRED_EDGE_STATE_CONFLICT, "仅已驳回的推测关系可撤销")
        if edge.revision != expected_revision:
            raise_api_error(409, INFERRED_EDGE_REVISION_CONFLICT, "推测关系已被其他操作更新")
        active_count = int(
            session.scalar(
                select(func.count())
                .select_from(StewardInferredEdge)
                .where(
                    StewardInferredEdge.space_id == space_id,
                    StewardInferredEdge.status == INFERRED_ACTIVE_STATE,
                )
            )
            or 0
        )
        if active_count >= config.STEWARD_INFERRED_MAX_ACTIVE_PER_SPACE:
            raise_api_error(409, INFERRED_EDGE_STATE_CONFLICT, "活跃推测关系已达上限")
        _bump(edge, status="proposed", now=moment)
        edge.resolved_at = None
        _emit_edge_event(
            session,
            event_type=steward_events.EVENT_STEWARD_INFERRED_REINSTATED,
            edge=edge,
            actor_account_id=account.id,
        )
        return {"id": edge.id, "status": edge.status, "revision": edge.revision}
