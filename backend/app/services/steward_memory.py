"""Steward 可读记忆级别的配置与读取身份解析。

## 为什么单独一个模块

「管家能读哪些级别的记忆」由三层配置取**交集**决定（部署 env ∩ 平台列 ∩ 空间列），
而「读的是谁的记忆」由 fenced run 的身份决定。这两件事都不属于 steward 的工具分派、
也不属于记忆检索本身，因此独立成模块，让检索层只接收已经算好的 `allowed_scopes`
与 `private_reader_account_id`。

## 交集而不是并集

三层任一为空即整体为空。理由与 `platform_features._steward_assist_effective` 一致：
env 是部署级 kill-switch（关了就一律关），平台是治理面，空间是数据所有者的上界。
用并集会让「空间关掉」被平台打开覆盖，那是数据所有者无法收回的授权。

## private 的额外约束（不可由配置放宽）

`readable_scopes` 在有效集之上再施加一条：**没有 viewer 的 kind 一律去掉 private**。
空间级 kind（`candidate`/`ranking`/`explanation`）的 run 身份回落到 space admin，
它无法证明「读的是谁的私有记忆」——放开就等于让管家读到管理员本人的私事。
只有带 viewer 的 kind（当前为 `terminology`）才可能读 private，且只能读该 viewer 本人
（由检索层的 `private_reader_account_id` 保证）。

## 脏配置的方向

读取时**忽略未知 scope 并告警**，不因一个拼错的词而放开任何东西；写入时（管理面）
由 `parse_scopes_strict` 直接 422。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models.account import Account
from app.models.agent_provider import AgentSpaceProviderSetting
from app.models.memory import MEMORY_SCOPES
from app.models.platform_features import PlatformFeatureConfig
from app.models.space import SpaceMember
from app.models.user import User
from app.services import agent_provider

logger = logging.getLogger(__name__)

# 只有带 viewer 的 kind 才可能读 private。当前唯一带 viewer 的是 terminology
# （`steward_assist` 只在 terminology 上写 `viewer_account_id`）。
VIEWER_SCOPED_KINDS: frozenset[str] = frozenset({"terminology"})

#: steward 可以读的 RAG `source_type`（可读集的**第二个维度**）。
#:
#: ## 为什么是显式枚举，而不是从 scope 推导
#:
#: 「允许 `household`」不能推出「`household` 下的所有类别都可以给管家」。
#: 用户确认的记忆、家族故事、附件授权文档、公共称谓知识的**授权强度不同**：
#: 记忆是用户逐条确认过的事实，公共知识虽无个人数据但语义上是空间外的语料。
#: 把「读哪些级别」和「读哪些类别」压成一个维度，会让放开一个 scope 时静默
#: 放开该 scope 下的全部类别——默认方向是**放开**，这是错的方向。
#:
#: ## 新增 source_type 默认不可读
#:
#: 新类别不会出现在这个元组里，也不会因为某个 scope 被放开而获得访问。要让 steward
#: 读到新类别，必须改这一行——即一次显式的、可评审的代码改动，而不是一次配置写入。
#: 这是刻意的：放宽管家的读取面应当是**部署 + 评审**，不是管理员界面上的一次点击。
#:
#: `tests/test_steward_memory_scopes.py` 用 `public_kinship`（2026-10-10 新增的
#: 第一个新类别）做反向断言，证明「新增类别不会自动进入管家可读集」。
STEWARD_READABLE_SOURCE_TYPES: tuple[str, ...] = ("memory",)


def parse_scopes(raw: str | None) -> tuple[str, ...]:
    """宽松解析：忽略未知项并告警，返回按 `MEMORY_SCOPES` 顺序的规范化元组。"""
    if not raw:
        return ()
    seen: set[str] = set()
    unknown: list[str] = []
    for token in str(raw).split(","):
        name = token.strip()
        if not name:
            continue
        if name not in MEMORY_SCOPES:
            unknown.append(name)
            continue
        seen.add(name)
    if unknown:
        logger.warning("steward_memory_scopes: ignoring unknown scope(s): %s", sorted(unknown))
    return tuple(scope for scope in MEMORY_SCOPES if scope in seen)


def parse_scopes_strict(raw: str | None) -> tuple[str, ...]:
    """严格解析（管理面写入用）：未知项抛 `ValueError`，由路由转 422。"""
    if raw is None or not str(raw).strip():
        return ()
    names = [token.strip() for token in str(raw).split(",") if token.strip()]
    unknown = sorted({name for name in names if name not in MEMORY_SCOPES})
    if unknown:
        raise ValueError(f"unknown memory scope(s): {', '.join(unknown)}")
    return tuple(scope for scope in MEMORY_SCOPES if scope in set(names))


def encode_scopes(scopes: Iterable[str]) -> str:
    """规范化编码（存库用）：按 `MEMORY_SCOPES` 顺序去重后逗号分隔。"""
    return ",".join(parse_scopes(",".join(scopes)))


def env_scopes() -> tuple[str, ...]:
    """部署级上界。空 = 部署级关闭。"""
    return parse_scopes(config.STEWARD_MEMORY_SCOPES)


def platform_scopes(db: Session) -> tuple[str, ...]:
    """平台治理面配置；行缺失 = 空集（与 assist 开关「行缺失不视为开启」一致）。"""
    row = db.get(PlatformFeatureConfig, 1)
    return parse_scopes(row.steward_memory_scopes) if row is not None else ()


def space_scopes(db: Session, space_id: int) -> tuple[str, ...]:
    """空间上界；无显式 steward 行 = 空集（默认全关）。"""
    row = db.scalar(
        select(AgentSpaceProviderSetting).where(
            AgentSpaceProviderSetting.space_id == space_id,
            AgentSpaceProviderSetting.agent_kind == agent_provider.AGENT_KIND_STEWARD,
        )
    )
    return parse_scopes(row.steward_memory_scopes) if row is not None else ()


def _intersect(*groups: tuple[str, ...]) -> tuple[str, ...]:
    if not groups:
        return ()
    allowed = set(groups[0])
    for group in groups[1:]:
        allowed &= set(group)
    return tuple(scope for scope in MEMORY_SCOPES if scope in allowed)


def effective_scopes(db: Session, *, space_id: int) -> tuple[str, ...]:
    """三层交集：部署 env ∩ 平台列 ∩ 空间列。任一为空即整体为空。"""
    return _intersect(env_scopes(), platform_scopes(db), space_scopes(db, space_id))


def readable_scopes(
    db: Session, *, space_id: int, viewer_account_id: int | None
) -> tuple[str, ...]:
    """在有效集之上施加 private 约束：无 viewer 一律去掉 private。"""
    scopes = effective_scopes(db, space_id=space_id)
    if viewer_account_id is None:
        return tuple(scope for scope in scopes if scope != "private")
    return scopes


def kind_may_read_private(assist_kind: str | None) -> bool:
    """该 assist kind 是否属于「带 viewer、可读 private」的一类。"""
    return assist_kind in VIEWER_SCOPED_KINDS


def resolve_reader(
    db: Session, *, space_id: int, viewer_account_id: int | None
) -> tuple[User, Account] | None:
    """解析检索身份：viewer 优先，否则回落到该空间的 active space admin。

    与 `api.internal_agent._steward_run_context` 的身份选择同口径（那里的回落是为了
    给空间级 kind 一个 policy 身份）。回落身份**只用于**空间级 household/lineage 的
    成员判据；private 由 `private_reader_account_id` 单独把关，因此回落不会扩大
    private 的读取范围。找不到可用身份时返回 None，调用方必须 fail-closed。
    """
    account_id = viewer_account_id
    if account_id is None:
        admin_user_id = db.scalar(
            select(SpaceMember.user_id).where(
                SpaceMember.space_id == space_id,
                SpaceMember.role == "space_admin",
                SpaceMember.status == "active",
            )
        )
        if admin_user_id is None:
            return None
        account_id = db.scalar(select(Account.id).where(Account.user_id == admin_user_id))
    if account_id is None:
        return None
    account = db.get(Account, account_id)
    if account is None:
        return None
    user = db.get(User, account.user_id)
    if user is None:
        return None
    return user, account


__all__ = [
    "STEWARD_READABLE_SOURCE_TYPES",
    "VIEWER_SCOPED_KINDS",
    "effective_scopes",
    "encode_scopes",
    "env_scopes",
    "kind_may_read_private",
    "parse_scopes",
    "parse_scopes_strict",
    "platform_scopes",
    "readable_scopes",
    "resolve_reader",
    "space_scopes",
]
