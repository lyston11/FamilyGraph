"""家庭投影端点共用：当前授权复核与载荷 ETag（任务 09-02-personal-family-view-followup）。

三个浏览器面投影（household card / space stats / notifications）共用同一
授权边界：
- 认证主体只允许 family_user（require_authenticated_user 已保证）；
- ``space_id`` 仅是请求上下文：空间不存在、请求者无 active 成员资格时返回
  与 PersonalFamilyView 相同的安全 404 envelope（防枚举，无存在性探针）；
- ETag 由「合同版本 + 最终序列化载荷」派生：任何被服务字段变化都会改变
  ETag；304 只在授权复核通过后才可能命中（先授权、后比较）。
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.errors import (
    PERSONAL_FAMILY_VIEW_DISABLED,
    PERSONAL_FAMILY_VIEW_NOT_FOUND,
    raise_api_error,
)
from app.models.account import Account
from app.models.space import FamilySpace, SpaceMember
from app.models.user import User

HOUSEHOLD_KIND = "household"


def require_pfv_enabled() -> None:
    """消费 PersonalFamilyView 的投影端点与视图本体共用同一启用开关。"""
    if not config.PERSONAL_FAMILY_VIEW_ENABLED:
        raise_api_error(503, PERSONAL_FAMILY_VIEW_DISABLED, "个人家族视图功能未启用")


def authorized_household_space_or_404(
    session: Session, *, account: Account, space_id: int
) -> tuple[FamilySpace, User]:
    """空间 + 当前 active 成员资格 + household kind 三重复核，任一失败安全 404。"""
    space = _authorized_space_or_404(session, account=account, space_id=space_id)
    if space.kind != HOUSEHOLD_KIND:
        # lineage 空间不是 household card 的可用上下文：与未知空间同一拒绝路径
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "个人家族视图不存在")
    return space, _actor_or_404(session, account)


def authorized_space_or_404(
    session: Session, *, account: Account, space_id: int
) -> tuple[FamilySpace, User]:
    """空间 + 当前 active 成员资格复核；失败与未知空间同一安全 404。"""
    space = _authorized_space_or_404(session, account=account, space_id=space_id)
    return space, _actor_or_404(session, account)


def _authorized_space_or_404(session: Session, *, account: Account, space_id: int) -> FamilySpace:
    if space_id <= 0:
        raise_api_error(422, "VALIDATION_ERROR", "空间参数不合法")
    active = session.scalar(
        select(SpaceMember.id).where(
            SpaceMember.space_id == space_id,
            SpaceMember.user_id == account.user_id,
            SpaceMember.status == "active",
        )
    )
    space = session.get(FamilySpace, space_id)
    if active is None or space is None:
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "个人家族视图不存在")
    return space


def _actor_or_404(session: Session, account: Account) -> User:
    actor = session.get(User, account.user_id)
    if actor is None:  # pragma: no cover - account FK 保证存在
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "个人家族视图不存在")
    return actor


def etag_for_json(contract_version: str, payload_json: str) -> str:
    """由合同版本与最终序列化载荷派生强 ETag（带引号的 HTTP 形态）。"""
    digest = hashlib.sha256(f"{contract_version}:{payload_json}".encode()).hexdigest()
    return f'"{digest}"'


__all__ = [
    "authorized_household_space_or_404",
    "authorized_space_or_404",
    "etag_for_json",
    "require_pfv_enabled",
]
