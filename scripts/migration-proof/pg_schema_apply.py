"""C9：把完整 PostgreSQL schema 应用到目标库（幂等、可复跑）。

## 为什么需要它

`create_all` 只能建 ORM 元数据里有的东西。PostgreSQL 部署还缺三类**元数据外**对象：

| 缺失对象 | 数量 | 后果 |
|---|---|---|
| SQLite 触发器的 plpgsql 等价物 | 66 | 不变量静默消失（scope/evidence 不可变性、revision 计数） |
| `writer_state` 表（迁移 0058） | 1 | `/ready` 回落默认阶段，epoch 守卫失效 |
| 容量门的 `capacity_*` 列（迁移 0057） | 2×2 | 归还走 Core SQL → `UndefinedColumn`，配额路径直接报错 |
| PGroonga 索引 + 扩展 | 1 | 中文词法检索退化为无索引 LIKE |

前两轮实际部署正是这样失败的：`create_all` 建出 89 张表，但触发器 0 个、
`writer_state` 不存在、gate 列不存在。因此必须有一条**完整且幂等**的 schema 路径。

## 为什么不重放历史 Alembic

实测：历史链在 PostgreSQL 上**无法运行**（`0008` 就报
`operator does not exist: boolean = integer`）。原因是这些迁移按 SQLite 的宽松类型
语义写（SQLite 用 0/1 表示布尔，PostgreSQL 是真正的 BOOLEAN）。因此 PostgreSQL 走
**专用 baseline**：ORM 元数据 + 显式补齐元数据外对象，而不是重放历史。

## 幂等

每一步都是 `IF NOT EXISTS` / 存在性检查，可反复执行。这是必需的：部署会重跑，
而重跑不能失败，也不能重复建对象。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    override = os.environ.get("MIGRATION_PROOF_ROOT")
    if override:
        return Path(override)
    return Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True, cwd=Path(__file__).parent
        ).strip()
    )


ROOT = _repo_root()
BACKEND = ROOT / "backend"
OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", str(ROOT / "artifacts/migration-proof")))

#: 迁移 0058 的 `writer_state`。此处重述而非 import 迁移模块：迁移文件是历史快照，
#: 不应被运行时依赖（那会让「改迁移」意外改变部署行为）。
WRITER_STATE_DDL = """
CREATE TABLE IF NOT EXISTS writer_state (
  id integer PRIMARY KEY,
  stage varchar(16) NOT NULL,
  epoch integer NOT NULL DEFAULT 0,
  updated_at timestamptz,
  updated_by varchar(128),
  CONSTRAINT ck_writer_state_stage
    CHECK (stage IN ('sqlite','shadow','pg_control','pg_all')),
  CONSTRAINT ck_writer_state_epoch_non_negative CHECK (epoch >= 0),
  CONSTRAINT ck_writer_state_singleton CHECK (id = 1)
);
"""

#: 迁移 0057 的容量门列。加列是幂等的（`IF NOT EXISTS`）。
CAPACITY_GATE_DDL = (
    "ALTER TABLE steward_model_calls"
    " ADD COLUMN IF NOT EXISTS capacity_acquired_at timestamptz",
    "ALTER TABLE steward_model_calls"
    " ADD COLUMN IF NOT EXISTS capacity_released_at timestamptz",
    "ALTER TABLE steward_jobs"
    " ADD COLUMN IF NOT EXISTS capacity_acquired_at timestamptz",
    "ALTER TABLE steward_jobs"
    " ADD COLUMN IF NOT EXISTS capacity_released_at timestamptz",
)

#: 容量 CHECK 必须包含集群级资源名。`create_all` 用的是**旧**枚举（缺 cluster_*），
#: 因此这里显式替换——否则集群计数行会被数据库拒绝。
CAPACITY_RESOURCE_KINDS = (
    "assistant_run",
    "steward_job",
    "steward_assist",
    "tool",
    "provider_stream",
    "cluster_provider",
    "cluster_tool",
)


def _apply_capacity_check(conn) -> None:
    """把 resource_kind 的 CHECK 对齐到当前枚举（含 cluster_*）。

    ## 为什么必须按**模式**匹配约束名

    ORM 用 naming convention，因此 `create_all` 建出的约束名是
    `ck_agent_capacity_counters_ck_acc_resource_kind`（表名前缀 + 逻辑名）。
    若按精确名 `ck_acc_resource_kind` 去 DROP，会**漏掉旧约束**并新增一条同名新约束：
    两条并存，旧的那条（不含 cluster_*）仍然生效 → 集群计数行被拒绝。

    实测症状：`bootstrap` 插入 `cluster_provider` 时
    `CheckViolation ... ck_agent_capacity_counters_ck_acc_resource_kind`。

    因此先按 `%acc_resource_kind%` 删掉**所有**候选，再建一条规范命名的。
    """
    values = ",".join(f"'{k}'" for k in CAPACITY_RESOURCE_KINDS)
    rows = conn.execute(
        "SELECT conname FROM pg_constraint c"
        " JOIN pg_class t ON t.oid = c.conrelid"
        " WHERE t.relname = 'agent_capacity_counters' AND c.contype = 'c'"
        " AND c.conname LIKE '%acc_resource_kind%'"
    ).fetchall()
    for (name,) in rows:
        conn.execute(
            f'ALTER TABLE agent_capacity_counters DROP CONSTRAINT IF EXISTS "{name}"'
        )
    conn.execute(
        "ALTER TABLE agent_capacity_counters"
        f" ADD CONSTRAINT ck_acc_resource_kind CHECK (resource_kind IN ({values}))"
    )


def main() -> int:
    try:
        import psycopg  # noqa: F401
        from sqlalchemy import create_engine, text as sa_text
    except ImportError:
        print("SKIP: 需要 psycopg 与 sqlalchemy")
        return 2
    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL（不得指向开发库/线上）")
        return 2

    plain = dsn.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("DATA_DIR", "/tmp/fg-schema-apply")

    import app.models  # noqa: F401,E402
    from app.models.base import Base  # noqa: E402

    engine = create_engine(dsn)
    failures: list[str] = []
    steps: dict[str, object] = {}

    # 1) ORM 元数据
    try:
        Base.metadata.create_all(engine)
        steps["create_all"] = True
        print("  [OK ] ORM metadata create_all")
    except Exception as exc:  # noqa: BLE001
        steps["create_all"] = False
        failures.append(f"create_all 失败：{type(exc).__name__}: {exc}")
        print(f"  [BAD] create_all -> {type(exc).__name__}: {str(exc)[:160]}")

    # 2) 扩展（必须在索引之前）
    extensions: list[str] = []
    with engine.begin() as conn:
        for ext in ("vector", "pgroonga"):
            try:
                conn.execute(sa_text(f"CREATE EXTENSION IF NOT EXISTS {ext}"))
                extensions.append(ext)
            except Exception as exc:  # noqa: BLE001
                # 缺扩展是**环境阻塞**：如实记录，并让后续断言失败。
                extensions.append(f"MISSING:{ext}:{type(exc).__name__}")
                print(f"  [BAD] 扩展 {ext} 不可用：{type(exc).__name__}")
    steps["extensions"] = extensions
    if any(str(e).startswith("MISSING:") for e in extensions):
        failures.append(f"扩展缺失：{extensions}")

    # 3) 元数据外对象
    from app.services import rag_search_provider

    with engine.begin() as conn:
        conn.execute(sa_text(WRITER_STATE_DDL))
        for stmt in CAPACITY_GATE_DDL:
            conn.execute(sa_text(stmt))
        _apply_capacity_check(conn.connection.driver_connection)
        if not any(str(e).startswith("MISSING:") for e in extensions):
            conn.execute(sa_text(rag_search_provider.PGROONGA_INDEX_DDL))
    print("  [OK ] writer_state / 容量门列 / 容量 CHECK / PGroonga 索引")

    # 4) 触发器等价物（复用 C1 的转换脚本）
    try:
        trigger_script = ROOT / "scripts/migration-proof/pg_trigger_equivalents.py"
        env = {**os.environ, "PGTEST_DSN": dsn}
        proc = subprocess.run(
            [sys.executable, str(trigger_script)],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
            check=False,
        )
        if proc.returncode == 0:
            print("  [OK ] 触发器等价物已应用")
        else:
            failures.append(f"触发器应用失败（exit {proc.returncode}）")
            # 打印**完整** stderr：截断会让真正的错误行消失（实测 -300 只留下
            # SQLAlchemy 的 "Background on this error" 尾巴）。
            print(f"  [BAD] 触发器脚本 exit={proc.returncode}")
            print("  --- stderr ---")
            print(proc.stderr[-2000:])
            print("  --- stdout tail ---")
            print(proc.stdout[-800:])
    except Exception as exc:  # noqa: BLE001
        failures.append(f"触发器应用异常：{type(exc).__name__}: {exc}")

    # 5) 验证：断言元数据外对象**真的存在**（而不是只看命令 exit 0）
    import psycopg

    with psycopg.connect(plain) as conn:
        def scalar(sql: str) -> int:
            return conn.execute(sql).fetchone()[0]

        triggers = scalar("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal")
        writer_state = scalar(
            "SELECT count(*) FROM information_schema.tables"
            " WHERE table_schema='public' AND table_name='writer_state'"
        )
        gate_cols = scalar(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE column_name IN ('capacity_acquired_at','capacity_released_at')"
        )
        pgroonga_idx = scalar(
            "SELECT count(*) FROM pg_indexes WHERE indexname='ix_rag_chunks_pgroonga'"
        )
        pgroonga_ext = scalar(
            "SELECT count(*) FROM pg_extension WHERE extname='pgroonga'"
        )
        # 检查**所有** resource_kind 约束都必须含 cluster_*：只要有一条旧约束残留，
        # 集群计数行就会被拒。因此不能只看一条（实测正是漏了带前缀的旧约束）。
        defs = [
            row[0]
            for row in conn.execute(
                "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c"
                " JOIN pg_class t ON t.oid = c.conrelid"
                " WHERE t.relname='agent_capacity_counters' AND c.contype='c'"
                " AND c.conname LIKE '%acc_resource_kind%'"
            ).fetchall()
        ]
        cluster_in_check = bool(defs) and all(
            "cluster_provider" in d for d in defs
        )

    # 6) alembic 版本标记。
    #
    # ## 为什么必须有这一步
    #
    # PostgreSQL 的 schema 由**本脚本**（ORM 元数据 + 元数据外对象）建立，而不是由
    # 历史 Alembic 链重放——那条链在 PG 上根本跑不通（0008 就报
    # `boolean = integer`，实测）。
    #
    # 但容器启动命令是 `alembic upgrade head && python -m app.serve`。若
    # `alembic_version` 不存在，容器每次启动都会尝试**从头重放整条链**：要么在
    # 0008 崩掉、要么在已有表上重复建表。因此必须把版本**标记**为当前 head，
    # 使 `upgrade head` 成为 no-op。
    #
    # 实测 dev 上就是缺这一步：PG 库没有 `alembic_version`，只因 dev 用 systemd
    # 直接跑 `app.serve`（不经 alembic）才没暴露；生产用容器 CMD 就会立刻失败。
    try:
        from alembic.config import Config as _AlembicConfig
        from alembic.script import ScriptDirectory as _ScriptDirectory

        backend_dir = ROOT / "backend"
        cfg = _AlembicConfig(str(backend_dir / "alembic.ini"))
        cfg.set_main_option("script_location", str(backend_dir / "migrations"))
        head = _ScriptDirectory.from_config(cfg).get_current_head()
        if head is None:
            failures.append("无法解析 alembic head（script_location 是否正确？）")
        else:
            with psycopg.connect(plain) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
                conn.execute("DELETE FROM alembic_version")
                conn.execute("INSERT INTO alembic_version (version_num) VALUES (%s)", (head,))
                conn.commit()
            print(f"  [OK ] alembic_version 标记为 {head}（upgrade head 成为 no-op）")
            steps["alembic_stamped"] = head
    except Exception as exc:  # noqa: BLE001
        failures.append(f"alembic 版本标记失败：{type(exc).__name__}: {exc}")
        steps["resource_kind_constraints"] = defs

    steps.update(
        {
            "triggers": triggers,
            "writer_state": writer_state,
            "gate_columns": gate_cols,
            "pgroonga_index": pgroonga_idx,
            "pgroonga_extension": pgroonga_ext,
            "cluster_in_check": cluster_in_check,
        }
    )

    print(
        f"  触发器={triggers} writer_state={writer_state} gate列={gate_cols} "
        f"pgroonga索引={pgroonga_idx} 扩展={pgroonga_ext} cluster枚举={cluster_in_check}"
    )

    if triggers < 60:
        failures.append(f"触发器只有 {triggers} 个（预期 >= 60）：不变量会静默消失")
    if writer_state != 1:
        failures.append("writer_state 表不存在：epoch 守卫与 /ready 会失准")
    if gate_cols != 4:
        failures.append(f"容量门列只有 {gate_cols} 个（预期 4）：归还路径会 UndefinedColumn")
    if pgroonga_ext and pgroonga_idx != 1:
        failures.append("PGroonga 索引缺失：中文词法检索会退化为无索引扫描")
    if not cluster_in_check:
        failures.append("容量 CHECK 缺 cluster_*：集群计数行会被拒绝")

    report = {"steps": steps, "failures": failures}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "pg-schema-apply.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 完整 schema 已应用（含元数据外对象），全部断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
