"""平台角色服务（v2 Foundation，spec/architecture.md §0.2）。

platform_operator 仅管理系统代码、Provider、工具白名单与安全策略；
对家庭数据**没有任何读取权**：visibility.py 不消费本模块，
operator 在可见性判定中等同无关用户（none → 404）。
break-glass 属未来独立审计接口（后续任务），不属于本模块职责。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import raise_api_error
from app.models.account import Account
from app.models.v2_foundation import PlatformRoleAssignment

ROLE_PLATFORM_OPERATOR = "platform_operator"


def platform_roles(session: Session, account: Account | None) -> frozenset[str]:
    """账号当前持有的平台角色集合。"""
    if account is None:
        return frozenset()
    rows = session.scalars(
        select(PlatformRoleAssignment.role).where(PlatformRoleAssignment.account_id == account.id)
    ).all()
    return frozenset(rows)


def is_platform_operator(session: Session, account: Account | None) -> bool:
    """是否平台运营者。返回 True 不暗示任何家庭数据可见性。"""
    return ROLE_PLATFORM_OPERATOR in platform_roles(session, account)


def require_platform_operator(session: Session, account: Account) -> None:
    """管理后台统一入口；非 operator 403。"""
    if not is_platform_operator(session, account):
        raise_api_error(403, "FORBIDDEN_ADMIN_ONLY", "仅平台运营者可执行该操作")
