"""空间管理者申请命令（平台运营者审批制，任务 08-30-space-manager-approval）。

合同（PRD 用户确认 2026-08-30，记录于 spec/architecture.md §0.7）：
- 只有成为已有空间的管理者需要审批：active member 申请 member → space_admin；
- 拉人、邀请不需要审批，active member（除 guest）可邀请，受邀人仍需接受；
- 新空间开辟和 Owner Invitation 保持既有语义；现有空间 owner 仅经 ownership_transfers FSM 变更。
- 每条命令单短事务：授权 → 校验 → 写入 → domain_events → audit。
"""

from __future__ import annotations

from sqlalchemy import select
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
    ManagerTransferConsent,
    SpaceManagerApplication,
    SpaceMember,
)
from app.models.user import User
from app.schemas.space import (
    EligibleManagerTarget,
    ManagerApplicationOut,
    ManagerTransferConsentOut,
)
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
        if space.kind != "lineage":
            raise_api_error(422, VALIDATION_ERROR, "管理员申请只能针对 lineage 家族空间")
        member = space_fsm.find_membership(session, space.id, actor.id)
        if member is None or space_fsm.effective_status(member) != "active":
            raise_api_error(404, SPACE_NOT_FOUND, "家庭空间不存在")
        if member.role != "member":
            raise_api_error(409, VALIDATION_ERROR, "你已是该空间的空间管理员")

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


def _current_manager(session: Session, space_id: int) -> SpaceMember | None:
    return space_fsm.active_space_manager(session, space_id)


