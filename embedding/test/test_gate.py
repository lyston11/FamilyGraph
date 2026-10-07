"""Embedding 闸门与配置的测试（纯标准库，无需模型）。

## 本文件守护的是**资源上界**，不是检索质量

检索质量由隔离环境上的基准证明。这里证明的是「embedding 不会拖垮这台机器」：

1. **并发上界真的成立**（`max_observed_active <= max_concurrent`）；
2. **名额在推理结束后才释放**——最容易被写错的一点，见下；
3. 队列有界，满了立即拒绝（背压）；
4. 等待超时是有界的；
5. 推理抛异常也释放名额（否则服务变成「永久繁忙」）；
6. 在线优先于后台。

## 为什么第 2 条需要专门的用例

一个看似合理但**错误**的实现：

```python
async with sem:
    await asyncio.wait_for(to_thread(infer), timeout=T)
```

超时后 `sem` 已释放，但线程里的推理**还在跑**。于是「并发 1」实际变成
「并发无限」，只表现为部分请求超时。多租户并发下这会把 4 核机器占满。

`InferenceGate.slot()` 的设计是「占用 / 推理 / 释放在同一线程内顺序完成」，
`wait_timeout` 只约束**等待名额**。下面的用例用「推理期间并发计数」直接验证这一点。
"""

from __future__ import annotations

import threading
import time

import pytest

from app.config import EmbeddingConfig, from_env
from app.gate import (
    PRIORITY_BACKGROUND,
    PRIORITY_ONLINE,
    InferenceGate,
    QueueFull,
    SlotTimeout,
)


def _gate(**overrides) -> InferenceGate:
    kwargs = {"max_concurrent": 1, "max_waiting": 2, "wait_timeout_seconds": 0.3}
    kwargs.update(overrides)
    return InferenceGate(**kwargs)


# ---------------------------------------------------------------- 并发上界


def test_concurrent_inference_never_exceeds_the_limit():
    """并发上界：无论多少线程，同时在推理的份数不超过 max_concurrent。

    这是本服务存在的核心目的——这台 4 核机器同时跑生产与开发。
    """
    gate = _gate(max_concurrent=1, max_waiting=64, wait_timeout_seconds=10.0)
    inside = 0
    peak = 0
    lock = threading.Lock()
    errors: list[str] = []

    def worker() -> None:
        nonlocal inside, peak
        try:
            with gate.slot():
                with lock:
                    inside += 1
                    peak = max(peak, inside)
                time.sleep(0.01)  # 模拟推理耗时
                with lock:
                    inside -= 1
        except (QueueFull, SlotTimeout) as exc:
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert peak <= 1, f"观测到并发 {peak}，超过配置的 1"
    assert gate.metrics().max_observed_active <= 1


def test_slot_is_held_for_the_whole_inference_not_just_the_wait():
    """名额覆盖整个推理过程，而不是「等待阶段」。

    ## 反例（本用例要抓的实现错误）

    ```python
    # 超时即释放名额，推理仍在后台跑 -> 并发失控
    async with sem:
        await asyncio.wait_for(to_thread(infer), timeout=T)
    ```

    本用例让推理耗时明显长于 `wait_timeout`。若实现是「超时释放名额」，第二个
    请求会在第一个推理**还没结束**时进入，`peak` 就会变成 2。

    正确实现下，第二个请求要么等到推理结束（拿到名额），要么因等待超时被拒绝——
    但**不会**在第一个推理进行中被放进临界区。
    """
    gate = _gate(max_concurrent=1, max_waiting=8, wait_timeout_seconds=0.05)
    inside = 0
    peak = 0
    lock = threading.Lock()

    def worker(inference_seconds: float) -> None:
        nonlocal inside, peak
        try:
            with gate.slot():
                with lock:
                    inside += 1
                    peak = max(peak, inside)
                # 推理耗时远超 wait_timeout（0.05s）
                time.sleep(inference_seconds)
                with lock:
                    inside -= 1
        except (QueueFull, SlotTimeout):
            return

    first = threading.Thread(target=worker, args=(0.5,))
    first.start()
    time.sleep(0.05)  # 确保第一个已在推理中
    others = [threading.Thread(target=worker, args=(0.01,)) for _ in range(5)]
    for t in others:
        t.start()
    for t in others:
        t.join(timeout=30)
    first.join(timeout=30)

    assert peak == 1, (
        f"观测到并发 {peak}：名额在推理结束前被释放（超时释放名额的经典错误）"
    )


