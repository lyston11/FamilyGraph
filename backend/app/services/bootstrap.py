"""首启引导：空库一次性创建平台运营者（v2 Foundation §0.2）。

随机 PIN 仅在 initialize 响应中返回一次，服务端不留任何明文痕迹。
v2：bootstrap 账号获得 platform_operator 角色而非 users.is_admin；
operator 角色不携带任何家庭数据读取权（visibility 不消费平台角色）。
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.errors import BOOTSTRAP_ALREADY_INITIALIZED, raise_api_error
from app.models.account import Account
from app.models.user import User
from app.models.v2_foundation import PlatformRoleAssignment
from app.services import audit
from app.services.identity_fsm import PROFILE_IDENTITY_CONFIRMED
from app.services.platform_roles import ROLE_PLATFORM_OPERATOR
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
    """创建平台运营者账号，返回 (user, 明文 PIN)；PIN 仅此一次。"""
    ensure_not_initialized(session)
    pin = security.generate_pin()
    now = timeutil.utcnow()
    user = User(
        name=name.strip(),
        created_at=now,
        profile_status=PROFILE_IDENTITY_CONFIRMED,
        profile_confirmed_at=now,
    )
    account = Account(
        pin_hash=security.hash_pin(pin),
        pin_must_change=True,
        token_version=0,
        failed_attempts=0,
        locked_until=None,
        # bootstrap 创建者即本人，直接置 claimed（与 v1 语义一致）
        status="claimed",
        claimed_at=now,
    )
    user.account = account  # relationship 回填 user_id（见 models/user.py）
    session.add(user)
    session.flush()  # 取得 id 供角色分配与审计引用
    session.add(
        PlatformRoleAssignment(
            account_id=account.id,
            role=ROLE_PLATFORM_OPERATOR,
            created_by=user.id,
            created_at=now,
        )
    )
    audit.write_audit(
        session,
        action="bootstrap_initialized",
        actor_id=user.id,
        target_id=user.id,
        ip=ip,
        detail={"operator_name": name.strip()},
    )
    return user, pin
