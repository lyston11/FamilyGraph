"""容量计数行的 bootstrap 与对账（C9/P0）。

## 为什么需要它，以及它为什么是安全要求

counter 采用「渐进引入」：登记了才由它裁决配额，未登记时沿用旧的计数查询。
这在**迁移期间**是正确的（既有 SQLite 行为不变），但在 `pg_all` 阶段是危险的：

```text
已切到 PostgreSQL，但 counter 未登记
→ try_acquire 返回 None
→ 配额**完全没有生效**
→ 多租户隔离在「迁移已完成」的外观下静默失效
```

这是「迁移完成但配额未启用」的**假完成**。因此本模块做两件事：

1. `bootstrap()`：按当前真实活跃数建立计数行；
2. `assert_ready()`：在 `pg_all` 阶段检查计数行存在且与真实状态一致，
   缺失即**拒绝**（fail-closed），而不是当成「不限制」。

## 为什么 `active` 不能直接写 0

若把 `active` 写 0，而实际有在途 run/attempt，则：

```text
真实在途 2 个 + counter.active = 0 + capacity = 2
→ 还能再放 2 个
→ 实际并发 4 > 配额 2
```

因此 bootstrap **必须**从真实状态重算 `active`。这也是为什么本模块的对账是
「双向」的：既检查 `active <= capacity`，也检查 `active == 真实活跃数`。

## 锁序

bootstrap 只操作计数行（`global → agent_kind → tenant`），不触碰 run/attempt 行锁，
因此与租约/结算路径的冻结锁序一致，不会引入反向顺序。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app import config
from app.models.agent import RUN_ACTIVE_STATUSES, AgentCapacityCounter
from app.services import capacity

logger = logging.getLogger(__name__)

#: steward job 的活跃态。**包含 `queued`**：入队即占用配额（见 steward.enqueue_steward_job），
#: 因此 bootstrap 重算时必须把 queued 计入，否则会低估占用。
STEWARD_JOB_ACTIVE_STATUSES = ("queued", "leased", "running")

#: steward assist attempt 的活跃态。只有 `in_flight` 占用名额（reserved 是计划期，
#: 尚未发送，从未占用）。
ASSIST_ACTIVE_STATUSES = ("in_flight",)


class CapacityBootstrapIncomplete(RuntimeError):
    """`pg_all` 阶段容量计数行缺失或不一致。

    这是**安全**异常：继续运行意味着配额静默失效。调用方不得降级为「不限制」，
    必须显式失败并触发运维介入（执行 bootstrap）。
    """


@dataclass
class ReconcileReport:
    """bootstrap / 对账结果。"""

    created: int = 0
    already_present: int = 0
    adjusted: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.orphaned


def _tenant_ids(db: Session) -> tuple[list[int], list[int]]:
    """当前存在的 account 与 space id（只取有活跃工作的，避免为历史租户建行）。"""
    accounts = [
        int(row[0])
        for row in db.execute(
            sa.text(
                "SELECT DISTINCT s.account_id FROM agent_runs r"
                " JOIN agent_sessions s ON s.id = r.session_id"
                " WHERE r.status IN :active"
            ).bindparams(sa.bindparam("active", expanding=True)),
            {"active": list(RUN_ACTIVE_STATUSES)},
        ).fetchall()
    ]
    spaces = [
        int(row[0])
        for row in db.execute(
            sa.text(
                "SELECT DISTINCT space_id FROM steward_jobs WHERE status IN :active"
                " UNION"
                " SELECT DISTINCT space_id FROM steward_model_calls"
                " WHERE status IN :assist"
            ).bindparams(
                sa.bindparam("active", expanding=True),
                sa.bindparam("assist", expanding=True),
            ),
            {"active": list(STEWARD_JOB_ACTIVE_STATUSES), "assist": list(ASSIST_ACTIVE_STATUSES)},
        ).fetchall()
    ]
    return accounts, spaces


#: 资源 -> `agent_kind` 维度的 scope_id（`agent_kind` 用 0=assistant / 1=steward）。
#:
#: 只有 provider_stream 有 agent_kind 维度（见 `capacity.stream_specs`）。
KIND_FOR_RESOURCE: dict[str, int] = {
    capacity.RESOURCE_PROVIDER_STREAM: 0,
}


def _active_counts(db: Session) -> dict[tuple[str, int, str], int]:
    """从真实状态重算每个维度的活跃占用。"""
    counts: dict[tuple[str, int, str], int] = {}

    def bump(key: tuple[str, int, str], amount: int = 1) -> None:
        if amount:
            counts[key] = counts.get(key, 0) + amount

    # assistant run：按 account 分桶
    rows = db.execute(
        sa.text(
            "SELECT s.account_id, count(*) FROM agent_runs r"
            " JOIN agent_sessions s ON s.id = r.session_id"
            " WHERE r.status IN :active AND r.kind = 'assistant'"
            " GROUP BY s.account_id"
        ).bindparams(sa.bindparam("active", expanding=True)),
        {"active": list(RUN_ACTIVE_STATUSES)},
    ).fetchall()
    for account_id, n in rows:
        bump(("account", int(account_id), capacity.RESOURCE_ASSISTANT_RUN), int(n))

    # steward job：按 space 分桶（含 queued）
    rows = db.execute(
        sa.text(
            "SELECT space_id, count(*) FROM steward_jobs WHERE status IN :active"
            " GROUP BY space_id"
        ).bindparams(sa.bindparam("active", expanding=True)),
        {"active": list(STEWARD_JOB_ACTIVE_STATUSES)},
    ).fetchall()
    for space_id, n in rows:
        bump(("space", int(space_id), capacity.RESOURCE_STEWARD_JOB), int(n))

    # steward assist：按 space 分桶（只有 in_flight）
    rows = db.execute(
        sa.text(
            "SELECT space_id, count(*) FROM steward_model_calls WHERE status IN :active"
            " GROUP BY space_id"
        ).bindparams(sa.bindparam("active", expanding=True)),
        {"active": list(ASSIST_ACTIVE_STATUSES)},
    ).fetchall()
    for space_id, n in rows:
        bump(("space", int(space_id), capacity.RESOURCE_STEWARD_ASSIST), int(n))

    return counts


def _global_capacity(resource_kind: str) -> int:
    """该资源在 global 维度上的容量。"""
    if resource_kind == capacity.RESOURCE_ASSISTANT_RUN:
        # 全局上限 = 每账户上限 × 允许的账户数（部署可覆盖）。
        return max(
            config.AGENT_ACCOUNT_ASSISTANT_RUN_LIMIT,
            int(getattr(config, "AGENT_GLOBAL_ASSISTANT_RUN_LIMIT", 0))
            or config.AGENT_ACCOUNT_ASSISTANT_RUN_LIMIT * 8,
        )
    if resource_kind == capacity.RESOURCE_STEWARD_JOB:
        return max(config.STEWARD_MAX_CONCURRENT_JOBS, 1) * 8
    if resource_kind == capacity.RESOURCE_STEWARD_ASSIST:
        return max(config.STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE, 1) * 8
    if resource_kind == capacity.RESOURCE_PROVIDER_STREAM:
        return config.AGENT_STREAM_GLOBAL_CAPACITY
    if resource_kind == capacity.RESOURCE_CLUSTER_PROVIDER:
        return max(config.AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS, 1) * 4
    if resource_kind == capacity.RESOURCE_CLUSTER_TOOL:
        return config.AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS
    return 1


def _tenant_capacity(resource_kind: str) -> int:
    """该资源在租户维度上的容量。"""
    if resource_kind == capacity.RESOURCE_ASSISTANT_RUN:
        return config.AGENT_ACCOUNT_ASSISTANT_RUN_LIMIT
    if resource_kind == capacity.RESOURCE_STEWARD_JOB:
        # 每空间至多一个活跃 job（partial unique index 兜底）。
        return 1
    if resource_kind == capacity.RESOURCE_STEWARD_ASSIST:
        return config.STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE
    if resource_kind == capacity.RESOURCE_PROVIDER_STREAM:
        return config.AGENT_STREAM_PER_TENANT_CAPACITY
    return 1


def _kind_capacity(resource_kind: str) -> int:
    if resource_kind == capacity.RESOURCE_PROVIDER_STREAM:
        return config.AGENT_STREAM_PER_KIND_CAPACITY
    return _global_capacity(resource_kind)


def bootstrap(db: Session, *, dry_run: bool = False) -> ReconcileReport:
    """按当前真实活跃数建立计数行。

    `active` 从真实状态**重算**，不写 0——写 0 会低估占用并允许超额。
    已存在的行只做**对账**，不覆盖（除非 dry_run=False 且值不一致，
    此时按真实值修正并记录，因为「迁移后计数行必须等于真相」）。
    """
    report = ReconcileReport()
    actual = _active_counts(db)
    accounts, spaces = _tenant_ids(db)

    specs: list[tuple[capacity.CounterSpec, int]] = []

    # 全局与 kind 维度（每个资源一条）
    for resource_kind in (
        capacity.RESOURCE_ASSISTANT_RUN,
        capacity.RESOURCE_STEWARD_JOB,
        capacity.RESOURCE_STEWARD_ASSIST,
        capacity.RESOURCE_PROVIDER_STREAM,
        capacity.RESOURCE_CLUSTER_PROVIDER,
        capacity.RESOURCE_CLUSTER_TOOL,
    ):
        specs.append(
            (
                capacity.CounterSpec("global", 0, resource_kind),
                _global_capacity(resource_kind),
            )
        )
    specs.append(
        (
            capacity.CounterSpec("agent_kind", 0, capacity.RESOURCE_PROVIDER_STREAM),
            _kind_capacity(capacity.RESOURCE_PROVIDER_STREAM),
        )
    )

    # 租户维度。
    #
    # 注意 `spaces` 必须来自 `_tenant_ids`（它按**活跃工作**筛选），而不是只从
    # 某个资源取——否则会出现「steward_job 有活跃行但 space 未建计数行」，
    # 于是 `assert_ready` 报缺失、`/ready` 503。这正是首次 bootstrap 后实际发生的：
    # 全局行建好了，但 space:1/space:2 的 steward_job 行没建。
    for account_id in accounts:
        specs.append(
            (
                capacity.CounterSpec("account", account_id, capacity.RESOURCE_ASSISTANT_RUN),
                _tenant_capacity(capacity.RESOURCE_ASSISTANT_RUN),
            )
        )
    for space_id in spaces:
        specs.append(
            (
                capacity.CounterSpec("space", space_id, capacity.RESOURCE_STEWARD_JOB),
                _tenant_capacity(capacity.RESOURCE_STEWARD_JOB),
            )
        )
        specs.append(
            (
                capacity.CounterSpec("space", space_id, capacity.RESOURCE_STEWARD_ASSIST),
                _tenant_capacity(capacity.RESOURCE_STEWARD_ASSIST),
            )
        )
        specs.append(
            (
                capacity.CounterSpec("space", space_id, capacity.RESOURCE_PROVIDER_STREAM),
                _tenant_capacity(capacity.RESOURCE_PROVIDER_STREAM),
            )
        )

    # 聚合维度（global / agent_kind）的 active 必须等于**其下所有租户之和**。
    #
    # 不能直接从 `_active_counts` 取：它只按租户维度统计（account/space），
    # 因此 global/kind 的值永远是 0。而 `assert_ready` 的不变量正是
    # 「global == 所有租户之和」——于是**只要存在活跃工作**，bootstrap 之后
    # `/ready` 立刻失败，fail-closed 会阻塞全部写入。
    #
    # 实测生产切流时正是如此：`global:0:steward_job 计数行 0 != 真实 17`。
    # dev 上没暴露，是因为 bootstrap 时恰好没有任何活跃工作（全 0 才「一致」）。
    # 键与 `actual` 同形（scope_kind, scope_id, resource_kind），避免混合元数。
    aggregate: dict[tuple[str, int, str], int] = {}
    for (_scope_kind, _scope_id, resource_kind), n in actual.items():
        gkey = ("global", 0, resource_kind)
        aggregate[gkey] = aggregate.get(gkey, 0) + n
        kind = KIND_FOR_RESOURCE.get(resource_kind)
        if kind is not None:
            kkey = ("agent_kind", kind, resource_kind)
            aggregate[kkey] = aggregate.get(kkey, 0) + n

    for spec, cap in specs:
        key = (spec.scope_kind, spec.scope_id, spec.resource_kind)
        if spec.scope_kind in ("global", "agent_kind"):
            active = aggregate.get(key, 0)
        else:
            active = actual.get(key, 0)
        if active > cap:
            # 真实占用超过容量：不能自动「修正」成容量值——那会掩盖真实超额，
            # 也无法判断该拒绝谁。如实报告并要求人工判断。
            report.mismatches.append(
                f"{spec.scope_kind}:{spec.scope_id}:{spec.resource_kind}"
                f" 真实活跃 {active} > 容量 {cap}"
            )
            continue
        if dry_run:
            report.created += 1
            continue
        existed = (
            db.get(
                AgentCapacityCounter,
                (spec.scope_kind, spec.scope_id, spec.resource_kind),
            )
            is not None
        )
        capacity.ensure_counter(db, spec, capacity=cap)
        if existed:
            report.already_present += 1
        else:
            report.created += 1
        # 计数行的 active 必须等于真相（bootstrap 的语义就是「对齐」）
        capacity.set_active(db, spec, active=active)
        report.adjusted.append(f"{spec.scope_kind}:{spec.scope_id}:{spec.resource_kind}={active}")

    return report


def _aggregate_expected(actual: dict[tuple[str, int, str], int]) -> dict[tuple[str, int, str], int]:
    """由租户维度的真实值推导 global / agent_kind 维度的期望值。

    ## 为什么必须推导，而不能只算租户

    配额是**分层**的：`global` 与 `agent_kind` 计数行的活跃值是**所有租户之和**。
    只计算租户维度会让这两个维度永远没有期望值，于是它们的活跃值一律被报成
    「孤儿占用」——实测：`孤儿占用 global:0:steward_job 计数行 active=20`，
    而那是 20 个真实活跃 job 的正确汇总。

    把汇总当成泄漏会让对账永远失败，从而 `assert_ready` 永远 503。
    """
    totals: dict[tuple[str, int, str], int] = {}
    for (scope_kind, _scope_id, resource_kind), value in actual.items():
        if scope_kind not in _TENANT_SCOPES:
            continue
        totals[("global", 0, resource_kind)] = totals.get(("global", 0, resource_kind), 0) + value
        if resource_kind == capacity.RESOURCE_PROVIDER_STREAM:
            # 流级有 kind 维度：所有 kind 的汇总等于 global。
            totals[("agent_kind", 0, resource_kind)] = (
                totals.get(("agent_kind", 0, resource_kind), 0) + value
            )
    return totals


#: 参与汇总的租户维度。
_TENANT_SCOPES = frozenset({"account", "space"})


def reconcile(db: Session) -> ReconcileReport:
    """对账：计数行必须存在、必须与真实活跃数一致、不得有孤儿。"""
    report = ReconcileReport()
    actual = _active_counts(db)
    # global / agent_kind 的期望值来自租户汇总（它们是分层配额，不是独立真相）。
    expected = {**actual, **_aggregate_expected(actual)}

    rows = db.execute(
        sa.text(
            "SELECT scope_kind, scope_id, resource_kind, capacity, active"
            " FROM agent_capacity_counters"
        )
    ).fetchall()
    present = {(r[0], int(r[1]), r[2]): (int(r[3]), int(r[4])) for r in rows}

    # ① 真实有占用但无计数行 → 配额失效
    for key, active in expected.items():
        if key not in present:
            report.mismatches.append(f"缺失计数行 {key[0]}:{key[1]}:{key[2]}（真实活跃 {active}）")
        elif present[key][1] != active:
            report.mismatches.append(
                f"计数不一致 {key[0]}:{key[1]}:{key[2]}"
                f" 计数行 {present[key][1]} != 真实 {active}"
            )

    # ② 孤儿：计数行有 active 但真实无占用（可能是泄漏）
    for key, (_cap, active) in present.items():
        if active > 0 and expected.get(key, 0) == 0:
            report.orphaned.append(f"孤儿占用 {key[0]}:{key[1]}:{key[2]} 计数行 active={active}")

    return report


def assert_ready(db: Session, *, stage: str) -> None:
    """`pg_all` 阶段必须在启动/写路径前调用。

    计数行缺失即**拒绝**。这是把「迁移完成」与「配额生效」绑定的唯一机制：
    没有它，`pg_all` 会在配额未启用的情况下正常运行，而外观完全正常。
    """
    if stage != "pg_all":
        return

    # ① **存在性**：全局计数行必须存在。
    #
    # 只做一致性对账是不够的——空库是「一致」的（没有真实占用、没有计数行），
    # 但那正是最危险的状态：没有任何计数行登记，`try_acquire` 对所有维度返回
    # `None`，配额**从未生效**，而一致性检查会vacuous通过。
    #
    # bootstrap 无条件创建全部全局/kind 行，因此「全局行存在」是 bootstrap 已执行
    # 的充分判据。
    missing = [
        f"global:0:{resource_kind}"
        for resource_kind in (
            capacity.RESOURCE_ASSISTANT_RUN,
            capacity.RESOURCE_STEWARD_JOB,
            capacity.RESOURCE_STEWARD_ASSIST,
            capacity.RESOURCE_PROVIDER_STREAM,
            capacity.RESOURCE_CLUSTER_PROVIDER,
            capacity.RESOURCE_CLUSTER_TOOL,
        )
        if db.get(AgentCapacityCounter, ("global", 0, resource_kind)) is None
    ]

    # ② 一致性：计数行必须等于真实活跃数，且无孤儿。
    report = reconcile(db)
    problems = [*missing, *report.mismatches, *report.orphaned]
    if problems:
        detail = "; ".join(problems[:5])
        raise CapacityBootstrapIncomplete(
            f"pg_all 阶段容量计数行未就绪：{detail}。"
            "请执行 `python -m app.capacity_bootstrap` 后重试。"
        )