def test_max_observed_active_is_recorded_as_evidence():
    """`max_observed_active` 是并发上界的**直接证据**，必须被记录。"""
    gate = _gate(max_concurrent=2, max_waiting=16, wait_timeout_seconds=5.0)

    def worker() -> None:
        with gate.slot():
            time.sleep(0.02)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    metrics = gate.metrics()
    assert metrics.max_observed_active <= 2
    assert metrics.completed == 6
    assert metrics.last_duration_ms is not None


# ---------------------------------------------------------------- 背压


def test_queue_is_bounded_and_rejects_immediately():
    """队列满 → 立即拒绝（背压），不是无限排队。

    无界队列会把「资源不足」变成「内存增长 + 全部请求超时」，且故障期间队列越长、
    恢复越慢。
    """
    gate = _gate(max_concurrent=1, max_waiting=1, wait_timeout_seconds=5.0)
    release = threading.Event()
    entered = threading.Event()

    def holder() -> None:
        with gate.slot():
            entered.set()
            release.wait(timeout=10)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(timeout=5), "占用者未进入"

    # 第一个等待者填满队列
    waiting_done = threading.Event()

    def waiter() -> None:
        try:
            with gate.slot():
                pass
        except (QueueFull, SlotTimeout):
            pass
        finally:
            waiting_done.set()

    threading.Thread(target=waiter).start()
    time.sleep(0.1)  # 让它进入队列

    with pytest.raises(QueueFull):
        with gate.slot():
            pass

    assert gate.metrics().rejected_queue_full >= 1
    release.set()
    thread.join(timeout=10)
    waiting_done.wait(timeout=10)


def test_wait_timeout_is_bounded():
    """等待超时必须发生在有界时间内，不能无限等待。"""
    gate = _gate(max_concurrent=1, max_waiting=8, wait_timeout_seconds=0.2)
    release = threading.Event()
    entered = threading.Event()

    def holder() -> None:
        with gate.slot():
            entered.set()
            release.wait(timeout=10)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(timeout=5)

    started = time.monotonic()
    with pytest.raises(SlotTimeout):
        with gate.slot():
            pass
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"等待了 {elapsed:.2f}s，远超配置的 0.2s"
    assert gate.metrics().rejected_timeout >= 1
    release.set()
    thread.join(timeout=10)


def test_slot_is_released_even_when_inference_raises():
    """推理抛异常也必须释放名额。

    否则一次失败会永久占用名额，服务变成「永久繁忙」——表现为全部请求 429/504，
    而健康检查却显示正常。
    """
    gate = _gate(max_concurrent=1, max_waiting=4, wait_timeout_seconds=1.0)

    with pytest.raises(RuntimeError):
        with gate.slot():
            raise RuntimeError("模拟推理失败")

    # 名额已释放：后续请求可正常进入
    with gate.slot():
        pass
    assert gate.metrics().completed == 2


def test_slot_is_released_when_caller_is_cancelled_after_acquire():
    """取得名额后调用方抛任意异常，名额仍释放。"""
    gate = _gate(max_concurrent=1, max_waiting=4, wait_timeout_seconds=1.0)
    try:
        with gate.slot():
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    with gate.slot():
        pass
    assert gate.metrics().active == 0


# ---------------------------------------------------------------- 优先级


