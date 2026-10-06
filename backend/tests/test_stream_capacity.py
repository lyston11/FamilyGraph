"""C4：Provider 流级名额（三层维度）。

## 为什么与建连名额分开

建连名额在流开始前归还，所以一个租户可以同时持有多个**已建立**的长流；而一个
100 秒的流既不占工作线程也不占连接（由
`test_stream_does_not_pin_a_pool_connection_between_chunks` 锁定）。因此没有任何
既有名额能限制「同时有多少个上游流在跑」——本层是唯一的那道约束。

维度：`global → agent_kind → tenant`（Assistant=account，Steward=space）。
kind 维度防止 Steward 批量计算把 Assistant 交互请求挤掉。
"""

from __future__ import annotations

from app.models.agent import AgentCapacityCounter
from app.services import capacity


def _register(
    db_session, *, tenant_kind: str, tenant_id: int, global_cap: int, kind_cap: int, tenant_cap: int
) -> None:
    specs = capacity.stream_specs(
        tenant_kind=tenant_kind, tenant_id=tenant_id, capacity_tenant=tenant_cap
    )
    caps = [global_cap, kind_cap, tenant_cap]
    for spec, cap in zip(specs, caps, strict=True):
        capacity.ensure_counter(db_session, spec, capacity=cap)
    db_session.commit()


def _acquire(db_session, *, tenant_kind: str, tenant_id: int) -> bool | None:
    return capacity.try_acquire_stream(
        db_session,
        tenant_kind=tenant_kind,
        tenant_id=tenant_id,
        capacity_tenant=2,
    )


def test_unregistered_stream_resource_does_not_limit(db_session):
    """未 bootstrap 时不限制：未启用流级配额的部署行为与改动前一致。"""
    assert _acquire(db_session, tenant_kind="account", tenant_id=1) is None


def test_tenant_dimension_is_enforced(db_session):
    """单租户流数受 tenant 上限约束（这是最常触发的维度）。"""
    _register(
        db_session, tenant_kind="account", tenant_id=5, global_cap=16, kind_cap=12, tenant_cap=2
    )
    assert _acquire(db_session, tenant_kind="account", tenant_id=5) is True
    assert _acquire(db_session, tenant_kind="account", tenant_id=5) is True
    assert _acquire(db_session, tenant_kind="account", tenant_id=5) is False


def test_assistant_and_steward_have_separate_kind_buckets(db_session):
    """kind 分桶：Steward 用满自己的额度不影响 Assistant。

    两者时间尺度不同（Steward 批量计算 vs Assistant 交互），共用一个额度会让
    一个空间的批量作业把所有用户的对话请求挡在门外。
    """
    _register(
        db_session, tenant_kind="account", tenant_id=1, global_cap=16, kind_cap=1, tenant_cap=1
    )
    _register(db_session, tenant_kind="space", tenant_id=2, global_cap=16, kind_cap=1, tenant_cap=1)

    assert _acquire(db_session, tenant_kind="account", tenant_id=1) is True
    # assistant 的 kind 额度已满
    assert _acquire(db_session, tenant_kind="account", tenant_id=1) is False
    # steward 仍有自己的额度
    assert _acquire(db_session, tenant_kind="space", tenant_id=2) is True


def test_global_dimension_is_the_ceiling(db_session):
    """global 维度是总上限：单租户未满但全局已满时仍拒绝。"""
    _register(
        db_session, tenant_kind="account", tenant_id=1, global_cap=1, kind_cap=12, tenant_cap=2
    )
    _register(db_session, tenant_kind="space", tenant_id=9, global_cap=1, kind_cap=12, tenant_cap=2)
    assert _acquire(db_session, tenant_kind="account", tenant_id=1) is True
    # 另一个租户：自身额度充足，但全局只剩 0
    assert _acquire(db_session, tenant_kind="space", tenant_id=9) is False


def test_stream_release_reuses_capacity(db_session):
    """归还后可复用；重复归还不增加名额。"""
    _register(
        db_session, tenant_kind="account", tenant_id=3, global_cap=16, kind_cap=12, tenant_cap=1
    )
    assert _acquire(db_session, tenant_kind="account", tenant_id=3) is True
    assert _acquire(db_session, tenant_kind="account", tenant_id=3) is False

    assert capacity.release_stream(db_session, tenant_kind="account", tenant_id=3) > 0
    assert capacity.release_stream(db_session, tenant_kind="account", tenant_id=3) == 0
    db_session.commit()
    assert _acquire(db_session, tenant_kind="account", tenant_id=3) is True


def test_all_three_dimensions_are_decremented_together(db_session):
    """归还必须同时释放三层维度，否则名额会永久泄漏在其中一层。"""
    _register(
        db_session, tenant_kind="space", tenant_id=4, global_cap=16, kind_cap=12, tenant_cap=1
    )
    _acquire(db_session, tenant_kind="space", tenant_id=4)
    db_session.commit()
    for spec in capacity.stream_specs(tenant_kind="space", tenant_id=4, capacity_tenant=0):
        row = db_session.get(
            AgentCapacityCounter, (spec.scope_kind, spec.scope_id, spec.resource_kind)
        )
        assert row is not None and row.active == 1, f"{spec.scope_kind} 未占用"

    capacity.release_stream(db_session, tenant_kind="space", tenant_id=4)
    db_session.commit()
    for spec in capacity.stream_specs(tenant_kind="space", tenant_id=4, capacity_tenant=0):
        row = db_session.get(
            AgentCapacityCounter, (spec.scope_kind, spec.scope_id, spec.resource_kind)
        )
        assert row is not None and row.active == 0, f"{spec.scope_kind} 未归还"
