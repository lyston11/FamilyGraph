"""系统管理员专用治理元数据 API。

这些查询使用显式列和专用 schema，不能通过家庭可见性或档案详情端点旁路读取。
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_system_admin
from app.models import (
    Account,
    FamilySpace,
    SpaceManagerApplication,
    SpaceMember,
    User,
)
from app.models.space import ManagerTransferConsent
from app.models.system_admin import SystemAdmin, SystemAdminAccount
from app.schemas.admin_metadata import (
    AdminAccountMetadata,
    SpaceManagerMetadata,
    SpaceMemberMetadata,
    SpaceMetadata,
    TransferConsentMetadata,
)
from app.services import audit
from app.services.space_fsm import active_space_manager

router = APIRouter(prefix="/admin", tags=["system-admin-metadata"])


def _audit_query(db: Session, admin_id: int, action: str, target_id: int | None = None) -> None:
    audit.write_audit(
        db,
        action=action,
        actor_id=None,
        target_id=target_id or admin_id,
        detail={"principal_type": "system_admin", "system_admin_id": admin_id},
    )
    db.commit()


@router.get("/accounts", response_model=list[AdminAccountMetadata])
def list_accounts(
    request: Request,
    identity: tuple[SystemAdmin, SystemAdminAccount] = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[AdminAccountMetadata]:
    rows = db.query(Account, User).join(User, User.id == Account.user_id).all()
    output = [
        AdminAccountMetadata(
            account_id=account.id,
            subject_id=user.id,
            subject_type="family_user",
            status=account.status,
            locked_until=account.locked_until,
            created_at=user.created_at,
        )
        for account, user in rows
    ]
    output.extend(
        AdminAccountMetadata(
            account_id=account.id,
            subject_id=admin.id,
            subject_type="system_admin",
            status=account.status,
            locked_until=account.locked_until,
            created_at=admin.created_at,
        )
        for admin, account in db.query(SystemAdmin, SystemAdminAccount)
        .join(SystemAdminAccount, SystemAdminAccount.system_admin_id == SystemAdmin.id)
        .all()
    )
    _audit_query(db, identity[0].id, "system_admin_accounts_queried")
    return output


@router.get("/space-managers", response_model=list[SpaceManagerMetadata])
def list_space_managers(
    identity: tuple[SystemAdmin, SystemAdminAccount] = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[SpaceManagerMetadata]:
    rows = (
        db.query(FamilySpace, SpaceMember, User)
        .join(
            SpaceMember,
            (SpaceMember.space_id == FamilySpace.id)
            & (SpaceMember.role == "space_admin")
            & (SpaceMember.status == "active"),
        )
        .join(User, User.id == SpaceMember.user_id)
        .order_by(FamilySpace.id)
        .all()
    )
    output = []
    for space, _member, user in rows:
        account = db.scalar(select(Account).where(Account.user_id == user.id))
        output.append(
            SpaceManagerMetadata(
                space_id=space.id,
                space_name=space.name,
                space_kind=space.kind,
                manager_user_id=user.id,
                manager_account_id=account.id if account else None,
                manager_name=user.name,
            )
        )
    _audit_query(db, identity[0].id, "system_admin_space_managers_queried")
    return output


@router.get("/spaces", response_model=list[SpaceMetadata])
def list_spaces(
    identity: tuple[SystemAdmin, SystemAdminAccount] = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[SpaceMetadata]:
    output = []
    for space in db.scalars(select(FamilySpace).order_by(FamilySpace.id)).all():
        manager = active_space_manager(db, space.id)
        user = db.get(User, manager.user_id) if manager else None
        account = (
            db.scalar(select(Account).where(Account.user_id == manager.user_id))
            if manager
            else None
        )
        output.append(
            SpaceMetadata(
                id=space.id,
                name=space.name,
                kind=space.kind,
                status="active",
                created_at=space.created_at,
                manager_user_id=manager.user_id if manager else None,
                manager_account_id=account.id if account else None,
                manager_name=user.name if user else None,
            )
        )
    _audit_query(db, identity[0].id, "system_admin_spaces_queried")
    return output


@router.get("/spaces/{space_id}/members", response_model=list[SpaceMemberMetadata])
def list_space_members(
    space_id: int,
    identity: tuple[SystemAdmin, SystemAdminAccount] = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[SpaceMemberMetadata]:
    space = db.get(FamilySpace, space_id)
    if space is None:
        # Avoid turning this endpoint into a family-data existence oracle.
        return []
    rows = (
        db.query(SpaceMember, User)
        .join(User, User.id == SpaceMember.user_id)
        .filter(SpaceMember.space_id == space_id)
        .order_by(SpaceMember.id)
        .all()
    )
    output = []
    for member, user in rows:
        account = db.scalar(select(Account).where(Account.user_id == user.id))
        output.append(
            SpaceMemberMetadata(
                user_id=user.id,
                account_id=account.id if account else None,
                name=user.name,
                role=member.role,
                status=member.status,
                created_at=member.created_at,
                updated_at=member.updated_at,
            )
        )
    _audit_query(db, identity[0].id, "system_admin_space_members_queried", space_id)
    return output


@router.get("/manager-transfer-consents", response_model=list[TransferConsentMetadata])
def list_transfer_consents(
    identity: tuple[SystemAdmin, SystemAdminAccount] = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[TransferConsentMetadata]:
    rows = (
        db.query(ManagerTransferConsent, SpaceManagerApplication, FamilySpace)
        .join(
            SpaceManagerApplication,
            SpaceManagerApplication.id == ManagerTransferConsent.application_id,
        )
        .join(FamilySpace, FamilySpace.id == ManagerTransferConsent.space_id)
        .order_by(ManagerTransferConsent.id.desc())
        .all()
    )
    output = []
    for consent, application, space in rows:
        applicant = db.get(User, application.applicant_user_id)
        manager = db.get(User, consent.current_manager_user_id)
        if applicant is None or manager is None:
            continue
        output.append(
            TransferConsentMetadata(
                id=consent.id,
                application_id=application.id,
                space_id=space.id,
                space_name=space.name,
                space_kind=space.kind,
                applicant_user_id=applicant.id,
                applicant_name=applicant.name,
                current_manager_user_id=manager.id,
                current_manager_name=manager.name,
                status=consent.status,
                requested_at=consent.requested_at,
                responded_at=consent.responded_at,
                response_reason=consent.response_reason,
            )
        )
    _audit_query(db, identity[0].id, "system_admin_transfer_consents_queried")
    return output
