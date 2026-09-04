"""/admin-api/v1 审批唯一写例外（09-04 RM-F5，仅 8002）。

仅有的两个业务写端点：POST /manager-applications/{id}/approve 与 /reject。
- approve 理由可选、reject 理由必填非空；二者都必须携带二次确认字段
  （``confirm: true`` 由 schema 校验，缺失/为 false 一律 422）；
- 复用 commands/manager_applications.decide_manager_application_as_system_admin
  的单事务裁决（申请状态 + consent + 唯一 active space_admin + domain event
  + 家庭 audit + 独立 admin audit）；
- 终态重复裁决 409；目标不存在统一 404；不触碰 family_spaces.owner_id。
除本模块外 /admin-api/v1 无任何写端点。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.admin_deps import AdminPrincipal, require_admin_ready
from app.api.deps import get_db
from app.commands import manager_applications
from app.schemas.admin_read import (
    AdminApplicationApproveRequest,
    AdminApplicationRejectRequest,
    AdminManagerApplicationOut,
)
from app.services import admin_read_model

router = APIRouter(prefix="/admin-api/v1", tags=["admin-governance"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post(
    "/manager-applications/{application_id}/approve",
    response_model=AdminManagerApplicationOut,
)
def approve_manager_application(
    application_id: int,
    payload: AdminApplicationApproveRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    """批准申请：理由可选；首次调用只进入交接准备（申请仍 pending）。"""
    admin, _account = identity
    application = manager_applications.decide_manager_application_as_system_admin(
        session,
        application_id,
        decision="approve",
        note=payload.note,
        system_admin_id=admin.id,
        ip=_client_ip(request),
        endpoint="/admin-api/v1/manager-applications",
    )
    return admin_read_model.serialize_admin_application(session, application)


@router.post(
    "/manager-applications/{application_id}/reject",
    response_model=AdminManagerApplicationOut,
)
def reject_manager_application(
    application_id: int,
    payload: AdminApplicationRejectRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    """驳回申请：理由必填非空（schema + 命令层双重校验）；终态不可改判。"""
    admin, _account = identity
    application = manager_applications.decide_manager_application_as_system_admin(
        session,
        application_id,
        decision="reject",
        note=payload.note,
        system_admin_id=admin.id,
        ip=_client_ip(request),
        endpoint="/admin-api/v1/manager-applications",
    )
    return admin_read_model.serialize_admin_application(session, application)
