"""C9/P0：容量计数行的 bootstrap、对账与 `pg_all` fail-closed。

## 本文件守护的核心不变量

counter 的「渐进引入」设计（未登记 → 沿用旧路径）在**迁移期**正确，在 `pg_all` 危险：

```text
pg_all 且计数行缺失
→ try_acquire 返回 None
→ 配额完全没有生效
→ 多租户隔离在「迁移已完成」的外观下静默失效
```

因此三件事必须成立，且各有断言：

1. `active` 从真实状态**重算**，不写 0（写 0 会低估占用并允许超额）；
2. 对账**双向**（真实 > 计数行、以及孤儿占用都要报）；
3. `pg_all` 且计数行缺失时**拒绝**，其他阶段是 no-op。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.models.agent import AgentCapacityCounter
from app.services import capacity, capacity_bootstrap
from app.services.capacity import CapacityNotBootstrapped


def _set_stage(db_session, stage: str) -> None:
    db_session.execute(
        text(
            "INSERT INTO writer_state (id, stage, epoch, updated_at, updated_by)"
            " VALUES (1, :stage, 0, CURRENT_TIMESTAMP, 'test')"
            " ON CONFLICT (id) DO UPDATE SET stage = :stage"
        ),
        {"stage": stage},
    )
    db_session.commit()


def test_bootstrap_recomputes_active_from_truth(db_session):
    """`active` 必须来自真实活跃数。

    若 bootstrap 写 0 而实际有在途占用，配额会凭空多出名额（实测场景：
    真实 2 个在途 + capacity 2 + active 0 → 还能再放 2 个 = 实际 4）。
    """
    db_session.execute(
        text(
            "INSERT INTO users (id, name, gender, privacy_mode, profile_status, created_at)"
            " VALUES (1, 'u', 'unknown', 'perpetual', 'identity_confirmed', CURRENT_TIMESTAMP)"
        )
    )
    db_session.execute(
        text(
            "INSERT INTO family_spaces (id, name, owner_id, kind, created_at)"
            " VALUES (1, 's', 1, 'household', CURRENT_TIMESTAMP)"
        )
    )
    # 一个活跃 steward job（queued 也算活跃：入队即占配额）
    db_session.execute(
        text(
            "INSERT INTO steward_jobs (id, space_id, cause, trigger_cursor, status,"
            " attempt, max_attempts, policy_version, checkpoint_json, created_at, updated_at)"
            " VALUES (1, 1, 'integrity_scan', 1, 'queued', 0, 3, 'p', '{}',"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )
    db_session.commit()

    report = capacity_bootstrap.bootstrap(db_session)
    db_session.commit()
    assert report.ok, f"bootstrap 报告不一致：{report.mismatches}"

    row = db_session.get(AgentCapacityCounter, ("space", 1, "steward_job"))
    assert row is not None, "未建立 space 维度的 steward_job 计数行"
    assert row.active == 1, f"active 未按真实状态重算（得到 {row.active}，期望 1）"


def test_reconcile_detects_missing_counter_row(db_session):
    """真实有占用但计数行缺失 → 必须报不一致（这是配额失效的形态）。"""
    db_session.execute(
        text(
            "INSERT INTO users (id, name, gender, privacy_mode, profile_status, created_at)"
            " VALUES (1, 'u', 'unknown', 'perpetual', 'identity_confirmed', CURRENT_TIMESTAMP)"
        )
    )
    db_session.execute(
        text(
            "INSERT INTO family_spaces (id, name, owner_id, kind, created_at)"
            " VALUES (1, 's', 1, 'household', CURRENT_TIMESTAMP)"
        )
    )
    db_session.execute(
        text(
            "INSERT INTO steward_jobs (id, space_id, cause, trigger_cursor, status,"
            " attempt, max_attempts, policy_version, checkpoint_json, created_at, updated_at)"
            " VALUES (1, 1, 'integrity_scan', 1, 'queued', 0, 3, 'p', '{}',"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )
    db_session.commit()

    report = capacity_bootstrap.reconcile(db_session)
    assert not report.ok
    assert any("缺失计数行" in m for m in report.mismatches), report.mismatches


def test_reconcile_detects_counter_drift(db_session):
    """计数行与真实值不一致 → 必须报（两个方向都要）。"""
    db_session.execute(
        text(
            "INSERT INTO users (id, name, gender, privacy_mode, profile_status, created_at)"
            " VALUES (1, 'u', 'unknown', 'perpetual', 'identity_confirmed', CURRENT_TIMESTAMP)"
        )
    )
    db_session.execute(
        text(
            "INSERT INTO family_spaces (id, name, owner_id, kind, created_at)"
            " VALUES (1, 's', 1, 'household', CURRENT_TIMESTAMP)"
        )
    )
    db_session.execute(
        text(
            "INSERT INTO steward_jobs (id, space_id, cause, trigger_cursor, status,"
            " attempt, max_attempts, policy_version, checkpoint_json, created_at, updated_at)"
            " VALUES (1, 1, 'integrity_scan', 1, 'queued', 0, 3, 'p', '{}',"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )
    capacity.ensure_counter(
        db_session,
        capacity.CounterSpec("space", 1, capacity.RESOURCE_STEWARD_JOB),
        capacity=1,
    )
    db_session.commit()  # active 仍为 0，真实为 1

    report = capacity_bootstrap.reconcile(db_session)
    assert any("计数不一致" in m for m in report.mismatches), report.mismatches


def test_reconcile_detects_orphaned_occupancy(db_session):
    """计数行有 active 但真实无占用 → 报孤儿（名额泄漏的形态）。"""
    capacity.ensure_counter(
        db_session,
        capacity.CounterSpec("space", 99, capacity.RESOURCE_STEWARD_JOB),
        capacity=1,
    )
    capacity.set_active(
        db_session,
        capacity.CounterSpec("space", 99, capacity.RESOURCE_STEWARD_JOB),
        active=1,
    )
    db_session.commit()

    report = capacity_bootstrap.reconcile(db_session)
    assert any("孤儿占用" in o for o in report.orphaned), report.orphaned


def test_assert_ready_only_gates_pg_all(db_session):
    """只有 `pg_all` 需要容量就绪；其他阶段是 no-op（迁移期不得阻塞）。"""
    for stage in ("sqlite", "shadow", "pg_control"):
        _set_stage(db_session, stage)
        capacity_bootstrap.assert_ready(db_session, stage=stage)  # 不应抛出

    _set_stage(db_session, "pg_all")
    with pytest.raises(capacity_bootstrap.CapacityBootstrapIncomplete):
        capacity_bootstrap.assert_ready(db_session, stage="pg_all")


def test_assert_ready_passes_after_bootstrap(db_session):
    """bootstrap 之后 `pg_all` 通过。"""
    capacity_bootstrap.bootstrap(db_session)
    db_session.commit()
    _set_stage(db_session, "pg_all")
    capacity_bootstrap.assert_ready(db_session, stage="pg_all")  # 不应抛出


def test_pg_all_fails_closed_when_counter_missing(db_session):
    """**最关键的一条**：`pg_all` 下计数行缺失时 `try_acquire` 必须拒绝。

    就绪探针只保护新启动的实例；一个在 bootstrap 之前就已运行的实例不会被它拦住，
    因此写路径必须自己拒绝。否则「迁移完成但配额未启用」会静默通过。
    """
    _set_stage(db_session, "pg_all")
    spec = capacity.CounterSpec("space", 7, capacity.RESOURCE_STEWARD_ASSIST)
    with pytest.raises(CapacityNotBootstrapped):
        capacity.try_acquire(db_session, [spec])


def test_non_pg_all_stage_still_degrades_gracefully(db_session):
    """迁移期（非 `pg_all`）仍返回 `None`，既有 SQLite 行为逐字不变。"""
    _set_stage(db_session, "sqlite")
    spec = capacity.CounterSpec("space", 7, capacity.RESOURCE_STEWARD_ASSIST)
    assert capacity.try_acquire(db_session, [spec]) is None


def test_empty_writer_state_is_treated_as_pre_migration(db_session):
    """`writer_state` 无行（迁移前/未初始化）→ 按非 `pg_all` 处理，不阻塞。

    这条区分很重要：`read_state` 会回落到部署默认值，而这里需要区分
    「无行（未切换）」与「阶段是 pg_all」。
    """
    # 不 DROP 表：cleanup 只删行、不重建 schema，DROP 会让后续用例的表消失
    # （实测导致 18 个无关用例失败）。改为模拟「迁移前」的判据——表存在但无行时
    # `_writer_is_pg_all` 同样返回 False（行缺失）。
    db_session.execute(text("DELETE FROM writer_state"))
    db_session.commit()
    spec = capacity.CounterSpec("space", 7, capacity.RESOURCE_STEWARD_ASSIST)
    assert capacity.try_acquire(db_session, [spec]) is None


def test_bootstrap_refuses_to_silently_fix_over_capacity(db_session, monkeypatch):
    """真实占用 > 容量时**不自动修正**，如实报告。

    自动把它「修」成容量值会掩盖真实超额，也无法判断该拒绝谁——那是需要人工
    判断的运维事件，不是可以静默吸收的状态。

    ## 为什么直接注入 `_active_counts` 而不是构造真实行

    构造「真实 > 容量」需要特定表的组合（steward_jobs 有 partial unique index
    禁止同空间两个活跃 job，attempt 又有多层 FK 链），而这些约束会随迁移变化。
    本用例要守的是 **bootstrap 的判定逻辑**：真实值超过容量时必须报告而不是
    静默写入。注入真实值可以直接、稳定地测这一点，不依赖无关 schema 细节。

    真实行数 → `active` 的映射由 `test_bootstrap_recomputes_active_from_truth`
    与 `test_reconcile_detects_*` 覆盖。
    """
    from app import config

    spec = capacity.CounterSpec("space", 1, capacity.RESOURCE_STEWARD_ASSIST)
    capacity.ensure_counter(
        db_session, spec, capacity=config.STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE
    )
    db_session.commit()

    # 注入「真实活跃 3」而容量是 2
    monkeypatch.setattr(
        capacity_bootstrap,
        "_active_counts",
        lambda _db: {("space", 1, capacity.RESOURCE_STEWARD_ASSIST): 3},
    )
    monkeypatch.setattr(capacity_bootstrap, "_tenant_ids", lambda _db: ([], [1]))

    report = capacity_bootstrap.bootstrap(db_session)
    assert any(
        "真实活跃" in m for m in report.mismatches
    ), f"真实占用超过容量时未报告：{report.mismatches}"
    assert not report.ok, "报告不应视为通过"
    # 计数行不得被静默改成容量值
    row = db_session.get(AgentCapacityCounter, ("space", 1, "steward_assist"))
    assert row is not None and row.active == 0, "静默修改了计数行"
