"""admin_app 独立审计唯一入口（09-04 RM-F4）。

只追加：每次普通查询、敏感详情使用、会话创建/拒绝、审批裁决都写一行
``admin_access_audits``。审计正文只含动作、目标、过滤器摘要、结果数量与
请求元信息（request_id/IP）；响应正文、密码、token、原始错误与私人文本
永不进入审计（filters 经 admin_sanitizer.sanitize_filters 清洗）。
"""

from typing import Any

from sqlalchemy.orm import Session

from app import logctx
from app.models.admin_access import AdminAccessAudit, AdminAccessSession
from app.services.admin_sanitizer import sanitize_filters
from app.utils import timeutil


def record_access(
    session: Session,
    *,
    action: str,
    endpoint: str,
    system_admin_id: int,
    session_row: AdminAccessSession | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    filters: dict[str, Any] | None = None,
    result_count: int | None = None,
    ip: str | None = None,
) -> AdminAccessAudit:
    """追加一条后台访问审计；由调用方事务统一提交。"""
    entry = AdminAccessAudit(
        system_admin_id=system_admin_id,
        session_id=session_row.id if session_row is not None else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        endpoint=endpoint[:255],
        filters_json=sanitize_filters(filters or {}),
        result_count=result_count,
        request_id=logctx.request_id_var.get() or None,
        ip=ip,
        created_at=timeutil.utcnow(),
    )
    session.add(entry)
    return entry
