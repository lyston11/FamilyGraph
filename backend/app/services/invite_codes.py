"""邀请码原语：生成 / 校验 / 核销 / 撤销 + 归因审计（09-05 design.md §2/§3）。

本模块只提供码原语与授权判定，事务由命令层（commands/registration.py）拥有；
household/lineage 码的加入动作复用既有 SpaceMember pending→accept 唯一路径
（services/space_fsm），注册填码与设置页填码共用 join_space_with_code，
不许出现第二条状态机路径。

- 无混淆字符集（去 0/O/1/I/L）；
- household/lineage 一次性（max_uses=1），stranger 多人次可设上限；
- 默认 7 天有效期（创建时可选）；
- 归因不建新表：兑换/注册成功写既有 audit 事件，载荷含 code_id、code_kind、
  creator_id、被邀请 user_id；SpaceMember.added_by 天然记录邀请人。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import (
    INVITE_CODE_INVALID,
    INVITE_CODE_STATE_CONFLICT,
    SPACE_NOT_FOUND,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models.invite_code import INVITE_CODE_KINDS, InviteCode
from app.models.space import FamilySpace, SpaceMember
from app.models.user import User
from app.services import audit, space_fsm
from app.services.domain_events import emit
from app.utils.timeutil import utcnow

# 无混淆字符集：去 0/O/1/I/L（31 个可安全手抄的字符）
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH_MIN = 8
CODE_LENGTH_MAX = 10
DEFAULT_CODE_LENGTH = 8
DEFAULT_TTL_DAYS = 7

# 码校验失败的字段级文案（PRD：只描述码本身状态，不暴露创建者等任何其他信息）
MESSAGE_INVALID = "邀请码不存在或无效"
MESSAGE_EXPIRED = "邀请码已过期"
MESSAGE_REVOKED = "邀请码已撤销"
MESSAGE_EXHAUSTED = "邀请码使用次数已达上限"
MESSAGE_STRANGER_REGISTER_ONLY = "陌生人码仅可在注册时使用"


def generate_code(length: int = DEFAULT_CODE_LENGTH) -> str:
    """CSPRNG 随机码：无混淆字符集；8–10 位（design.md 决策 12）。"""
    if not CODE_LENGTH_MIN <= length <= CODE_LENGTH_MAX:
        raise ValueError(f"invite code length must be {CODE_LENGTH_MIN}–{CODE_LENGTH_MAX}")
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def normalize_raw_code(raw_code: str | None) -> str:
    """入参归一：去空白 + 统一大写（用户手抄小写也能命中）。"""
    return (raw_code or "").strip().upper()


def _rejection_reason(code: InviteCode, now: datetime) -> str | None:
    """码不可用原因（文案区分：仅码本身状态，不暴露创建者信息）。"""
    if code.revoked_at is not None:
        return MESSAGE_REVOKED
    if code.expires_at <= now:
        return MESSAGE_EXPIRED
    if code.max_uses is not None and code.used_count >= code.max_uses:
        return MESSAGE_EXHAUSTED
    return None


def resolve_usable_code(session: Session, raw_code: str | None) -> InviteCode:
    """按明文码取行并校验（未过期/未撤销/未用尽）；任何失败统一 400 字段级文案。"""
    normalized = normalize_raw_code(raw_code)
    code = session.scalar(select(InviteCode).where(InviteCode.code == normalized))
    reason = _rejection_reason(code, utcnow()) if code is not None else None
    if code is None or reason is not None:
        raise_api_error(400, INVITE_CODE_INVALID, reason or MESSAGE_INVALID)
    return code


def consume_code(session: Session, code: InviteCode) -> None:
    """原子核销：条件 UPDATE used_count+1，rowcount 裁决并发上界（A3 合同）。

    必须在命令层的立即事务（写锁前置）内调用：resolve → consume 的检查窗口
    由 BEGIN IMMEDIATE 收窄，条件 UPDATE 兜底裁决，两个并发核销恰好一个胜出。
    """
    now = utcnow()
    result = session.execute(
        update(InviteCode)
        .where(
            InviteCode.id == code.id,
            InviteCode.revoked_at.is_(None),
            InviteCode.expires_at > now,
            or_(InviteCode.max_uses.is_(None), InviteCode.used_count < InviteCode.max_uses),
        )
        .values(used_count=InviteCode.used_count + 1)
    )
    if result.rowcount != 1:
        # 并发窗口内状态突变（理论不可达：写锁已前置）；按当前态给出字段级文案
        reason = _rejection_reason(code, now) or MESSAGE_INVALID
        raise_api_error(400, INVITE_CODE_INVALID, reason)
    session.expire(code, ["used_count"])


def write_attribution_audit(
    session: Session,
    *,
    code: InviteCode,
    user_id: int,
    ip: str | None,
    scene: str,
    space_id: int | None = None,
) -> None:
    """归因审计（design.md §2）：载荷含 code_id、code_kind、creator_id、被邀请 user_id。

    陌生人码场景同样落本事件（归因不建新表）；audit 行独立于主体删除保留。
    """
    audit.write_audit(
        session,
        action="invite_code_redeemed",
        actor_id=user_id,
        target_id=user_id,
        ip=ip,
        detail={
            "code_id": code.id,
            "code_kind": code.kind,
            "creator_id": code.creator_id,
            "scene": scene,
            "space_id": space_id,
        },
    )


def join_space_with_code(
    session: Session,
    *,
    code: InviteCode,
    user: User,
    account_id: int,
    ip: str | None,
    scene: str,
) -> SpaceMember:
    """household/lineage 码加入空间：既有 SpaceMember pending→accept 唯一路径。

    - SpaceMember.added_by 记录码创建者（邀请人归因）；
    - 接受动作 = 既有 respond 语义（同一 space_fsm.transition 调用、同一审计形状）；
    - 核销码（used_count+1）+ 归因审计，全部在调用方事务内完成。
    """
    space = session.get(FamilySpace, code.space_id)
    if space is None:  # 理论不可达：码随空间 CASCADE 删除，残留码按无效处理
        raise_api_error(400, INVITE_CODE_INVALID, MESSAGE_INVALID)
    if code.creator_id is None:
        # 理论不可达（0032）：创建者删除时其码已被 delete_profile_core 自动撤销，
        # 可用码必有创建者；数据异常时按无效码拒绝，不让 NULL 归因进入成员行。
        raise_api_error(400, INVITE_CODE_INVALID, MESSAGE_INVALID)
    member, _created = space_fsm.invite(
        session, space=space, user_id=user.id, added_by=code.creator_id
    )
    if space_fsm.effective_status(member) == "active":
        # 已是该空间成员：码不核销、无状态变化（幂等拒绝优于静默重复核销）
        raise_api_error(409, VALIDATION_ERROR, "你已经是该空间成员")
    space_fsm.transition(member, "accept", user.id, session)
    emit(
        session,
        event_type="space.membership.changed",
        aggregate_type="space",
        aggregate_id=space.id,
        payload={"action": "accepted", "user_id": user.id, "by": user.id},
        space_id=space.id,
        actor_account_id=account_id,
    )
    audit.write_audit(
        session,
        action="space_invite_accepted",
        actor_id=user.id,
        target_id=user.id,
        ip=ip,
        detail={"space_id": space.id, "invite_code_id": code.id},
    )
    consume_code(session, code)
    write_attribution_audit(
        session, code=code, user_id=user.id, ip=ip, scene=scene, space_id=space.id
    )
    return member


def create_code(
    session: Session,
    *,
    creator: User,
    kind: str,
    space_id: int | None = None,
    max_uses: int | None = None,
    ttl_days: int | None = None,
    ip: str | None = None,
) -> InviteCode:
    """建码原语 + 资格判定（PRD 决策 11/13 修订）：每个已登录账号都可建码。

    - household/lineage：须为该空间 active 成员（无成员资格 404 防枚举），
      max_uses 恒为 1；
    - stranger：任意已登录账号可建（纯归因码，持码者得自己的独立空间，
      无数据暴露面），可设使用上限（NULL=不限）；
    - 不设身份确认门槛（决策 13 修订：provisional 同样可建码，接受侧本就无门槛）。
    事务由调用方拥有；码唯一性 = 命名唯一约束兜底 + 写锁前置查重。
    """
    if kind not in INVITE_CODE_KINDS:
        raise_api_error(422, VALIDATION_ERROR, f"未知邀请码类型 {kind}")

    if kind in ("household", "lineage"):
        if space_id is None:
            raise_api_error(422, VALIDATION_ERROR, "家庭/家族码必须绑定目标空间")
        if max_uses is not None and max_uses != 1:
            raise_api_error(422, VALIDATION_ERROR, "家庭/家族码为一次性码，使用上限恒为 1")
        member = space_fsm.find_membership(session, space_id, creator.id)
        if member is None or space_fsm.effective_status(member) != "active":
            raise_api_error(404, SPACE_NOT_FOUND, "目标家庭空间不存在或无权操作")
    else:
        if space_id is not None:
            raise_api_error(422, VALIDATION_ERROR, "陌生人码不绑定空间")
        if max_uses is not None and max_uses < 1:
            raise_api_error(422, VALIDATION_ERROR, "使用上限必须为正整数")

    now = utcnow()
    effective_max_uses = 1 if kind in ("household", "lineage") else max_uses
    days = DEFAULT_TTL_DAYS if ttl_days is None else ttl_days
    # 唯一约束兜底 + savepoint 重试：写锁前置（调用方 immediate 事务）下查重即无竞态，
    # 此处再用 SAVEPOINT 重试对冲极端碰撞，避免唯一冲突外溢为 500。
    code: InviteCode | None = None
    for _attempt in range(5):
        try:
            with session.begin_nested():
                candidate = InviteCode(
                    code=generate_code(),
                    kind=kind,
                    creator_id=creator.id,
                    space_id=space_id,
                    max_uses=effective_max_uses,
                    used_count=0,
                    expires_at=now + timedelta(days=days),
                    created_at=now,
                )
                session.add(candidate)
                session.flush()
            code = candidate
            break
        except IntegrityError:
            continue
    if code is None:
        raise RuntimeError("invite code generation failed after retries")
    audit.write_audit(
        session,
        action="invite_code_created",
        actor_id=creator.id,
        target_id=code.id,
        ip=ip,
        detail={"kind": kind, "space_id": space_id, "max_uses": effective_max_uses},
    )
    return code


def revoke_code(
    session: Session,
    *,
    code_id: int,
    actor: User,
    ip: str | None,
) -> InviteCode:
    """撤销码（PRD 决策 11）：创建者本人，或家庭/家族码所在空间的 active 空间管理员。

    陌生人码无空间归属 → 仅创建者可撤销。未授权与未知 id 同一 404（防枚举）；
    已撤销 409 终态不可再变。
    """
    code = session.get(InviteCode, code_id)
    if code is None:
        raise_api_error(404, INVITE_CODE_INVALID, "邀请码不存在或无权操作")
    is_creator = code.creator_id == actor.id
    is_space_manager = code.space_id is not None and space_fsm.is_space_manager(
        session, code.space_id, actor.id
    )
    if not (is_creator or is_space_manager):
        raise_api_error(404, INVITE_CODE_INVALID, "邀请码不存在或无权操作")
    if code.revoked_at is not None:
        raise_api_error(409, INVITE_CODE_STATE_CONFLICT, "邀请码已撤销")
    code.revoked_at = utcnow()
    session.flush()
    audit.write_audit(
        session,
        action="invite_code_revoked",
        actor_id=actor.id,
        target_id=code.id,
        ip=ip,
        detail={"kind": code.kind, "by_space_manager": not is_creator},
    )
    return code


def auto_revoke_for_creator_delete(
    session: Session,
    *,
    creator_id: int,
    actor_id: int | None,
    ip: str | None,
) -> list[int]:
    """主体删除前的码处置（delete_profile_core 专用，09-05 P2-2）：自动撤销未撤销码。

    码是临时分享凭据：创建者注销/被删除时自动撤销，避免遗留仍可兑换的凭据，
    无数据损失也不阻断删除流程；audit 事件 ``invite_code_auto_revoked_on_delete``
    记录码 id 清单（码行本身随 0032 SET NULL 保留使用计数/撤销历史）。已核销或
    已撤销的码不动。事务由调用方（删除命令）拥有。
    """
    codes = list(
        session.scalars(
            select(InviteCode).where(
                InviteCode.creator_id == creator_id,
                InviteCode.revoked_at.is_(None),
            )
        )
    )
    if not codes:
        return []
    now = utcnow()
    for code in codes:
        code.revoked_at = now
    session.flush()
    audit.write_audit(
        session,
        action="invite_code_auto_revoked_on_delete",
        actor_id=actor_id,
        target_id=creator_id,
        ip=ip,
        detail={"code_ids": [code.id for code in codes], "count": len(codes)},
    )
    return [code.id for code in codes]


def list_for_creator(session: Session, creator_id: int) -> list[InviteCode]:
    """我的码列表（设置页「邀请码」区块）。"""
    return list(
        session.scalars(
            select(InviteCode)
            .where(InviteCode.creator_id == creator_id)
            .order_by(InviteCode.id.desc())
        )
    )


def space_names(session: Session, codes: list[InviteCode]) -> dict[int, str]:
    """批量取码绑定空间名（列表展示用；无 N+1）。"""
    space_ids = {code.space_id for code in codes if code.space_id is not None}
    if not space_ids:
        return {}
    rows = session.query(FamilySpace.id, FamilySpace.name).filter(FamilySpace.id.in_(space_ids))
    return {space_id: name for space_id, name in rows}
