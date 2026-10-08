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
from app.utils import timeutil


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


def _seed_space(db_session, space_id: int, owner_id: int = 1) -> None:
    """建一个用户 + 家庭空间（`steward_jobs.space_id` 有 FK）。"""
    db_session.execute(
        text(
            "INSERT INTO users (id, name, gender, privacy_mode, profile_status, created_at)"
            " VALUES (:id, :name, 'unknown', 'perpetual', 'identity_confirmed',"
            " CURRENT_TIMESTAMP) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": owner_id, "name": f"u{owner_id}"},
    )
    db_session.flush()
    db_session.execute(
        text(
            "INSERT INTO family_spaces (id, name, owner_id, kind, created_at)"
            " VALUES (:id, :name, :owner, 'household', CURRENT_TIMESTAMP)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": space_id, "name": f"s{space_id}", "owner": owner_id},
    )
    db_session.flush()


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


def test_tenant_counter_is_created_lazily_after_bootstrap(db_session):
    """bootstrap 之后，**新出现**的租户必须自动纳入配额。

    ## 为什么这是必需的

    bootstrap 只能给当时活跃的租户建行。服务运行后新租户随时变为活跃（新用户
    注册、新空间创建 steward job），此时它的计数行不存在。若把「行不存在」当作
    「未登记 → 不限制」，配额对新租户**静默失效**；若当作「缺失 → 拒绝」，系统会在
    新租户出现时立刻停摆。

    实测症状：首次 bootstrap 后 `/ready` 返回 503，报
    `缺失计数行 space:1:steward_job（真实活跃 1）`——启动后新建的 steward job
    所在 space 没有行。
    """
    capacity_bootstrap.bootstrap(db_session)
    db_session.commit()

    # 一个 bootstrap 时不存在的新租户
    spec = capacity.CounterSpec("space", 4242, capacity.RESOURCE_STEWARD_ASSIST)
    assert capacity.try_acquire(db_session, [spec]) is True, "新租户未被纳入配额"
    row = db_session.get(AgentCapacityCounter, ("space", 4242, "steward_assist"))
    assert row is not None, "新租户的计数行未按需创建"
    assert row.active == 1


def test_lazy_registration_requires_bootstrap_marker(db_session):
    """未 bootstrap 的部署**不得**靠惰性创建绕过 fail-closed。

    惰性创建只在 global 行（bootstrap 标记）存在时才发生，否则
    `pg_all` 仍必须拒绝——否则「从未 bootstrap」会变成可用。
    """
    _set_stage(db_session, "pg_all")
    spec = capacity.CounterSpec("space", 5, capacity.RESOURCE_STEWARD_ASSIST)
    with pytest.raises(CapacityNotBootstrapped):
        capacity.try_acquire(db_session, [spec])


def test_lazy_registration_does_not_create_global_rows(db_session):
    """global/kind 行不得由惰性创建产生——它们必须来自 bootstrap。

    若惰性创建也建 global 行，`assert_ready` 的「bootstrap 已执行」判据就失效了。
    """
    capacity_bootstrap.bootstrap(db_session)
    db_session.commit()
    before = db_session.get(AgentCapacityCounter, ("global", 0, "assistant_run"))
    assert before is not None

    # 请求一个不存在的 global 维度：不得被创建
    spec = capacity.CounterSpec("global", 0, capacity.RESOURCE_STEWARD_ASSIST)
    assert capacity.try_acquire(db_session, [spec]) is True  # bootstrap 已建该行

    # agent_kind 维度只对 provider_stream 由 bootstrap 建；其他不得被惰性创建
    other = capacity.CounterSpec("agent_kind", 0, capacity.RESOURCE_STEWARD_ASSIST)
    assert capacity.try_acquire(db_session, [other]) is None, "非租户维度被惰性创建"


def test_global_counter_is_sum_of_tenants_not_an_orphan(db_session, monkeypatch):
    """global/kind 计数行的活跃值必须等于**租户汇总**，不得被报成孤儿。

    ## 为什么这条重要

    配额是分层的：global 与 agent_kind 是**所有租户之和**。若对账只算租户维度，
    这两个维度永远没有期望值，于是它们的活跃值一律被判为「孤儿占用」——
    实测症状：`孤儿占用 global:0:steward_job 计数行 active=20`，
    而 20 正是真实活跃 job 的汇总。把汇总当成泄漏会让 `assert_ready` 永远 503。
    """
    capacity_bootstrap.bootstrap(db_session)
    db_session.commit()

    # 两个租户各有占用，global 是汇总
    monkeypatch.setattr(
        capacity_bootstrap,
        "_active_counts",
        lambda _db: {
            ("space", 1, capacity.RESOURCE_STEWARD_JOB): 1,
            ("space", 2, capacity.RESOURCE_STEWARD_JOB): 2,
        },
    )
    spec = capacity.CounterSpec("global", 0, capacity.RESOURCE_STEWARD_JOB)
    capacity.set_active(db_session, spec, active=3)
    db_session.commit()

    report = capacity_bootstrap.reconcile(db_session)
    assert not any(
        "孤儿" in o for o in report.orphaned
    ), f"global 汇总被误报为孤儿：{report.orphaned}"


