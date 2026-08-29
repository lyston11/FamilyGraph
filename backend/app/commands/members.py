"""成员档案命令（建档/档案修改/披露开关/删除）——HTTP 与未来 Agent 共用（AC-F7）。

每条命令一个短事务：授权（custody/space_fsm）→ 校验 → 写入 → domain_events → audit。
建档（F-1/F-3）：provisional 档案 + managed 账号；选空间只建 space_profile_refs
最小节点引用，provisional 人物不是 SpaceMember。确档清单项随建档播种。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import (
    AUTH_INVALID_CREDENTIALS,
    CONFIRM_NAME_MISMATCH,
    DISCLOSURE_SCOPE_REQUIRES_SELF,
    IDEMPOTENCY_PAYLOAD_CONFLICT,
    OWNER_TRANSFER_REQUIRED,
    SPACE_NOT_FOUND,
    UNIFIED_CREDENTIAL_MESSAGE,
    USER_NOT_FOUND,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models import Account, User
from app.models.attachment import Attachment
from app.models.space import FamilySpace, SpaceProfileRef
from app.models.v2_foundation import MemberCreationRequest, ProfileFactReview
from app.services import audit, custody, identity_fsm, relation_fsm, source_facts
from app.services import disclosure as disclosure_service
from app.services.domain_events import emit
from app.utils import security, timeutil


@dataclass
class DeletedProfile:
    """删除命令结果：物理文件清理在事务提交后由调用方执行（外部 I/O 不进事务）。"""

    profile_id: int
    snapshot: dict[str, Any]
    purge_image_paths: list[str] = field(default_factory=list)


def _enrich(value: Any) -> Any:
    from app.services.lunar import enrich_structured_date

    return enrich_structured_date(value) if isinstance(value, dict) else value


def _seed_fact_reviews(session: Session, member: User) -> None:
    """确档清单播种（F-1）：名字必审，其余按已填字段生成；创建者关系单独一项。

    自由描述不自动成为正式事实 —— 清单项只是「待本人核对」的提议。
    """
    now = timeutil.utcnow()
    items: list[tuple[str, dict[str, Any]]] = [("name", {"field": "name", "value": member.name})]
    for field_name in ("gender", "birth", "death", "bio"):
        value = getattr(member, field_name)
        if value not in (None, "") and value != "unknown":
            items.append((field_name, {"field": field_name}))
    if member.created_by is not None:
        creator = session.get(User, member.created_by)
        items.append(
            (
                "relation_to_creator",
                {
                    "creator_id": member.created_by,
                    "creator_name": creator.name if creator else None,
                },
            )
        )
    for item_type, ref in items:
        session.add(
            ProfileFactReview(
                profile_id=member.id,
                item_type=item_type,
                item_ref_json=ref,
                proposed_by=member.created_by,
                status="proposed",
                created_at=now,
            )
        )


def _create_member_core(
    session: Session,
    ctx: ActorContext,
    *,
    name: str,
    gender: str = "unknown",
    birth: dict[str, Any] | None = None,
    death: dict[str, Any] | None = None,
    bio: str | None = None,
    privacy_mode: str = "handover",
    space_membership_space_id: int | None = None,
) -> tuple[User, str]:
    """建房核心（F-1/F-3）：provisional 档案 + managed 账号 + 一次性 PIN + 空间引用
    + 确档清单。不管理事务，由调用方包在自己的 command_transaction 内。
    """
    actor = load_actor(session, ctx)
    now = timeutil.utcnow()
    pin = security.generate_pin()
    member = User(
        name=name.strip(),
        created_at=now,
        gender=gender,
        birth=_enrich(birth),
        death=_enrich(death),
        bio=bio,
        privacy_mode=privacy_mode,
        created_by=actor.id,
        # F-3：新建他人恒为 provisional 档案；身份确认由本人完成
        profile_status="provisional",
    )
    member.account = Account(
        pin_hash=security.hash_pin(pin),
        pin_must_change=True,
        token_version=0,
        failed_attempts=0,
        locked_until=None,
        status="managed",
    )
    session.add(member)
    session.flush()  # 取得 id 供空间引用/清单/审计引用

    space_id: int | None = None
    if space_membership_space_id is not None:
        space = session.get(FamilySpace, space_membership_space_id)
        if space is None or not space_fsm_is_active(session, space.id, actor.id):
            raise_api_error(404, SPACE_NOT_FOUND, "目标家庭空间不存在或无权操作")
        space_id = space.id
        session.add(
            SpaceProfileRef(
                space_id=space.id,
                user_id=member.id,
                added_by=actor.id,
                status="active",
                created_at=now,
            )
        )

    _seed_fact_reviews(session, member)
    emit(
        session,
        event_type="profile.created",
        aggregate_type="profile",
        aggregate_id=member.id,
        payload={"name": member.name, "created_by": actor.id},
        space_id=space_id,
        actor_account_id=ctx.account_id,
    )
    audit.write_audit(
        session,
        action="profile_created",
        actor_id=actor.id,
        target_id=member.id,
        ip=ctx.ip,
        detail={"name": member.name, "privacy_mode": privacy_mode},
    )
    return member, pin


def create_member(
    session: Session,
    ctx: ActorContext,
    *,
    name: str,
    gender: str = "unknown",
    birth: dict[str, Any] | None = None,
    death: dict[str, Any] | None = None,
    bio: str | None = None,
    privacy_mode: str = "handover",
    space_membership_space_id: int | None = None,
) -> tuple[User, str]:
    """低层建房（内部/测试用）：仅 user+account+PIN，无关系。

    公开「名字+关系必填」语义由 create_managed_member（POST /users）强制。
    返回 (member, 明文 PIN)；PIN 仅本次响应可见（A3/AD-1）。
    """
    with command_transaction(session):
        return _create_member_core(
            session,
            ctx,
            name=name,
            gender=gender,
            birth=birth,
            death=death,
            bio=bio,
            privacy_mode=privacy_mode,
            space_membership_space_id=space_membership_space_id,
        )


def create_managed_member(
    session: Session,
    ctx: ActorContext,
    *,
    name: str,
    relation_dir_class: str,
    idempotency_key: str,
    request_hash: str,
    gender: str = "unknown",
    birth: dict[str, Any] | None = None,
    death: dict[str, Any] | None = None,
    bio: str | None = None,
    privacy_mode: str = "handover",
    space_membership_space_id: int | None = None,
    relation_label: str | None = None,
    relation_text: str | None = None,
) -> tuple[User, str | None, bool]:
    """F-1 原子建档：provisional 档案 + managed 账号 +（AD-4 新建例外）直接 active
    关系 + 关系原文 + proposed SourceFact + 空间引用 + 事件/审计 + 幂等台账，
    任一步失败整体回滚。

    返回 (member, pin, replayed)。新建时返回一次性 PIN；同幂等键重放返回原
    档案但不回放 PIN（replayed=True, pin=None）—— 初始 PIN 只出现一次。
    """
    actor = load_actor(session, ctx)
    key = idempotency_key.strip()
    if not key:
        raise_api_error(422, VALIDATION_ERROR, "缺少幂等请求键")
    if len(key) > 120:
        raise_api_error(422, VALIDATION_ERROR, "幂等请求键过长")

    prior = _find_member_creation(session, actor.id, key)
    if prior is not None:
        return _replay_member_creation(session, prior, request_hash)

    try:
        with command_transaction(session):
            member, pin = _create_member_core(
                session,
                ctx,
                name=name,
                gender=gender,
                birth=birth,
                death=death,
                bio=bio,
                privacy_mode=privacy_mode,
                space_membership_space_id=space_membership_space_id,
            )

            # AD-4 新建账号例外：managed 新档由代管人创建 → relation 直接 active
            edge = relation_fsm.create_relation(
                session,
                from_user=actor.id,
                to_user=member.id,
                dir_class=relation_dir_class,
                label=relation_label,
                status="active",
            )

            # 关系原文 append-only（有则保存）；无原文时以 dir_class+label 组成上下文
            raw_text_id: int | None = None
            if relation_text is not None and relation_text.strip():
                raw = source_facts.create_raw_relation_input(
                    session,
                    author_account_id=ctx.account_id,
                    text=relation_text,
                    context={"dir_class": relation_dir_class, "label": relation_label},
                )
                raw_text_id = raw.id
            # proposed SourceFact（关系仍待对方确档确认；结构真源不由写入者单方决定）
            source_facts.create_structural_edge_proposal(
                session,
                from_user=actor.id,
                to_user=member.id,
                dir_class=relation_dir_class,
                raw_text_id=raw_text_id,
                asserted_by_account_id=ctx.account_id,
            )

            emit(
                session,
                event_type="relation.created",
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
            audit.write_audit(
                session,
                action="relation_created",
                actor_id=actor.id,
                target_id=member.id,
                ip=ctx.ip,
                detail={"relation_id": edge.id, "dir_class": relation_dir_class},
            )

            session.add(
                MemberCreationRequest(
                    actor_user_id=actor.id,
                    idempotency_key=key,
                    request_hash=request_hash,
                    member_user_id=member.id,
                    relation_id=edge.id,
                    created_at=timeutil.utcnow(),
                )
            )
    except IntegrityError:
        # 并发窗口：同 key 由另一请求先提交 → 唯一约束冲突，按重放裁决
        session.rollback()
        prior = _find_member_creation(session, actor.id, key)
        if prior is not None:
            return _replay_member_creation(session, prior, request_hash)
        raise
    return member, pin, False


def _find_member_creation(
    session: Session, actor_user_id: int, key: str
) -> MemberCreationRequest | None:
    return session.scalar(
        select(MemberCreationRequest).where(
            MemberCreationRequest.actor_user_id == actor_user_id,
            MemberCreationRequest.idempotency_key == key,
        )
    )


def _replay_member_creation(
    session: Session, prior: MemberCreationRequest, request_hash: str
) -> tuple[User, str | None, bool]:
    if prior.request_hash != request_hash:
        raise_api_error(409, IDEMPOTENCY_PAYLOAD_CONFLICT, "相同请求键但请求内容不同")
    member = session.get(User, prior.member_user_id)
    if member is None:
        raise_api_error(409, VALIDATION_ERROR, "幂等请求对应的档案已不存在")
    return member, None, True


def canonical_member_request_hash(
    *,
    name: str,
    gender: str,
    birth: dict[str, Any] | None,
    death: dict[str, Any] | None,
    bio: str | None,
    privacy_mode: str,
    space_membership_space_id: int | None,
    relation_dir_class: str,
    relation_label: str | None,
    relation_text: str | None,
) -> str:
    """请求内容指纹：稳定字段序 + key 排序；同请求键重放时据此判定内容一致。"""
    canonical = {
        "name": name.strip(),
        "gender": gender,
        "birth": birth,
        "death": death,
        "bio": bio,
        "privacy_mode": privacy_mode,
        "space_membership_space_id": space_membership_space_id,
        "relation_dir_class": relation_dir_class,
        "relation_label": relation_label,
        "relation_text": relation_text.strip() if relation_text else None,
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def space_fsm_is_active(session: Session, space_id: int, user_id: int) -> bool:
    """局部转发避免模块命名冲突（services.space_fsm.is_active_member）。"""
    from app.services import space_fsm

    return space_fsm.is_active_member(session, space_id, user_id)


def rename_own_profile(session: Session, ctx: ActorContext, *, name: str) -> User:
    """本人改名（A1）：随时可改，不改名不失效会话。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        old_name = actor.name
        actor.name = name.strip()
        emit(
            session,
            event_type="profile.updated",
            aggregate_type="profile",
            aggregate_id=actor.id,
            payload={"fields": ["name"], "updated_by": actor.id},
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="name_changed",
            actor_id=actor.id,
            target_id=actor.id,
            ip=ctx.ip,
            detail={"old_name": old_name},
        )
    return actor


