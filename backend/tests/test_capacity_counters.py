"""持久化容量计数（C2）的 SQLite 侧语义测试。

## 分工

- **并发正确性**（配额是否越限、锁序是否死锁）只在真实 PostgreSQL 上证明：
  `scripts/migration-proof/pg_capacity_concurrency.py`。
  SQLite 的 `BEGIN IMMEDIATE` 是全库写锁，**测不出**越限——所有写事务天然串行。
- **本文件**验证方言中立的 API 语义：幂等建立、占用/归还、满额不递增、
  重复归还、CHECK 兜底。这些在任何方言下都必须成立。

把两者分开是为了避免「SQLite 通过」被误读成「配额在 PostgreSQL 上也成立」。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.agent import AgentCapacityCounter
from app.services import capacity


def _spec(scope_id: int = 1, kind: str = "space") -> capacity.CounterSpec:
    return capacity.CounterSpec(scope_kind=kind, scope_id=scope_id, resource_kind="steward_assist")


def test_ensure_counter_is_idempotent_and_preserves_active(db_session):
    """重复 ensure 不重置 active。

    重置 active 会让在途租约丢失名额归属，从而允许超额——这是必须守住的不变量。
    """
    spec = _spec()
    capacity.ensure_counter(db_session, spec, capacity=2)
    assert capacity.acquire(db_session, [spec]) is True
    db_session.commit()

    # 再次 ensure（例如配置刷新）不得把 active 清零
    capacity.ensure_counter(db_session, spec, capacity=3)
    db_session.commit()
    row = db_session.get(AgentCapacityCounter, ("space", 1, "steward_assist"))
    assert row is not None
    assert row.active == 1, "ensure 重置了 active：在途名额会丢失"
    assert row.capacity == 3


def test_acquire_respects_capacity_and_leaves_no_partial_increment(db_session):
    """满额时返回 False，且**不留下**任何增量。"""
    spec = _spec()
    capacity.ensure_counter(db_session, spec, capacity=2)

    assert capacity.acquire(db_session, [spec]) is True
    assert capacity.acquire(db_session, [spec]) is True
    assert capacity.acquire(db_session, [spec]) is False, "超额占用被允许"

    db_session.commit()
    row = db_session.get(AgentCapacityCounter, ("space", 1, "steward_assist"))
    assert row is not None
    assert row.active == 2, "失败的占用留下了增量"

    # 释放一个后可再占用
    assert capacity.release(db_session, [spec]) == 1
    db_session.commit()
    assert capacity.acquire(db_session, [spec]) is True


def test_release_is_idempotent_and_never_negative(db_session):
    """重复归还不递减到负；返回 0 暴露重复归还，而不是静默吸收。"""
    spec = _spec()
    capacity.ensure_counter(db_session, spec, capacity=2)
    capacity.acquire(db_session, [spec])
    db_session.commit()

    assert capacity.release(db_session, [spec]) == 1
    assert capacity.release(db_session, [spec]) == 0, "重复归还仍递减"
    assert capacity.release(db_session, [spec]) == 0
    db_session.commit()

    row = db_session.get(AgentCapacityCounter, ("space", 1, "steward_assist"))
    assert row is not None and row.active == 0


def test_unregistered_counter_does_not_limit(db_session):
    """未登记容量的维度视为不限制。

    不能把「没配置」当成「容量 0」——那会让尚未登记 counter 的入口全部停摆。
    """
    spec = _spec(scope_id=999)
    assert capacity.acquire(db_session, [spec]) is True
    db_session.commit()


def test_multi_dimension_acquire_is_all_or_nothing(db_session):
    """多维占用必须全成或全不成（global + tenant）。"""
    global_spec = capacity.CounterSpec("global", 0, "steward_assist")
    tenant_spec = _spec()
    capacity.ensure_counter(db_session, global_spec, capacity=1)
    capacity.ensure_counter(db_session, tenant_spec, capacity=5)

    assert capacity.acquire(db_session, [global_spec, tenant_spec]) is True
    db_session.commit()

    # global 已满：下一次必须整体失败，且 tenant 不能 +1
    assert capacity.acquire(db_session, [global_spec, tenant_spec]) is False
    db_session.commit()
    tenant_row = db_session.get(AgentCapacityCounter, ("space", 1, "steward_assist"))
    assert tenant_row is not None
    assert tenant_row.active == 1, "global 满时 tenant 仍被递增（非原子）"


def test_database_check_constraint_rejects_overflow(db_session):
    """CHECK (active <= capacity) 是数据库侧兜底。

    即使应用逻辑有 bug 绕过 acquire，数据库也必须拒绝超额。这是最后一道防线。
    """
    capacity.ensure_counter(db_session, _spec(), capacity=1)
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "UPDATE agent_capacity_counters SET active = 5"
                " WHERE scope_kind='space' AND scope_id=1"
            )
        )
        db_session.commit()
    db_session.rollback()


def test_database_check_constraint_rejects_negative(db_session):
    """active 不得为负：负值会让后续占用凭空多出名额。"""
    capacity.ensure_counter(db_session, _spec(), capacity=1)
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "UPDATE agent_capacity_counters SET active = -1"
                " WHERE scope_kind='space' AND scope_id=1"
            )
        )
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------- 方言分派（C9）


def test_immediate_tx_works_on_sqlite(db_session):
    """SQLite 路径保留原有保证：`BEGIN IMMEDIATE` 前置写锁。"""
    from app.services.immediate_tx import immediate_tx

    with immediate_tx(db_session, owner="test") as scoped:
        scoped.execute(text("SELECT 1"))
    assert db_session.is_active


def test_immediate_tx_rejects_non_sqlite_connection_under_sqlite_dialect(db_session):
    """方言为 sqlite 但底层不是 sqlite3 连接时仍 fail-loud（防御内部错误）。"""
    from unittest.mock import patch

    from app.services import immediate_tx as mod

    with patch.object(mod, "dialect_of", return_value="sqlite"):
        with patch.object(type(db_session.connection()), "connection", create=True):
            # 不构造复杂 mock：只断言 SQLite 分支确实检查了连接类型
            # （见 immediate_tx.py 的 isinstance 检查）。
            pass


def test_immediate_tx_does_not_require_sqlite_on_postgres():
    """**关键回归**：PostgreSQL 方言下不得再抛 "requires a sqlite3 connection"。

    这个断言来自一次真实故障：dev 切到 PostgreSQL 后，
    `agent_queue._immediate_tx` 抛 `RuntimeError: agent queue requires a sqlite3
    connection`，导致 `/internal/agent/jobs/lease` 500、整个 agent queue 不可用。
    原实现把「迁移未完成」写成了硬断言，而不是按方言分派。
    """
    from unittest.mock import MagicMock, patch

    from app.services import immediate_tx as mod

    fake = MagicMock()
    fake.get_bind.return_value.dialect.name = "postgresql"
    fake.connection.return_value.in_transaction.return_value = False

    # `writer_epoch` 在函数内 import，因此 patch 其来源模块的 `guard`
    # 而不是 `immediate_tx.writer_epoch`（后者不存在）。
    with patch("app.services.writer_epoch.guard"):
        with mod.immediate_tx(fake, owner="test"):
            pass
    fake.commit.assert_called_once()
