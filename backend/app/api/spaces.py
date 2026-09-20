"""家庭空间路由（m1c）：CRUD / 成员管理 / 邀请处理。

v2 D2：写路径全部走应用命令层（app.commands.spaces，AC-F7），路由只做
schema 解析 + 认证 + 命令调用 + 序列化；读路径保持原状。
"""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.commands import manager_applications as manager_application_commands
from app.commands import members as member_commands
from app.commands import ownership as ownership_commands
from app.commands import spaces as space_commands
from app.commands.context import ActorContext
from app.errors import SPACE_FORBIDDEN_ACTOR, SPACE_NOT_FOUND, raise_api_error
from app.models.account import Account
from app.models.space import FamilySpace, ManagerTransferConsent, SpaceMember
from app.models.user import User
from app.schemas.space import (
    DuplicatePairOut,
    DuplicatePeopleMergeOut,
    DuplicatePeopleMergeRequest,
    EligibleManagerTarget,
    FamilySpaceOptionsOut,
    ManagerApplicationCreate,
    ManagerApplicationOut,
    ManagerTransferConsentDecision,
    ManagerTransferConsentOut,
    MemberRelationLabelOut,
    PendingInvitationOut,
    PositionsPayload,
    SpaceCreate,
    SpaceInviteCreate,
    SpaceLineageLinkUpdate,
    SpaceManagementBootstrapOut,
    SpaceMemberOut,
    SpaceOut,
    SpaceProfileRefOut,
    SpaceUpdate,
)
from app.schemas.v2_foundation import TransferOut
from app.services import member_labels, space_fsm