def change_own_pin(
    session: Session,
    ctx: ActorContext,
    *,
    old_pin: str,
    new_pin: str,
) -> tuple[User, bool]:
    """改 PIN：验旧 PIN → 新哈希 → 版本+1；首登强制改完成时触发认领转换。

    返回 (user, was_forced)；全部旧 refresh 会话同事务作废。
    """
    from app.services import refresh_session as refresh_session_service

    actor = load_actor(session, ctx)
    if not security.verify_pin(old_pin, actor.account.pin_hash):
        # 旧 PIN 错误同样走防枚举统一文案
        raise_api_error(401, AUTH_INVALID_CREDENTIALS, UNIFIED_CREDENTIAL_MESSAGE)

    was_forced = bool(actor.account.pin_must_change)
    with command_transaction(session):
        actor.account.pin_hash = security.hash_pin(new_pin)
        actor.account.pin_must_change = False
        actor.account.token_version += 1
        actor.account.failed_attempts = 0
        actor.account.locked_until = None
        if was_forced and actor.account.status == "managed":
            # 首登强制改 PIN 完成 = 认领完成（v2：managed→claimed 唯一转换点）
            identity_fsm.claim_account(session, actor.account)
        refresh_session_service.revoke_all_active(session, actor.id, ip=None, reason="pin_change")
        audit.write_audit(
            session,
            action="pin_changed",
            actor_id=actor.id,
            target_id=actor.id,
            ip=ctx.ip,
            detail={"claim_completed": was_forced},
        )
    return actor, was_forced


