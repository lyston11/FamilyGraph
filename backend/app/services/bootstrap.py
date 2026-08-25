"""首启引导：空库一次性创建管理员（锁定决策 A4 / 待定 Q3 默认方案）。

随机 PIN 仅在 initialize 响应中返回一次，服务端不留任何明文痕迹。
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.errors import BOOTSTRAP_ALREADY_INITIALIZED, raise_api_error
from app.models.account import Account
from app.models.user import User
from app.services import audit
from app.utils import security, timeutil


def has_any_user(session: Session) -> bool:
    count = session.query(func.count(User.id)).scalar()
    return bool(count and count > 0)


def ensure_not_initialized(session: Session) -> None:
    """已有用户时拒绝重复初始化（并发窗口由调用方事务串行化兜底）。"""
    if has_any_user(session):
        raise_api_error(
            403,
            BOOTSTRAP_ALREADY_INITIALIZED,
            "系统已完成初始化，无法重复执行",
        )


def initialize_admin(session: Session, name: str, ip: str | None) -> tuple[User, str]:
    """创建管理员账号，返回 (user, 明文 PIN)；PIN 仅此一次。"""
    ensure_not_initialized(session)
    pin = security.generate_pin()
    user = User(name=name.strip(), is_admin=True, created_at=timeutil.utcnow())
    account = Account(
        pin_hash=security.hash_pin(pin),
        pin_must_change=True,
        token_version=0,
        failed_attempts=0,
        locked_until=None,
    )
    user.account = account  # relationship 回填 user_id（见 models/user.py）
    session.add(user)
    session.flush()  # 取得 user.id 供审计 actor/target 引用
    audit.write_audit(
        session,
        action="bootstrap_initialized",
        actor_id=user.id,
        target_id=user.id,
        ip=ip,
        detail={"admin_name": name.strip()},
    )
    return user, pin
