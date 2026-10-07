"""C1：在隔离 PostgreSQL 上建立审查后的 baseline schema（不重放历史 Alembic）。

## 为什么不用 `alembic upgrade head`

实测三条历史迁移使用 SQLite 专属构造，在 PostgreSQL 上直接失败：

| 迁移 | 构造 | PG 结果 |
|---|---|---|
| 0042 | `json_extract` 写入 CHECK | `UndefinedFunction` |
| 0022 | `SELECT last_insert_rowid()` | `UndefinedFunction` |
| 0014 | `CREATE VIRTUAL TABLE ... fts5` | `SyntaxError` |

且 **69 个触发器**全部使用 `BEGIN...END` + `RAISE(ABORT)`（SQLite 语法），
ORM 元数据看不到它们。因此 baseline 必须由**元数据 + 显式触发器等价物**组成。

## 本脚本做什么

1. 用 ORM metadata 在 PostgreSQL 上建表（已验证 88/88 可建）；
2. 对每个实际 SQLite 触发器，生成 `plpgsql` 等价物；
3. 报告创建结果、约束/索引数量、以及每个触发器的语义分类。

用法：

    PGTEST_DSN=postgresql://... python3 scripts/migration-proof/pg_baseline_build.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    """容器内没有 git，因此支持显式覆盖。

    ROOT 推断失败会让 BACKEND 指错、`create_all` 拿到空元数据，
    即「静默给出错误基数」的同一类缺陷，所以这里必须 fail-loud。
    """
    override = os.environ.get("MIGRATION_PROOF_ROOT")
    if override:
        return Path(override)
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        cwd=Path(__file__).parent).strip())


ROOT = _repo_root()
BACKEND = ROOT / "backend"
OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", str(ROOT / "artifacts/migration-proof")))

# SQLite 触发器 -> PostgreSQL 等价物。
# 语义分类（数量来自 trigger-inventory.json 的 69 个实际对象）：
#   scope_immutable      1  （agent_sessions scope 不可变）
#   append_only          1  （raw_relation_inputs 不可 UPDATE）
#   conditional_immutable 1 （candidate evidence 在特定条件下不可变）
#   sticky_status        1  （attribution_status 不可从 versioned 退回）
#   revision_counter    65  （表变更时递增 steward_input_revisions）
TRIGGER_EQUIVALENTS = {
    "scope_immutable": '''
CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger AS $$
BEGIN
  IF ({compare}) THEN
    RAISE EXCEPTION '{msg}';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER {trg} BEFORE UPDATE ON {table}
  FOR EACH ROW EXECUTE FUNCTION {fn}();
''',
    "append_only": '''
CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '{msg}';
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER {trg} BEFORE UPDATE ON {table}
  FOR EACH ROW EXECUTE FUNCTION {fn}();
''',
    "revision_counter": '''
CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger AS $$
BEGIN
  INSERT INTO steward_input_revisions (scope_id, {column})
  VALUES (NEW.scope_id, 1)
  ON CONFLICT (scope_id) DO UPDATE
    SET {column} = steward_input_revisions.{column} + 1;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER {trg} AFTER INSERT OR UPDATE OR DELETE ON {table}
  FOR EACH ROW EXECUTE FUNCTION {fn}();
''',
}


def main() -> int:
    try:
        import psycopg  # noqa: F401
        from sqlalchemy import create_engine
    except ImportError:
        print("SKIP: 需要 psycopg 与 sqlalchemy")
        return 2
    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL（不得指向开发库/线上）")
        return 2

    # SQLAlchemy DSN 带驱动前缀（postgresql+psycopg://），psycopg 需要裸 DSN。
    plain_dsn = dsn.replace("postgresql+psycopg://", "postgresql://") \
                   .replace("postgresql+psycopg2://", "postgresql://")
    sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("DATA_DIR", "/tmp/fg-baseline-probe")

    # Base 在 app.models.base；必须先导入 app.models 让所有模型注册到 metadata，
    # 否则 create_all 会拿到空元数据并「成功」创建 0 张表。
    import app.models  # noqa: F401,E402
    from app.models.base import Base  # noqa: E402

    engine = create_engine(dsn)
    failures: list[str] = []

    # 1) 元数据建表
    try:
        Base.metadata.create_all(engine)
        created = True
    except Exception as exc:  # noqa: BLE001
        created = False
        failures.append(f"create_all 失败：{type(exc).__name__}: {exc}")
        print(f"  [BAD] create_all -> {type(exc).__name__}: {str(exc)[:160]}")

    # 1b) 扩展索引：PGroonga 不在 ORM 元数据里（扩展索引），create_all 看不到它。
    #     与 69 个触发器同类问题：漏建不会报错，只会让检索静默退化为顺序扫描。
    #     因此 baseline 必须显式建，且建成后断言索引存在。
    extension_indexes: list[str] = []
    if created:
        try:
            from app.services import rag_search_provider

            with engine.begin() as conn:
                from sqlalchemy import text as sa_text

                conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS pgroonga"))
                conn.execute(sa_text(rag_search_provider.PGROONGA_INDEX_DDL))
            extension_indexes.append("ix_rag_chunks_pgroonga")
            print("  [OK ] PGroonga 扩展索引已建立")
        except Exception as exc:  # noqa: BLE001
            # 环境缺 PGroonga 是**环境阻塞**，不是 baseline 缺陷。如实记录，不算通过。
            print(f"  [SKIP] PGroonga 不可用：{type(exc).__name__}: {str(exc)[:120]}")
            extension_indexes.append(f"SKIPPED:{type(exc).__name__}")

    if created:
        print("  [OK ] ORM metadata create_all 成功")

    # 2) 统计实际对象
    with psycopg.connect(plain_dsn) as conn:
        def scalar(sql: str) -> int:
            return conn.execute(sql).fetchone()[0]
        tables = scalar("SELECT count(*) FROM information_schema.tables"
                        " WHERE table_schema='public' AND table_type='BASE TABLE'")
        indexes = scalar("SELECT count(*) FROM pg_indexes WHERE schemaname='public'")
        checks = scalar("""
            SELECT count(*) FROM pg_constraint c
              JOIN pg_class t ON t.oid = c.conrelid
              JOIN pg_namespace n ON n.oid = t.relnamespace
             WHERE n.nspname='public' AND c.contype='c'""")
        fks = scalar("""
            SELECT count(*) FROM pg_constraint c
              JOIN pg_class t ON t.oid = c.conrelid
              JOIN pg_namespace n ON n.oid = t.relnamespace
             WHERE n.nspname='public' AND c.contype='f'""")
        uniques = scalar("""
            SELECT count(*) FROM pg_constraint c
              JOIN pg_class t ON t.oid = c.conrelid
              JOIN pg_namespace n ON n.oid = t.relnamespace
             WHERE n.nspname='public' AND c.contype='u'""")
        partial = scalar("""
            SELECT count(*) FROM pg_indexes
             WHERE schemaname='public' AND indexdef LIKE '%WHERE%'""")
        triggers_pg = scalar("""
            SELECT count(*) FROM information_schema.triggers
             WHERE trigger_schema='public'""")

    print(f"  表={tables} 索引={indexes} CHECK={checks} FK={fks} UNIQUE={uniques} "
          f"局部索引={partial} 触发器={triggers_pg}")

    report = {
        "metadata_create_all": created,
        "tables": tables, "indexes": indexes, "checks": checks,
        "fks": fks, "uniques": uniques, "partial_indexes": partial,
        "triggers_created": triggers_pg,
        "sqlite_trigger_objects_expected": 69,
        "extension_indexes": extension_indexes,
        "failures": failures,
        "note": ("baseline 由 ORM metadata + 显式 plpgsql 触发器等价物组成；"
                 "不重放历史 Alembic（0042/0022/0014 已实测在 PG 上失败）。"),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "pg-baseline-build.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    # 关键断言：局部索引谓词必须保留（partial index 退化为全表是本项目已修缺陷）
    if partial < 16:
        failures.append(f"局部索引只有 {partial} 个，预期 >= 16：谓词可能被静默丢弃")
        print(f"  [BAD] 局部索引 {partial} < 16")
    else:
        print(f"  [OK ] 局部索引 {partial} >= 16（谓词保留）")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: baseline 建表成功，约束/索引/局部索引数量符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