def update_member_profile(
    session: Session,
    ctx: ActorContext,
    target_id: int,
    changes: dict[str, Any],
) -> User:
    """档案字段编辑：custody 授权单点，变更审计 + 领域事件。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        target = session.get(User, target_id)
        if target is None:
            raise_api_error(404, USER_NOT_FOUND, "资源不存在")
        custody.assert_can_edit(actor, target)

        applied: list[str] = []
        for field_name in ("gender", "birth", "death", "bio"):
            if field_name in changes:
                setattr(target, field_name, _enrich(changes[field_name]))
                applied.append(field_name)
        if changes.get("name") is not None:
            target.name = str(changes["name"]).strip()
            applied.append("name")

        emit(
            session,
            event_type="profile.updated",
            aggregate_type="profile",
            aggregate_id=target.id,
            payload={"fields": sorted(applied), "updated_by": actor.id},
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="profile_updated",
            actor_id=actor.id,
            target_id=target.id,
            ip=ctx.ip,
            detail={"fields": sorted(applied)},
        )
    return target


def update_disclosure(
    session: Session,
    ctx: ActorContext,
    target_id: int,
    flags: dict[str, bool],
    *,
    space_id: int | None = None,
) -> User:
    """披露开关整体替换（基础五类）；修改权：全局=档案编辑权主体，
    逐空间覆盖（space_id 非空）仅档案本人（v2 Gap3：防止代管人代设空间披露）。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        target = session.get(User, target_id)
        if target is None:
            raise_api_error(404, USER_NOT_FOUND, "资源不存在")
        custody.assert_can_edit(actor, target)

        scope_payload: dict[str, Any] = {"disclosure": flags, "updated_by": actor.id}
        if space_id is not None:
            if actor.id != target.id:
                raise_api_error(
                    403, DISCLOSURE_SCOPE_REQUIRES_SELF, "逐空间披露偏好仅档案本人可修改"
                )
            space = session.get(FamilySpace, space_id)
            if space is None:
                raise_api_error(404, SPACE_NOT_FOUND, "目标家庭空间不存在")
            disclosure_service.set_space_disclosure(session, target, space_id, flags)
            scope_payload.update(scope="space", space_id=space_id)
        else:
            disclosure_service.set_basic_disclosure(session, target, flags)
            scope_payload["scope"] = "global"
        emit(
            session,
            event_type="disclosure.updated",
            aggregate_type="profile",
            aggregate_id=target.id,
            payload=scope_payload,
            actor_account_id=ctx.account_id,
            space_id=space_id,
        )
        audit.write_audit(
            session,
            action="disclosure_updated",
            actor_id=actor.id,
            target_id=target.id,
            ip=ctx.ip,
            detail=dict(scope_payload),
        )
    return target