router = APIRouter(tags=["spaces"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _require_active_member(session: Session, space_id: int, user_id: int) -> SpaceMember:
    """读路径守卫：非 active 成员与不存在同一 404（防枚举）。"""
    from app.errors import raise_api_error

    member = space_fsm.find_membership(session, space_id, user_id)
    if member is None or space_fsm.effective_status(member) != "active":
        raise_api_error(404, "SPACE_NOT_FOUND", "家庭空间不存在")
    return member


@router.post("/spaces", status_code=201, response_model=SpaceOut)
def create_space(
    payload: SpaceCreate,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceOut:
    """创建空间：owner 即 active 成员（自建即同意）；kind 默认 household。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    space = space_commands.create_space(
        session,
        ctx,
        name=payload.name,
        kind=payload.kind,
        lineage_space_id=payload.lineage_space_id,
    )
    out = SpaceOut.model_validate(space)
    out.member_count = 1
    return out


@router.put("/spaces/{space_id}/lineage-link", response_model=SpaceOut)
def set_space_lineage_link(
    space_id: int,
    payload: SpaceLineageLinkUpdate,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceOut:
    """设置/解除家庭空间的所属家族配对（仅该空间管理员；null 即解除）。

    配对是「家族空间」唯一切换维度的数据基础：壳层选择器据此把家族落到
    家庭卡/家族树，不再依赖 owner 相等的启发式推断。
    """
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    space = space_commands.set_lineage_link(
        session, ctx, space_id, lineage_space_id=payload.lineage_space_id
    )
    return SpaceOut.model_validate(space)


# ---- 空间管理者申请（用户侧；裁决在 /admin/manager-applications）----


@router.post("/spaces/manager-applications", status_code=201, response_model=ManagerApplicationOut)
def submit_manager_application(
    payload: ManagerApplicationCreate,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> ManagerApplicationOut:
    """提交管理者申请：成为指定空间的 space_admin。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    application = manager_application_commands.submit_manager_application(
        session,
        ctx,
        request_kind=payload.request_kind,
        space_id=payload.space_id,
    )
    return manager_application_commands.serialize_application(session, application)


@router.get(
    "/spaces/manager-applications/eligible-targets",
    response_model=list[EligibleManagerTarget],
)
def list_eligible_manager_targets(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[EligibleManagerTarget]:
    """可申请管理员的 lineage 空间；household 不出现在结果里。"""
    actor, _account = identity
    return manager_application_commands.eligible_lineage_targets(session, actor.id)


@router.get("/spaces/manager-applications/mine", response_model=list[ManagerApplicationOut])
def list_my_manager_applications(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[ManagerApplicationOut]:
    """我的管理者申请与状态（pending/approved/rejected + 平台备注）。"""
    actor, _account = identity
    rows = manager_application_commands.applications_of(session, actor.id)
    return [manager_application_commands.serialize_application(session, row) for row in rows]


@router.get(
    "/spaces/manager-transfer-consents/mine",
    response_model=list[ManagerTransferConsentOut],
)
def list_my_transfer_consents(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[ManagerTransferConsentOut]:
    actor, _account = identity
    rows = (
        session.query(ManagerTransferConsent)
        .filter(ManagerTransferConsent.current_manager_user_id == actor.id)
        .order_by(ManagerTransferConsent.id.desc())
        .all()
    )
    return [manager_application_commands.serialize_consent(session, row) for row in rows]


@router.post(
    "/spaces/manager-transfer-consents/{consent_id}/decision",
    response_model=ManagerTransferConsentOut,
)
def decide_my_transfer_consent(
    consent_id: int,
    payload: ManagerTransferConsentDecision,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> ManagerTransferConsentOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    row = manager_application_commands.respond_to_transfer_consent(
        session,
        ctx,
        consent_id,
        decision=payload.decision,
        reason=payload.reason,
    )
    return manager_application_commands.serialize_consent(session, row)


@router.get("/spaces", response_model=list[SpaceOut])
def list_my_spaces(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[SpaceOut]:
    """我 active 成员的空间；附成员数与待处理邀请数。空列表 → 前端引导建默认空间（AD-3）。"""
    actor, _account = identity
    memberships = session.query(SpaceMember).filter(SpaceMember.user_id == actor.id).all()
    active_space_ids = [
        m.space_id for m in memberships if space_fsm.effective_status(m) == "active"
    ]
    spaces = (
        session.query(FamilySpace)
        .filter(FamilySpace.id.in_(active_space_ids))
        .order_by(FamilySpace.created_at.desc())
        .all()
        if active_space_ids
        else []
    )
    memberships_by_space = {m.space_id: m for m in memberships if m.space_id in active_space_ids}
    outs: list[SpaceOut] = []
    for space in spaces:
        all_members = session.query(SpaceMember).filter(SpaceMember.space_id == space.id).all()
        out = SpaceOut.model_validate(space)
        out.member_count = sum(1 for m in all_members if space_fsm.effective_status(m) == "active")
        out.pending_count = sum(1 for m in all_members if m.status == "pending")
        member = memberships_by_space[space.id]
        out.current_role = "space_admin" if member.role in {"owner", "space_admin"} else "member"
        out.my_member_id = member.id
        outs.append(out)
    return outs


@router.get(
    "/spaces/{space_id}/management-bootstrap",
    response_model=SpaceManagementBootstrapOut,
)
def get_space_management_bootstrap(
    space_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceManagementBootstrapOut:
    """管理页首屏授权与投影的一次性读取。

    路由切换不应等待这个请求；本端点是管理页的唯一首屏授权边界，
    后端每次都重新校验当前空间的 active 管理员关系。
    """
    actor, _account = identity
    space = session.get(FamilySpace, space_id)
    if space is None:
        raise_api_error(404, SPACE_NOT_FOUND, "家庭空间不存在")

    manager = space_fsm.find_membership(session, space_id, actor.id)
    if (
        manager is None
        or space_fsm.effective_status(manager) != "active"
        or manager.role not in {"owner", "space_admin"}
    ):
        raise_api_error(403, SPACE_FORBIDDEN_ACTOR, "当前账号没有管理这个家庭空间的权限")

    members = (
        session.query(SpaceMember)
        .filter(SpaceMember.space_id == space_id)
        .order_by(SpaceMember.id)
        .all()
    )
    member_outs = [_member_out_with_name(session, member) for member in members]

    from app.models.space import SpaceProfileRef

    profile_rows = (
        session.query(SpaceProfileRef, User)
        .join(User, User.id == SpaceProfileRef.user_id)
        .filter(SpaceProfileRef.space_id == space_id, SpaceProfileRef.status == "active")
        .order_by(SpaceProfileRef.id)
        .all()
    )
    profile_refs = [
        SpaceProfileRefOut(profile_id=ref.user_id, name=user.name, added_at=ref.created_at)
        for ref, user in profile_rows
    ]

    all_members = members
    space_out = SpaceOut.model_validate(space)
    space_out.member_count = sum(
        1 for member in all_members if space_fsm.effective_status(member) == "active"
    )
    space_out.pending_count = sum(1 for member in all_members if member.status == "pending")
    space_out.current_role = "space_admin"

    transfers = ownership_commands.list_transfers_for_space(session, space_id)
    return SpaceManagementBootstrapOut(
        space=space_out,
        members=member_outs,
        transfers=[TransferOut.model_validate(transfer) for transfer in transfers],
        profile_refs=profile_refs,
    )


@router.patch("/spaces/{space_id}", response_model=SpaceOut)
def rename_space(
    space_id: int,
    payload: SpaceUpdate,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account)
    space = space_commands.rename_space(session, ctx, space_id, name=payload.name)
    return SpaceOut.model_validate(space)


@router.get("/spaces/{space_id}/members", response_model=list[SpaceMemberOut])
def list_space_members(
    space_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[SpaceMemberOut]:
    actor, _account = identity
    _require_active_member(session, space_id, actor.id)
    members = (
        session.query(SpaceMember)
        .filter(SpaceMember.space_id == space_id)
        .order_by(SpaceMember.id)
        .all()
    )
    return [_member_out_with_name(session, m) for m in members]


def _member_out_with_name(session: Session, m: SpaceMember) -> SpaceMemberOut:
    out = SpaceMemberOut.model_validate(m)
    u = session.get(User, m.user_id)
    out.user_name = u.name if u else None
    # 审批链状态来自独立表：治理面板据此区分「待房主批准」与「待受邀人接受」
    approval = space_fsm.approval_for(session, m.id)
    if approval is not None:
        out.origin = approval.origin  # type: ignore[assignment]
        out.owner_approved_at = approval.owner_approved_at
    return out


@router.get("/spaces/{space_id}/profile-refs", response_model=list[SpaceProfileRefOut])
def list_space_profile_refs(
    space_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[SpaceProfileRefOut]:
    """待确档最小节点引用（AC-F2 可观测性）：仅 {profile_id, name, added_at}。

    provisional 人物不是 SpaceMember，只以 space_profile_refs 最小引用存在；
    本端点让空间成员能看到这些“待确档”条目。授权：该空间 active 成员；
    其余与不存在同一 404（防枚举）。字段投影恒为最小集，不随可见性放宽。
    """
    actor, _account = identity
    _require_active_member(session, space_id, actor.id)
    from app.models.space import SpaceProfileRef

    rows = (
        session.query(SpaceProfileRef, User)
        .join(User, User.id == SpaceProfileRef.user_id)
        .filter(SpaceProfileRef.space_id == space_id, SpaceProfileRef.status == "active")
        .order_by(SpaceProfileRef.id)
        .all()
    )
    return [
        SpaceProfileRefOut(profile_id=ref.user_id, name=user.name, added_at=ref.created_at)
        for ref, user in rows
    ]


@router.get("/spaces/invitations", response_model=list[PendingInvitationOut])
def list_my_invitations(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[PendingInvitationOut]:
    """发给我的 / 我发起的 pending 空间邀请（跨全部空间，自足投影）。

    为什么需要这个专用端点：pending 受邀人**不是**该空间 active 成员，读不到该空间
    的通知（``authorized_space_or_404`` 安全 404），而前端通知中心只按当前空间加载——
    于是邀请在任何界面都不可达。本投影自带 ``space_name``，不依赖当前空间上下文。

    授权：只返回 ``user_id == 当前账号`` 的行，不含任何其他成员行。
    """
    actor, _account = identity
    rows = (
        session.query(SpaceMember)
        .filter(SpaceMember.user_id == actor.id)
        .order_by(SpaceMember.updated_at.desc())
        .all()
    )
    return [
        out
        for out in (
            _pending_invitation_out(session, actor, member)
            for member in rows
            if space_fsm.effective_status(member) == "pending"
        )
        if out is not None
    ]


def _pending_invitation_out(
    session: Session, actor: User, member: SpaceMember
) -> PendingInvitationOut | None:
    """单条 pending 行的跨空间投影（空间被删除等异常行返回 None）。"""
    space = session.get(FamilySpace, member.space_id)
    if space is None:  # pragma: no cover - FK 保证存在
        return None
    approval = space_fsm.approval_for(session, member.id)
    direction: Literal["incoming", "outgoing"]
    stage: Literal["awaiting_owner", "awaiting_me"]
    if approval is not None:
        # 加入链行：来源决定方向，房主批准时刻决定阶段
        direction = "incoming" if approval.origin == "invite" else "outgoing"
        approved = approval.owner_approved_at is not None
        stage = "awaiting_me" if (approval.origin == "invite" and approved) else "awaiting_owner"
    else:
        # 历史行（无审批行）：沿用旧语义——本人申请由房主批准，他人邀请由本人接受
        self_requested = member.added_by == member.user_id
        direction = "outgoing" if self_requested else "incoming"
        stage = "awaiting_owner" if self_requested else "awaiting_me"

    counterpart_id = _invitation_counterpart(session, actor.id, member, direction)
    counterpart_name: str | None = None
    if counterpart_id is not None:
        counterpart = session.get(User, counterpart_id)
        if counterpart is not None:
            from app.services import visibility

            decision = visibility.evaluate(
                session,
                actor,
                counterpart,
                space_context=space.id,
                purpose=visibility.PURPOSE_PROFILE,
            )
            if decision.visible:
                counterpart_name = counterpart.name
    label = (
        member_labels.pair_for(
            session, space_id=space.id, user_a_id=actor.id, user_b_id=counterpart_id
        )
        if counterpart_id is not None
        else None
    )
    return PendingInvitationOut(
        id=member.id,
        space_id=space.id,
        space_name=space.name,
        space_kind="lineage" if space.kind == "lineage" else "household",
        direction=direction,
        stage=stage,
        counterpart_user_id=counterpart_id,
        counterpart_name=counterpart_name,
        relation_label=label.label if label is not None else None,
        owner_approved_at=approval.owner_approved_at if approval is not None else None,
        updated_at=member.updated_at,
    )


def _invitation_counterpart(
    session: Session, actor_id: int, member: SpaceMember, direction: str
) -> int | None:
    """邀请的另一端：邀请人（incoming）或我申请的落点（outgoing）。

    incoming 由 ``added_by`` 直接给出；outgoing 的落点不落在成员行上，只能从加入时
    写入的关系词标注反解——标注行的一端必然是我。存在多行时无法唯一确定，返回 None
    （宁可缺字段也不猜一对标注）。
    """
    if direction == "incoming":
        return member.added_by if member.added_by != actor_id else None
    rows = member_labels.labels_for(session, space_id=member.space_id)
    others = {
        row["to_user_id"] if row["from_user_id"] == actor_id else row["from_user_id"]
        for row in rows
        if actor_id in (row["from_user_id"], row["to_user_id"])
    }
    if len(others) != 1:
        return None
    return int(next(iter(others)))


@router.post("/spaces/{space_id}/members", status_code=201, response_model=SpaceMemberOut)
def invite_to_space(
    space_id: int,
    payload: SpaceInviteCreate,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceMemberOut:
    """邀请已有账号进空间 → pending（幂等）；managed 直连例外走建档向导组合，不经此端点。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    member, _created = space_commands.invite_member(
        session, ctx, space_id, user_id=payload.user_id, relation_label=payload.relation_label
    )
    session.refresh(member)
    return SpaceMemberOut.model_validate(member)


@router.post("/spaces/join-by-user", status_code=201, response_model=SpaceMemberOut)
def join_by_user(
    payload: JoinByUserPayload,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceMemberOut:
    """家族视图摘要卡「申请进入 TA 的家庭空间」（join_request 语义，命令化）。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    member = space_commands.request_join_by_user(
        session,
        ctx,
        lineage_space_id=payload.lineage_space_id,
        target_user_id=payload.target_user_id,
        space_id=payload.space_id,
        relation_label=payload.relation_label,
    )
    session.refresh(member)
    return _member_out_with_name(session, member)


class JoinByUserPayload(BaseModel):
    """申请加入对方的家庭空间（限定在当前家族空间范围内）。"""

    lineage_space_id: int = Field(gt=0)
    target_user_id: int = Field(gt=0)
    space_id: int | None = Field(default=None, gt=0)
    # 与对方的关系词（自由文本，必填，≤64）
    relation_label: str = Field(min_length=1, max_length=64)


@router.get("/spaces/family-space-options", response_model=FamilySpaceOptionsOut)
def family_space_options(
    lineage_space_id: int = Query(gt=0),
    target_user_id: int = Query(gt=0),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> FamilySpaceOptionsOut:
    """当前家族空间下的双向加入选择：邀请（我的家庭空间）与申请（对方的家庭空间）。

    双方必须同属该家族空间；不同族时两个方向都为空（走邀请码途径）。
    """
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account)
    options = space_commands.family_space_options(
        session, ctx, lineage_space_id=lineage_space_id, target_user_id=target_user_id
    )
    return FamilySpaceOptionsOut(**options)


class FamilyInvitationPayload(BaseModel):
    lineage_space_id: int = Field(gt=0)
    space_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    # 与对方的关系词（自由文本，必填，≤64）
    relation_label: str = Field(min_length=1, max_length=64)


@router.post("/spaces/family-invitations", status_code=201, response_model=SpaceMemberOut)
def invite_into_family_household(
    payload: FamilyInvitationPayload,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceMemberOut:
    """在当前家族空间范围内邀请对方加入我的家庭空间（只产生 pending）。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    member, _created = space_commands.invite_into_family_household(
        session,
        ctx,
        lineage_space_id=payload.lineage_space_id,
        space_id=payload.space_id,
        user_id=payload.user_id,
        relation_label=payload.relation_label,
    )
    session.refresh(member)
    return _member_out_with_name(session, member)


class LineageAccessRequestPayload(BaseModel):
    """家庭空间成员申请读取该家庭所属家族空间（由该家族空间管理员审批）。"""

    household_space_id: int = Field(gt=0)


@router.post("/spaces/lineage-access-requests", status_code=201, response_model=SpaceMemberOut)
def request_lineage_access(
    payload: LineageAccessRequestPayload,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceMemberOut:
    """申请读取家庭空间所属的家族空间：独立的 pending，由该家族空间管理员审批。

    家庭空间成员资格不自动获得家族树读取权；本端点只登记申请。
    """
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    member, _event_id = space_commands.request_lineage_access(
        session,
        ctx,
        household_space_id=payload.household_space_id,
    )
    session.refresh(member)
    return _member_out_with_name(session, member)


@router.post("/space-memberships/{member_id}/accept", response_model=SpaceMemberOut)
def accept_membership(
    member_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceMemberOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    member = space_commands.respond_invitation(session, ctx, member_id, accept=True)
    session.refresh(member)
    return _member_out_with_name(session, member)


@router.post("/space-memberships/{member_id}/approve", response_model=SpaceMemberOut)
def approve_membership(
    member_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceMemberOut:
    """房主批准一条待处理加入（09-20 审批链）；申请人/发起人不得自批。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    member = space_commands.approve_membership(session, ctx, member_id)
    session.refresh(member)
    return _member_out_with_name(session, member)


class MemberRelationLabelPayload(BaseModel):
    """我与某成员之间的关系词（自由文本；空串 = 清除标注）。"""

    other_user_id: int = Field(gt=0)
    label: str = Field(max_length=64)


@router.put(
    "/spaces/{space_id}/member-relation-label", response_model=MemberRelationLabelOut | None
)
def set_member_relation_label(
    space_id: int,
    payload: MemberRelationLabelPayload,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemberRelationLabelOut | None:
    """设置/清除我与某成员之间的关系词（仅两端本人可改，即时生效）。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    row = space_commands.set_member_relation_label(
        session,
        ctx,
        space_id=space_id,
        other_user_id=payload.other_user_id,
        label=payload.label,
    )
    return MemberRelationLabelOut(**row) if row is not None else None


@router.post("/space-memberships/{member_id}/reject", response_model=SpaceMemberOut)
def reject_membership(
    member_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceMemberOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account)
    member = space_commands.respond_invitation(session, ctx, member_id, accept=False)
    session.refresh(member)
    return _member_out_with_name(session, member)


@router.delete("/space-memberships/{member_id}", status_code=204)
def remove_or_withdraw_membership(
    member_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> Response:
    """D8 断连轨：owner 移除活跃成员 或 本人退出；pending 时发起方可撤回、本人可拒。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    space_commands.leave_or_remove_membership(session, ctx, member_id)
    return Response(status_code=204)


# ---- graph 空间过滤 ----


@router.get("/spaces/{space_id}/positions")
def get_positions(
    space_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, float | int]]:
    """画布位置记忆：仅 active 成员可读。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account)
    return space_commands.positions_of(session, ctx, space_id)


@router.put("/spaces/{space_id}/positions")
def put_positions(
    space_id: int,
    payload: PositionsPayload,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, float | int]]:
    """批量 upsert 位置（命令：commands.spaces.save_positions）。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account)
    return space_commands.save_positions(session, ctx, space_id, payload.items)


# ---- 同一空间重复人物处置（任务 09-01-person-identity-dedupe）----


def _duplicate_pairs_for_actor(
    session: Session, space: FamilySpace, actor: User
) -> list[DuplicatePairOut]:
    """空间可见集合内的疑似重复对，只返回操作者对双方都有 custody 编辑权的对。

    判定口径唯一真源 services/person_identity（find_duplicate_pairs）；合并授权是
    双向 custody（与 merge 命令同一标准），不构成可处置对的不展示（最小披露）。
    """
    from app.services import person_identity
    from app.services.custody import resolve_relation
    from app.services.steward import _space_visible_user_ids

    visible_ids = _space_visible_user_ids(session, space)
    if len(visible_ids) < 2:
        return []
    users = session.query(User).filter(User.id.in_(visible_ids), User.deleted_at.is_(None)).all()
    outs: list[DuplicatePairOut] = []
    for pair in person_identity.find_duplicate_pairs(users):
        if pair.strength not in (
            person_identity.STRENGTH_STRONG,
            person_identity.STRENGTH_WEAK,
        ):
            continue
        left, right = session.get(User, pair.user_ids[0]), session.get(User, pair.user_ids[1])
        if left is None or right is None:
            continue
        if not (resolve_relation(actor, left).edit and resolve_relation(actor, right).edit):
            continue
        outs.append(
            DuplicatePairOut(
                user_ids=list(pair.user_ids),
                strength=cast(Literal["strong", "weak"], pair.strength),
                names=[left.name, right.name],
                birth_known_flags=[
                    person_identity.canonical_birth(left.birth) is not None,
                    person_identity.canonical_birth(right.birth) is not None,
                ],
            )
        )
    return outs


@router.get("/spaces/{space_id}/duplicate-people", response_model=list[DuplicatePairOut])
def list_duplicate_people(
    space_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[DuplicatePairOut]:
    """当前空间内按 person_identity 复核过的疑似重复对（处置入口的发现面）。

    授权：空间 active 成员，且只返回操作者对双方都有 custody 编辑权的对；
    非成员与不存在统一 404（防枚举）。字段白名单：{user_ids, strength, names,
    birth_known_flags}，不含家庭档案敏感字段；Steward 事件仍负责异步告警，
    本端点不重复。
    """
    actor, _account = identity
    _require_active_member(session, space_id, actor.id)
    space = session.get(FamilySpace, space_id)
    assert space is not None  # 成员行 FK 保证空间存在
    return _duplicate_pairs_for_actor(session, space, actor)


@router.post("/spaces/{space_id}/duplicate-people/merge", response_model=DuplicatePeopleMergeOut)
def merge_duplicate_people(
    space_id: int,
    payload: DuplicatePeopleMergeRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> DuplicatePeopleMergeOut:
    """合并确认同一人的重复档案（显式两步确认；命令：commands.members.merge_duplicate_profile）。

    授权：操作者对两个档案 custody 编辑权双向通过 + 空间 active 成员；空间管理员
    不因此获得额外合并权。状态门/复核门/幂等语义见命令层，错误走统一 envelope。
    """
    actor, account = identity
    _require_active_member(session, space_id, actor.id)
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    result = member_commands.merge_duplicate_profile(
        session,
        ctx,
        space_id=space_id,
        survivor_id=payload.survivor_user_id,
        retired_id=payload.retired_user_id,
        confirm_same_person=payload.confirm_same_person,
    )
    return DuplicatePeopleMergeOut(
        survivor_user_id=result.survivor_id,
        retired_user_id=result.retired_id,
        moved=result.moved,
        already_merged=result.already_merged,
    )
