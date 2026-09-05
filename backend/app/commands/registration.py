"""注册与邀请码命令（09-05 家庭账号开通与注册流程；HTTP 与未来 Agent 工具共用）。

设计合同（design.md §3/§4）：
- 注册一个短事务：用户名查重（防枚举统一文案）→ User(provisional) +
  Account(claimed, pin_must_change=False) → 码分支 → refresh 会话签发 → 审计。
  Account 的 claimed 是**初始态直接落库**（自设 PIN、无强制改密），不走也不新增
  managed→claimed 转换点；Profile 恒为 provisional，identity_confirmed 只能由
  identity_fsm 既有唯一转换点（家人确认）获得。
- 码分支：无码 → §215 空态引导（后端不代建空间）；household/lineage → 复用
  services/invite_codes.join_space_with_code 的既有 SpaceMember pending→accept
  唯一路径；stranger → create_space(household, 「我的家庭」)（创建者即
  space_admin）+ 归因审计，与码创建者之间无任何成员关系。
- 码原语（生成/校验/核销/撤销）在 services/invite_codes.py；本层只做编排与
  事务所有权（§0.6 组合事务惯例）。
- 设置页填码（redeem_invite_code）与注册填码共用同一加入语义（决策 8：同一
  语义、两个入口）；stranger 码在登录态兑换一律 400 明确拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commands.context import ActorContext, command_transaction, load_actor
from app.commands.spaces import create_space as create_space_command
from app.errors import (
    INVITE_CODE_STRANGER_REGISTER_ONLY,
    USERNAME_ALREADY_REGISTERED,
    USERNAME_TAKEN_MESSAGE,
    raise_api_error,
)
from app.models import User
from app.models.account import Account
from app.models.invite_code import InviteCode
from app.services import audit, invite_codes
from app.services.refresh_session import issue_refresh_session
from app.utils import security, timeutil

# 陌生人码分支代建空间的固定名（PRD 决策 9/17、architecture §215 引导语义同源）
MY_FAMILY_SPACE_NAME = "我的家庭"


@dataclass(frozen=True)
class RegistrationResult:
    """注册命令结果：refresh 原文仅本次响应可见；access 由 HTTP 层按 user 签发。"""

    user: User
    refresh_token: str


def register_user(
    session: Session,
    *,
    username: str,
    pin: str,
    invite_code: str | None = None,
    ip: str | None = None,
) -> RegistrationResult:
    """自助注册（POST /auth/register 命令；开关校验与 IP 限流在 HTTP 层前置）。"""
    normalized = username.strip()
    now = timeutil.utcnow()
    with command_transaction(session, immediate=True):
        # 1. 用户名查重：防枚举统一文案 + 假 PIN 校验对齐时序（与登录同源）。
        #    只统计持有 Account 的行：撞名绑定中的 provisional 人物（决策 16）
        #    没有凭据、不可登录，不得占用注册用户名（与登录 join accounts 同口径）。
        taken = session.scalar(
            select(User.id)
            .join(Account, Account.user_id == User.id)
            .where(User.name == normalized)
            .limit(1)
        )
        if taken is not None:
            security.verify_dummy_pin(pin)
            raise_api_error(409, USERNAME_ALREADY_REGISTERED, USERNAME_TAKEN_MESSAGE)

        # 2. 码预检（字段级文案；原子核销在分支末尾，竞态由写锁 + 条件 UPDATE 兜底）
        code = (
            invite_codes.resolve_usable_code(session, invite_code)
            if invite_code is not None
            else None
        )

        # 3. 初始身份：Account 直接 claimed + pin_must_change=False（自设 PIN，无强制
        #    改密步骤）——不是 managed→claimed 转换，是初始态；Profile provisional。
        user = User(
            name=normalized,
            created_at=now,
            privacy_mode="handover",
            created_by=None,
            profile_status="provisional",
        )
        user.account = Account(
            pin_hash=security.hash_pin(pin),
            pin_must_change=False,
            token_version=0,
            failed_attempts=0,
            locked_until=None,
            status="claimed",
            claimed_at=now,
        )
        session.add(user)
        session.flush()  # 取得 user.id / account.user_id 供分支与 refresh 引用

        # 4. 码分支（design.md §4）
        if code is None:
            pass  # §215：无空间空态由前端首登引导创建「我的家庭」，后端不代建
        elif code.kind == "stranger":
            # 纯拉新归因：注册者得到自己的独立新家庭空间（创建者即 space_admin），
            # 与码创建者之间没有任何空间成员关系或档案可见性
            ctx = ActorContext(
                user_id=user.id,
                account_id=user.account.id,
                account_status=user.account.status,
                ip=ip,
            )
            create_space_command(
                session, ctx, name=MY_FAMILY_SPACE_NAME, kind="household", commit=False
            )
            invite_codes.consume_code(session, code)
            invite_codes.write_attribution_audit(
                session, code=code, user_id=user.id, ip=ip, scene="register"
            )
        else:
            # household/lineage：既有 pending→accept 唯一路径（同事务、同审计形状）
            invite_codes.join_space_with_code(
                session,
                code=code,
                user=user,
                account_id=user.account.id,
                ip=ip,
                scene="register",
            )

        # 5. 直接登录态：refresh 会话与账号同事务落库（access 由 HTTP 层签发）
        refresh_raw = issue_refresh_session(session, user.account, rotated_from=None)
        audit.write_audit(
            session,
            action="user_registered",
            actor_id=user.id,
            target_id=user.id,
            ip=ip,
            detail={
                "via_invite_code": code is not None,
                "code_kind": code.kind if code is not None else None,
            },
        )
    return RegistrationResult(user=user, refresh_token=refresh_raw)


# ---- 设置页「邀请码」区块（Chunk C 命令层；HTTP 层薄）----


def create_my_invite_code(
    session: Session,
    ctx: ActorContext,
    *,
    kind: str,
    space_id: int | None = None,
    max_uses: int | None = None,
    ttl_days: int | None = None,
) -> InviteCode:
    """创建我的码：资格与参数校验在码原语内（services/invite_codes.create_code）。"""
    actor = load_actor(session, ctx)
    with command_transaction(session, immediate=True):
        return invite_codes.create_code(
            session,
            creator=actor,
            kind=kind,
            space_id=space_id,
            max_uses=max_uses,
            ttl_days=ttl_days,
            ip=ctx.ip,
        )


def list_my_invite_codes(session: Session, ctx: ActorContext) -> list[InviteCode]:
    """我的码列表（只读，无事务）。"""
    actor = load_actor(session, ctx)
    return invite_codes.list_for_creator(session, actor.id)


def revoke_my_invite_code(session: Session, ctx: ActorContext, code_id: int) -> InviteCode:
    """撤销码：creator 本人或该码空间的空间管理员（权限判定在码原语内）。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        return invite_codes.revoke_code(session, code_id=code_id, actor=actor, ip=ctx.ip)


def redeem_invite_code(session: Session, ctx: ActorContext, *, raw_code: str) -> InviteCode:
    """设置页填码（已登录）：与注册码分支同一加入语义；stranger 码 400 拒绝。"""
    actor = load_actor(session, ctx)
    with command_transaction(session, immediate=True):
        code = invite_codes.resolve_usable_code(session, raw_code)
        if code.kind == "stranger":
            raise_api_error(
                400, INVITE_CODE_STRANGER_REGISTER_ONLY, invite_codes.MESSAGE_STRANGER_REGISTER_ONLY
            )
        invite_codes.join_space_with_code(
            session,
            code=code,
            user=actor,
            account_id=ctx.account_id,
            ip=ctx.ip,
            scene="redeem",
        )
    return code