def delete_profile_core(
    session: Session,
    ctx: ActorContext,
    target: User,
    *,
    confirm_name: str,
) -> DeletedProfile:
    """删除核心（delete_member 与数据权利 execute-delete 共用）。

    单事务级联（账号/会话随 FK CASCADE）；空间所有者先经显式义务预检引导移交，
    owner_id RESTRICT 作为数据库兜底；audit 保留快照；tombstone 失效事件驱动
    缓存/附件/披露投影清理（§0.6 合同）。调用方负责在其自身 command_transaction
    内调用并在提交后清理物理文件。
    """
    from app.commands.ownership import assert_no_owner_obligations

    assert_no_owner_obligations(session, ctx, target.id)

    if confirm_name.strip() != target.name.strip():
        raise_api_error(409, CONFIRM_NAME_MISMATCH, "输入的名字与档案名字不一致")

    image_paths = [
        row.url_or_path
        for row in session.query(Attachment)
        .filter(Attachment.user_id == target.id, Attachment.type == "image")
        .all()
    ]
    snapshot = {
        "id": target.id,
        "name": target.name,
        "gender": target.gender,
        "birth": target.birth,
        "death": target.death,
        "bio": target.bio,
        "privacy_mode": target.privacy_mode,
        "profile_status": target.profile_status,
        "account_status": target.account.status,
        "created_by": target.created_by,
    }
    # Publish before the profile is flushed away so the RAG invalidation can
    # still find documents owned by this profile. The event payload is a
    # deletion-safe snapshot and does not use a foreign key to the user.
    emit(
        session,
        event_type="profile.deleted",
        aggregate_type="profile",
        aggregate_id=target.id,
        payload={
            "snapshot_name": snapshot["name"],
            "deleted_by": ctx.user_id,
            "deleted_by_account": ctx.account_id,
        },
    )
    session.delete(target)  # flush 时级联删除账号等子行，audit 行保留（无 FK）
    try:
        session.flush()
    except Exception:
        # v2 §0.5 兜底：义务预检与 RESTRICT 之间的竞态窗口
        session.rollback()
        raise_api_error(
            409,
            OWNER_TRANSFER_REQUIRED,
            "该档案是家庭空间所有者，请先完成 owner 移交后再删除",
        )

    # The deletion event was emitted before the cascade so RAG tombstones are
    # durable even when the owner_user_id foreign key is set NULL by SQLite.
    emit(
        session,
        event_type="attachments.invalidated",
        aggregate_type="profile",
        aggregate_id=target.id,
        payload={"attachment_count": len(image_paths)},
    )
    emit(
        session,
        event_type="disclosure.invalidated",
        aggregate_type="profile",
        aggregate_id=target.id,
        payload={},
    )
    return DeletedProfile(profile_id=target.id, snapshot=snapshot, purge_image_paths=image_paths)


def delete_member(
    session: Session,
    ctx: ActorContext,
    target_id: int,
    *,
    confirm_name: str,
) -> DeletedProfile:
    """删除档案：本人 ∨ 代管创建者（custody 判定）；二次确认名字。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        target = session.get(User, target_id)
        if target is None:
            raise_api_error(404, USER_NOT_FOUND, "资源不存在")
        custody.assert_can_delete(actor, target)

        result = delete_profile_core(session, ctx, target, confirm_name=confirm_name)
        audit.write_audit(
            session,
            action="profile_deleted",
            # 自删场景：actor 行已随级联删除，审计以快照留痕（actor_id 置 NULL）
            actor_id=None if actor.id == result.profile_id else actor.id,
            target_id=result.profile_id,
            ip=ctx.ip,
            detail={
                "snapshot": result.snapshot,
                "actor_id": actor.id,
                "self_deleted": actor.id == result.profile_id,
            },
        )
    return result
