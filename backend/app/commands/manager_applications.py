"""空间管理者申请命令（平台运营者审批制，任务 08-30-space-manager-approval）。

合同（PRD 用户确认 2026-08-30，记录于 spec/architecture.md §0.7）：
- 只有成为已有空间的管理者需要审批：active member 申请 member → space_admin；
- 拉人、邀请不需要审批，active member（除 guest）可邀请，受邀人仍需接受；
- 新空间开辟和 Owner Invitation 保持既有语义；现有空间 owner 仅经 ownership_transfers FSM 变更。
- 每条命令单短事务：授权 → 校验 → 写入 → domain_events → audit。
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import (
    AUTH_INVALID_CREDENTIALS,
    SPACE_MANAGER_APPLICATION_DECIDED,
    SPACE_MANAGER_APPLICATION_EXISTS,
    SPACE_MANAGER_APPLICATION_NOT_FOUND,
    SPACE_MANAGER_APPLICATION_NOTE_REQUIRED,
    SPACE_NOT_FOUND,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models.account import Account
from app.models.space import (
    MANAGER_REQUEST_KINDS,
    FamilySpace,
    SpaceManagerApplication,
    SpaceMember,
)
from app.models.user import User
from app.schemas.space import ManagerApplicationOut
from app.services import audit, platform_roles, space_fsm
from app.services.domain_events import emit
from app.utils.timeutil import utcnow


def _application_or_404(session: Session, application_id: int) -> SpaceManagerApplication:
    application = session.get(SpaceManagerApplication, application_id)
    if application is None:
        raise_api_error(404, SPACE_MANAGER_APPLICATION_NOT_FOUND, "申请不存在")
    return application


def _reject_guest_only(session: Session, user_id: int) -> None:
    """guest 不能提交管理者申请（guest 是最小可见角色，无治理升级通道）。

    无任何 active 成员资格的用户不属于 guest；管理员申请必须指定其已有的 active member 资格。
    仅当其全部 active 成员资格均为 guest 时拒绝。
    """
    memberships = session.query(SpaceMember).filter(SpaceMember.user_id == user_id).all()
    active = [m for m in memberships if space_fsm.effective_status(m) == "active"]
    if active and all(m.role == "guest" for m in active):
        raise_api_error(403, VALIDATION_ERROR, "访客身份不能提交管理者申请")


def submit_manager_application(
    session: Session,
    ctx: ActorContext,
    *,
    request_kind: str = "space_admin",
    space_id: int,
) -> SpaceManagerApplication:
    """提交成为指定空间管理员的申请。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        if request_kind not in MANAGER_REQUEST_KINDS:
            raise_api_error(422, VALIDATION_ERROR, "未知的申请类型")
        if actor.profile_status != "identity_confirmed":
            raise_api_error(403, VALIDATION_ERROR, "请先完成身份确认后再提交申请")
        _reject_guest_only(session, actor.id)

        space = session.get(FamilySpace, space_id)
        if space is None:
            raise_api_error(404, SPACE_NOT_FOUND, "家庭空间不存在")
        member = space_fsm.find_membership(session, space.id, actor.id)
        if member is None or space_fsm.effective_status(member) != "active":
            raise_api_error(404, SPACE_NOT_FOUND, "家庭空间不存在")
        if member.role != "member":
            raise_api_error(409, VALIDATION_ERROR, "你已是该空间的所有者或管理员")

        duplicate = session.scalar(
            select(SpaceManagerApplication).where(
                SpaceManagerApplication.applicant_user_id == actor.id,
                SpaceManagerApplication.request_kind == request_kind,
                SpaceManagerApplication.status == "pending",
                SpaceManagerApplication.space_id == space_id,
            )
        )
        if duplicate is not None:
            raise_api_error(
                409,
                SPACE_MANAGER_APPLICATION_EXISTS,
                "你已有一条同目标的待审批申请，请等待平台运营者裁决",
            )

        application = SpaceManagerApplication(
            applicant_user_id=actor.id,
            space_id=space_id,
            request_kind=request_kind,
            status="pending",
            created_at=utcnow(),
        )
        try:
            # Keep the insert inside a savepoint so a uniqueness race does not
            # poison the enclosing command transaction.
            with session.begin_nested():
                session.add(application)
                session.flush()
        except IntegrityError:
            raise_api_error(
                409,
                SPACE_MANAGER_APPLICATION_EXISTS,
                "你已有一条同目标的待审批申请，请等待平台运营者裁决",
            )
        audit.write_audit(
            session,
            action="manager_application_submitted",
            actor_id=actor.id,
            target_id=application.id,
            ip=ctx.ip,
            detail={"request_kind": request_kind, "space_id": space_id},
        )
    return application


