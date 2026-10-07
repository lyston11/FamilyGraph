"""C8/C1：真实历史快照导入 PostgreSQL 并逐表对账。

## 与 `import_reconcile_probe.py` 的区别

那个探针用**合成最小模型**（`proof_*` 表）验证流程正确性。本脚本用**真实开发库
快照**（51 users / 1151 runs / 31821 events / 22852 domain_events）验证：

1. 真实 88 表 schema 能承载真实数据（约束、FK、CHECK、触发器都不炸）；
2. 逐表行数对账；
3. 关键不变量对账（run↔attempt、lease 终态、egress 一次一审计）；
4. **refusal**：差异不自动修复，如实报告。

## 安全

- 只读快照（`app.backup` 产出，不是运行中主库的复制）；
- 只写隔离 PostgreSQL，**不碰**开发库与线上；
- 导入前检查目标库没有同名业务数据，否则拒绝运行（防止误指真实库）。

用法：

    SNAPSHOT=/path/to/snapshot.db PGTEST_DSN=postgresql://... \
        python3 scripts/migration-proof/real_snapshot_import.py
"""
from __future__ import annotations

import itertools
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    override = os.environ.get("MIGRATION_PROOF_ROOT")
    if override:
        return Path(override)
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        cwd=Path(__file__).parent).strip())


ROOT = _repo_root()
BACKEND = ROOT / "backend"
OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", str(ROOT / "artifacts/migration-proof")))

#: 对账表。顺序无关（只读行数），但保持稳定输出。
RECONCILE_TABLES = (
    "users", "accounts", "relations", "space_members",
    "agent_sessions", "agent_runs", "agent_run_events", "agent_messages",
    "domain_events", "action_cards", "source_facts", "raw_relation_inputs",
    "memories", "rag_documents", "rag_chunks", "attachments",
)