def test_global_counter_mismatch_is_reported(db_session, monkeypatch):
    """global 与租户汇总不一致时必须报——那是真的漂移。"""
    capacity_bootstrap.bootstrap(db_session)
    db_session.commit()
    monkeypatch.setattr(
        capacity_bootstrap,
        "_active_counts",
        lambda _db: {("space", 1, capacity.RESOURCE_STEWARD_JOB): 2},
    )
    # global 写 5，而租户汇总只有 2
    capacity.set_active(
        db_session,
        capacity.CounterSpec("global", 0, capacity.RESOURCE_STEWARD_JOB),
        active=5,
    )
    db_session.commit()
    report = capacity_bootstrap.reconcile(db_session)
    assert any(
        "计数不一致" in m and "global" in m for m in report.mismatches
    ), f"global 漂移未报：{report.mismatches}"


# ---------------------------------------------------------------- 聚合维度（C9）


def test_global_aggregate_equals_the_sum_of_tenants(db_session):
    """global 计数行必须等于其下所有租户之和。

    ## 为什么这是安全要求（实测阻塞生产切流）

    `_active_counts` 只按**租户**维度统计（account/space），因此 global 与
    agent_kind 的值恒为 0。而 `assert_ready` 的不变量是「global == 租户之和」。

    于是：只要存在活跃工作，bootstrap 之后 `/ready` **立刻失败**，fail-closed
    阻塞全部写入。实测生产切回时正是
    `global:0:steward_job 计数行 0 != 真实 17`。

    dev 上没暴露，因为 bootstrap 时恰好没有活跃工作（全 0 才「一致」）——
    这正是「只在空库上验证」会漏掉的缺陷类型。
    """
    from app.models.steward import StewardJob
    from app.services import capacity

    # 每个 space 只能有一个活跃 job（部分唯一索引），因此用**两个 space**
    # 来制造「租户之和 > 0」的形状。
    now = timeutil.utcnow()
    for space_id in (4242, 4243):
        _seed_space(db_session, space_id)
        db_session.add(
            StewardJob(
                space_id=space_id,
                cause="integrity_scan",
                trigger_cursor=1,
                status="queued",
                attempt=0,
                max_attempts=3,
                policy_version="p",
                checkpoint_json={},
                created_at=now,
                updated_at=now,
            )
        )
    db_session.commit()

    capacity_bootstrap.bootstrap(db_session)
    db_session.commit()

    spec = capacity.CounterSpec("global", 0, capacity.RESOURCE_STEWARD_JOB)
    row = db_session.get(
        capacity.AgentCapacityCounter, (spec.scope_kind, spec.scope_id, spec.resource_kind)
    )
    assert row is not None, "global 计数行未建立"
    assert row.active == 2, (
        f"global.active={row.active}，期望 2（两个活跃 job 之和）——"
        "global 不聚合会让 assert_ready 永久失败"
    )


def test_ready_passes_after_bootstrap_with_active_work(db_session):
    """有活跃工作时，bootstrap 后 `assert_ready` 必须通过。

    这条是对上一条的端到端形式：不只看 global 的值，而是看它是否真的让
    fail-closed 的门放行。
    """
    from app.models.steward import StewardJob

    space_id = 4343
    _seed_space(db_session, space_id)
    now = timeutil.utcnow()
    db_session.add(
        StewardJob(
            space_id=space_id,
            cause="integrity_scan",
            trigger_cursor=1,
            status="leased",
            attempt=1,
            max_attempts=3,
            policy_version="p",
            checkpoint_json={},
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    capacity_bootstrap.bootstrap(db_session)
    db_session.commit()
    # 不应抛出
    capacity_bootstrap.assert_ready(db_session, stage="pg_all")


def test_bootstrap_zeroes_orphaned_rows_without_active_work(db_session):
    """bootstrap 必须把「无真实活跃但 active>0」的行归零。

    ## 为什么（实测阻塞生产切流）

    `_tenant_ids` 只列出**当前**有活跃工作的租户，因此已存在的计数行若不再有活跃
    工作，就完全不在 bootstrap 的对齐范围内。若该行因故停在 `active=1`（例如旧代码
    在切换窗口内没有释放），`reconcile` 判它为**孤儿占用**，`assert_ready` 又把孤儿
    列为阻塞问题 → `/ready` 永久 503，fail-closed 阻塞全部写入。

    实测生产切流时正是如此：3 个 space 的行停在 active=1，而其 job 早已完成。
    """
    space_id = 5151
    _seed_space(db_session, space_id)

    # 先手工造一个「有行但无活跃工作」的状态
    spec = capacity.CounterSpec("space", space_id, capacity.RESOURCE_STEWARD_JOB)
    capacity.ensure_counter(db_session, spec, capacity=1)
    capacity.set_active(db_session, spec, active=1)
    db_session.commit()

    # 前置：reconcile 应判为孤儿
    pre = capacity_bootstrap.reconcile(db_session)
    assert pre.orphaned, "前置不成立：该行未被判为孤儿"

    capacity_bootstrap.bootstrap(db_session)
    db_session.commit()

    row = db_session.get(AgentCapacityCounter, ("space", space_id, "steward_job"))
    assert row is not None and row.active == 0, (
        f"孤儿行未被归零（active={row.active if row else None}）——" "/ready 会永久 503"
    )
    # 且 bootstrap 后对账通过
    assert capacity_bootstrap.reconcile(db_session).ok