def test_online_is_served_before_background():
    """在线查询优先于后台索引。

    后台索引是**可延迟**工作，在线查询有用户在等。不加区分时，一次全库重建会让
    所有在线查询排队到超时。
    """
    gate = _gate(max_concurrent=1, max_waiting=8, wait_timeout_seconds=5.0)
    order: list[str] = []
    lock = threading.Lock()
    release = threading.Event()
    holder_entered = threading.Event()

    def holder() -> None:
        with gate.slot():
            holder_entered.set()
            release.wait(timeout=10)

    thread = threading.Thread(target=holder)
    thread.start()
    assert holder_entered.wait(timeout=5)

    def waiter(name: str, priority: int) -> None:
        try:
            with gate.slot(priority=priority):
                with lock:
                    order.append(name)
        except (QueueFull, SlotTimeout):
            pass

    # 先提交后台，再提交在线；在线的应被优先服务。
    background = threading.Thread(target=waiter, args=("background", PRIORITY_BACKGROUND))
    background.start()
    time.sleep(0.1)
    online = threading.Thread(target=waiter, args=("online", PRIORITY_ONLINE))
    online.start()
    time.sleep(0.1)

    release.set()
    online.join(timeout=10)
    background.join(timeout=10)

    assert order and order[0] == "online", f"服务顺序错误：{order}"


def test_unknown_priority_is_rejected():
    """未知优先级 fail-loud，不静默当成某一级。"""
    gate = _gate()
    with pytest.raises(ValueError):
        with gate.slot(priority=99):
            pass


# ---------------------------------------------------------------- 配置校验


def _config(**overrides) -> EmbeddingConfig:
    base = {
        "model_dir": "/models/x",
        "model_name": "x",
        "dimension": 512,
        "max_tokens": 512,
        "query_instruction": "",
        "max_concurrent": 1,
        "max_batch": 4,
        "max_waiting": 8,
        "wait_timeout_seconds": 5.0,
        "max_texts_per_request": 16,
        "max_chars_per_text": 8000,
        "threads": 1,
    }
    base.update(overrides)
    return EmbeddingConfig(**base)


def test_valid_config_passes():
    _config().validate()


@pytest.mark.parametrize(
    "overrides",
    [
        {"dimension": 0},
        {"max_tokens": 0},
        {"max_concurrent": 0},
        {"max_batch": 0},
        {"max_waiting": -1},
        {"wait_timeout_seconds": 0},
        {"max_texts_per_request": 0},
        {"max_chars_per_text": 0},
        {"threads": 0},
    ],
)
def test_invalid_config_is_rejected(overrides):
    """配置错误必须启动即拒绝。

    否则运行期表现为「永久拒绝」或「永久等待」，运维只能看到 503，无法区分是
    资源不足还是配置错误。
    """
    with pytest.raises(ValueError):
        _config(**overrides).validate()


def test_gate_rejects_invalid_construction():
    with pytest.raises(ValueError):
        InferenceGate(max_concurrent=0, max_waiting=1, wait_timeout_seconds=1)
    with pytest.raises(ValueError):
        InferenceGate(max_concurrent=1, max_waiting=-1, wait_timeout_seconds=1)
    with pytest.raises(ValueError):
        InferenceGate(max_concurrent=1, max_waiting=1, wait_timeout_seconds=0)


def test_from_env_defaults_match_the_agreed_budget(monkeypatch):
    """默认值必须与约定的预算一致：并发 1、队列 8、线程 1。

    这些默认值是「不会拖垮主服务」的起点，改动需要理由。
    """
    for key in list(__import__("os").environ):
        if key.startswith("EMBEDDING_"):
            monkeypatch.delenv(key, raising=False)
    config = from_env()
    assert config.max_concurrent == 1
    assert config.max_waiting == 8
    assert config.threads == 1
    assert config.dimension == 512
    assert config.max_tokens == 512
    assert config.model_name == "bge-small-zh-v1.5"


def test_public_summary_does_not_leak_paths():
    """`/readyz` 可能被运维或监控读取，不得泄露部署路径。"""
    summary = _config().public_summary()
    blob = repr(summary)
    assert "/models" not in blob
    assert "model_dir" not in blob
