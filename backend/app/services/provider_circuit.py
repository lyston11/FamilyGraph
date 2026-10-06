"""Provider 上游熔断器（C4 剩余项）。

## 为什么需要

当前上游故障的表现是**每个 run 各自失败**：DERP 丢路由或 provider 挂掉时，
每个租户的每个 run 都会完整走一遍 connect → 超时 → 重试预算耗尽。实测过的最坏
形态是 18.8 小时持续失败，每次 run 耗尽 24 次出站尝试。

熔断器把「上游整体不可用」变成**发送前快速拒绝**：

- 连续失败达到阈值 → `open`，后续请求**不发往上游**，立刻返回可重试错误；
- 冷却后进入 `half_open`，只放**有界数量**的探针；
- 探针成功 → `closed` 恢复；失败 → 回到 `open` 并延长冷却。

## 分区（关键设计）

熔断必须按 **upstream × kind** 分区，**不能全局**：

- 一个 provider profile 挂掉不能熔断另一个（不同上游、不同网络路径）；
- Assistant 与 Steward 可能走不同 provider 设置，互相不应牵连；
- 全局熔断会把「一个上游不可用」放大成「整个系统不可用」——比不熔断更糟。

**不按 tenant 分区**：上游可用性是上游的属性，不是租户的属性。按租户分区会让
「A 租户先失败」只影响 A，但 B 仍然会重复发现同一个上游故障——熔断失去意义。
租户级隔离由 capacity/stream 配额负责（C2/C4 已交付）。

## 为什么不用 Redis

熔断状态是**进程内**的：它是对「本进程观察到的上游健康」的快速判断。
跨实例共享会引入 Redis 依赖（而 Redis 本身可能故障），且各实例的网络路径可能
不同（Tailscale DERP 就如此）。跨实例的**持久**信号由 egress 审计承担。

## 与重试预算的关系

熔断器在**发送前**拒绝，因此不消耗重试预算；它减少的是「明知会失败还发出去」
的无效出站。两者互补：预算限制单 run 的尝试数，熔断限制全系统对已知坏上游的尝试。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

#: 熔断状态。
CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitSnapshot:
    """熔断状态快照（供诊断，不含凭据或请求内容）。"""

    key: str
    state: str
    consecutive_failures: int
    opened_at: float | None
    cooldown_seconds: float
    half_open_in_flight: int


@dataclass
class _Circuit:
    state: str = CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None
    half_open_in_flight: int = 0
    #: 连续成功次数达到该值才从 half_open 回到 closed。要求多次成功（而非一次）
    #: 是为了避免「上游抖动一次成功就恢复」——那会让熔断反复开合。
    half_open_successes: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class ProviderCircuitBreaker:
    """按 `upstream × kind` 分区的熔断器。

    线程安全：状态由每个 circuit 自己的锁保护（粒度为单个上游，避免一个慢上游
    阻塞其他上游的状态更新）。
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        half_open_probes: int = 1,
        half_open_successes_required: int = 2,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be > 0")
        if half_open_probes < 1:
            raise ValueError("half_open_probes must be >= 1")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_probes = half_open_probes
        self.half_open_successes_required = half_open_successes_required
        self._circuits: dict[str, _Circuit] = {}
        self._registry_lock = threading.Lock()

    # ---- 内部 ---------------------------------------------------------

    def _circuit(self, key: str) -> _Circuit:
        with self._registry_lock:
            circuit = self._circuits.get(key)
            if circuit is None:
                circuit = _Circuit()
                self._circuits[key] = circuit
            return circuit

    # ---- 公开 API -----------------------------------------------------

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """是否允许把请求发往该上游。

        - `closed`：允许；
        - `open` 且冷却未到：**拒绝**（这是本模块的主要价值：不发明知会失败的请求）；
        - `open` 且冷却已到：转入 `half_open`，放有界探针；
        - `half_open`：仅当在途探针数 < `half_open_probes` 时允许。

        返回 False 时调用方应立刻返回**可重试**错误，且**不得**消耗上游尝试。
        """
        moment = now if now is not None else time.monotonic()
        circuit = self._circuit(key)
        with circuit.lock:
            if circuit.state == CLOSED:
                return True
            if circuit.state == OPEN:
                opened = circuit.opened_at or 0.0
                if moment - opened < self.cooldown_seconds:
                    return False
                # 冷却结束：转入 half_open，只放有界探针
                circuit.state = HALF_OPEN
                circuit.half_open_in_flight = 0
                circuit.half_open_successes = 0
            # half_open：限制并发探针
            if circuit.half_open_in_flight >= self.half_open_probes:
                return False
            circuit.half_open_in_flight += 1
            return True

    def record_success(self, key: str, *, now: float | None = None) -> None:
        """记录一次成功：half_open 下累计成功次数，达标即关闭。"""
        circuit = self._circuit(key)
        with circuit.lock:
            if circuit.state == HALF_OPEN:
                circuit.half_open_in_flight = max(0, circuit.half_open_in_flight - 1)
                circuit.half_open_successes += 1
                if circuit.half_open_successes >= self.half_open_successes_required:
                    circuit.state = CLOSED
                    circuit.consecutive_failures = 0
                    circuit.opened_at = None
                    circuit.half_open_successes = 0
                return
            circuit.consecutive_failures = 0

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        """记录一次失败：closed 下累计到阈值即打开；half_open 下立即重新打开。"""
        moment = now if now is not None else time.monotonic()
        circuit = self._circuit(key)
        with circuit.lock:
            if circuit.state == HALF_OPEN:
                # 探针失败：立刻回到 open 并重新计时，不继续放探针。
                circuit.half_open_in_flight = max(0, circuit.half_open_in_flight - 1)
                circuit.state = OPEN
                circuit.opened_at = moment
                circuit.half_open_successes = 0
                return
            circuit.consecutive_failures += 1
            if circuit.consecutive_failures >= self.failure_threshold:
                circuit.state = OPEN
                circuit.opened_at = moment

    def snapshot(self, key: str) -> CircuitSnapshot:
        circuit = self._circuit(key)
        with circuit.lock:
            return CircuitSnapshot(
                key=key,
                state=circuit.state,
                consecutive_failures=circuit.consecutive_failures,
                opened_at=circuit.opened_at,
                cooldown_seconds=self.cooldown_seconds,
                half_open_in_flight=circuit.half_open_in_flight,
            )

    def reset(self) -> None:
        """清空全部状态（测试与运维用）。"""
        with self._registry_lock:
            self._circuits.clear()


