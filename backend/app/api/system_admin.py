"""独立 system_admin 平台后台的申请裁决 API。

账号、空间、成员和交接工单查询由 ``admin_metadata`` 提供；本模块只保留
申请队列和裁决写操作，避免重复注册同一管理后台路径。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_system_admin
from app.commands import manager_applications
from app.models.system_admin import SystemAdmin, SystemAdminAccount
from app.schemas.space import ManagerApplicationDecision, ManagerApplicationOut

router = APIRouter(prefix="/admin", tags=["system-admin"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/manager-applications", response_model=list[ManagerApplicationOut])
def list_manager_applications(
    status: str | None = None,
    session: Session = Depends(get_db),
    identity: tuple[SystemAdmin, SystemAdminAccount] = Depends(require_system_admin),
) -> list[ManagerApplicationOut]:
    """系统管理员查看申请最小投影。"""
    _admin, _admin_account = identity
    rows = manager_applications.list_applications(session, status=status)
    return [manager_applications.serialize_application(session, row) for row in rows]


@router.post(
    "/manager-applications/{application_id}/decision",
    response_model=ManagerApplicationOut,
)
def decide_manager_application(
    application_id: int,
    payload: ManagerApplicationDecision,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[SystemAdmin, SystemAdminAccount] = Depends(require_system_admin),
) -> ManagerApplicationOut:
    """系统管理员审核申请；已有管理员时先创建交接同意工单。"""
    admin, _admin_account = identity
    row = manager_applications.decide_manager_application_as_system_admin(
        session,
        application_id,
        decision=payload.decision,
        note=payload.note,
        system_admin_id=admin.id,
        ip=_client_ip(request),
    )
    return manager_applications.serialize_application(session, row)
