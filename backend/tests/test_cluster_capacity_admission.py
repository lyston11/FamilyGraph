"""C3/AC-4：集群级执行名额（跨实例配额）。

## 为什么需要这一层

`ResourceLimiter` 的 `active` 是**进程内**状态。两个实例各自
`AGENT_EXECUTION_GLOBAL_CAPACITY=2` 时，全集群实际并发可达 **4**——每个实例都
认为「我只用了 2」。这不是推断：`scripts/migration-proof/cross_instance_capacity.py`
在隔离 PostgreSQL 上实测「进程内 4 / 配置 2」「持久化 counter 2 / 配置 2」。

因此执行平面需要第二层上限，真相在持久化计数行里。

## 分工

- 进程内 limiter：**排队与公平**（唯一能在事件循环上等待、按租户 aging、有界拒绝）；
- 集群级 counter：**跨实例总量**（不排队，满了立刻 False）。

## 本文件验证什么

1. 未登记 counter 时**不限制**（部署未 bootstrap 的行为与改动前一致）；
2. 登记后严格受 `capacity` 约束；
3. 归还后名额可复用；
4. 归还**恰好一次**（重复归还不增加名额）。

并发下的实际约束由 PostgreSQL 探针证明（SQLite 的全库写锁测不出跨实例行为）。
"""

from __future__ import annotations

# CHECK 约束违规在各方言下都表现为 IntegrityError 的子类；用具体类型而非
# `Exception`，否则断言会被任何错误满足（包括拼错列名）。
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.models.agent import AgentCapacityCounter
from app.services import capacity


def _register(db_session, resource_kind: str, capacity_value: int) -> None:
    for spec in capacity.cluster_specs(resource_kind):
        capacity.ensure_counter(db_session, spec, capacity=capacity_value)
    db_session.commit()


def test_unregistered_cluster_resource_does_not_limit(db_session):
    """未 bootstrap 时返回 None（不限制），保证未启用集群层的部署行为不变。"""
    assert (
        capacity.try_acquire_cluster(db_session, resource_kind=capacity.RESOURCE_CLUSTER_PROVIDER)
        is None
    )
    assert (
        capacity.try_acquire_cluster(db_session, resource_kind=capacity.RESOURCE_CLUSTER_PROVIDER)
        is None
    )


def test_registered_cluster_resource_is_bounded(db_session):
    """登记后严格受 capacity 约束，且失败不留下增量。"""
    kind = capacity.RESOURCE_CLUSTER_PROVIDER
    _register(db_session, kind, 2)

    assert capacity.try_acquire_cluster(db_session, resource_kind=kind) is True
    assert capacity.try_acquire_cluster(db_session, resource_kind=kind) is True
    assert capacity.try_acquire_cluster(db_session, resource_kind=kind) is False
    db_session.commit()

    row = db_session.get(AgentCapacityCounter, ("global", 0, kind))
    assert row is not None
    assert row.active == 2, "失败的占用留下了增量"


def test_cluster_release_reuses_capacity_and_is_idempotent(db_session):
    """归还后可复用；重复归还不增加名额。"""
    kind = capacity.RESOURCE_CLUSTER_TOOL
    _register(db_session, kind, 1)

    assert capacity.try_acquire_cluster(db_session, resource_kind=kind) is True
    assert capacity.try_acquire_cluster(db_session, resource_kind=kind) is False

    assert capacity.release_cluster(db_session, resource_kind=kind) == 1
    assert capacity.release_cluster(db_session, resource_kind=kind) == 0, "重复归还使名额凭空增加"
    db_session.commit()

    row = db_session.get(AgentCapacityCounter, ("global", 0, kind))
    assert row is not None and row.active == 0
    assert capacity.try_acquire_cluster(db_session, resource_kind=kind) is True


def test_cluster_resources_are_independent(db_session):
    """provider 与 tool 的集群名额互不影响。

    两者的时间尺度差几个数量级（长流 vs 毫秒级工具），共用一个额度会让长流
    挡住同租户的工具调用——这是设计里明确分开的原因。
    """
    _register(db_session, capacity.RESOURCE_CLUSTER_PROVIDER, 1)
    _register(db_session, capacity.RESOURCE_CLUSTER_TOOL, 1)

    assert (
        capacity.try_acquire_cluster(db_session, resource_kind=capacity.RESOURCE_CLUSTER_PROVIDER)
        is True
    )
    # provider 满，但 tool 仍可占
    assert (
        capacity.try_acquire_cluster(db_session, resource_kind=capacity.RESOURCE_CLUSTER_TOOL)
        is True
    )
    assert (
        capacity.try_acquire_cluster(db_session, resource_kind=capacity.RESOURCE_CLUSTER_PROVIDER)
        is False
    )


def test_cluster_check_constraint_is_the_backstop(db_session):
    """CHECK (active <= capacity) 是数据库侧兜底：应用逻辑有 bug 时也不超额。"""
    kind = capacity.RESOURCE_CLUSTER_PROVIDER
    _register(db_session, kind, 1)
    db_session.commit()
    import pytest
    from sqlalchemy import text

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "UPDATE agent_capacity_counters SET active = 5"
                " WHERE scope_kind='global' AND scope_id=0 AND resource_kind=:k"
            ),
            {"k": kind},
        )
        db_session.commit()
    db_session.rollback()