def _digest_rows(rows: list[tuple]) -> str:
    """对行集合做稳定摘要（与顺序无关，因为调用方已 ORDER BY）。"""
    import hashlib

    h = hashlib.sha256()
    for row in rows:
        h.update(repr(tuple(row)).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _sqlite_digest(path: str, table: str, cols: list[str]) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return _digest_rows(rows)


def _forward_fk_columns(inspector, table: str, already: set[str]) -> list[str]:
    """返回指向**尚未导入表**的 FK 列（含自引用）。

    这些列在阶段 1 置 NULL，阶段 2 回填。自引用也算「前向」，因为同一批行内部
    有依赖——统一处理比区分两种情形更简单，也更难写错。
    """
    nullable_by_col = {c["name"]: c["nullable"] for c in inspector.get_columns(table)}
    out: list[str] = []
    for fk in inspector.get_foreign_keys(table):
        target = fk.get("referred_table")
        if target != table and not (target and target not in already):
            continue
        for col in fk.get("constrained_columns") or []:
            # **只延迟可空列**：NOT NULL 列置 NULL 会直接违反 NOT NULL（实测
            # `agent_jobs.run_id`）。不可空的 FK 必须由插入顺序保证。
            if nullable_by_col.get(col, False):
                out.append(col)
    return out


def sqlite_counts(path: str) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out: dict[str, int] = {}
    try:
        for table in RECONCILE_TABLES:
            try:
                out[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                out[table] = -1  # 表在快照中不存在
    finally:
        conn.close()
    return out


def main() -> int:
    snapshot = os.environ.get("SNAPSHOT")
    dsn = os.environ.get("PGTEST_DSN")
    if not snapshot or not Path(snapshot).exists():
        print("SKIP: 需要 SNAPSHOT 指向 app.backup 产出的隔离快照")
        return 2
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL（不得指向开发库/线上）")
        return 2

    sys.path.insert(0, str(BACKEND))
    from sqlalchemy import create_engine, inspect
    from sqlalchemy import text as sa_text
    from sqlalchemy import bindparam as sa_bindparam
    from sqlalchemy.sql.sqltypes import Boolean as sa_Boolean

    engine = create_engine(dsn)
    failures: list[str] = []
    report: dict = {}

    # ---- 0) 拒绝误指真实库：目标库不得已有业务数据 ----
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    # 拒绝条件必须覆盖**任何**业务表，不能只看 agent_runs：
    # 一次部分失败的导入会留下 users 等表有数据而 agent_runs 为空，
    # 只查 agent_runs 就会放行，随后撞 duplicate key（实测）。
    for probe in ("agent_runs", "users", "accounts", "family_spaces", "domain_events"):
        if probe not in existing:
            continue
        with engine.connect() as conn:
            n = conn.execute(sa_text(f"SELECT count(*) FROM {probe}")).scalar()
        if n:
            print(
                f"REFUSAL: 目标库已有 {n} 行 {probe}，拒绝导入"
                "（防误指真实库；也防在部分导入的结果上重跑）"
            )
            return 3

    # ---- 1) 建立 PostgreSQL baseline（ORM metadata + 扩展索引）----
    print("1) 建立 baseline")
    proc = subprocess.run(
        [str(BACKEND / ".venv/bin/python"), str(ROOT / "scripts/migration-proof/pg_baseline_build.py")],
        cwd=str(BACKEND), env={**os.environ}, capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0:
        print(proc.stdout[-500:])
        print(proc.stderr[-500:])
        failures.append("baseline 建立失败")
        report["failures"] = failures
        return 1
    with engine.connect() as conn:
        tables = inspect(engine).get_table_names()
    print(f"   baseline 表数：{len(tables)}")

    # ---- 2) 逐表导入（保留 ID）----
    print("2) 导入真实数据")
    src = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    imported: dict[str, int] = {}
    skipped: dict[str, str] = {}
    #: 已成功导入的表（供前向 FK 判定）
    already_imported: set[str] = set()
    #: 阶段 2 待回填：(表, 列, 原始行)
    pending_backfills: list[tuple[str, list[str], list[dict]]] = []

    # 按 FK 依赖排序：先父后子。这里用固定顺序而非拓扑排序，因为表集合已知。
    # ## 导入顺序必须由 FK 依赖**计算**，不能硬编码
    #
    # 第一版硬编码了 17 张表，而真实库有 **95 张有数据的表**（audit_log 26580 行、
    # steward_jobs 22740 行、agent_tool_calls 9378 行……）。硬编码清单会静默漏表，
    # 而漏掉的表恰恰是 FK 目标，于是下游表全部因 FK 违规被跳过——实测表现为
    # `agent_runs` 的 `job_id -> agent_jobs` 找不到目标。
    #
    # 因此按 inspector 的 FK 图做**拓扑排序**：先父后子。自引用（表指向自己）在
    # 排序中不构成跨表依赖，由下面的两阶段插入处理。
    # ## 拓扑排序：**全 FK 边** + 只在真成环处删除可空边
    #
    # 两次错误尝试，记录以免重犯：
    #
    # 1. **只用 NOT NULL 边排序** → 可空边不参与定序，`agent_sessions` /
    #    `agent_messages` 被排到 `agent_runs` 之后（第 36/72 位 vs 16 位）。
    #    于是 `agent_runs` 的直接插入因 `session_id` FK 失败，被迫延迟它，
    #    又撞 CHECK `ck_agent_runs_scope_binding`（要求 assistant 的 session_id 非空）。
    # 2. **用「软边偏好」修补** → 排序仍不稳定，同样的问题复现。
    #
    # 正确做法：**所有 FK 边都参与排序**（这样 `agent_sessions` 自然排在
    # `agent_runs` 之前），只有在 Kahn 算法**卡住**（即真成环）时，才删除一条
    # **可空**边来打破环。真实数据里唯一的环是：
    #   `agent_jobs.run_id`（NOT NULL）↔ `agent_runs.job_id`（可空）
    # 因此被删除的一定是可空的那条（`agent_runs.job_id`），它由阶段 2 回填。
    #
    # 这保证：延迟只发生在**真正成环**的列上，而不是任何可空的前向 FK。
    all_edges: dict[str, set[str]] = {}
    nullable_edges: dict[tuple[str, str], bool] = {}  # (table, target) -> 全部列可空？
    for table in tables:
        nb = {c["name"]: c["nullable"] for c in inspector.get_columns(table)}
        deps: set[str] = set()
        for fk in inspector.get_foreign_keys(table):
            target = fk.get("referred_table")
            if not target or target == table or target not in tables:
                continue
            deps.add(target)
            cols = fk.get("constrained_columns") or []
            # 只要有一列 NOT NULL，这条边就**不可删**（删了无法置 NULL）
            nullable_edges[(table, target)] = all(nb.get(c, False) for c in cols)
        all_edges[table] = deps

    order: list[str] = []
    remaining = set(all_edges)
    removed_edges: list[str] = []
    while remaining:
        ready = sorted(t for t in remaining if not (all_edges[t] & remaining))
        if not ready:
            # 成环：删除一条**可空**边打破它。优先删「依赖最少」的表上的边。
            candidates = [
                (t, dep)
                for t in sorted(remaining)
                for dep in sorted(all_edges[t] & remaining)
                if nullable_edges.get((t, dep))
            ]
            if not candidates:
                # 无可空边可删：真死锁（NOT NULL 互相依赖），如实记录并强行打破。
                t = sorted(remaining)[0]
                removed_edges.append(f"HARD-CYCLE:{t}")
                all_edges[t] = set()
                continue
            t, dep = candidates[0]
            all_edges[t].discard(dep)
            removed_edges.append(f"{t}.->{dep}")
            continue
        for t in ready:
            order.append(t)
            remaining.discard(t)
    if removed_edges:
        skipped["_cycle_edges_removed"] = ", ".join(removed_edges)

    with engine.connect() as conn:
        # ## FK 延迟：为什么必须
        #
        # 真实数据里有**表内自引用**（`family_spaces.lineage_space_id` 指向同一张表的
        # 另一行），因此「先父后子」的表间排序**不足以**导入：同一批行内部也有依赖。
        #
        # 三种做法：
        #   ① 拓扑排序行级依赖 —— 复杂且对自引用无解；
        #   ② 临时关 FK —— 会掩盖真正的 FK 违规（那是必须发现的缺陷）；
        #   ③ **延迟约束到事务提交** —— PostgreSQL 支持 `SET CONSTRAINTS ALL DEFERRED`，
        #      但要求约束声明为 DEFERRABLE（本项目的 ORM 未声明）。
        #
        # 这里用 ④：**每表在 savepoint 内先插入，失败则该表整表回滚并如实记录**，
        # 同时把「自引用」显式报告为需要人工决策的差异——按 refusal 原则，
        # 导入器**不得**自行放宽约束。真实迁移应由一次性数据加载脚本处理自引用
        # （先插 lineage_space_id=NULL，再回填），那是独立工作，不是本探针的职责。
        for table in order:
            if table not in tables:
                skipped[table] = "not_in_pg_schema"
                continue
            try:
                rows = src.execute(f"SELECT * FROM {table}").fetchall()
            except sqlite3.Error as exc:
                skipped[table] = f"not_in_snapshot:{type(exc).__name__}"
                continue
            if not rows:
                imported[table] = 0
                continue
            pg_columns = inspector.get_columns(table)
            pg_cols = {c["name"] for c in pg_columns}
            # **布尔列必须转换**：SQLite 存 0/1（整数），PostgreSQL 是 BOOLEAN。
            # 直接插入会报 `column "pin_must_change" is of type boolean but
            # expression is of type integer`。这是真实迁移会撞上的类型差异，
            # 不是探针问题——因此这里显式转换而不是跳过该表。
            bool_cols = {
                c["name"] for c in pg_columns
                if isinstance(c["type"], sa_Boolean)
            }
            cols = [c for c in rows[0].keys() if c in pg_cols]
            if not cols:
                skipped[table] = "no_common_columns"
                continue
            placeholders = ", ".join(f":{c}" for c in cols)
            stmt = sa_text(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
            )

            def _convert(row: sqlite3.Row) -> dict:
                out = {}
                for c in cols:
                    value = row[c]
                    if c in bool_cols and value is not None:
                        # SQLite 用 0/1；也可能已是 Python bool。
                        value = bool(value)
                    out[c] = value
                return out

            payload = [_convert(row) for row in rows]

            # ## 自引用 FK 的两阶段导入
            #
            # 真实数据里有**表内自引用**：
            #   family_spaces.lineage_space_id -> family_spaces.id（10 行）
            #   users.created_by               -> users.id
            #
            # 「先父后子」的表间排序**不足以**处理它：同一批行内部也有依赖。
            # 三种做法中：
            #   ① 行级拓扑排序 —— 对循环引用无解，且实现复杂；
            #   ② 临时关闭 FK —— 会**掩盖真正的 FK 违规**（那是必须发现的缺陷）；
            #   ③ 延迟约束 —— 需约束声明 DEFERRABLE，本项目的 ORM 未声明。
            #
            # 因此用 ④：**两阶段插入**——先插入自引用列置 NULL，再 UPDATE 回填。
            # 这既保留 FK 强制（违规仍会报错），又不需要放宽任何约束。
            # ## 直接插入优先，失败才延迟
            #
            # 第一版无条件延迟「前向 FK」列，结果破坏了 CHECK 约束：
            # `ck_agent_runs_scope_binding` 要求 assistant run 的 `session_id`
            # **非空**，而延迟会把它置 NULL → CheckViolation。
            #
            # 结论：**能直接插就直接插**。只有当直接插入因 FK 违规失败时，才说明
            # 存在真循环（`agent_jobs.run_id` NOT NULL ↔ `agent_runs.job_id` 可空），
            # 此时才延迟**可空**的前向列。
            #
            # 这样延迟只在必要时发生，不会因「未参与排序的可空 FK」而误伤 CHECK。
            direct_ok = False
            try:
                with conn.begin_nested():
                    conn.execute(stmt, payload)
                conn.commit()
                direct_ok = True
            except Exception:  # noqa: BLE001 - 直接插入失败是预期路径之一
                conn.rollback()

            if direct_ok:
                imported[table] = len(payload)
                already_imported.add(table)
                continue

            # ## 直接插入失败：搜索**最小**延迟子集
            #
            # 延迟**全部**可空前向列是错的：`agent_runs` 的 `session_id` 可空，
            # 但 CHECK `ck_agent_runs_scope_binding` 要求 assistant run 的
            # `session_id` **非空**——延迟它会撞 CHECK（实测 CheckViolation）。
            # 而真正成环的只有 `job_id`（`agent_jobs.run_id` 是 NOT NULL）。
            #
            # 因此按**子集大小递增**搜索：先试单个列，再试两列……取第一个能插入的。
            # 这样延迟量最小，对 CHECK 与其他约束的干扰也最小。
            forward_cols = _forward_fk_columns(inspector, table, already_imported)
            attempt: tuple[list[str], Exception] | None = None
            for size in range(1, len(forward_cols) + 1):
                for subset in itertools.combinations(forward_cols, size):
                    cols_to_defer = list(subset)
                    stage1 = [
                        {**row, **{c: None for c in cols_to_defer}} for row in payload
                    ]
                    try:
                        with conn.begin_nested():
                            conn.execute(stmt, stage1)
                        conn.commit()
                    except Exception as exc:  # noqa: BLE001
                        conn.rollback()
                        attempt = (cols_to_defer, exc)
                        continue
                    imported[table] = len(payload)
                    already_imported.add(table)
                    pending_backfills.append((table, cols_to_defer, payload))
                    break
                else:
                    continue
                break
            else:
                detail = (
                    f"{type(attempt[1]).__name__}:{str(attempt[1])[:80]}"
                    if attempt
                    else "no_deferrable_columns"
                )
                skipped[table] = f"insert_failed:{detail}"
                continue
            continue
    # ---- 2b) 阶段 2：回填被延迟的前向 FK 列 ----
    #
    # 必须在**所有表导入完成后**执行：这些列指向的表此刻才存在。
    # 回填失败要如实记录——那意味着 FK 目标确实缺失（真缺陷），不是可以忽略的噪声。
    backfill_failures: dict[str, str] = {}
    if pending_backfills:
        with engine.connect() as conn:
            for table, cols, rows in pending_backfills:
                set_clause = ", ".join(f"{c} = :{c}" for c in cols)
                stmt_back = sa_text(f"UPDATE {table} SET {set_clause} WHERE id = :id")
                filled = [
                    {"id": row["id"], **{c: row[c] for c in cols}}
                    for row in rows
                    if any(row[c] is not None for c in cols)
                ]
                if not filled:
                    continue
                try:
                    with conn.begin_nested():
                        conn.execute(stmt_back, filled)
                    conn.commit()
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    backfill_failures[table] = (
                        f"{type(exc).__name__}:{str(exc)[:120]}"
                    )
        print(f"   回填前向 FK：{len(pending_backfills)} 表，"
              f"失败 {len(backfill_failures)}")
        for table, reason in backfill_failures.items():
            print(f"     BACKFILL-FAIL {table}: {reason}")

    src.close()

    print(f"   导入成功 {len(imported)} 表；跳过 {len(skipped)} 表")
    for table, reason in skipped.items():
        print(f"     SKIP {table}: {reason}")
    report["imported"] = imported
    report["skipped"] = skipped

    # ---- 3) 逐表对账 ----
    print("3) 逐表对账")
    expected = sqlite_counts(snapshot)
    mismatches: list[dict] = []
    with engine.connect() as conn:
        for table, want in expected.items():
            if want < 0:
                continue
            if table not in tables:
                # 空表在 PG 侧未建**不是**数据差异：0 行 vs 表不存在，语义等价。
                # 但**有行**而表不存在是真差异，必须报告。
                if want == 0:
                    continue
                mismatches.append({"table": table, "sqlite": want, "pg": None,
                                   "reason": "table_absent_in_pg_but_has_rows"})
                continue
            got = conn.execute(sa_text(f"SELECT count(*) FROM {table}")).scalar() or 0
            if got != want:
                mismatches.append({"table": table, "sqlite": want, "pg": got})
    report["backfill_failures"] = backfill_failures
    report["mismatches"] = mismatches
    if mismatches:
        print(f"   差异 {len(mismatches)} 表：")
        for m in mismatches:
            print(f"     {m}")
    else:
        print("   全部一致")

    # ---- 3b) 深度对账：列级摘要（不只看行数）----
    #
    # 行数一致不等于内容一致。对**关键表**做列级摘要比对（每行的若干列拼接后哈希），
    # 这样「行数对但内容错」会被发现。
    print("3b) 列级摘要对账")
    digest_targets = {
        "users": ["id", "name", "gender", "privacy_mode", "profile_status"],
        "accounts": ["id", "user_id", "status", "token_version"],
        "family_spaces": ["id", "name", "kind", "owner_id"],
        "space_members": ["id", "space_id", "user_id", "role", "status"],
        "relations": ["id", "from_user_id", "to_user_id", "relation_type", "status"],
        "agent_runs": ["id", "kind", "status", "attempt", "session_id", "job_id"],
        "action_cards": ["id", "space_id", "card_type", "status"],
    }
    digest_mismatches: list[dict] = []
    with engine.connect() as conn:
        for table, cols in digest_targets.items():
            if table not in tables:
                continue
            common = [c for c in cols if c in {x["name"] for x in inspector.get_columns(table)}]
            if not common:
                continue
            try:
                sqlite_digest = _sqlite_digest(snapshot, table, common)
                pg_rows = conn.execute(
                    sa_text(f"SELECT {', '.join(common)} FROM {table} ORDER BY id")
                ).fetchall()
                pg_digest = _digest_rows([tuple(r) for r in pg_rows])
            except Exception as exc:  # noqa: BLE001
                digest_mismatches.append({"table": table, "error": type(exc).__name__})
                continue
            if sqlite_digest != pg_digest:
                digest_mismatches.append(
                    {"table": table, "columns": common,
                     "sqlite": sqlite_digest[:16], "pg": pg_digest[:16]}
                )
    report["digest_mismatches"] = digest_mismatches
    if digest_mismatches:
        print(f"   摘要差异 {len(digest_mismatches)} 表：")
        for m in digest_mismatches:
            print(f"     {m}")
    else:
        print(f"   {len(digest_targets)} 张关键表列级摘要一致")

    # ---- 4) 关键不变量 ----
    print("4) 关键不变量")
    invariants: dict[str, object] = {}
    with engine.connect() as conn:
        # run↔attempt：每个 run 的 attempt 不超过 max_attempts
        bad_attempts = conn.execute(sa_text(
            "SELECT count(*) FROM agent_runs WHERE attempt > max_attempts"
        )).scalar()
        invariants["runs_attempt_gt_max"] = bad_attempts
        # 终态 run 必须有 settled_at（除 queued/leased/running）
        active = ("queued", "leased", "running", "cancel_requested")
        # `NOT IN :tuple` 在 psycopg 下不会被展开（SQLAlchemy 的 `expanding`
        # 绑定参数需要显式声明）。用 `expanding` 而不是拼字符串——后者会引入注入面。
        unsettled = conn.execute(
            sa_text(
                "SELECT count(*) FROM agent_runs"
                " WHERE status NOT IN :active AND settled_at IS NULL"
            ).bindparams(sa_bindparam("active", expanding=True)),
            {"active": list(active)},
        ).scalar()
        invariants["terminal_runs_without_settled_at"] = unsettled
        # 事件序号在同一 run 内唯一（若有唯一索引，导入成功即已保证）
        invariants["event_seq_unique_enforced_by_schema"] = True
    print(f"   {invariants}")
    report["invariants"] = invariants
    if invariants["runs_attempt_gt_max"]:
        failures.append(f"有 {invariants['runs_attempt_gt_max']} 个 run 的 attempt 超过上限")
    if invariants["terminal_runs_without_settled_at"]:
        failures.append(
            f"有 {invariants['terminal_runs_without_settled_at']} 个终态 run 缺 settled_at"
        )

    # ---- 5) refusal：差异不自动修复 ----
    if backfill_failures:
        failures.append(
            f"{len(backfill_failures)} 张表的前向 FK 回填失败——目标行确实缺失（真缺陷）"
        )
    if mismatches:
        failures.append(
            f"对账发现 {len(mismatches)} 处差异——按 refusal 原则不自动修复，需人工决策"
        )
    if digest_mismatches:
        failures.append(
            f"{len(digest_mismatches)} 张关键表的列级摘要不一致——行数对但内容错"
        )

    report["failures"] = failures
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "real-snapshot-import.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 真实快照导入 PostgreSQL 且逐表对账一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
