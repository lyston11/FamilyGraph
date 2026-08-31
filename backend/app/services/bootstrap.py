"""首启引导：空库一次性创建平台运营者（v2 Foundation §0.2）。

随机 PIN 仅在 initialize 响应中返回一次，服务端不留任何明文痕迹。
v2：bootstrap 账号获得 platform_operator 角色而非 users.is_admin；
operator 角色不携带任何家庭数据读取权（visibility 不消费平台角色）。
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.errors import BOOTSTRAP_ALREADY_INITIALIZED, raise_api_error
from app.models.system_admin import SystemAdmin, SystemAdminAccount
from app.models.user import User
from app.services import audit
from app.utils import security, timeutil


def has_any_user(session: Session) -> bool:
    user_count = session.query(func.count(User.id)).scalar()
    admin_count = session.query(func.count(SystemAdmin.id)).scalar()
    return bool((user_count and user_count > 0) or (admin_count and admin_count > 0))


def ensure_not_initialized(session: Session) -> None:
    """已有用户时拒绝重复初始化（并发窗口由调用方事务串行化兜底）。"""
    if has_any_user(session):
        raise_api_error(
            403,
            BOOTSTRAP_ALREADY_INITIALIZED,
            "系统已完成初始化，无法重复执行",
        )


def initialize_admin(session: Session, name: str, ip: str | None) -> tuple[SystemAdmin, str]:
    """创建独立系统管理员主体；绝不创建家庭 User/Account。"""
    ensure_not_initialized(session)
    pin = security.generate_pin()
    now = timeutil.utcnow()
    admin = SystemAdmin(login_name=name.strip(), status="active", created_at=now)
    admin.account = SystemAdminAccount(
        pin_hash=security.hash_pin(pin),
        pin_must_change=True,
        token_version=0,
        failed_attempts=0,
        locked_until=None,
        status="managed",
        claimed_at=None,
    )
    session.add(admin)
    session.flush()
    audit.write_audit(
        session,
        action="bootstrap_initialized",
        actor_id=None,
        target_id=admin.id,
        ip=ip,
        detail={"principal_type": "system_admin"},
    )
    return admin, pin
