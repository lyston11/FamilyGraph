"""C2：容量归还的「恰好一次」门。

## 为什么需要专门用例

counter 的归还分散在多个路径（assist 的写回栅栏/失败结算/成功结算/租约过期恢复/
崩溃点④，job 的结算/回收）。靠「每个调用点都记得归还」不可证明，因此门放在**行上**：

```text
capacity_acquired_at IS NOT NULL   -> 曾占用名额
capacity_released_at IS NULL       -> 尚未归还
```

本文件证明三件事：

1. 首次归还成功且 `active` 减 1；
2. **重复归还不再递减**（否则名额凭空增加）；
3. **未占用过的行归还是 no-op**（渐进路径：counter 未登记时从未占用）。

## 与并发探针的分工

配额在并发下是否越限由 `scripts/migration-proof/pg_capacity_concurrency.py` 在真实
PostgreSQL 上证明（SQLite 的 `BEGIN IMMEDIATE` 是全库写锁，测不出越限）。
本文件只验证 API 语义，两者不可互相替代。
"""

from __future__ import annotations

from sqlalchemy import text

from app.models.agent import AgentCapacityCounter
from app.services import capacity


def _bootstrap_counter(db_session, space_id: int, capacity_value: int) -> None:
    for spec in capacity.steward_assist_specs(space_id=space_id):
        capacity.ensure_counter(db_session, spec, capacity=capacity_value)
    for spec in capacity.steward_job_specs(space_id=space_id):
        capacity.ensure_counter(db_session, spec, capacity=capacity_value)
    db_session.commit()


def _ensure_space(db_session, space_id: int) -> None:
    """steward_model_calls.space_id 与 steward_jobs.space_id 都有 FK，需先建空间。"""
    # 顺序固定：users 先于 family_spaces（owner_id 是 FK）。
    db_session.execute(
        text(
            "INSERT INTO users (id, name, gender, privacy_mode, profile_status, created_at)"
            " VALUES (1, 'probe-owner', 'unknown', 'perpetual', 'identity_confirmed',"
            " CURRENT_TIMESTAMP)"
            " ON CONFLICT (id) DO NOTHING"
        )
    )
    db_session.flush()
    db_session.execute(
        text(
            "INSERT INTO family_spaces (id, name, owner_id, kind, created_at)"
            " VALUES (:id, :name, 1, 'household', CURRENT_TIMESTAMP)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": space_id, "name": f"probe-space-{space_id}"},
    )
    db_session.flush()


