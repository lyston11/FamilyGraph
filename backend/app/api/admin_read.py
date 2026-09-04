"""/admin-api/v1 只读模型与访问会话路由（09-04 子任务 2，仅 8002）。

- 全部端点要求 system_admin 主体（require_admin_ready：无效令牌 401，首登未改密 403）；
- 普通列表与敏感详情每次访问写独立审计（admin_access_audits）；
- 敏感详情（档案详情 / 头像缩略图 / 附件元数据 / 关系边 / confirmed 事实）
  要求绑定单目标的 ``X-Admin-Access-Session`` 票据，响应 no-store；
- 本路由面除审批（admin_governance.py）外无任何写端点。
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from fastapi import APIRouter, Depends, Query, Request, Response
from PIL import Image
from sqlalchemy.orm import Session

from app.api.admin_deps import AdminPrincipal, enforce_access_session, require_admin_ready
from app.api.deps import get_db
from app.errors import raise_api_error
from app.schemas.admin_read import (
    PAGE_SIZE_MAX,
    AdminAccessSessionCreate,
    AdminAccessSessionOut,
    AdminAgentJobOut,
    AdminAgentRunOut,
    AdminAttachmentMetadataOut,
    AdminAuditAccessOut,
    AdminFactOut,
    AdminMemberOut,
    AdminNotificationOut,
    AdminOperationsQueueItemOut,
    AdminOverviewPageOut,
    AdminPageOut,
    AdminProfileOut,
    AdminRelationOut,
    AdminSpaceAdminOut,
    AdminSpaceDetailOut,
    AdminSpaceSummaryOut,
)
from app.services import (
    admin_access_sessions,
    admin_audit,
    admin_read_model,
)
from app.services.admin_read_model import ADMIN_RESOURCE_NOT_FOUND, ADMIN_RESOURCE_NOT_FOUND_MESSAGE

router = APIRouter(prefix="/admin-api/v1", tags=["admin-read-model"])

THUMBNAIL_MAX_EDGE = 256


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _audit_read(
    session: Session,
    identity: AdminPrincipal,
    request: Request,
    *,
    action: str,
    endpoint: str,
    target_type: str | None = None,
    target_id: int | None = None,
    filters: dict[str, object] | None = None,
    result_count: int | None = None,
) -> None:
    """普通读取审计：filters 经脱敏后只保留标量安全值。"""
    admin, _account = identity
    admin_audit.record_access(
        session,
        action=action,
        endpoint=endpoint,
        system_admin_id=admin.id,
        target_type=target_type,
        target_id=target_id,
        filters=filters,
        result_count=result_count,
        ip=_client_ip(request),
    )
    session.commit()


# ---- overview / space-admins / spaces ----


@router.get("/overview", response_model=AdminOverviewPageOut)
def get_overview(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=PAGE_SIZE_MAX),
    search: str | None = Query(default=None, max_length=100),
    status: Literal["healthy", "anomaly"] | None = None,
    from_dt: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    result = admin_read_model.overview(
        session,
        page=page,
        page_size=page_size,
        search=search,
        status=status,
        from_dt=from_dt,
        to_dt=to,
    )
    _audit_read(
        session,
        identity,
        request,
        action="read.overview",
        endpoint="/admin-api/v1/overview",
        filters={"search": search, "status": status, "from": from_dt, "to": to},
        result_count=len(result["items"]) if isinstance(result["items"], list) else None,
    )
    return result


@router.get("/space-admins", response_model=AdminPageOut[AdminSpaceAdminOut])
def get_space_admins(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=PAGE_SIZE_MAX),
    search: str | None = Query(default=None, max_length=100),
    status: Literal["managed", "claimed"] | None = None,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    result = admin_read_model.space_admins(
        session, page=page, page_size=page_size, search=search, status=status
    )
    _audit_read(
        session,
        identity,
        request,
        action="read.space_admins",
        endpoint="/admin-api/v1/space-admins",
        filters={"search": search, "status": status},
        result_count=len(result["items"]),
    )
    return result


@router.get(
    "/space-admins/{admin_user_id}/spaces", response_model=AdminPageOut[AdminSpaceSummaryOut]
)
def get_space_admin_spaces(
    admin_user_id: int,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=PAGE_SIZE_MAX),
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    result = admin_read_model.admin_spaces(session, admin_user_id, page=page, page_size=page_size)
    _audit_read(
        session,
        identity,
        request,
        action="read.space_admin_spaces",
        endpoint="/admin-api/v1/space-admins/{admin_user_id}/spaces",
        filters={"admin_user_id": admin_user_id},
        result_count=len(result["items"]),
    )
    return result


@router.get("/spaces/{space_id}", response_model=AdminSpaceDetailOut)
def get_space_detail(
    space_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    detail = admin_read_model.space_detail(session, space_id)
    _audit_read(
        session,
        identity,
        request,
        action="read.space_detail",
        endpoint="/admin-api/v1/spaces/{space_id}",
        target_type="space",
        target_id=space_id,
    )
    if detail is None:
        raise_api_error(404, ADMIN_RESOURCE_NOT_FOUND, ADMIN_RESOURCE_NOT_FOUND_MESSAGE)
    return detail


@router.get("/spaces/{space_id}/members", response_model=AdminPageOut[AdminMemberOut])
def get_space_members(
    space_id: int,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=PAGE_SIZE_MAX),
    search: str | None = Query(default=None, max_length=100),
    status: Literal["pending", "active", "rejected", "withdrawn", "removed"] | None = None,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    result = admin_read_model.space_members(
        session, space_id, page=page, page_size=page_size, search=search, status=status
    )
    _audit_read(
        session,
        identity,
        request,
        action="read.space_members",
        endpoint="/admin-api/v1/spaces/{space_id}/members",
        target_type="space",
        target_id=space_id,
        filters={"search": search, "status": status},
        result_count=len(result["items"]),
    )
    return result


@router.get("/spaces/{space_id}/relations", response_model=AdminPageOut[AdminRelationOut])
def get_space_relations(
    space_id: int,
    request: Request,
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=PAGE_SIZE_MAX),
    status: Literal["pending", "active", "rejected", "cancelled", "revoked"] | None = None,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    """空间关系边（敏感详情）：需要 space 访问会话；no-store。"""
    enforce_access_session(
        request,
        session,
        identity,
        target_type="space",
        target_id=space_id,
        endpoint="/admin-api/v1/spaces/{space_id}/relations",
    )
    result = admin_read_model.space_relations(
        session, space_id, page=page, page_size=page_size, status=status
    )
    admin, _account = identity
    admin_audit.record_access(
        session,
        action="sensitive.read.space_relations",
        endpoint="/admin-api/v1/spaces/{space_id}/relations",
        system_admin_id=admin.id,
        target_type="space",
        target_id=space_id,
        filters={"status": status},
        result_count=len(result["items"]),
        ip=_client_ip(request),
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/spaces/{space_id}/facts", response_model=AdminPageOut[AdminFactOut])
def get_space_facts(
    space_id: int,
    request: Request,
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=PAGE_SIZE_MAX),
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    """confirmed 事实（敏感详情）：需要 space 访问会话；no-store。"""
    enforce_access_session(
        request,
        session,
        identity,
        target_type="space",
        target_id=space_id,
        endpoint="/admin-api/v1/spaces/{space_id}/facts",
    )
    result = admin_read_model.space_facts(session, space_id, page=page, page_size=page_size)
    admin, _account = identity
    admin_audit.record_access(
        session,
        action="sensitive.read.space_facts",
        endpoint="/admin-api/v1/spaces/{space_id}/facts",
        system_admin_id=admin.id,
        target_type="space",
        target_id=space_id,
        result_count=len(result["items"]),
        ip=_client_ip(request),
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


# ---- 档案 / 头像 / 附件 ----


@router.get("/users/{user_id}/profile", response_model=AdminProfileOut)
def get_user_profile(
    user_id: int,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    """基础档案（敏感详情）：需要 user 访问会话；no-store；未知目标安全 404。"""
    enforce_access_session(
        request,
        session,
        identity,
        target_type="user",
        target_id=user_id,
        endpoint="/admin-api/v1/users/{user_id}/profile",
    )
    profile = admin_read_model.user_profile(session, user_id)
    admin, _account = identity
    admin_audit.record_access(
        session,
        action="sensitive.read.user_profile",
        endpoint="/admin-api/v1/users/{user_id}/profile",
        system_admin_id=admin.id,
        target_type="user",
        target_id=user_id,
        ip=_client_ip(request),
    )
    session.commit()
    if profile is None:
        raise_api_error(404, ADMIN_RESOURCE_NOT_FOUND, ADMIN_RESOURCE_NOT_FOUND_MESSAGE)
    response.headers["Cache-Control"] = "no-store"
    return profile


@router.get("/users/{user_id}/avatar/thumbnail")
def get_user_avatar_thumbnail(
    user_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> Response:
    """鉴权头像缩略图（敏感详情）：需要 user 访问会话；不暴露任何存储路径。"""
    enforce_access_session(
        request,
        session,
        identity,
        target_type="user",
        target_id=user_id,
        endpoint="/admin-api/v1/users/{user_id}/avatar/thumbnail",
    )
    filename = admin_read_model.user_avatar_filename(session, user_id)
    admin, _account = identity
    admin_audit.record_access(
        session,
        action="sensitive.read.user_avatar",
        endpoint="/admin-api/v1/users/{user_id}/avatar/thumbnail",
        system_admin_id=admin.id,
        target_type="user",
        target_id=user_id,
        ip=_client_ip(request),
    )
    session.commit()
    if filename is None:
        raise_api_error(404, ADMIN_RESOURCE_NOT_FOUND, ADMIN_RESOURCE_NOT_FOUND_MESSAGE)
    from app.config import UPLOADS_DIR

    # 仅取文件名（防路径穿越）；原图永不外泄，缩略图实时重编码。
    path = UPLOADS_DIR / Path(filename).name
    if not path.exists():
        raise_api_error(404, ADMIN_RESOURCE_NOT_FOUND, ADMIN_RESOURCE_NOT_FOUND_MESSAGE)
    try:
        with Image.open(path) as img:
            img.thumbnail((THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE))
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
    except Exception:  # noqa: BLE001 - 损坏文件按资源缺失处理，不泄露细节
        raise_api_error(404, ADMIN_RESOURCE_NOT_FOUND, ADMIN_RESOURCE_NOT_FOUND_MESSAGE)
    return Response(
        content=buffer.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/users/{user_id}/attachments", response_model=AdminPageOut[AdminAttachmentMetadataOut])
def get_user_attachments(
    user_id: int,
    request: Request,
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=PAGE_SIZE_MAX),
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    """附件安全元数据（敏感详情）：需要 user 访问会话；no-store。"""
    enforce_access_session(
        request,
        session,
        identity,
        target_type="user",
        target_id=user_id,
        endpoint="/admin-api/v1/users/{user_id}/attachments",
    )
    result = admin_read_model.user_attachments(session, user_id, page=page, page_size=page_size)
    admin, _account = identity
    admin_audit.record_access(
        session,
        action="sensitive.read.user_attachments",
        endpoint="/admin-api/v1/users/{user_id}/attachments",
        system_admin_id=admin.id,
        target_type="user",
        target_id=user_id,
        filters={"page": page, "page_size": page_size},
        result_count=len(result["items"]),
        ip=_client_ip(request),
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


# ---- 运营队列 / 通知 ----


@router.get("/operations/queue", response_model=AdminPageOut[AdminOperationsQueueItemOut])
def get_operations_queue(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=PAGE_SIZE_MAX),
    kind: Literal["space_anomaly", "manager_application"] | None = None,
    status: str | None = Query(None, max_length=32),
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    result = admin_read_model.operations_queue(
        session, page=page, page_size=page_size, kind=kind, status=status
    )
    _audit_read(
        session,
        identity,
        request,
        action="read.operations_queue",
        endpoint="/admin-api/v1/operations/queue",
        filters={"kind": kind, "status": status},
        result_count=len(result["items"]),
    )
    return result


@router.get("/operations/notifications", response_model=AdminPageOut[AdminNotificationOut])
def get_operations_notifications(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=PAGE_SIZE_MAX),
    space_id: int | None = None,
    read: bool | None = None,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    result = admin_read_model.notifications(
        session, page=page, page_size=page_size, space_id=space_id, read=read
    )
    _audit_read(
        session,
        identity,
        request,
        action="read.notifications",
        endpoint="/admin-api/v1/operations/notifications",
        filters={"space_id": space_id, "read": read},
        result_count=len(result["items"]),
    )
    return result


# ---- Agent 运行诊断 ----


@router.get("/agent/runs", response_model=AdminPageOut[AdminAgentRunOut])
def get_agent_runs(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=PAGE_SIZE_MAX),
    space_id: int | None = None,
    status: (
        Literal["queued", "leased", "running", "succeeded", "failed", "cancelled", "expired"] | None
    ) = None,
    from_dt: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    result = admin_read_model.agent_runs(
        session,
        page=page,
        page_size=page_size,
        space_id=space_id,
        status=status,
        from_dt=from_dt,
        to_dt=to,
    )
    _audit_read(
        session,
        identity,
        request,
        action="read.agent_runs",
        endpoint="/admin-api/v1/agent/runs",
        filters={"space_id": space_id, "status": status, "from": from_dt, "to": to},
        result_count=len(result["items"]),
    )
    return result


@router.get("/agent/jobs", response_model=AdminPageOut[AdminAgentJobOut])
def get_agent_jobs(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=PAGE_SIZE_MAX),
    space_id: int | None = None,
    status: (
        Literal["queued", "leased", "running", "succeeded", "failed", "cancelled", "expired"] | None
    ) = None,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    result = admin_read_model.agent_jobs(
        session, page=page, page_size=page_size, space_id=space_id, status=status
    )
    _audit_read(
        session,
        identity,
        request,
        action="read.agent_jobs",
        endpoint="/admin-api/v1/agent/jobs",
        filters={"space_id": space_id, "status": status},
        result_count=len(result["items"]),
    )
    return result


# ---- 审计时间线 / 访问会话 ----


@router.get("/audit/access", response_model=AdminPageOut[AdminAuditAccessOut])
def get_access_audits(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=PAGE_SIZE_MAX),
    target_type: Literal["user", "space"] | None = None,
    target_id: int | None = None,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    result = admin_read_model.access_audits(
        session, page=page, page_size=page_size, target_type=target_type, target_id=target_id
    )
    _audit_read(
        session,
        identity,
        request,
        action="read.audit_access",
        endpoint="/admin-api/v1/audit/access",
        filters={"target_type": target_type, "target_id": target_id},
        result_count=len(result["items"]),
    )
    return result


@router.post("/access-sessions", response_model=AdminAccessSessionOut)
def create_access_session(
    payload: AdminAccessSessionCreate,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> AdminAccessSessionOut:
    """签发绑定单目标的 30 分钟访问会话；明文票据只在本次响应中出现。"""
    admin, _account = identity
    # 目标不存在：统一安全 404（防存在性枚举，不区分 user/space）。
    admin_access_sessions.ensure_target(session, payload.target_type, payload.target_id)
    session_row, raw_token = admin_access_sessions.create_session(
        session,
        system_admin_id=admin.id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        reason=payload.reason,
    )
    admin_audit.record_access(
        session,
        action="access_session.created",
        endpoint="/admin-api/v1/access-sessions",
        system_admin_id=admin.id,
        session_row=session_row,
        target_type=payload.target_type,
        target_id=payload.target_id,
        ip=_client_ip(request),
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return AdminAccessSessionOut(
        session_id=raw_token,
        target_type=cast(Literal["user", "space"], session_row.target_type),
        target_id=session_row.target_id,
        allowed_scopes=list(session_row.scopes_json),
        issued_at=session_row.issued_at,
        expires_at=session_row.expires_at,
    )