def decide_manager_application(
    session: Session,
    ctx: ActorContext,
    application_id: int,
    *,
    decision: str,
    note: str | None,
    decided_by: int,
) -> SpaceManagerApplication:
    """平台裁决申请；有现任管理员时先创建其明确同意的交接工单。

    ``approve`` 的第一次调用只进入交接准备，申请仍为 pending；只有工单
    accepted 后的再次 approve 才在同一事务中交换唯一 space_admin 关系。
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
        application = _application_or_404(session, application_id)
        if application.status != "pending":
            raise_api_error(
                409,
                SPACE_MANAGER_APPLICATION_DECIDED,
                "该申请已裁决",
                detail={"status": application.status},
            )
        space = session.get(FamilySpace, application.space_id)
        member = space_fsm.find_membership(
            session, application.space_id, application.applicant_user_id
        )
        if space is None or space.kind != "lineage":
            raise_api_error(409, VALIDATION_ERROR, "申请目标已不是 lineage 家族空间")
        if (
            member is None
            or space_fsm.effective_status(member) != "active"
            or member.role != "member"
        ):
            raise_api_error(409, VALIDATION_ERROR, "申请人当前不是该空间的普通成员，无法批准升级")

        manager = _current_manager(session, application.space_id)
        now = utcnow()
        if decision == "approve" and manager is None:
            raise_api_error(409, VALIDATION_ERROR, "目标空间当前没有管理员，请走空间修复流程")

        if decision == "approve" and manager is not None:
            consent = session.scalar(
                select(ManagerTransferConsent).where(
                    ManagerTransferConsent.application_id == application.id
                )
            )
            if consent is None:
                consent = ManagerTransferConsent(
                    application_id=application.id,
                    space_id=application.space_id,
                    current_manager_user_id=manager.user_id,
                    status="pending",
                    requested_at=now,
                    version=1,
                )
                session.add(consent)
                session.flush()
                audit.write_audit(
                    session,
                    action="manager_transfer_consent_sent",
                    actor_id=decided_by,
                    target_id=consent.id,
                    ip=ctx.ip,
                    detail={
                        "application_id": application.id,
                        "applicant_user_id": application.applicant_user_id,
                        "space_id": application.space_id,
                        "target_space_name": space.name,
                    },
                )
                emit(
                    session,
                    event_type="space.manager_transfer_consent.requested",
                    aggregate_type="manager_transfer_consent",
                    aggregate_id=consent.id,
                    payload={
                        "application_id": application.id,
                        "applicant_user_id": application.applicant_user_id,
                        "current_manager_user_id": manager.user_id,
                        "space_id": application.space_id,
                    },
                    space_id=application.space_id,
                    actor_account_id=ctx.account_id,
                )
                return application
            if consent.status != "accepted" or consent.current_manager_user_id != manager.user_id:
                raise_api_error(409, VALIDATION_ERROR, "等待当前空间管理员明确同意后再批准")
            # Accepted consent is bound to the manager snapshot. Consume it so a
            # later retry cannot reuse an old agreement.
            consent.status = "expired"
            consent.responded_at = now
            consent.version += 1
            manager.role = "member"
            manager.updated_at = now
            member.role = "space_admin"
            member.updated_at = now
            application.status = "approved"
            application.decision_note = trimmed_note
            application.decided_by = decided_by
            application.decided_at = now
        else:
            application.status = "rejected"
            application.decision_note = trimmed_note
            application.decided_by = decided_by
            application.decided_at = now

        session.flush()
        emit(
            session,
            event_type="space.manager_application.decided",
            aggregate_type="space_manager_application",
            aggregate_id=application.id,
            payload={
                "decision": decision,
                "applicant_user_id": application.applicant_user_id,
                "space_id": application.space_id,
                "decided_by": decided_by,
            },
            space_id=application.space_id,
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
                "applicant_user_id": application.applicant_user_id,
                "space_id": application.space_id,
                "note": trimmed_note,
                "admin_action": True,
            },
        )
    return application


def respond_to_transfer_consent(
    session: Session,
    ctx: ActorContext,
    consent_id: int,
    *,
    decision: str,
    reason: str | None,
) -> ManagerTransferConsent:
    """目标空间当前唯一管理员处理站内交接工单。"""
    actor = load_actor(session, ctx)
    # 失效判定先独立成一个事务：过期必须真正落库。若与 409 放在同一事务里，
    # raise 会触发 command_transaction 回滚，工单将永远停在 pending 而可被反复
    # 重试（PRD R3：管理员资格变化后旧同意不可复用）。
    with command_transaction(session):
        consent = session.get(ManagerTransferConsent, consent_id)
        if consent is None or consent.current_manager_user_id != actor.id:
            raise_api_error(404, SPACE_MANAGER_APPLICATION_NOT_FOUND, "交接工单不存在")
        if consent.status != "pending":
            raise_api_error(409, SPACE_MANAGER_APPLICATION_DECIDED, "交接工单已处理")
        stale = not space_fsm.is_space_manager(session, consent.space_id, actor.id)
        if stale:
            consent.status = "expired"
            consent.responded_at = utcnow()
            consent.version += 1
            audit.write_audit(
                session,
                action="manager_transfer_consent_expired",
                actor_id=actor.id,
                target_id=consent.id,
                ip=ctx.ip,
                detail={"reason": "actor_no_longer_manager", "space_id": consent.space_id},
            )
    if stale:
        raise_api_error(409, VALIDATION_ERROR, "你已不是目标空间当前管理员")

    with command_transaction(session):
        if decision not in ("accept", "reject"):
            raise_api_error(422, VALIDATION_ERROR, "未知的工单动作")
        trimmed_reason = (reason or "").strip() or None
        now = utcnow()
        consent.status = "accepted" if decision == "accept" else "rejected"
        consent.responded_at = now
        consent.response_reason = trimmed_reason
        consent.version += 1
        application = _application_or_404(session, consent.application_id)
        if decision == "reject":
            application.status = "rejected"
            application.decision_note = trimmed_reason or "原管理员拒绝交接"
            application.decided_at = now
        audit.write_audit(
            session,
            action=(
                "manager_transfer_consent_accepted"
                if decision == "accept"
                else "manager_transfer_consent_rejected"
            ),
            actor_id=actor.id,
            target_id=consent.id,
            ip=ctx.ip,
            detail={"application_id": consent.application_id, "space_id": consent.space_id},
        )
    return consent


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
    consent = session.scalar(
        select(ManagerTransferConsent).where(
            ManagerTransferConsent.application_id == application.id
        )
    )
    manager = _current_manager(session, application.space_id)
    manager_user = session.get(User, manager.user_id) if manager else None
    return ManagerApplicationOut.model_validate(application).model_copy(
        update={
            "applicant_name": applicant.name if applicant else None,
            "space_name": space.name if space else None,
            "space_kind": space.kind if space else None,
            "current_manager_user_id": manager.user_id if manager else None,
            "current_manager_name": manager_user.name if manager_user else None,
            "transfer_consent_id": consent.id if consent else None,
            "transfer_consent_status": consent.status if consent else None,
        }
    )


def serialize_consent(
    session: Session, consent: ManagerTransferConsent
) -> ManagerTransferConsentOut:
    """工单投影：目标空间名称/类型与申请人标识由服务端解析。"""
    space = session.get(FamilySpace, consent.space_id)
    application = session.get(SpaceManagerApplication, consent.application_id)
    applicant = (
        session.get(User, application.applicant_user_id) if application is not None else None
    )
    return ManagerTransferConsentOut.model_validate(consent).model_copy(
        update={
            "space_name": space.name if space else None,
            "space_kind": space.kind if space else None,
            "applicant_user_id": application.applicant_user_id if application else None,
            "applicant_name": applicant.name if applicant else None,
        }
    )


def eligible_lineage_targets(session: Session, user_id: int) -> list[EligibleManagerTarget]:
    """可提交管理员申请的 lineage 空间。

    入口不能显示无目标的"申请成为管理员"（PRD R4），因此资格规则留在服务端：
    申请人必须是目标 lineage 空间的 active 普通 member；household 与已由本人
    管理的空间不出现在列表里。已有 pending 申请的空间仍然返回，但标记为
    ``has_pending_application`` 让前端显示状态而不是重复提交入口。
    """
    rows = (
        session.query(FamilySpace, SpaceMember)
        .join(SpaceMember, SpaceMember.space_id == FamilySpace.id)
        .filter(
            SpaceMember.user_id == user_id,
            FamilySpace.kind == "lineage",
        )
        .order_by(FamilySpace.id)
        .all()
    )
    output: list[EligibleManagerTarget] = []
    for space, member in rows:
        if space_fsm.effective_status(member) != "active" or member.role != "member":
            continue
        pending = session.scalar(
            select(SpaceManagerApplication).where(
                SpaceManagerApplication.applicant_user_id == user_id,
                SpaceManagerApplication.space_id == space.id,
                SpaceManagerApplication.status == "pending",
            )
        )
        manager = _current_manager(session, space.id)
        manager_user = session.get(User, manager.user_id) if manager else None
        output.append(
            EligibleManagerTarget(
                space_id=space.id,
                space_name=space.name,
                space_kind="lineage",
                current_manager_user_id=manager.user_id if manager else None,
                current_manager_name=manager_user.name if manager_user else None,
                has_pending_application=pending is not None,
            )
        )
    return output


def decide_manager_application_as_system_admin(
    session: Session,
    application_id: int,
    *,
    decision: str,
    note: str | None,
    system_admin_id: int,
    ip: str | None,
) -> SpaceManagerApplication:
    """系统主体裁决入口；不构造或查询家庭 User 作为操作人。"""
    with command_transaction(session):
        application = _application_or_404(session, application_id)
        if application.status != "pending":
            raise_api_error(
                409,
                SPACE_MANAGER_APPLICATION_DECIDED,
                "该申请已裁决",
                detail={"status": application.status},
            )
        if decision not in ("approve", "reject"):
            raise_api_error(422, VALIDATION_ERROR, "未知的裁决动作")
        trimmed_note = (note or "").strip() or None
        if decision == "reject" and trimmed_note is None:
            raise_api_error(422, SPACE_MANAGER_APPLICATION_NOTE_REQUIRED, "驳回必须填写理由")
        space = session.get(FamilySpace, application.space_id)
        member = space_fsm.find_membership(
            session, application.space_id, application.applicant_user_id
        )
        if space is None or space.kind != "lineage":
            raise_api_error(409, VALIDATION_ERROR, "申请目标已不是 lineage 家族空间")
        if (
            member is None
            or space_fsm.effective_status(member) != "active"
            or member.role != "member"
        ):
            raise_api_error(409, VALIDATION_ERROR, "申请人当前不是该空间的普通成员")
        now = utcnow()
        manager = _current_manager(session, application.space_id)
        if decision == "approve":
            if manager is None:
                raise_api_error(409, VALIDATION_ERROR, "目标空间当前没有管理员，请走空间修复流程")
            consent = session.scalar(
                select(ManagerTransferConsent).where(
                    ManagerTransferConsent.application_id == application.id
                )
            )
            if consent is None:
                session.add(
                    ManagerTransferConsent(
                        application_id=application.id,
                        space_id=application.space_id,
                        current_manager_user_id=manager.user_id,
                        status="pending",
                        requested_at=now,
                        version=1,
                    )
                )
                session.flush()
                audit.write_audit(
                    session,
                    action="manager_transfer_consent_sent",
                    actor_id=None,
                    target_id=application.id,
                    ip=ip,
                    detail={
                        "principal_type": "system_admin",
                        "system_admin_id": system_admin_id,
                        "applicant_user_id": application.applicant_user_id,
                        "space_id": application.space_id,
                        "target_space_name": space.name,
                    },
                )
                return application
            if consent.status != "accepted" or consent.current_manager_user_id != manager.user_id:
                raise_api_error(409, VALIDATION_ERROR, "等待当前空间管理员明确同意后再批准")
            manager.role = "member"
            manager.updated_at = now
            member.role = "space_admin"
            member.updated_at = now
            consent.status = "expired"
            consent.responded_at = now
            consent.version += 1
        application.status = "approved" if decision == "approve" else "rejected"
        application.decision_note = trimmed_note
        application.decided_at = now
        application.system_admin_decided_by = system_admin_id
        session.flush()
        # 与家庭裁决路径保持同一事件契约；系统主体没有 account_id，故不带
        # actor_account_id，裁决人身份由 system_admin_id 表达。
        emit(
            session,
            event_type="space.manager_application.decided",
            aggregate_type="space_manager_application",
            aggregate_id=application.id,
            payload={
                "decision": decision,
                "applicant_user_id": application.applicant_user_id,
                "space_id": application.space_id,
                "system_admin_decided_by": system_admin_id,
            },
            space_id=application.space_id,
        )
        audit.write_audit(
            session,
            action="manager_application_approved"
            if decision == "approve"
            else "manager_application_rejected",
            actor_id=None,
            target_id=application.id,
            ip=ip,
            detail={
                "principal_type": "system_admin",
                "system_admin_id": system_admin_id,
                "applicant_user_id": application.applicant_user_id,
                "space_id": application.space_id,
                "note": trimmed_note,
                "admin_action": True,
            },
        )
    return application
