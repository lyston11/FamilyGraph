"""C2：attempt 容量归还的「恰好一次」门。

## 为什么需要专门用例

counter 的归还分散在五个路径（写回栅栏退休、失败/未知结算、成功/降级结算、
租约过期恢复、崩溃点④退休）。靠「每个调用点都记得归还」不可证明，因此门放在**行上**：

```text
capacity_acquired_at IS NOT NULL   -> 曾占用名额
capacity_released_at IS NULL       -> 尚未归还
```

本文件证明三件事：

1. 首次归还成功且 `active` 减 1；
2. **重复归还不再递减**（否则名额凭空增加，`CHECK` 会报错或超额）；
3. **未占用过的 attempt 归还是 no-op**（渐进路径：counter 未登记时从未占用）。

## 与并发探针的分工

配额在并发下是否越限由 `scripts/migration-proof/pg_capacity_concurrency.py` 在真实
PostgreSQL 上证明（SQLite 的 `BEGIN IMMEDIATE` 是全库写锁，测不出越限）。
本文件只验证 API 语义，两者不可互相替代。
"""

from __future__ import annotations

from app.models.agent import AgentCapacityCounter
from app.services import capacity
from app.services.steward_assist import _in_flight_for_space  # noqa: F401


def _bootstrap_counter(db_session, space_id: int, capacity_value: int) -> None:
    """登记该空间的 counter（模拟部署时 bootstrap）。"""
    for spec in capacity.steward_assist_specs(space_id=space_id):
        capacity.ensure_counter(db_session, spec, capacity=capacity_value)
    db_session.commit()


def _active(db_session, space_id: int) -> int:
    row = db_session.get(
        AgentCapacityCounter, ("space", space_id, capacity.RESOURCE_STEWARD_ASSIST)
    )
    assert row is not None
    return row.active


class _FakeAttempt:
    """最小 attempt 替身：只需要两个门时间戳。"""

    def __init__(self) -> None:
        self.capacity_acquired_at = None
        self.capacity_released_at = None


def test_release_is_exactly_once(db_session):
    """首次归还递减；第二次调用是 no-op。"""
    _bootstrap_counter(db_session, space_id=77, capacity_value=2)
    attempt = _FakeAttempt()

    assert capacity.try_acquire(db_session, capacity.steward_assist_specs(space_id=77)) is True
    capacity.mark_attempt_acquired(attempt)
    db_session.commit()
    assert _active(db_session, 77) == 1

    assert capacity.release_attempt(db_session, attempt, space_id=77) is True
    db_session.commit()
    assert _active(db_session, 77) == 0

    # 重复归还：门已关闭（released_at 非空），不得再递减
    assert capacity.release_attempt(db_session, attempt, space_id=77) is False
    db_session.commit()
    assert _active(db_session, 77) == 0, "重复归还使名额凭空增加"


def test_release_without_acquire_is_noop(db_session):
    """未占用过的 attempt（渐进路径）归还不递减。

    若这里错误地递减，一个从未占用的 attempt 会把别人的名额释放掉，
    等价于允许超额。
    """
    _bootstrap_counter(db_session, space_id=88, capacity_value=2)
    # 另一个 attempt 占用了名额
    assert capacity.try_acquire(db_session, capacity.steward_assist_specs(space_id=88)) is True
    db_session.commit()
    assert _active(db_session, 88) == 1

    never_acquired = _FakeAttempt()  # acquired_at 仍为 None
    assert capacity.release_attempt(db_session, never_acquired, space_id=88) is False
    db_session.commit()
    assert _active(db_session, 88) == 1, "未占用的 attempt 释放了别人的名额"


def test_release_twice_across_paths_keeps_counter_consistent(db_session):
    """跨路径重复归还（例如栅栏退休后又走恢复）仍只递减一次。"""
    _bootstrap_counter(db_session, space_id=99, capacity_value=3)
    attempt = _FakeAttempt()
    capacity.try_acquire(db_session, capacity.steward_assist_specs(space_id=99))
    capacity.try_acquire(db_session, capacity.steward_assist_specs(space_id=99))
    capacity.mark_attempt_acquired(attempt)
    db_session.commit()
    assert _active(db_session, 99) == 2

    # 路径 ③（写回栅栏退休）
    assert capacity.release_attempt(db_session, attempt, space_id=99) is True
    db_session.commit()
    # 路径 ①（租约过期恢复）对同一行再试
    assert capacity.release_attempt(db_session, attempt, space_id=99) is False
    db_session.commit()
    assert _active(db_session, 99) == 1


def test_acquire_after_release_reuses_capacity(db_session):
    """归还后名额可被再次占用（不是一次性）。"""
    _bootstrap_counter(db_session, space_id=111, capacity_value=1)
    first = _FakeAttempt()
    assert capacity.try_acquire(db_session, capacity.steward_assist_specs(space_id=111)) is True
    capacity.mark_attempt_acquired(first)
    db_session.commit()
    assert capacity.try_acquire(db_session, capacity.steward_assist_specs(space_id=111)) is False
    assert capacity.release_attempt(db_session, first, space_id=111) is True
    db_session.commit()
    assert capacity.try_acquire(db_session, capacity.steward_assist_specs(space_id=111)) is True