def decide_manager_application(
    session: Session,
    ctx: ActorContext,
    application_id: int,
    *,
    decision: str,
    note: str | None,
    decided_by: int,
) -> SpaceManagerApplication:
    """平台运营者裁决已有空间的管理员申请。

    approve 只把目标空间内仍为 active member 的申请人升为 space_admin，
    绝不修改 family_spaces.owner_id；reject 必须填写理由。终态不可再变（409）。
    """
    actor = load_actor(session, ctx)
    with command_transaction(session):
        operator_account = session.get(Account, ctx.account_id)
        if operator_account is None:
            raise_api_error(401, AUTH_INVALID_CREDENTIALS, "名字或 PIN 码错误")
        platform_roles.require_platform_operator(session, operator_account)
        if decided_by != actor.id:
            raise_api_error(403, "SPACE_FORBIDDEN_ACTOR", "裁决人必须是当前平台运营者")
        if decision not in ("approve", "reject"):
            raise_api_error(422, VALIDATION_ERROR, "未知的裁决动作")
        trimmed_note = (note or "").strip() or None
        if decision == "reject" and trimmed_note is None:
            raise_api_error(422, SPACE_MANAGER_APPLICATION_NOTE_REQUIRED, "驳回必须填写理由")

        # Claim the pending row atomically before any approval side effect. A
        # second operator either observes the committed terminal state or gets
        # rowcount=0; it can never run the same create/upgrade twice.
        terminal_status = "approved" if decision == "approve" else "rejected"
        claimed_at = utcnow()
        result = session.execute(
            update(SpaceManagerApplication)
            .where(
                SpaceManagerApplication.id == application_id,
                SpaceManagerApplication.status == "pending",
            )
            .values(
                status=terminal_status,
                decision_note=trimmed_note,
                decided_by=decided_by,
                decided_at=claimed_at,
            )
        )
        if result.rowcount != 1:
            application = _application_or_404(session, application_id)
            raise_api_error(
                409,
                SPACE_MANAGER_APPLICATION_DECIDED,
                "该申请已裁决",
                detail={"status": application.status},
            )
        application = _application_or_404(session, application_id)

        result_space_id: int = application.space_id
        if decision == "approve":
            member = space_fsm.find_membership(
                session, result_space_id, application.applicant_user_id
            )
            if (
                member is None
                or space_fsm.effective_status(member) != "active"
                or member.role != "member"
            ):
                raise_api_error(
                    409,
                    VALIDATION_ERROR,
                    "申请人当前不是该空间的普通成员，无法批准升级",
                )
            member.role = "space_admin"
            member.updated_at = utcnow()
            session.flush()

        emit(
            session,
            event_type="space.manager_application.decided",
            aggregate_type="space_manager_application",
            aggregate_id=application.id,
            payload={
                "decision": decision,
                "request_kind": application.request_kind,
                "applicant_user_id": application.applicant_user_id,
                "space_id": result_space_id,
                "decided_by": decided_by,
            },
            space_id=result_space_id,
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action=(
                "manager_application_approved"
                if decision == "approve"
                else "manager_application_rejected"
            ),
            actor_id=decided_by,
            target_id=application.id,
            ip=ctx.ip,
            detail={
                "request_kind": application.request_kind,
                "applicant_user_id": application.applicant_user_id,
                "space_id": result_space_id,
                "note": trimmed_note,
                "admin_action": True,
            },
        )
    return application


def applications_of(session: Session, user_id: int) -> list[SpaceManagerApplication]:
    """本人申请列表（申请人自助查询状态与平台备注）。"""
    return list(
        session.scalars(
            select(SpaceManagerApplication)
            .where(SpaceManagerApplication.applicant_user_id == user_id)
            .order_by(SpaceManagerApplication.id.desc())
        ).all()
    )


def list_applications(session: Session, status: str | None = None) -> list[SpaceManagerApplication]:
    """运营者审批队列（可选状态过滤；仅申请行本身的最小数据）。"""
    stmt = select(SpaceManagerApplication).order_by(SpaceManagerApplication.id.desc()).limit(200)
    if status is not None:
        stmt = stmt.where(SpaceManagerApplication.status == status)
    return list(session.scalars(stmt).all())


def serialize_application(
    session: Session, application: SpaceManagerApplication
) -> ManagerApplicationOut:
    """申请行最小投影：申请人名与目标空间名，不包含家庭档案字段。"""
    applicant = session.get(User, application.applicant_user_id)
    space = session.get(FamilySpace, application.space_id)
    return ManagerApplicationOut.model_validate(application).model_copy(
        update={
            "applicant_name": applicant.name if applicant else None,
            "space_name": space.name if space else None,
        }
    )
