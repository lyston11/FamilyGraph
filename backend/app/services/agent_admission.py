"""多租户执行准入：全局 + 租户双层配额，带公平排队与有界等待。

为什么需要这一层（而不是继续用单个全局信号量）：

1. **租户隔离**。现有工具准入只有一个进程级名额上限，它保护的是「别把连接池
   打满」，但不区分调用者。一个空间提交几十个工具调用就能占满全部名额，另一个
   空间的 Assistant 只能排队——「能并发 lease」不等于「能隔离执行」。这里给
   每个租户（Assistant=account，Steward=space）单独上限，使单租户突发最多占
   自己的份额。

2. **控制面保留**。heartbeat/lease/settle/cancel/context/health 不取本层的名额，
   但它们共享 AnyIO 工作线程与数据库连接池。因此执行面名额必须**明显小于**连接
   池上限，为控制面留出余量；这个上界由 `config` 在启动时强制。

3. **公平排队**。等待者按「该租户当前活跃数最少，其次入队最早」出队，避免一个
   租户的长队列把后来者饿死。

等待发生在**事件循环**上：等名额期间既不占工作线程也不占数据库连接，这正是
09-30 那次缺陷的教训（在工作线程里等连接会耗尽线程池，让心跳一起拿不到执行机会）。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


class AdmissionRejected(Exception):
    """名额不可用且已超过有界等待：调用方必须给出明确的可重试/拒绝语义。

    不能把「排队超时」伪装成成功或无限等待——那会让系统在过载时表现为卡住，
    而不是表现为有界拒绝。
    """

    def __init__(self, resource: str, tenant: str, waited_seconds: float, reason: str) -> None:
        super().__init__(
            f"admission rejected for {resource} tenant={tenant} "
            f"after {waited_seconds:.3f}s ({reason})"
        )
        self.resource = resource
        self.tenant = tenant
        self.waited_seconds = waited_seconds
        self.reason = reason


@dataclass
class _Waiter:
    tenant: str
    future: asyncio.Future[None]
    seq: int
    enqueued_at: float


@dataclass
class AdmissionSnapshot:
    resource: str
    active: int
    waiting: int
    global_capacity: int
    per_tenant_capacity: int
    active_by_tenant: dict[str, int] = field(default_factory=dict)
    waiting_by_tenant: dict[str, int] = field(default_factory=dict)


class ResourceLimiter:
    """按租户隔离的执行名额。

    只在**一个事件循环**内有效（与既有工具准入同一约束）：`asyncio.Future` 与
    计数器都不跨进程，因此它是**单实例**保护，不是跨实例全局配额。跨实例配额需要
    持久化协调（PostgreSQL/Redis），属于父任务的后续阶段；这一点在诊断里如实标注。
    """

    def __init__(
        self,
        *,
        name: str,
        global_capacity: int,
        per_tenant_capacity: int,
        max_wait_seconds: float,
        max_queue: int,
    ) -> None:
        if global_capacity < 1:
            raise ValueError("global_capacity must be >= 1")
        if per_tenant_capacity < 1:
            raise ValueError("per_tenant_capacity must be >= 1")
        if per_tenant_capacity > global_capacity:
            # 单租户上限高于全局上限时它永远不生效——那是配置错误，不是宽松策略。
            raise ValueError("per_tenant_capacity must be <= global_capacity")
        if max_queue < 1:
            raise ValueError("max_queue must be >= 1")
        self.name = name
        self.global_capacity = global_capacity
        self.per_tenant_capacity = per_tenant_capacity
        self.max_wait_seconds = max_wait_seconds
        self.max_queue = max_queue
        self._active: dict[str, int] = {}
        self._waiters: list[_Waiter] = []
        self._seq = 0
        self._lock = asyncio.Lock()

    # ---- 观测 -------------------------------------------------------------

    @property
    def active_total(self) -> int:
        return sum(self._active.values())

    def snapshot(self) -> AdmissionSnapshot:
        waiting_by_tenant: dict[str, int] = {}
        for waiter in self._waiters:
            waiting_by_tenant[waiter.tenant] = waiting_by_tenant.get(waiter.tenant, 0) + 1
        return AdmissionSnapshot(
            resource=self.name,
            active=self.active_total,
            waiting=len(self._waiters),
            global_capacity=self.global_capacity,
            per_tenant_capacity=self.per_tenant_capacity,
            active_by_tenant=dict(self._active),
            waiting_by_tenant=waiting_by_tenant,
        )

    # ---- 准入 -------------------------------------------------------------

    def _has_room(self, tenant: str) -> bool:
        """本租户现在能否再取一个名额。

        关键语义是**按竞争保留**，而不是静态均分：

        - 没有别的租户在等时，一个租户可以用满全局名额。这是必需的——一次模型
          回合可以合法地扇出几十个工具调用（生产实测单回合 26 个），静态把单租户
          限到 2 会把正常工作量变成秒级排队。
        - 一旦**别的租户**在等待，本租户的新增名额就被压到 `per_tenant_capacity`，
          为等待者留下 `global - per_tenant_capacity` 的保留量。已在执行的调用不被
          抢占，因此只是停止继续授予，不打断进行中的工作。

        这样「单租户突发」与「跨租户不互相饿死」同时成立，而代价只是把静态上限
        换成一个依赖当前竞争状态的判据。
        """
        if self.active_total >= self.global_capacity:
            return False
        if self._active.get(tenant, 0) >= self.global_capacity:
            return False
        if self._other_tenants_waiting(tenant):
            return self._active.get(tenant, 0) < self.per_tenant_capacity
        return True

    def _other_tenants_waiting(self, tenant: str) -> bool:
        return any(not waiter.future.done() and waiter.tenant != tenant for waiter in self._waiters)

    async def acquire(self, tenant: str) -> None:
        """取得一个名额；超时或队列满则抛 `AdmissionRejected`。

        调用方必须在 `finally` 中 `release(tenant)`；本方法**不**返回上下文管理器，
        以便调用方能像既有工具准入那样把归还挂在 done-callback 上（取消时立即返回，
        但名额等 worker 真正结束才归还）。
        """
        async with self._lock:
            if self._has_room(tenant):
                self._active[tenant] = self._active.get(tenant, 0) + 1
                return
            if len(self._waiters) >= self.max_queue:
                # 队列也满：立刻有界拒绝，不制造无界等待。
                raise AdmissionRejected(self.name, tenant, 0.0, "queue_full")
            self._seq += 1
            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiters.append(
                _Waiter(
                    tenant=tenant,
                    future=future,
                    seq=self._seq,
                    enqueued_at=time.monotonic(),
                )
            )

        started = time.perf_counter()
        try:
            await asyncio.wait_for(future, timeout=self.max_wait_seconds)
        except TimeoutError:
            # 边界竞态：`release` 可能正好在超时瞬间把名额授予我们（它已把
            # `_active` 加一）。此时 `future` 已完成，名额是真的拿到了，必须
            # 当作成功返回；若在这里直接抛拒绝，那个名额就永远泄漏。
            if future.done() and not future.cancelled():
                return
            async with self._lock:
                self._waiters = [w for w in self._waiters if w.future is not future]
            raise AdmissionRejected(
                self.name, tenant, time.perf_counter() - started, "max_wait_exceeded"
            ) from None
        except asyncio.CancelledError:
            # 等待被取消：必须把自己移出队列且**不**消耗名额（名额由唤醒者记账）。
            # 同样处理边界：若恰好已授予，则归还后再抛出，避免泄漏。
            if future.done() and not future.cancelled():
                self.release(tenant)
            async with self._lock:
                self._waiters = [w for w in self._waiters if w.future is not future]
            raise

    def _waiter_priority(self, waiter: _Waiter) -> tuple[int, float, int]:
        """出队优先级：先按「本租户已占名额」分组，再按等待时长。

        分组是为了让**没有在用名额**的租户先跑（它显然不是造成拥塞的一方）；
        组内按等待时长排序（aging），使最早入队者优先，避免一个持续提交的租户
        用后来的请求插队饿死先到者。最后用入队序号兜底，保证排序稳定、可复现。
        """
        return (
            self._active.get(waiter.tenant, 0),
            waiter.enqueued_at,
            waiter.seq,
        )

    def release(self, tenant: str) -> None:
        """归还名额并唤醒一个等待者。

        唤醒选择顺序：先看该租户是否还有余额，再按「该租户活跃数最少 → 入队最早」
        排序。这让单租户的长队列不能把其他租户饿死。
        """
        current = self._active.get(tenant, 0)
        if current <= 1:
            self._active.pop(tenant, None)
        else:
            self._active[tenant] = current - 1

        if not self._waiters:
            return
        # 同 loop 内同步执行：此处不 await，因此不需要持锁。
        candidates = [w for w in self._waiters if not w.future.done() and self._has_room(w.tenant)]
        if not candidates:
            return
        candidates.sort(key=self._waiter_priority)
        chosen = candidates[0]
        self._waiters = [w for w in self._waiters if w is not chosen]
        self._active[chosen.tenant] = self._active.get(chosen.tenant, 0) + 1
        if not chosen.future.done():
            chosen.future.set_result(None)
