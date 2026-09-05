"""并流绑定命令（09-05 决策 16；design.md §2/§4；HTTP 与未来 Agent 工具共用）。

建档查重命中已存在的 claimed 自注册账号时（commands/members.py 撞名分支），
不建第二份可登录凭据，改创建 account_bindings(pending)。本模块是绑定请求的
终态处置：

- confirm（被绑定人本人 + PIN 复验 + 「这是我」）：先经 identity_fsm 唯一转换点
  confirm_profile_identity 完成本人身份确认（已确认则条件跳过，同
  claim_and_confirm 的先例——不新造转换），再把建档产生的无凭据 provisional 人物
  并回既有 user（space_profile_refs / space_members / 关系边 / 附件 / 事实改指向，
  §0.9：并回后不得留下重复人物）。
- reject（被绑定人）/ cancel（发起人）：pending → 终态，不可逆；撞名人物随终态
  废弃（任一终态都会删除该人物，绑定行保留为处置记录）。

PIN 复验复用登录侧惯例（auth_guard 失败计数/锁定 + verify_pin + 防枚举统一文案）；
失败计数与审计先落库再抛 401（命令事务回滚不丢失败预算，与登录端点同一处理）。

隐私边界：target 侧视图（list_incoming）只披露发起人显示名与建档人物名——
这是「这是我」判断所需的最小信息；确认前发起人侧不获得被绑定方任何数据
（建档响应只有 bound_to_existing 标记）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import (
    ACCOUNT_LOCKED,
    AUTH_INVALID_CREDENTIALS,
    BINDING_ALREADY_RESOLVED,
    BINDING_INVALID,
    BINDING_NOT_FOUND,
    UNIFIED_CREDENTIAL_MESSAGE,
    raise_api_error,
)
from app.models.account_binding import AccountBinding
from app.models.attachment import Attachment
from app.models.relation import NON_TERMINAL_STATUSES, Relation
from app.models.space import SpaceMember, SpaceProfileRef
from app.models.user import User
from app.services import audit, auth_guard, identity_fsm, relation_fsm, source_facts
from app.services.domain_events import emit
from app.utils import security, timeutil

MESSAGE_NOT_FOUND = "绑定请求不存在或无权操作"
MESSAGE_RESOLVED = "绑定请求已处理"
MESSAGE_INVALID = "绑定请求对应的人物已不存在，请拒绝该请求"


@dataclass(frozen=True)
class BindingSummary:
    """target 侧待确认视图：仅含「这是我」判断所需最小字段（无档案/空间/关系数据）。"""

    binding: AccountBinding
    initiator_name: str | None
    person_name: str | None


def list_incoming_bindings(session: Session, ctx: ActorContext) -> list[BindingSummary]:
    """我的待确认绑定（被绑定人视角；含已决议历史，前端按 status 过滤展示）。"""
    actor = load_actor(session, ctx)
    bindings = list(
        session.scalars(
            select(AccountBinding)
            .where(AccountBinding.target_id == actor.id)
            .order_by(AccountBinding.id.desc())
        )
    )
    initiator_ids = {binding.initiator_id for binding in bindings if binding.initiator_id}
    person_ids = {binding.person_id for binding in bindings if binding.person_id}
    names: dict[int, str] = {}
    if initiator_ids or person_ids:
        rows = session.query(User.id, User.name).filter(User.id.in_((*initiator_ids, *person_ids)))
        names = {user_id: name for user_id, name in rows}
    return [
        BindingSummary(
            binding=binding,
            initiator_name=names.get(binding.initiator_id) if binding.initiator_id else None,
            person_name=names.get(binding.person_id) if binding.person_id else None,
        )
        for binding in bindings
    ]


# ---- confirm：本人 + PIN 复验 + 身份确认 + 人物并回 ----


def confirm_binding(
    session: Session, ctx: ActorContext, binding_id: int, *, pin: str
) -> AccountBinding:
    """「这是我」确认：被绑定人本人 + PIN 复验 + provisional 人物并回既有 user。

    单事务（立即事务：合并含 check-then-act 的关系碰撞判定）内完成：
    授权/状态门 → PIN 复验（登录侧同源）→ identity_fsm.confirm_profile_identity
    （唯一转换点，已确认则跳过）→ 人物并回（§0.9）→ 终态 + 审计。
    """
    actor = load_actor(session, ctx)
    with command_transaction(session, immediate=True):
        binding = session.get(AccountBinding, binding_id)
        # 非本人与不存在同一 404（防枚举）；只有 target 能看到并处理该请求
        if binding is None or binding.target_id != actor.id:
            raise_api_error(404, BINDING_NOT_FOUND, MESSAGE_NOT_FOUND)
        if binding.status != "pending":
            # confirmed/rejected/cancelled 终态不可逆：重复确认 409
            raise_api_error(
                409,
                BINDING_ALREADY_RESOLVED,
                MESSAGE_RESOLVED,
                detail={"status": binding.status},
            )

        # PIN 复验复用登录侧惯例：锁定窗口检查 + 失败计数 + 统一文案
        try:
            auth_guard.ensure_not_locked([actor.account])
        except auth_guard.AccountLockedError as locked:
            raise_api_error(
                429,
                ACCOUNT_LOCKED,
                "失败次数过多，账户已临时锁定，请稍后再试",
                detail={"retry_after_seconds": locked.retry_after_seconds},
                headers={"Retry-After": str(locked.retry_after_seconds)},
            )
        if not security.verify_pin(pin, actor.account.pin_hash):
            auth_guard.register_failures(session, [actor.account], ctx.ip)
            auth_guard.audit_login_failure_if_needed(session, [actor.account], ctx.ip)
            # 失败计数与审计须落库：401 异常路径的回滚不能丢失败预算（同登录端点）
            session.commit()
            raise_api_error(401, AUTH_INVALID_CREDENTIALS, UNIFIED_CREDENTIAL_MESSAGE)
        auth_guard.register_success(actor.account)

        person = session.get(User, binding.person_id) if binding.person_id is not None else None
        if person is None:
            # 人物已被发起人经建档删除路径处置：请求失效，由 target 拒绝收尾
            raise_api_error(409, BINDING_INVALID, MESSAGE_INVALID)

        # 「这是我」= Profile 身份确认：identity_fsm 唯一转换点；已确认则条件跳过
        # （同 claim_and_confirm_own_identity 的先例，不新造转换、不重复确认）
        identity_confirmed = False
        if actor.profile_status == identity_fsm.PROFILE_PROVISIONAL:
            identity_fsm.confirm_profile_identity(session, actor)
            identity_confirmed = True
            emit(
                session,
                event_type="profile.identity_confirmed",
                aggregate_type="profile",
                aggregate_id=actor.id,
                payload={"confirmed_by_account": ctx.account_id, "via": "account_binding"},
                actor_account_id=ctx.account_id,
            )

        moved = _merge_person_into_target(session, ctx, person=person, target=actor)

        binding.status = "confirmed"
        binding.resolved_at = timeutil.utcnow()
        session.flush()
        audit.write_audit(
            session,
            action="account_binding_confirmed",
            actor_id=actor.id,
            target_id=binding.id,
            ip=ctx.ip,
            detail={
                "person_id": person.id,
                "identity_confirmed": identity_confirmed,
                "moved": moved,
            },
        )
    return binding


def _active_identity_space_ids(session: Session, *user_ids: int) -> list[int]:
    """人物/目标 active refs/members 覆盖的空间集合（profile.merged 失效范围）。"""
    ids: set[int] = set()
    for user_id in user_ids:
        ids.update(
            session.scalars(
                select(SpaceProfileRef.space_id).where(
                    SpaceProfileRef.user_id == user_id,
                    SpaceProfileRef.status == "active",
                )
            )
        )
        ids.update(
            session.scalars(
                select(SpaceMember.space_id).where(
                    SpaceMember.user_id == user_id,
                    SpaceMember.status == "active",
                )
            )
        )
    return sorted(ids)


def _merge_person_into_target(
    session: Session, ctx: ActorContext, *, person: User, target: User
) -> dict[str, int]:
    """把撞名建档产生的 provisional 人物并回既有 user（§0.9 人物唯一）。

    迁移清单与 merge_duplicate_profile 同构（refs/members 改指向、附件随档案、
    事实层 classify+repoint、CASCADE 只兜底非身份承载行），差异：

    - 不搬运档案字段（合并不覆写 survivor 存储值，同一哲学）；
    - 关系边改指向后由 target 本人按 FSM 处置：pending 且 target 为被请求方 →
      accept（撞名建档的关系意图生效）；与 target 既有非终态边碰撞 → 终态化
      （reject/cancel/revoke，不新造转换）；person↔target 之间的边不能改指向
      （会成自环），按 FSM 终态化后随人物删除。

    无凭据人物没有会话/记忆/成员资格，触达面以本清单为界。
    """
    # ---- 迁移清单预分类（只读）----
    person_refs = list(
        session.scalars(select(SpaceProfileRef).where(SpaceProfileRef.user_id == person.id))
    )
    person_members = list(
        session.scalars(select(SpaceMember).where(SpaceMember.user_id == person.id))
    )
    target_ref_space_ids = set(
        session.scalars(
            select(SpaceProfileRef.space_id).where(SpaceProfileRef.user_id == target.id)
        )
    )
    target_member_space_ids = set(
        session.scalars(select(SpaceMember.space_id).where(SpaceMember.user_id == target.id))
    )
    # target 已占据该空间身份槽位（member 或 ref）→ 重复引用随并回删除，不得留下双节点
    refs_to_repoint = [
        ref
        for ref in person_refs
        if ref.space_id not in target_ref_space_ids and ref.space_id not in target_member_space_ids
    ]
    refs_to_drop = [ref for ref in person_refs if ref not in refs_to_repoint]
    members_to_repoint = [
        member for member in person_members if member.space_id not in target_member_space_ids
    ]
    members_to_drop = [member for member in person_members if member not in members_to_repoint]
    edges = list(
        session.scalars(
            select(Relation).where(
                or_(Relation.from_user == person.id, Relation.to_user == person.id)
            )
        )
    )
    attachments_to_move = list(
        session.query(Attachment).filter(Attachment.user_id == person.id).all()
    )
    facts_to_repoint, facts_colliding = source_facts.classify_facts_for_identity_merge(
        session, retired_user_id=person.id, survivor_user_id=target.id
    )

    space_ids = _active_identity_space_ids(session, person.id, target.id)
    moved = {
        "space_profile_refs": len(refs_to_repoint),
        "space_members": len(members_to_repoint),
        "relations": len(edges),
        "attachments": len(attachments_to_move),
        "source_facts": len(facts_to_repoint),
    }
    # 事件先行：source_fact.revised 的 payload 需携带 merged 事件 id
    merged_event = emit(
        session,
        event_type="profile.merged",
        aggregate_type="profile",
        aggregate_id=target.id,
        payload={
            "survivor_id": target.id,
            "retired_id": person.id,
            "space_ids": space_ids,
            "moved": moved,
        },
        actor_account_id=ctx.account_id,
    )

    # ---- 迁移改指向 ----
    for ref in refs_to_repoint:
        ref.user_id = target.id
    for ref in refs_to_drop:
        session.delete(ref)
    for member in members_to_repoint:
        member.user_id = target.id
    for member in members_to_drop:
        session.delete(member)

    for edge in edges:
        person_is_from = edge.from_user == person.id
        touches_target = (
            edge.to_user == target.id if person_is_from else edge.from_user == target.id
        )
        if edge.status in NON_TERMINAL_STATUSES:
            if touches_target:
                # 改指向会成自环：目标与人物本就被同一条边连接，按 FSM 终态化
                # （target 是边的一端，reject/cancel/revoke 均为其合法动作）
                _settle_edge(session, ctx, edge, target.id)
                continue
            if person_is_from:
                edge.from_user = target.id
            else:
                edge.to_user = target.id
            # person 原为 from → 改指向后 target 为 from（target_is_from 同义）
            _settle_repointed_edge(session, ctx, edge, target.id, target_is_from=person_is_from)
        else:
            # 终态边无唯一约束负担，直接改指向保留轨迹
            if person_is_from:
                edge.from_user = target.id
            else:
                edge.to_user = target.id
        if edge.created_by == person.id:
            edge.created_by = target.id

    for attachment in attachments_to_move:
        attachment.user_id = target.id
        if attachment.uploaded_by == person.id:
            attachment.uploaded_by = target.id

    for fact in facts_to_repoint:
        source_facts.repoint_fact_for_identity_merge(
            session,
            fact,
            retired_user_id=person.id,
            survivor_user_id=target.id,
            merged_event_id=merged_event.id,
            actor_account_id=ctx.account_id,
        )
    for fact in facts_colliding:
        # 碰撞行不能直接改指向（自环/撞 uq_source_facts_active）：confirmed/disputed
        # 走 FSM revoke 留事件痕迹；proposed 留给人物删除时的 FK CASCADE
        if fact.state in (source_facts.FACT_CONFIRMED, source_facts.FACT_DISPUTED):
            source_facts.transition_source_fact(
                session, fact, source_facts.ACTION_REVOKE, actor_account_id=ctx.account_id
            )

    session.delete(person)  # 无凭据人物：无账号/会话级联；残留引用行随 CASCADE 消失
    session.flush()
    return moved


def _settle_edge(session: Session, ctx: ActorContext, edge: Relation, target_id: int) -> None:
    """person↔target 自环冲突边的 FSM 终态化（target 恒为边的一端，动作合法）。"""
    if edge.status == "pending":
        action = "cancel" if edge.from_user == target_id else "reject"
    else:
        action = "revoke"
    relation_fsm.transition(edge, action, target_id, session)
    emit(
        session,
        event_type=f"relation.{action}",
        aggregate_type="relation",
        aggregate_id=edge.id,
        payload={
            "from_user": edge.from_user,
            "to_user": edge.to_user,
            "dir_class": edge.dir_class,
            "status": edge.status,
        },
        actor_account_id=ctx.account_id,
    )


def _settle_repointed_edge(
    session: Session, ctx: ActorContext, edge: Relation, target_id: int, *, target_is_from: bool
) -> None:
    """改指向后的非终态边处置：无碰撞且 target 为被请求方 → accept（建档关系意图）。

    与 target 既有非终态边碰撞 → 按 FSM 终态化（reject/cancel/revoke），保证
    uq_relations_pair_* 不被并回打破；pending 且 target 为发起方的边留给对方
    正常接受（不代接受）。
    """
    other = edge.to_user if target_is_from else edge.from_user
    colliding = session.scalar(
        select(Relation.id).where(
            or_(
                (Relation.from_user == other) & (Relation.to_user == target_id),
                (Relation.from_user == target_id) & (Relation.to_user == other),
            ),
            Relation.status.in_(NON_TERMINAL_STATUSES),
            Relation.id != edge.id,
        )
    )
    if colliding is None and edge.status == "pending" and not target_is_from:
        action = "accept"
    elif edge.status == "pending":
        action = "cancel" if target_is_from else "reject"
    else:
        action = "revoke"
    relation_fsm.transition(edge, action, target_id, session)
    emit(
        session,
        event_type=f"relation.{action}",
        aggregate_type="relation",
        aggregate_id=edge.id,
        payload={
            "from_user": edge.from_user,
            "to_user": edge.to_user,
            "dir_class": edge.dir_class,
            "status": edge.status,
        },
        actor_account_id=ctx.account_id,
    )


# ---- reject / cancel：pending → 终态（不可逆），撞名人物随终态废弃 ----


def reject_binding(session: Session, ctx: ActorContext, binding_id: int) -> AccountBinding:
    """被绑定人拒绝（终态）：撞名建档产生的人物随之废弃。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        binding = session.get(AccountBinding, binding_id)
        if binding is None or binding.target_id != actor.id:
            raise_api_error(404, BINDING_NOT_FOUND, MESSAGE_NOT_FOUND)
        if binding.status != "pending":
            raise_api_error(
                409, BINDING_ALREADY_RESOLVED, MESSAGE_RESOLVED, detail={"status": binding.status}
            )
        binding.status = "rejected"
        binding.resolved_at = timeutil.utcnow()
        _dispose_binding_person(session, ctx, binding=binding, actor_id=actor.id)
        audit.write_audit(
            session,
            action="account_binding_rejected",
            actor_id=actor.id,
            target_id=binding.id,
            ip=ctx.ip,
            detail={},
        )
    return binding


