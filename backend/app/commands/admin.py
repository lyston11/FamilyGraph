"""平台运营者 break-glass 命令（v2 §0.2/§0.6）。

operator 对家庭档案的兜底修正是受控例外：理由必填 + 完整审计；授权在命令
事务内按库内平台角色复核，且不产生任何日常家庭数据浏览权。每条命令一个
短事务：授权 → 校验 → 写入 → domain_events → audit。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import (
    AUTH_INVALID_CREDENTIALS,
    BREAK_GLASS_NOTE_REQUIRED,
    UNIFIED_CREDENTIAL_MESSAGE,
    USER_NOT_FOUND,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models.account import Account
from app.models.user import User
from app.services import audit
from app.services.domain_events import emit
from app.services.platform_roles import require_platform_operator


def _require_operator(session: Session, ctx: ActorContext) -> None:
    """命令内授权：actor 只能来自认证上下文；平台角色以当前库内状态为准。"""
    account = session.get(Account, ctx.account_id)
    if account is None:
        raise_api_error(401, AUTH_INVALID_CREDENTIALS, UNIFIED_CREDENTIAL_MESSAGE)
    require_platform_operator(session, account)


def admin_update_user(
    session: Session,
    ctx: ActorContext,
    user_id: int,
    *,
    name: str | None = None,
    privacy_mode: str | None = None,
    transfer_custody_to: int | None = None,
    note: str,
) -> tuple[User, dict[str, Any]]:
    """operator 数据兜底修正（break-glass）：改名 / 归属模式 / 代管权转移。

    created_by 变更是 custody 主体变更（F-5：与 owner 移交、Account claim
    互相独立）：新代管人必须存在且非档案本人；profile.updated 与 custody 事件
    随写入同事务落库，审计含必填理由。返回 (target, changes)；changes 键与
    既有 API 响应兼容（name / privacy_mode / transferred_to）。
    """
    if not note.strip():
        raise_api_error(422, BREAK_GLASS_NOTE_REQUIRED, "平台人工处理必须提供理由（break-glass）")
    actor = load_actor(session, ctx)
    with command_transaction(session):
        _require_operator(session, ctx)
        target = session.get(User, user_id)
        if target is None:
            raise_api_error(404, USER_NOT_FOUND, "用户不存在")

        changes: dict[str, Any] = {}
        profile_fields: list[str] = []
        old_custodian: int | None = None
        custody_transferred = False
        if name is not None:
            target.name = name.strip()
            changes["name"] = target.name
            profile_fields.append("name")
        if privacy_mode is not None:
            target.privacy_mode = privacy_mode
            changes["privacy_mode"] = privacy_mode
            profile_fields.append("privacy_mode")
        if transfer_custody_to is not None:
            new_guardian = session.get(User, transfer_custody_to)
            if new_guardian is None:
                raise_api_error(404, USER_NOT_FOUND, "新代管人不存在")
            if new_guardian.id == target.id:
                raise_api_error(422, VALIDATION_ERROR, "新代管人不能是档案本人")
            old_custodian = target.created_by
            target.created_by = new_guardian.id
            changes["transferred_to"] = new_guardian.id
            custody_transferred = True

        if not changes:
            raise_api_error(422, VALIDATION_ERROR, "未提供任何修改项")

        if profile_fields:
            emit(
                session,
                event_type="profile.updated",
                aggregate_type="profile",
                aggregate_id=target.id,
                payload={"fields": sorted(profile_fields), "updated_by_operator": True},
                actor_account_id=ctx.account_id,
            )
        if custody_transferred:
            emit(
                session,
                event_type="profile.custody.transferred",
                aggregate_type="profile",
                aggregate_id=target.id,
                payload={
                    "from_user": old_custodian,
                    "to_user": changes["transferred_to"],
                    "by_operator_account": ctx.account_id,
                },
                actor_account_id=ctx.account_id,
            )
        audit.write_audit(
            session,
            action="admin_user_updated",
            actor_id=actor.id,
            target_id=user_id,
            ip=ctx.ip,
            detail={
                "changes": changes,
                "note": note,
                "break_glass": True,
                "operator_account": ctx.account_id,
            },
        )
    return target, changes
