"""C4：Provider 上游熔断器。

## 为什么需要

上游整体故障时（DERP 丢路由、provider 挂掉），当前表现是**每个 run 各自失败**：
每个租户的每个 run 完整走一遍 connect → 超时 → 重试预算耗尽。实测最坏形态是
18.8 小时持续失败，每次 run 耗尽 24 次出站尝试。

熔断把「上游整体不可用」变成**发送前快速拒绝**：不发明知会失败的请求。

## 分区是本模块的关键设计

熔断按 `upstream × kind` 分区，**不按 tenant**：

- 一个 provider profile 挂掉不能熔断另一个（不同上游、不同网络路径）；
- Assistant 与 Steward 可能走不同设置，不应互相牵连；
- **不按租户**：上游可用性是上游的属性。按租户分区会让每个租户各自重复发现
  同一个上游故障，熔断失去意义。租户隔离由 capacity/stream 配额负责。
"""

from __future__ import annotations

from app.services.provider_circuit import (
    CLOSED,
    HALF_OPEN,
    OPEN,
    ProviderCircuitBreaker,
    circuit_key,
)


def _breaker(**overrides) -> ProviderCircuitBreaker:
    kwargs = {
        "failure_threshold": 3,
        "cooldown_seconds": 10.0,
        "half_open_probes": 1,
        "half_open_successes_required": 2,
    }
    kwargs.update(overrides)
    return ProviderCircuitBreaker(**kwargs)


def test_closed_allows_requests():
    b = _breaker()
    assert b.allow("k") is True
    assert b.snapshot("k").state == CLOSED


def test_opens_after_threshold_failures():
    b = _breaker()
    for _ in range(3):
        b.allow("k")
        b.record_failure("k")
    assert b.snapshot("k").state == OPEN
    assert b.allow("k") is False, "打开后仍放行——熔断没有生效"


def test_success_resets_failure_count_while_closed():
    """closed 下的偶发失败不应累积到阈值：成功必须清零。"""
    b = _breaker()
    b.record_failure("k")
    b.record_failure("k")
    b.record_success("k")
    b.record_failure("k")
    b.record_failure("k")
    assert b.snapshot("k").state == CLOSED, "成功未清零失败计数"
    assert b.snapshot("k").consecutive_failures == 2


def test_open_blocks_until_cooldown_then_half_opens():
    """冷却期内拒绝；冷却后转入 half_open 并放有界探针。"""
    b = _breaker(cooldown_seconds=10.0)
    for _ in range(3):
        b.allow("k", now=0.0)
        b.record_failure("k", now=0.0)
    assert b.allow("k", now=5.0) is False, "冷却期内不应放行"
    assert b.allow("k", now=11.0) is True, "冷却结束后应放探针"
    assert b.snapshot("k").state == HALF_OPEN


def test_half_open_limits_concurrent_probes():
    """half_open 只放有界探针：否则上游刚恢复就被打满。"""
    b = _breaker(half_open_probes=1)
    for _ in range(3):
        b.allow("k", now=0.0)
        b.record_failure("k", now=0.0)
    assert b.allow("k", now=11.0) is True  # 第一个探针
    assert b.allow("k", now=11.0) is False, "并发探针未被限制"


def test_half_open_probe_failure_reopens_immediately():
    """探针失败必须立刻回到 open 并重新计时，不继续放探针。"""
    b = _breaker(cooldown_seconds=10.0)
    for _ in range(3):
        b.allow("k", now=0.0)
        b.record_failure("k", now=0.0)
    b.allow("k", now=11.0)
    b.record_failure("k", now=11.0)
    assert b.snapshot("k").state == OPEN
    assert b.allow("k", now=11.5) is False, "探针失败后未重新计时"
    assert b.allow("k", now=22.0) is True


def test_half_open_requires_multiple_successes_before_closing():
    """需要多次成功才恢复：一次成功就关闭会让熔断反复开合。"""
    b = _breaker(half_open_successes_required=2)
    for _ in range(3):
        b.allow("k", now=0.0)
        b.record_failure("k", now=0.0)
    b.allow("k", now=11.0)
    b.record_success("k", now=11.0)
    assert b.snapshot("k").state == HALF_OPEN, "一次成功就关闭"
    b.allow("k", now=11.0)
    b.record_success("k", now=11.0)
    assert b.snapshot("k").state == CLOSED


def test_circuits_are_partitioned_by_upstream_and_kind():
    """分区：一个上游打开不影响另一个；不同 kind 也不互相影响。"""
    b = _breaker()
    a = circuit_key(provider_id=1, kind="assistant")
    c = circuit_key(provider_id=2, kind="assistant")
    d = circuit_key(provider_id=1, kind="steward")

    for _ in range(3):
        b.allow(a)
        b.record_failure(a)
    assert b.snapshot(a).state == OPEN
    assert b.allow(c) is True, "另一 provider 被牵连熔断"
    assert b.allow(d) is True, "另一 kind 被牵连熔断"


def test_circuit_key_excludes_tenant():
    """熔断键**不得**含租户。

    上游可用性是上游的属性：按租户分区会让每个租户各自重复发现同一个上游故障，
    熔断失去意义。租户隔离由 capacity/stream 配额负责。
    """
    key = circuit_key(provider_id=7, kind="steward")
    assert "account" not in key and "space" not in key and "tenant" not in key
    assert key == "provider:7:kind:steward"


def test_invalid_configuration_fails_loud():
    """配置错误必须 fail-loud，不能静默退化成「永不熔断」。"""
    import pytest

    with pytest.raises(ValueError):
        ProviderCircuitBreaker(failure_threshold=0, cooldown_seconds=1)
    with pytest.raises(ValueError):
        ProviderCircuitBreaker(failure_threshold=1, cooldown_seconds=0)
    with pytest.raises(ValueError):
        ProviderCircuitBreaker(failure_threshold=1, cooldown_seconds=1, half_open_probes=0)


def test_snapshot_contains_no_credentials():
    """诊断快照只含状态元数据。"""
    b = _breaker()
    snap = b.snapshot("provider:1:kind:assistant")
    assert set(vars(snap)) == {
        "key",
        "state",
        "consecutive_failures",
        "opened_at",
        "cooldown_seconds",
        "half_open_in_flight",
    }
