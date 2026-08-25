"""审计写入唯一入口（logging-guidelines.md：禁止裸 insert 散落）。

audit_log 记录：login_failed≥3、account_locked、challenge_rejected、
refresh_reuse_detected、pin_changed、bootstrap_initialized（AD-2 / PRD）。
detail 中禁止出现 PIN/JWT/challenge 明文。
"""

import json

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.utils import timeutil


def write_audit(
    session: Session,
    action: str,
    actor_id: int | None = None,
    target_id: int | None = None,
    ip: str | None = None,
    detail: dict[str, object] | None = None,
) -> AuditLog:
    """追加一条审计记录；由调用方事务统一提交。"""
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        target_id=target_id,
        ip=ip,
        detail_json=json.dumps(detail or {}, ensure_ascii=False),
        created_at=timeutil.utcnow(),
    )
    session.add(entry)
    return entry
