"""Redis 加速层与降级策略（C5）。

## 定位（不可协商）

Redis 是**加速层**，不是真源。任何 lease、settle、counter、授权、审计的终态都由
PostgreSQL 裁决；Redis 只做短生命周期加速：

```text
admission 快速拒绝 / token bucket / wakeup / 短期缓存
```

## 降级策略（冻结）

`REDIS_DEGRADATION_POLICY = "pg_fallback"`。理由：

- Redis 是加速层，它的失效**不应改变可用性语义**；
- PostgreSQL 已经有**有界**准入真源（C2 的 capacity counter），因此回退是安全的；
- 控制面（heartbeat/lease/settle/cancel/context/health）本来就不走 Redis。

替代方案 `fail_closed`（Redis 抖动即 503）被否决：它把加速层的故障升级成用户可见
的可用性故障，而本层的全部价值就是「可选加速」。

## 三条硬约束（各有对应断言）

1. **绝不 fail-open**：Redis 不可用时不得放行。放行 = 配额失效 = 比不可用更糟。
   实测：不可用时 `ConnectionError` 在 0.002s 内抛出（`redis_degradation_probe.py`）。
2. **缓存不承载授权**：缓存只存「已经由 PostgreSQL 授权过的结论」的短期投影，
   且 key 必须带 scope/epoch。缓存失效只会导致 cache miss，不会放宽权限。
3. **wakeup 丢失必须可补偿**：所有 wakeup 都是**提示**，持久扫描是真相。
   丢一条消息只意味着延迟，不意味着永不执行。

## 与 PostgreSQL 的关系

回退路径**必须是有界的**：回退不等于无限制放行。因此回退时沿用 C2 的 capacity
counter（它本身有 `CHECK (active <= capacity)` 兜底），而不是「Redis 挂了就全放」。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: 冻结的降级策略。`pg_fallback` = Redis 不可用时回退 PostgreSQL 有界准入。
REDIS_DEGRADATION_POLICY = "pg_fallback"

#: 不可用判定的缓存时长。避免每个请求都去尝试连接一个已经挂掉的后端——
#: 那会让 Redis 故障变成**每个请求**的固定延迟开销（fail-loud 也要有界）。
_UNAVAILABLE_COOLDOWN_SECONDS = float(os.environ.get("REDIS_UNAVAILABLE_COOLDOWN_SECONDS", "5"))


#: 单次操作的超时。必须小：加速层不该成为延迟来源。
#:
#: 生产默认 0.5s（同机 Redis 足够）。**经 SSH 隧道/跨可用区时必须调大**——实测
#: 0.5s 会让正常的建连超时，从而把「网络距离」误报成「Redis 不可用」。
def _operation_timeout() -> float:
    return float(os.environ.get("REDIS_OPERATION_TIMEOUT_SECONDS", "0.5"))


@dataclass(frozen=True)
class RedisHealth:
    """加速层健康快照（供诊断，不含 key 或值）。"""

    available: bool
    policy: str
    last_error: str | None
    checked_at: float


class RedisAccelerator:
    """Redis 加速层：可用时加速，不可用时**显式降级**（绝不静默放行）。

    线程安全：健康状态与冷却窗口由锁保护。这里用 `threading.Lock` 而不是
    `asyncio.Lock`，因为它同时被工作线程和事件循环调用（两者都会读健康状态）。
    """

    def __init__(self, url: str | None = None) -> None:
        self._url = url if url is not None else os.environ.get("REDIS_URL")
        self._client: Any | None = None
        self._lock = threading.Lock()
        self._unavailable_until = 0.0
        self._last_error: str | None = None

    # ---- 健康与降级判定 -----------------------------------------------

    @property
    def configured(self) -> bool:
        """是否配置了 Redis。

        未配置 = 本层**完全关闭**，所有调用方走 PostgreSQL 路径。这不是故障，
        因此不记警告、不进冷却。
        """
        return bool(self._url)

    def _client_or_none(self) -> Any | None:
        if not self.configured:
            return None
        if self._client is not None:
            return self._client
        import redis

        assert self._url is not None
        timeout = _operation_timeout()
        self._client = redis.Redis.from_url(
            self._url,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
            decode_responses=True,
        )
        return self._client

    def available(self) -> bool:
        """是否可尝试使用 Redis。

        冷却窗口内直接返回 False：Redis 挂掉时不应让**每个**请求都去等一次超时。
        """
        if not self.configured:
            return False
        with self._lock:
            return time.monotonic() >= self._unavailable_until

    def _mark_unavailable(self, exc: BaseException) -> None:
        with self._lock:
            self._unavailable_until = time.monotonic() + _UNAVAILABLE_COOLDOWN_SECONDS
            self._last_error = type(exc).__name__
        logger.warning(
            "redis accelerator degraded policy=%s error_class=%s cooldown_s=%.1f",
            REDIS_DEGRADATION_POLICY,
            type(exc).__name__,
            _UNAVAILABLE_COOLDOWN_SECONDS,
        )

    def health(self) -> RedisHealth:
        with self._lock:
            return RedisHealth(
                available=self.configured and time.monotonic() >= self._unavailable_until,
                policy=REDIS_DEGRADATION_POLICY,
                last_error=self._last_error,
                checked_at=time.time(),
            )

    # ---- 原子操作（可用时加速，不可用时返回 None 表示「本层无结论」）----

    def try_set_if_absent(self, key: str, *, ttl_seconds: int) -> bool | None:
        """`SET NX EX`。返回：

        - `True`：已获得（本层裁决：是赢家）；
        - `False`：已被占用（本层裁决：不是赢家）；
        - `None`：**本层不可用，无结论**——调用方必须走 PostgreSQL 有界路径。

        `None` 与 `False` 的区分是关键：把「不可用」当成「已被占用」会让
        admission 在 Redis 故障时**全部拒绝**（可用性故障）；当成 `True` 会
        fail-open（配额失效）。因此必须显式三态。
        """
        if not self.available():
            return None
        client = self._client_or_none()
        if client is None:
            return None
        try:
            return bool(client.set(key, "1", nx=True, ex=ttl_seconds))
        except Exception as exc:  # noqa: BLE001 - 任何后端错误都按不可用降级
            self._mark_unavailable(exc)
            return None

    def get(self, key: str) -> str | None:
        """读缓存。不可用时返回 None（= cache miss），不是错误。"""
        if not self.available():
            return None
        client = self._client_or_none()
        if client is None:
            return None
        try:
            value = client.get(key)
            return value if value is None else str(value)
        except Exception as exc:  # noqa: BLE001
            self._mark_unavailable(exc)
            return None

    def set(self, key: str, value: str, *, ttl_seconds: int) -> bool:
        """写缓存。不可用时返回 False（调用方忽略——缓存写失败不影响正确性）。"""
        if not self.available():
            return False
        client = self._client_or_none()
        if client is None:
            return False
        try:
            client.set(key, value, ex=ttl_seconds)
            return True
        except Exception as exc:  # noqa: BLE001
            self._mark_unavailable(exc)
            return False

    def delete(self, key: str) -> bool:
        """删除缓存。不可用时返回 False——但**持久状态不受影响**。"""
        if not self.available():
            return False
        client = self._client_or_none()
        if client is None:
            return False
        try:
            client.delete(key)
            return True
        except Exception as exc:  # noqa: BLE001
            self._mark_unavailable(exc)
            return False


#: 进程级单例。与 limiter 同理：健康状态与冷却窗口必须共享，否则每个请求各建一个
#: 客户端会让冷却机制失效（Redis 挂掉时仍然每个请求都去超时）。
_accelerator: RedisAccelerator | None = None
_accelerator_lock = threading.Lock()


def accelerator() -> RedisAccelerator:
    global _accelerator
    if _accelerator is None:
        with _accelerator_lock:
            if _accelerator is None:
                _accelerator = RedisAccelerator()
    return _accelerator


def reset_accelerator_for_tests() -> None:
    """测试用：丢弃单例，使下一次调用按当前环境重新建。"""
    global _accelerator
    with _accelerator_lock:
        _accelerator = None


# ---- key 规范 ---------------------------------------------------------
#
# 所有 key 必须带 scope 与 epoch，避免跨租户/跨代命中：
#   fg:{layer}:{scope_kind}:{scope_id}:{resource}:{epoch}
#
# 不带 scope 的 key 会让一个租户读到另一个租户的结论——那不只是缓存错误，
# 是**授权泄漏**。因此 key 构造集中在这里，不允许调用方自由拼接。


def scoped_key(
    *,
    layer: str,
    scope_kind: str,
    scope_id: int,
    resource: str,
    epoch: int = 0,
) -> str:
    """构造带 scope 与 epoch 的 key。

    `epoch` 用于失效整代（例如 writer epoch 变化、算法换版）：epoch 变了，
    旧 key 自然不再被读，不需要扫描删除。
    """
    if scope_kind not in {"global", "account", "space", "agent_kind"}:
        raise ValueError(f"unsupported scope_kind: {scope_kind}")
    return f"fg:{layer}:{scope_kind}:{scope_id}:{resource}:{epoch}"


# ---------------------------------------------------- 配额快速拒绝标记（C5 接入）
#
# ## 为什么是「负缓存」而不是「令牌桶」
#
# 冻结的降级策略是 `pg_fallback`：Redis 失效不能改变可用性语义。若 Redis 持有
# 令牌并**授权**请求，那它就是配额真源——Redis 一挂就必须 fail-closed，与策略矛盾。
#
# 因此 Redis 只存**负缓存**：某租户近期被权威路径判为「已满」时写一个短 TTL 标记；
# 后续请求命中标记即**快速拒绝**，不再打数据库。
#
# 三个性质保证它安全：
#
# 1. **只能拒绝，永不授权**——标记存在时拒绝，不存在时**照常走**权威路径。
#    因此 Redis 故障（返回 None）等价于「没有标记」，行为退化为未接入状态。
# 2. **不 fail-open**：命中标记时拒绝，而不是放行。
# 3. **权威裁决不变**：真正的准入仍由进程内 limiter + 集群 counter 决定。
#
# 代价（必须显式承认）：标记有 TTL，因此租户释放名额后最长 TTL 内仍可能被拒。
# 这是**有界**的假拒绝（默认 1 秒），换来的是「满租户的突发只打一次数据库」。

#: 标记 TTL。短是刻意的：它直接决定「名额释放后仍被拒」的上界。
#:
#: ## 硬约束：TTL 必须 > 权威路径的单次判定耗时
#:
#: 若 TTL 小于「一次权威判定 + 一次 Redis 往返」的耗时，标记会在下一个请求到来前
#: 就过期，负缓存完全失效（每个请求仍然打数据库）。
#:
#: 同机 Redis 下该耗时是毫秒级，1 秒有充足余量。**经 SSH 隧道或跨可用区时**该耗时
#: 可达秒级——那种环境下必须调大（实测隧道下 1 秒会失效）。这不是「测试特例」：
#: 任何高延迟 Redis 部署都适用同一条约束。
_OVER_QUOTA_TTL_SECONDS = int(os.environ.get("REDIS_OVER_QUOTA_TTL_SECONDS", "1"))


def over_quota_key(*, tenant: str, resource: str) -> str:
    """负缓存 key：按租户 + 执行平面分区。

    与熔断器不同，这里**必须**按租户分区：它表达的是「这个租户满了」，
    而不是「这个上游坏了」。混淆两者会让一个租户的饱和拒绝其他租户。
    """
    return f"fg:quota:{resource}:{tenant}"


def is_marked_over_quota(*, tenant: str, resource: str) -> bool:
    """该租户近期是否被判为已满。Redis 不可用时返回 False（走权威路径）。"""
    return accelerator().get(over_quota_key(tenant=tenant, resource=resource)) is not None


def mark_over_quota(*, tenant: str, resource: str) -> bool:
    """权威路径判定「已满」后写标记；写失败不影响正确性。"""
    return accelerator().set(
        over_quota_key(tenant=tenant, resource=resource),
        "1",
        ttl_seconds=_OVER_QUOTA_TTL_SECONDS,
    )


def clear_over_quota(*, tenant: str, resource: str) -> bool:
    """名额释放后清标记，缩短假拒绝窗口。清失败只影响延迟，不影响正确性。"""
    return accelerator().delete(over_quota_key(tenant=tenant, resource=resource))


# ---------------------------------------------------- 异步包装（事件循环安全）
#
# **必须用这些异步版本，不能在事件循环上直接调用同步版本。**
#
# Redis 客户端是同步的（`redis.Redis`），单次操作最多阻塞
# `REDIS_OPERATION_TIMEOUT_SECONDS`（默认 0.5s）。若在事件循环上调用，Redis 变慢
# 就会把整个进程的所有端点一起卡住——这正是 09-30 那次缺陷的形态（同步 I/O 阻塞
# 事件循环，心跳拿不到执行机会）。
#
# 因此所有在 async 上下文中使用负缓存的调用点都必须走 `asyncio.to_thread`。


async def is_marked_over_quota_async(*, tenant: str, resource: str) -> bool:
    """`is_marked_over_quota` 的事件循环安全版本。"""
    import asyncio

    return await asyncio.to_thread(
        is_marked_over_quota, tenant=tenant, resource=resource
    )


async def mark_over_quota_async(*, tenant: str, resource: str) -> bool:
    """`mark_over_quota` 的事件循环安全版本。"""
    import asyncio

    return await asyncio.to_thread(mark_over_quota, tenant=tenant, resource=resource)


async def clear_over_quota_async(*, tenant: str, resource: str) -> bool:
    """`clear_over_quota` 的事件循环安全版本。"""
    import asyncio

    return await asyncio.to_thread(clear_over_quota, tenant=tenant, resource=resource)