def _seed_job(db_session, row_id: int, space_id: int = 77) -> None:
    _ensure_space(db_session, space_id)
    db_session.execute(
        text(
            "INSERT INTO steward_jobs (id, space_id, cause, trigger_cursor, status,"
            " attempt, max_attempts, policy_version, checkpoint_json, created_at,"
            " updated_at)"
            " VALUES (:id, :space, 'integrity_scan', 1, 'queued', 0, 3, 'p', '{}',"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": row_id, "space": space_id},
    )
    db_session.flush()


def _seed_attempt(db_session, row_id: int, space_id: int = 77) -> None:
    """插入一行只用于门的 attempt 探针行。

    门列不在 ORM 映射里（见 capacity.py 的说明），因此用 Core SQL 写入。
    `job_id` 有 FK，故先建一个同空间的 steward_job。
    """
    _ensure_space(db_session, space_id)
    _seed_job(db_session, 1_000_000 + row_id, space_id=space_id)
    db_session.execute(
        text(
            "INSERT INTO steward_model_calls (id, space_id, job_id, policy_version,"
            " assist_kind, prompt_digest, prompt_chars, completion_chars, status, seq,"
            " created_at, attempt_no, carrier, input_hash, reserved_input_tokens,"
            " reserved_output_tokens)"
            " VALUES (:id, :space, :job, 'p', 'candidate', 'd', 1, 0, 'in_flight', :id,"
            " CURRENT_TIMESTAMP, 1, 'pi', 'h', 1, 1)"
        ),
        {"id": row_id, "space": space_id, "job": 1_000_000 + row_id},
    )
    db_session.flush()


def _active(db_session, space_id: int, resource: str) -> int:
    row = db_session.get(AgentCapacityCounter, ("space", space_id, resource))
    assert row is not None
    return row.active


class _RowRef:
    """最小行引用：门实现只需要 `id`。"""

    def __init__(self, row_id: int) -> None:
        self.id = row_id


def test_attempt_release_is_exactly_once(db_session):
    """首次归还递减；第二次调用是 no-op。"""
    _bootstrap_counter(db_session, space_id=77, capacity_value=2)
    _seed_attempt(db_session, 9001)
    specs = capacity.steward_assist_specs(space_id=77)
    assert capacity.try_acquire(db_session, specs) is True
    capacity.mark_acquired(db_session, table=capacity.GATE_TABLE_ATTEMPT, row_id=9001)
    db_session.commit()
    assert _active(db_session, 77, capacity.RESOURCE_STEWARD_ASSIST) == 1

    ref = _RowRef(9001)
    assert capacity.release_attempt(db_session, ref, space_id=77) is True
    db_session.commit()
    assert _active(db_session, 77, capacity.RESOURCE_STEWARD_ASSIST) == 0

    # 重复归还：门已关闭（released_at 非空），不得再递减
    assert capacity.release_attempt(db_session, ref, space_id=77) is False
    db_session.commit()
    assert _active(db_session, 77, capacity.RESOURCE_STEWARD_ASSIST) == 0, "重复归还使名额凭空增加"


def test_release_without_acquire_is_noop(db_session):
    """未占用过的行归还不递减。

    若这里错误地递减，一个从未占用的行会释放别人的名额，等价于允许超额。
    """
    _bootstrap_counter(db_session, space_id=88, capacity_value=2)
    _seed_attempt(db_session, 9002, space_id=88)
    assert capacity.try_acquire(db_session, capacity.steward_assist_specs(space_id=88)) is True
    db_session.commit()
    assert _active(db_session, 88, capacity.RESOURCE_STEWARD_ASSIST) == 1

    never = _RowRef(9002)  # acquired_at 仍为 NULL
    assert capacity.release_attempt(db_session, never, space_id=88) is False
    db_session.commit()
    assert (
        _active(db_session, 88, capacity.RESOURCE_STEWARD_ASSIST) == 1
    ), "未占用的行释放了别人的名额"


def test_release_twice_across_paths_keeps_counter_consistent(db_session):
    """跨路径重复归还（例如栅栏退休后又走恢复）仍只递减一次。"""
    _bootstrap_counter(db_session, space_id=99, capacity_value=3)
    _seed_attempt(db_session, 9003, space_id=99)
    specs = capacity.steward_assist_specs(space_id=99)
    capacity.try_acquire(db_session, specs)
    capacity.try_acquire(db_session, specs)
    capacity.mark_acquired(db_session, table=capacity.GATE_TABLE_ATTEMPT, row_id=9003)
    db_session.commit()
    assert _active(db_session, 99, capacity.RESOURCE_STEWARD_ASSIST) == 2

    ref = _RowRef(9003)
    assert capacity.release_attempt(db_session, ref, space_id=99) is True  # 路径 ③
    db_session.commit()
    assert capacity.release_attempt(db_session, ref, space_id=99) is False  # 路径 ①
    db_session.commit()
    assert _active(db_session, 99, capacity.RESOURCE_STEWARD_ASSIST) == 1


def test_job_release_is_exactly_once(db_session):
    """steward job 走同一套行级门。"""
    _bootstrap_counter(db_session, space_id=55, capacity_value=1)
    _seed_job(db_session, 9101, space_id=55)
    specs = capacity.steward_job_specs(space_id=55)
    assert capacity.try_acquire(db_session, specs) is True
    capacity.mark_acquired(db_session, table=capacity.GATE_TABLE_JOB, row_id=9101)
    db_session.commit()
    assert _active(db_session, 55, capacity.RESOURCE_STEWARD_JOB) == 1

    ref = _RowRef(9101)
    assert capacity.release_job(db_session, ref, space_id=55) is True
    db_session.commit()
    assert capacity.release_job(db_session, ref, space_id=55) is False
    db_session.commit()
    assert _active(db_session, 55, capacity.RESOURCE_STEWARD_JOB) == 0


def test_acquire_after_release_reuses_capacity(db_session):
    """归还后名额可被再次占用（不是一次性）。"""
    _bootstrap_counter(db_session, space_id=111, capacity_value=1)
    _seed_attempt(db_session, 9004, space_id=111)
    specs = capacity.steward_assist_specs(space_id=111)
    assert capacity.try_acquire(db_session, specs) is True
    capacity.mark_acquired(db_session, table=capacity.GATE_TABLE_ATTEMPT, row_id=9004)
    db_session.commit()
    assert capacity.try_acquire(db_session, specs) is False
    assert capacity.release_attempt(db_session, _RowRef(9004), space_id=111) is True
    db_session.commit()
    assert capacity.try_acquire(db_session, specs) is True
