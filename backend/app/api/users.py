"""用户路由：/me（当前账号）与 /users（成员档案）。

写路径全部走应用命令层（app.commands，AC-F7）：路由只做 schema 解析 +
认证上下文构造 + 命令调用 + 序列化，不再直接 commit。PUT /me/pin 在
pin_must_change 白名单内（deps.PIN_GATE_WHITELIST），改毕 token_version+1
使旧 access/refresh 全部即刻失效；首登强制改 PIN 完成时经 identity_fsm.claim_account
完成 managed→claimed 唯一转换点（v2 §0.3，Account 状态机权威在 accounts.status）；
「这是我」合并确认见 POST /me/identity/confirm（commands.identity）。

/users 成员端点可见性单点在 services/visibility.py（四级合同）；
edit/delete 判定在 services/custody.py 与命令层。platform_operator 无任何
家庭数据读取/编辑权（visibility 不消费平台角色）。删除空间所有者被义务预检
拦截并引导移交（§0.5，RESTRICT 兑底）。
"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.commands import members as member_commands
from app.commands.context import ActorContext
from app.errors import (
    raise_api_error,
)
from app.models import Account, User
from app.models.user import BASIC_DISCLOSURE_KEYS
from app.schemas.auth import ChangeNameRequest, ChangePinRequest, UserOut, public_user_payload
from app.schemas.user import (
    DisclosureMatrixOut,
    DisclosurePayload,
    MemberCreateRequest,
    MemberCreateResponse,
    MemberOut,
    MemberPermissions,
    MemberUpdateRequest,
    member_payload,
)
from app.services import custody, platform_roles
from app.services import disclosure as disclosure_service

router = APIRouter(tags=["me"])
members_router = APIRouter(prefix="/users", tags=["members"])


# ---- 当前账号（m0b 基线）----


@router.get("/me", response_model=UserOut)
def get_me(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> UserOut:
    user, _account = identity
    return UserOut(**public_user_payload(session, user))


@router.put("/me/name", response_model=UserOut)
def change_name(
    payload: ChangeNameRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> UserOut:
    """改名（命令：commands.members.rename_own_profile；A1：随时可改）。"""
    user, account = identity
    ctx = ActorContext.from_identity(user, account, ip=_client_ip(request))
    user = member_commands.rename_own_profile(session, ctx, name=payload.name)
    return UserOut(**public_user_payload(session, user))


@router.put("/me/pin", response_model=UserOut)
def change_pin(
    payload: ChangePinRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> UserOut:
    """改 PIN（命令：commands.members.change_own_pin）：验旧 PIN → 新哈希 →
    pin_must_change=false（首登认领）→ 版本+1。"""
    user, account = identity
    ctx = ActorContext.from_identity(user, account, ip=_client_ip(request))
    user, _was_forced = member_commands.change_own_pin(
        session, ctx, old_pin=payload.old_pin, new_pin=payload.new_pin
    )
    return UserOut(**public_user_payload(session, user))


# ---- 成员档案 ----

_MEMBER_NOT_FOUND = ("USER_NOT_FOUND", "资源不存在")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _member_out(session: Session, target: User, actor: User) -> MemberOut:
    """可见性已在路由入口判定，此处仅做投影。"""
    return MemberOut(**member_payload(session, target, actor))


@members_router.get("", response_model=list[MemberOut])
def list_related_members(
    q: str = "",
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[MemberOut]:
    """与我相关的档案列表：自己 + 我创建的（v2：operator 无全量家庭数据权）。

    q 为名字前缀过滤（「添加关系」搜人用），可见范围不变。
    """
    actor, _account = identity
    query = session.query(User)
    query = query.filter((User.id == actor.id) | (User.created_by == actor.id))
    if q:
        query = query.filter(User.name.like(f"{q}%"))
    members = query.order_by(User.id).all()
    return [_member_out(session, member, actor) for member in members]


@members_router.post("", status_code=201, response_model=MemberCreateResponse)
def create_member(
    payload: MemberCreateRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemberCreateResponse:
    """建档（命令：commands.members.create_member）：创建 user+account 并配发
    一次性 PIN（一次性展示，日志/审计不落值）。v2 F-3：创建他人为 provisional 档案；
    选择空间时仅建 space_profile_refs 最小节点引用 —— provisional 人物不是
    SpaceMember、不进推荐资格查询。重名允许（A2），ID 区分。

    注（F-1「名字和关系必填」）：MemberCreateRequest 刻意不含关系字段 —— 必填关系
    语义由建档向导提交后的 POST /connection-requests 承担（AD-4 合并请求语义：
    关系与可选空间邀请一次发出，对方确认后同时生效）。前端 MemberCreateWizard
    以 canNextFromInfo 强制名字与关系双必填后才可进入下一步。
    """
    actor, _account = identity
    ctx = ActorContext.from_identity(actor, _account, ip=_client_ip(request))
    birth = payload.birth.model_dump(exclude_none=True) if payload.birth else None
    death = payload.death.model_dump(exclude_none=True) if payload.death else None
    member, pin = member_commands.create_member(
        session,
        ctx,
        name=payload.name,
        gender=payload.gender,
        birth=birth,
        death=death,
        bio=payload.bio,
        privacy_mode=payload.privacy_mode,
        space_membership_space_id=(
            payload.space_membership.space_id if payload.space_membership else None
        ),
    )
    return MemberCreateResponse(user=_member_out(session, member, actor), pin=pin)


@members_router.get("/{user_id}", response_model=MemberOut)
def get_member(
    user_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemberOut:
    """可见性基线（v2 四级）：household_detail=完整 / lineage_summary=基线+披露 /
    none→404（防枚举）。"""
    from app.services import visibility

    actor, _account = identity
    target = session.query(User).filter(User.id == user_id).first()
    if target is None:
        raise_api_error(404, *_MEMBER_NOT_FOUND)
    payload = visibility.user_payload_for(session, actor, target)
    if payload is None:
        raise_api_error(404, *_MEMBER_NOT_FOUND)
    access = custody.resolve_relation(actor, target)

    def _keep(value: object) -> object:
        """MASKED 哨兵替换为遮罩结构；其余原样（结构化日期本身也是 dict）。"""
        if isinstance(value, dict) and value.get("__masked__"):
            return dict(visibility.MASKED)
        return value

    out = MemberOut(
        id=payload["id"],
        name=payload["name"],
        is_admin=platform_roles.is_platform_operator(session, target.account),
        gender=_keep(payload["gender"]),
        birth=_keep(payload["birth"]),
        death=_keep(payload["death"]),
        bio=_keep(payload["bio"]),
        avatar_path=_keep(payload["avatar_path"]),
        privacy_mode=payload.get("privacy_mode") or "handover",
        claim_status=payload.get("claim_status") or "managed",
        created_by=payload.get("created_by"),
        created_at=target.created_at,
        clan_disclosure=_safe_disclosure(session, target),
        permissions=MemberPermissions(edit=bool(access.edit), delete=bool(access.delete)),
    )
    return out


def _safe_disclosure(session: Session, target: User) -> dict[str, bool]:
    """基础五类披露开关视图（权威源：disclosure_preferences 全局行）。"""
    return disclosure_service.basic_disclosure_flags(session, target)


@members_router.patch("/{user_id}", response_model=MemberOut)
def update_member(
    user_id: int,
    payload: MemberUpdateRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemberOut:
    """档案字段编辑（命令：commands.members.update_member_profile；admin 编辑同样
    强制审计留痕）。"""
    actor, _account = identity
    ctx = ActorContext.from_identity(actor, _account, ip=_client_ip(request))
    changes = payload.model_dump(exclude_unset=True, exclude_none=False)
    target = member_commands.update_member_profile(session, ctx, user_id, changes)
    return _member_out(session, target, actor)


@members_router.get("/{user_id}/disclosure", response_model=DisclosureMatrixOut)
def get_disclosure_matrix(
    user_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> DisclosureMatrixOut:
    """披露偏好合并矩阵（全局 + 逐空间覆盖）；读者域与 PUT 一致（编辑权主体）。"""
    actor, _account = identity
    target = session.query(User).filter(User.id == user_id).first()
    if target is None:
        raise_api_error(404, *_MEMBER_NOT_FOUND)
    if not custody.resolve_relation(actor, target).edit:
        # 披露偏好属本人/代管人管理数据：无编辑权与不存在同一 404（防枚举）
        raise_api_error(404, *_MEMBER_NOT_FOUND)
    matrix = disclosure_service.disclosure_matrix(session, target)
    return DisclosureMatrixOut.model_validate(matrix)


@members_router.put("/{user_id}/disclosure", response_model=MemberOut)
def update_disclosure(
    user_id: int,
    payload: DisclosurePayload,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemberOut:
    """基础五类披露开关整体替换（命令：commands.members.update_disclosure）；
    全局修改权 = 该档案编辑权主体；携带 space_id 时为逐空间覆盖且仅本人可改。"""
    actor, _account = identity
    ctx = ActorContext.from_identity(actor, _account, ip=_client_ip(request))
    flags = payload.model_dump(include=set(BASIC_DISCLOSURE_KEYS))
    target = member_commands.update_disclosure(
        session, ctx, user_id, flags, space_id=payload.space_id
    )
    return _member_out(session, target, actor)


@members_router.delete("/{user_id}", status_code=204)
def delete_member(
    user_id: int,
    request: Request,
    confirm_name: str = "",
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> Response:
    """删除档案（命令：commands.members.delete_member）：本人 ∨ 代管创建者。

    二次确认在前端输入名字，后端以 confirm_name == target.name 作第二道校验；
    空间所有者被义务预检拦截为 409 OWNER_TRANSFER_REQUIRED 引导移交；audit_log
    保留并以快照文本记录被删档案；tombstone 失效事件随同一事务发布；物理文件
    在提交后清理。"""
    actor, _account = identity
    ctx = ActorContext.from_identity(actor, _account, ip=_client_ip(request))
    result = member_commands.delete_member(session, ctx, user_id, confirm_name=confirm_name)

    # 事务已提交：物理文件清理（外部 I/O 不进事务）
    from app.services.attachments import delete_file_quiet

    for path_name in result.purge_image_paths:
        if not delete_file_quiet(path_name):
            import logging

            logging.getLogger(__name__).warning("orphan file cleanup deferred: %s", path_name)
    return Response(status_code=204)