def circuit_key(*, provider_id: int, kind: str) -> str:
    """熔断分区键：`upstream × kind`。

    **不含 tenant**：上游可用性是上游的属性。按租户分区会让每个租户各自重复
    发现同一个上游故障，熔断失去意义。租户隔离由 capacity/stream 配额负责。
    """
    return f"provider:{provider_id}:kind:{kind}"


#: 进程级单例。熔断状态是「本进程观察到的上游健康」，跨实例共享会引入 Redis
#: 依赖（而 Redis 本身可能故障），且各实例网络路径可能不同（DERP 即如此）。
_breaker: ProviderCircuitBreaker | None = None
_breaker_lock = threading.Lock()


def breaker() -> ProviderCircuitBreaker:
    global _breaker
    if _breaker is None:
        with _breaker_lock:
            if _breaker is None:
                import os

                _breaker = ProviderCircuitBreaker(
                    failure_threshold=int(os.environ.get("AGENT_CIRCUIT_FAILURE_THRESHOLD", "5")),
                    cooldown_seconds=float(os.environ.get("AGENT_CIRCUIT_COOLDOWN_SECONDS", "30")),
                    half_open_probes=int(os.environ.get("AGENT_CIRCUIT_HALF_OPEN_PROBES", "1")),
                )
    return _breaker


def reset_breaker_for_tests() -> None:
    global _breaker
    with _breaker_lock:
        _breaker = None
