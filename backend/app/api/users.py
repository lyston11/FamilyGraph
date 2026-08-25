"""用户路由：/me（当前账号）与 /users（成员档案，m1a）。

PUT /me/pin 在 pin_must_change 白名单内（deps.PIN_GATE_WHITELIST），
改毕 token_version+1 使旧 access/refresh 全部即刻失效；
pin_must_change 由 true 翻转时同事务置 claim_status='claimed'
——managed→claimed 的唯一转换点（architecture.md §1 [AD-1]）。

/users 成员端点权限判定单点在 services/custody.py（m1a design 权限矩阵）：
view none 一律 404 防枚举；删除为单事务级联 + audit 快照（§7 [AD-5]）。
"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.errors import CONFIRM_NAME_MISMATCH, UNIFIED_CREDENTIAL_MESSAGE, raise_api_error
from app.models import Account, User
from app.schemas.auth import ChangeNameRequest, ChangePinRequest, UserOut, public_user_payload
from app.schemas.user import (
    DisclosurePayload,
    MemberCreateRequest,
    MemberCreateResponse,
    MemberOut,
    MemberUpdateRequest,
    member_payload,
)
from app.services import audit, custody
from app.services import refresh_session as refresh_session_service
from app.utils import security, timeutil

router = APIRouter(tags=["me"])
members_router = APIRouter(prefix="/users", tags=["members"])


# ---- 当前账号（m0b 基线）----


@router.get("/me", response_model=UserOut)
def get_me(
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> UserOut:
    user, _account = identity
    return UserOut(**public_user_payload(user))


@router.put("/me/name", response_model=UserOut)
def change_name(
    payload: ChangeNameRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> UserOut:
    """改名（锁定决策 A1：随时可改；Q2 待定默认不限频）。不改名不失效会话。"""
    user, _account = identity
    old_name = user.name
    user.name = payload.name.strip()
    audit.write_audit(
        session,
        action="name_changed",
        actor_id=user.id,
        target_id=user.id,
        ip=request.client.host if request.client else None,
        detail={"old_name": old_name},
    )
    session.commit()
    return UserOut(**public_user_payload(user))


@router.put("/me/pin", response_model=UserOut)
def change_pin(
    payload: ChangePinRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> UserOut:
    """改 PIN：验旧 PIN → 新哈希 → pin_must_change=false（首登认领）→ 版本+1。"""
    user, account = identity
    was_forced = bool(account.pin_must_change)
    if not security.verify_pin(payload.old_pin, account.pin_hash):
        # 旧 PIN 错误同样走防枚举统一文案
        raise_api_error(401, "AUTH_INVALID_CREDENTIALS", UNIFIED_CREDENTIAL_MESSAGE)

    account.pin_hash = security.hash_pin(payload.new_pin)
    account.pin_must_change = False
    account.token_version += 1
    account.failed_attempts = 0
    account.locked_until = None
    if was_forced and user.claim_status != "claimed":
        # 首登强制改 PIN 完成 = 认领完成（AD-1 唯一 managed→claimed 转换点）
        user.claim_status = "claimed"
    # 全部旧 refresh 会话一并作废（PRD：refresh 无法再换新）
    refresh_session_service.revoke_all_active(session, user.id, ip=None, reason="pin_change")
    audit.write_audit(
        session,
        action="pin_changed",
        actor_id=user.id,
        target_id=user.id,
        ip=request.client.host if request.client else None,
        detail={"claim_completed": was_forced},
    )
    session.commit()
    return UserOut(**public_user_payload(user))


# ---- 成员档案（m1a）----

_MEMBER_NOT_FOUND = ("USER_NOT_FOUND", "资源不存在")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _member_out(target: User, actor: User) -> MemberOut:
    """可见性已在路由入口判定为 full，此处仅做投影。"""
    return MemberOut(**member_payload(target, actor))


@members_router.get("", response_model=list[MemberOut])
def list_related_members(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[MemberOut]:
    """与我相关的档案列表：自己 + 我创建的；admin 可见全部（m1a design）。

    M1 无关系边/空间成员资格，范围即此；m1d 后该列表被画布取代。
    """
    actor, _account = identity
    query = session.query(User)
    if actor.is_admin:
        members = query.order_by(User.id).all()
    else:
        members = (
            query.filter((User.id == actor.id) | (User.created_by == actor.id))
            .order_by(User.id)
            .all()
        )
    return [_member_out(member, actor) for member in members]


@members_router.post("", status_code=201, response_model=MemberCreateResponse)
def create_member(
    payload: MemberCreateRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemberCreateResponse:
    """建档：创建 user+account 并配发一次性 PIN（A3/AD-1）。

    PIN 明文仅出现在本次响应；日志/审计只记事实不记值（logging-guidelines 红线）。
    重名允许（锁定决策 A2），ID 区分。
    """
    actor, _account = identity
    name = payload.name.strip()
    pin = security.generate_pin()
    # exclude_none：JSON 列不落显式 null 键（original_text 空即省略）
    birth = payload.birth.model_dump(exclude_none=True) if payload.birth else None
    death = payload.death.model_dump(exclude_none=True) if payload.death else None

    member = User(
        name=name,
        is_admin=False,
        created_at=timeutil.utcnow(),
        gender=payload.gender,
        birth=birth,
        death=death,
        bio=payload.bio,
        privacy_mode=payload.privacy_mode,
        created_by=actor.id,
        claim_status="managed",
    )
    member.account = Account(
        pin_hash=security.hash_pin(pin),
        pin_must_change=True,
        token_version=0,
        failed_attempts=0,
        locked_until=None,
    )
    session.add(member)
    session.flush()  # 取得 id 供审计与响应投影
    audit.write_audit(
        session,
        action="profile_created",
        actor_id=actor.id,
        target_id=member.id,
        ip=_client_ip(request),
        detail={"name": name, "privacy_mode": payload.privacy_mode},
    )
    session.commit()
    return MemberCreateResponse(user=_member_out(member, actor), pin=pin)


@members_router.get("/{user_id}", response_model=MemberOut)
def get_member(
    user_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemberOut:
    """按 resolve_relation.view 返回；none → 404（防枚举）。"""
    actor, _account = identity
    target = session.query(User).filter(User.id == user_id).first()
    target = custody.require_visible_target(target)
    access = custody.resolve_relation(actor, target)
    if access.view == custody.VIEW_NONE:
        raise_api_error(404, *_MEMBER_NOT_FOUND)
    return _member_out(target, actor)


@members_router.patch("/{user_id}", response_model=MemberOut)
def update_member(
    user_id: int,
    payload: MemberUpdateRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemberOut:
    """档案字段编辑（assert_can_edit 统一入口；admin 编辑同样强制审计留痕）。"""
    actor, _account = identity
    target = session.query(User).filter(User.id == user_id).first()
    target = custody.require_visible_target(target)
    custody.assert_can_edit(actor, target)

    changes = payload.model_dump(exclude_unset=True, exclude_none=False)
    fields = sorted(changes)
    if "name" in changes and changes["name"] is not None:
        target.name = str(changes["name"]).strip()
    for field in ("gender", "birth", "death", "bio"):
        if field in changes:
            setattr(target, field, changes[field])
    audit.write_audit(
        session,
        action="profile_updated",
        actor_id=actor.id,
        target_id=target.id,
        ip=_client_ip(request),
        detail={"fields": fields, "admin_action": actor.is_admin and actor.id != target.id},
    )
    session.commit()
    return _member_out(target, actor)


@members_router.put("/{user_id}/disclosure", response_model=MemberOut)
def update_disclosure(
    user_id: int,
    payload: DisclosurePayload,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemberOut:
    """AD-9 披露开关整体替换；修改权 = 该档案编辑权主体（键集合由 schema 恰好校验）。"""
    actor, _account = identity
    target = session.query(User).filter(User.id == user_id).first()
    target = custody.require_visible_target(target)
    custody.assert_can_edit(actor, target)

    target.clan_disclosure_json = payload.model_dump()
    audit.write_audit(
        session,
        action="disclosure_updated",
        actor_id=actor.id,
        target_id=target.id,
        ip=_client_ip(request),
        detail={"disclosure": payload.model_dump()},
    )
    session.commit()
    return _member_out(target, actor)


@members_router.delete("/{user_id}", status_code=204)
def delete_member(
    user_id: int,
    request: Request,
    confirm_name: str = "",
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> Response:
    """删除档案（architecture.md §7 [AD-5]）：本人 ∨ 代管创建者 ∨ admin。

    二次确认在前端输入名字，后端以 confirm_name == target.name 作第二道校验；
    单事务级联（accounts/refresh_sessions 等随 FK CASCADE 自然生效，目标会话
    随账号行删除自然失效）；audit_log 保留并以快照文本记录被删档案；
    物理文件异步清理随 m3a 上传功能一并落地（当前 avatar_path 尚无文件产物）。
    """
    actor, _account = identity
    target = session.query(User).filter(User.id == user_id).first()
    target = custody.require_visible_target(target)
    custody.assert_can_delete(actor, target)

    if confirm_name.strip() != target.name.strip():
        raise_api_error(409, CONFIRM_NAME_MISMATCH, "输入的名字与档案名字不一致")

    snapshot = {
        "id": target.id,
        "name": target.name,
        "gender": target.gender,
        "birth": target.birth,
        "death": target.death,
        "bio": target.bio,
        "privacy_mode": target.privacy_mode,
        "claim_status": target.claim_status,
        "created_by": target.created_by,
    }
    session.delete(target)  # flush 时级联删除账号等子行，audit 行保留（无 FK）
    session.flush()
    audit.write_audit(
        session,
        action="profile_deleted",
        actor_id=actor.id,
        target_id=user_id,
        ip=_client_ip(request),
        detail={"snapshot": snapshot, "admin_action": actor.is_admin and actor.id != user_id},
    )
    session.commit()
    return Response(status_code=204)
