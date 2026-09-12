"""关系提案命令（任务 09-11-steward-candidate-review；design.md）。

受限命令层：Steward 建议提交（submit）只能生成 **proposed** SourceFact
（provenance=agent_proposal）；最终入图必须由**有权当事人**（关系端点本人，
或未认领 managed 档案的合法代管人——复用 custody 能力矩阵，空间 owner 非
端点绝不等于代管人）经本命令完成既有授权确认步骤。

红线：
- ``services/source_facts.transition_source_fact`` 只是状态机，不是授权层；
  路由绝不允许直接调用它跳过确认资格——确认必须经本命令（命令层校验
  主体资格 + expected revision + 当前状态）。
- ``commands/connections.py`` 的 elder/younger 语义是结构方向，不是原子关系
  类型：本命令使用建议携带的原子 ``fact_type``（SOURCE_FACT_TYPES 白名单），
  绝不把收养/继亲强行模糊映射为生物学亲子。
- 两端主体的授权确认记录在 SourceFact 状态机与审计上；缺少合法确认主体时
  提案保持 pending——绝不以空确认集合当作全部同意。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import (
    RELATION_PROPOSAL_NOT_CONFIRMABLE,
    RELATION_PROPOSAL_NOT_FOUND,
    RELATION_PROPOSAL_REVISION_CONFLICT,
    SOURCE_FACT_DUPLICATE,
    SOURCE_FACT_INVALID_TRANSITION,
    SPACE_NOT_FOUND,
    VALIDATION_ERROR,
    extract_api_error,
    raise_api_error,
)
from app.models.relationship_facts import SOURCE_FACT_TYPES, SourceFact
from app.models.space import FamilySpace
from app.models.user import User
from app.services import audit, source_facts, space_fsm, steward_suggestions


def _load_space_with_member(session: Session, ctx: ActorContext, space_id: int) -> FamilySpace:
    space = session.get(FamilySpace, space_id)
    membership = space_fsm.find_membership(session, space_id, ctx.user_id) if space else None
    if space is None or membership is None or space_fsm.effective_status(membership) != "active":
        raise_api_error(404, SPACE_NOT_FOUND, "目标家庭空间不存在或无权操作")
    return space


def create_relationship_proposal(
    session: Session,
    ctx: ActorContext,
    *,
    space_id: int,
    fact_type: str,
    subject_user_id: int,
    object_user_id: int | None,
    evidence_json: dict[str, Any],
    suggestion_id: int | None = None,
    commit: bool = True,
) -> SourceFact:
    """由建议发起关系提案：只落 proposed SourceFact，绝不直接 confirmed。

    授权：发起人必须是该空间 active 成员且能看见两个端点；fact_type 只接受
    原子 SOURCE_FACT_TYPES 白名单；parent 类环检查/同元组重复由
    ``source_facts.create_source_fact`` 在同事务内完成。
    """
    actor = load_actor(session, ctx)
    if fact_type not in SOURCE_FACT_TYPES:
        raise_api_error(422, VALIDATION_ERROR, f"未知事实类型 {fact_type}")
    if object_user_id is None or subject_user_id == object_user_id:
        raise_api_error(422, VALIDATION_ERROR, "提案端点不合法")

    def _run() -> SourceFact:
        _load_space_with_member(session, ctx, space_id)
        # 端点对发起人可见（visibility 单点判定；隐藏人物 → 同形 404）
        from app.services import visibility

        for endpoint_id in (subject_user_id, object_user_id):
            endpoint = session.get(User, endpoint_id)
            if endpoint is None:
                raise_api_error(404, RELATION_PROPOSAL_NOT_FOUND, "提案对象不存在")
            decision = visibility.evaluate(
                session, actor, endpoint, purpose=visibility.PURPOSE_PROFILE
            )
            if not decision.visible:
                raise_api_error(404, RELATION_PROPOSAL_NOT_FOUND, "提案对象不存在")
        try:
            fact = source_facts.create_source_fact(
                session,
                fact_type=fact_type,
                subject_user_id=subject_user_id,
                object_user_id=object_user_id,
                provenance="agent_proposal",
                space_id=None,  # 血缘/配偶事实是全局事实（与 connection 映射口径一致）
                asserted_by_account_id=ctx.account_id,
                state=source_facts.FACT_PROPOSED,
            )
        except Exception as exc:  # noqa: BLE001 — 重复提案转同形 409
            api_error = extract_api_error(getattr(exc, "detail", None))
            if api_error is not None and api_error.get("code") == SOURCE_FACT_DUPLICATE:
                raise_api_error(409, SOURCE_FACT_DUPLICATE, "两人之间已存在同类型的有效事实")
            raise
        audit.write_audit(
            session,
            action="relationship_proposal_created",
            actor_id=actor.id,
            target_id=object_user_id,
            ip=ctx.ip,
            detail={
                "source_fact_id": fact.id,
                "fact_type": fact_type,
                "space_id": space_id,
                "suggestion_id": suggestion_id,
            },
        )
        return fact

    if commit:
        with command_transaction(session):
            return _run()
    return _run()


def eligible_confirmer_account_ids(
    session: Session, *, subject_user_id: int, object_user_id: int | None
) -> list[int]:
    """合法确认主体账号集合：端点本人 ∪ 未认领端点的合法代管人。

    复用 custody 能力矩阵（``custody.resolve_relation``）：owner 非端点且非
    代管人时绝不入选；端点已认领后创建者退只读也不再入选。空集合表示提案
    保持 pending（绝不视为全部同意）。
    """
    ids: set[int] = set()
    from sqlalchemy import select

    from app.models.account import Account

    for endpoint_id in (subject_user_id, object_user_id):
        if endpoint_id is None:
            continue
        endpoint = session.get(User, endpoint_id)
        if endpoint is None:
            continue
        own_account = session.scalar(
            select(Account.id).where(Account.user_id == endpoint.id).limit(1)
        )
        if own_account is not None:
            ids.add(int(own_account))
            continue
        # 无账号（未认领）：合法代管人（创建者）可确认
        if endpoint.created_by is not None:
            creator = session.get(User, endpoint.created_by)
            if creator is not None and _custody_access(creator, endpoint):
                creator_account = session.scalar(
                    select(Account.id).where(Account.user_id == creator.id).limit(1)
                )
                if creator_account is not None:
                    ids.add(int(creator_account))
    return sorted(ids)


def _custody_access(actor: User, target: User) -> bool:
    from app.services.custody import resolve_relation

    return bool(resolve_relation(actor, target).edit)


def confirm_relationship_proposal(
    session: Session,
    ctx: ActorContext,
    fact_id: int,
    *,
    expected_revision: int,
    commit: bool = True,
) -> SourceFact:
    """有权当事人确认提案（accept）：CAS expected revision + 资格校验后走 FSM。

    最终 actor 是实际确认者；命令层校验资格（端点本人或合法代管人），FSM
    只做状态机校验。确认入图后关联 submitted 建议 resolved（终局随领域对象）。
    """
    actor = load_actor(session, ctx)

    def _run() -> SourceFact:
        fact = session.get(SourceFact, fact_id)
        if (
            fact is None
            or fact.provenance != "agent_proposal"
            or fact.state
            not in (
                "proposed",
                "confirmed",
            )
        ):
            raise_api_error(404, RELATION_PROPOSAL_NOT_FOUND, "关系提案不存在")
        confirmers = eligible_confirmer_account_ids(
            session, subject_user_id=fact.subject_user_id, object_user_id=fact.object_user_id
        )
        if ctx.account_id not in confirmers:
            # 非当事人（含空间 owner）确认被拒；提案不存在/无权同形 404
            raise_api_error(404, RELATION_PROPOSAL_NOT_CONFIRMABLE, "关系提案不存在")
        if fact.state == "confirmed":
            # 已确认（幂等确认）：直接返回现状，不重复 FSM/事件
            return fact
        if fact.revision != expected_revision:
            raise_api_error(409, RELATION_PROPOSAL_REVISION_CONFLICT, "提案已被其他操作更新")
        source_facts.transition_source_fact(
            session, fact, source_facts.ACTION_CONFIRM, actor_account_id=ctx.account_id
        )
        steward_suggestions.resolve_for_linked_fact(session, fact_id=fact.id)
        audit.write_audit(
            session,
            action="relationship_proposal_confirmed",
            actor_id=actor.id,
            target_id=fact.object_user_id,
            ip=ctx.ip,
            detail={"source_fact_id": fact.id, "fact_type": fact.fact_type},
        )
        return fact

    if commit:
        with command_transaction(session):
            return _run()
    return _run()


def reject_relationship_proposal(
    session: Session,
    ctx: ActorContext,
    fact_id: int,
    *,
    commit: bool = True,
) -> str:
    """有权当事人拒绝提案：proposed 只能删除（FSM 无 proposed→revoked）。

    拒绝一个建议绝不撤销已经存在的正式事实（只处理本提案行）。
    """
    actor = load_actor(session, ctx)

    def _run() -> str:
        fact = session.get(SourceFact, fact_id)
        if fact is None or fact.provenance != "agent_proposal":
            raise_api_error(404, RELATION_PROPOSAL_NOT_FOUND, "关系提案不存在")
        confirmers = eligible_confirmer_account_ids(
            session, subject_user_id=fact.subject_user_id, object_user_id=fact.object_user_id
        )
        if ctx.account_id not in confirmers:
            raise_api_error(404, RELATION_PROPOSAL_NOT_CONFIRMABLE, "关系提案不存在")
        if fact.state != "proposed":
            raise_api_error(409, SOURCE_FACT_INVALID_TRANSITION, "提案当前状态不允许拒绝")
        fact_type = fact.fact_type
        session.delete(fact)
        session.flush()
        audit.write_audit(
            session,
            action="relationship_proposal_rejected",
            actor_id=actor.id,
            target_id=fact.object_user_id,
            ip=ctx.ip,
            detail={"fact_type": fact_type},
        )
        return fact_type

    if commit:
        with command_transaction(session):
            return _run()
    return _run()


__all__ = [
    "confirm_relationship_proposal",
    "create_relationship_proposal",
    "eligible_confirmer_account_ids",
    "reject_relationship_proposal",
]