def cancel_binding(session: Session, ctx: ActorContext, binding_id: int) -> AccountBinding:
    """发起人取消 pending 绑定（终态）：撞名建档产生的人物随之废弃。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        binding = session.get(AccountBinding, binding_id)
        if binding is None or binding.initiator_id != actor.id:
            raise_api_error(404, BINDING_NOT_FOUND, MESSAGE_NOT_FOUND)
        if binding.status != "pending":
            raise_api_error(
                409, BINDING_ALREADY_RESOLVED, MESSAGE_RESOLVED, detail={"status": binding.status}
            )
        binding.status = "cancelled"
        binding.resolved_at = timeutil.utcnow()
        _dispose_binding_person(session, ctx, binding=binding, actor_id=actor.id)
        audit.write_audit(
            session,
            action="account_binding_cancelled",
            actor_id=actor.id,
            target_id=binding.id,
            ip=ctx.ip,
            detail={},
        )
    return binding


def _dispose_binding_person(
    session: Session, ctx: ActorContext, *, binding: AccountBinding, actor_id: int
) -> None:
    """终态处置撞名建档人物（失效事件合同与 delete_profile_core 同形）。"""
    if binding.person_id is None:
        return
    person = session.get(User, binding.person_id)
    if person is None:  # 人物已被发起人经建档删除路径处置：仅终态化绑定行
        return
    space_ids = _active_identity_space_ids(session, person.id)
    attachment_count = session.query(Attachment).filter(Attachment.user_id == person.id).count()
    # 事件先行：RAG/投影失效需要能在删除前找到该人物的文档
    emit(
        session,
        event_type="profile.deleted",
        aggregate_type="profile",
        aggregate_id=person.id,
        payload={
            "snapshot_name": person.name,
            "deleted_by": actor_id,
            "deleted_by_account": ctx.account_id,
            "space_ids": space_ids,
        },
    )
    session.delete(person)
    session.flush()
    emit(
        session,
        event_type="attachments.invalidated",
        aggregate_type="profile",
        aggregate_id=person.id,
        payload={"attachment_count": attachment_count},
    )
    emit(
        session,
        event_type="disclosure.invalidated",
        aggregate_type="profile",
        aggregate_id=person.id,
        payload={},
    )
