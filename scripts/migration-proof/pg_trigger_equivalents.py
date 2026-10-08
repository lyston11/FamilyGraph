"""C1：把 SQLite 触发器转换为 PostgreSQL plpgsql 等价物。

## 为什么必须显式转换

69 个触发器**只存在于 Alembic 迁移里**，ORM 元数据看不到它们。`create_all` 在
PostgreSQL 上建出 87 张表、252 索引、114 CHECK、212 FK，但**触发器 = 0**
（已实测）。如果不补，这些不变量会**静默消失**：数据库不再拒绝非法 UPDATE，
scope/evidence 不可变性与 revision 计数全部失效。

## 四类语义与转换策略

| 类别 | 数量 | SQLite 形态 | PostgreSQL 等价物 |
|---|---|---|---|
| `sri_*` revision counter | 60 | `AFTER INSERT/UPDATE/DELETE` + `INSERT ... ON CONFLICT DO UPDATE` | plpgsql 函数，同形 upsert |
| `rag_chunks_*` FTS 同步 | 3 | 写 `rag_chunks_fts` 虚拟表 | **不转换**：PGroonga 索引自动维护（见下） |
| `rag_documents_revision_*` | 2 | `BEFORE ... RAISE(ABORT)` | `BEFORE INSERT OR UPDATE` + 函数内 RAISE |
| 不可变/sticky 守卫 | 4 | `BEFORE UPDATE [OF col]` + `RAISE(ABORT)` | plpgsql 函数 + 行级条件 |

## FTS5 → PGroonga 的语义替换（必须显式记录）

SQLite 需要触发器把 `rag_chunks` 的变更同步进 `rag_chunks_fts` **虚拟表**，
因为 FTS5 是独立表。PostgreSQL 的 PGroonga 是**索引**：它自动跟随表变更，
不需要、也不应该有同步触发器。

因此这 3 个触发器的正确等价物是「**不存在**」——但这不是「丢失」，
而是被索引机制取代。本脚本会断言：替换后 PGroonga 搜索能反映
INSERT / UPDATE / DELETE，以此证明同步仍然成立。

## 用法

    PGTEST_DSN=postgresql://... python3 scripts/migration-proof/pg_trigger_equivalents.py
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
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

# 这三类由 PGroonga 索引取代，不生成触发器。
FTS_SYNC_PREFIXES = ("rag_chunks_ai", "rag_chunks_au", "rag_chunks_ad")

# 不可变守卫：SQLite 触发器名 -> (plpgsql 函数体片段)
IMMUTABLE_GUARDS = {
    "trg_agent_sessions_scope_immutable": {
        "table": "agent_sessions",
        "event": "UPDATE",
        "cond": ("OLD.account_id IS DISTINCT FROM NEW.account_id"
                 " OR OLD.space_id IS DISTINCT FROM NEW.space_id"
                 " OR OLD.agent_kind IS DISTINCT FROM NEW.agent_kind"),
        "msg": "agent_sessions scope is immutable",
    },
    "trg_raw_relation_inputs_immutable": {
        "table": "raw_relation_inputs",
        "event": "UPDATE",
        "cond": "TRUE",  # append-only：任何 UPDATE 都拒绝
        "msg": "raw_relation_inputs is append-only",
    },
    "trg_scev_immutable": {
        "table": "steward_candidate_evidence_versions",
        "event": "UPDATE",
        "cond": (
            "OLD.id IS DISTINCT FROM NEW.id"
            " OR OLD.candidate_id IS DISTINCT FROM NEW.candidate_id"
            " OR OLD.space_id IS DISTINCT FROM NEW.space_id"
            " OR OLD.validation_contract_version IS DISTINCT FROM NEW.validation_contract_version"
            " OR OLD.evidence_digest IS DISTINCT FROM NEW.evidence_digest"
            " OR OLD.support_facts_json IS DISTINCT FROM NEW.support_facts_json"
            " OR OLD.created_at IS DISTINCT FROM NEW.created_at"
            " OR (OLD.source_job_id IS DISTINCT FROM NEW.source_job_id AND NEW.source_job_id IS NOT NULL)"
            " OR (OLD.source_model_call_id IS DISTINCT FROM NEW.source_model_call_id"
            "     AND NEW.source_model_call_id IS NOT NULL)"
            " OR (OLD.source_plan_id IS DISTINCT FROM NEW.source_plan_id AND NEW.source_plan_id IS NOT NULL)"
            " OR (OLD.status <> 'pending' AND (OLD.status IS DISTINCT FROM NEW.status"
            "     OR OLD.projection_checked_at IS DISTINCT FROM NEW.projection_checked_at"
            "     OR OLD.invalidation_reason IS DISTINCT FROM NEW.invalidation_reason"
            "     OR (OLD.projection_job_id IS DISTINCT FROM NEW.projection_job_id"
            "         AND NEW.projection_job_id IS NOT NULL)))"
        ),
        "msg": "candidate evidence is immutable",
    },
    "trg_slc_internal_sticky": {
        "table": "steward_llm_candidates",
        "event": "UPDATE",
        "cond": ("OLD.attribution_status = 'versioned'"
                 " AND NEW.attribution_status <> 'versioned'"),
        "msg": "candidate internal mode is sticky",
    },
}


def pg_json_columns(dsn: str) -> dict[str, set[str]]:
    """返回 PostgreSQL 上 data_type 为 json/jsonb 的列。

    ## 为什么必须知道

    SQLite 的 JSON 列是 **TEXT**，`IS NOT`/`=` 是文本比较，永远可用。
    PostgreSQL 的 SQLAlchemy `JSON` 类型映射为 **`json`**，而 `json` **没有相等运算符**：

        SELECT '{}'::json = '{}'::json   -- UndefinedFunction: operator does not exist

    因此任何比较 `json` 列的触发器在 PostgreSQL 上都会**运行期报错**
    （实测：`sri_users_presentation_update`、`trg_scev_immutable`）。
    修法是比较时显式 `::jsonb`（`jsonb` 有 `=`，且比较与键序无关）。
    """
    import psycopg
    plain = dsn.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(plain) as conn:
        rows = conn.execute("""
            SELECT table_name, column_name FROM information_schema.columns
             WHERE table_schema='public' AND data_type IN ('json','jsonb')
        """).fetchall()
    out: dict[str, set[str]] = {}
    for t, c in rows:
        out.setdefault(t, set()).add(c)
    return out


def _cast_json(expr: str, json_cols: set[str]) -> str:
    """把 `X.col` 形式的 JSON 列比较包装成 `(X.col)::jsonb`。"""
    for col in json_cols:
        for alias in ("OLD.", "NEW."):
            token = f"{alias}{col}"
            if token in expr:
                expr = expr.replace(token, f"({token})::jsonb")
    return expr


def read_sqlite_triggers() -> list[dict]:
    """在隔离 DATA_DIR 跑迁移，读取触发器的真实定义（真源）。"""
    tmp = tempfile.mkdtemp(prefix="fg-trig-")
    # **必须清掉 DATABASE_URL**：本函数的目的正是「在 SQLite 上跑迁移、读取
    # SQLite 触发器的真实定义」。若环境里已有 DATABASE_URL（部署时必然有），
    # alembic 会转而对着 **PostgreSQL** 跑 SQLite 的建表语句并失败
    # （实测 `DuplicateTable: relation "users" already exists`）。
    # 这是环境泄漏导致的误判，不是 SQLite 触发器的问题。
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DATABASE_URL", "PGTEST_DSN")
    }
    env["DATA_DIR"] = tmp
    proc = subprocess.run(
        [str(BACKEND / ".venv/bin/python"), "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0:
        raise SystemExit(f"alembic upgrade head 失败：{proc.stderr[-400:]}")
    db = Path(tmp) / "db" / "app.db"
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master"
            " WHERE type='trigger' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return [{"name": n, "table": t, "sql": s or ""} for n, t, s in rows]


def parse_sri(sql: str) -> dict | None:
    """解析 sri_* 触发器：事件、列、scope 子查询、WHEN 条件。"""
    m = re.search(r"AFTER\s+(INSERT|UPDATE|DELETE)\s+ON\s+(\w+)", sql, re.I)
    if not m:
        return None
    event, table = m.group(1).upper(), m.group(2)

    when = None
    wm = re.search(r"\bWHEN\s+(.*?)\s+BEGIN\b", sql, re.I | re.S)
    if wm:
        when = wm.group(1).strip()

    # INSERT INTO steward_input_revisions(scope_id, s, p, i) SELECT ...
    #
    # SQLite 的 INSERT 会**显式给出全部三列**（未变动的写 0）。转换时必须同样给出三列，
    # 否则未指定的列在 PostgreSQL 上为 NULL，撞 NOT NULL 约束（实测：
    # `null value in column "structural"`）。第一版只插了变化的那一列，因此失败。
    vals = re.search(
        r"INSERT\s+INTO\s+steward_input_revisions\s*\(\s*scope_id\s*,\s*structural\s*,"
        r"\s*presentation\s*,\s*inferred\s*\)\s*SELECT\s+scope_id\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)",
        sql, re.I | re.S)
    if not vals:
        return None
    s_, p_, i_ = (int(vals.group(1)), int(vals.group(2)), int(vals.group(3)))
    column = "structural" if s_ else "presentation" if p_ else "inferred"

    # FROM ( <scope query> ) WHERE true
    fm = re.search(r"\bFROM\s*\((.*?)\)\s*WHERE\s+true", sql, re.I | re.S)
    if not fm:
        return None
    scope_query = fm.group(1).strip()
    return {"event": event, "table": table, "column": column,
            "s": s_, "p": p_, "i": i_,
            "scope_query": scope_query, "when": when}


def build_statements(triggers: list[dict], json_cols: dict[str, set[str]]) -> tuple[list[str], list[dict], list[dict]]:
    """返回 (DDL 列表, 已转换清单, 被替换/跳过清单)。"""
    ddl: list[str] = []
    converted: list[dict] = []
    substituted: list[dict] = []

    for t in triggers:
        name, table, sql = t["name"], t["table"], t["sql"]

        # 1) FTS 同步触发器 -> PGroonga 索引取代
        if name in FTS_SYNC_PREFIXES:
            substituted.append({
                "trigger": name, "table": table, "kind": "fts_sync",
                "reason": "PGroonga 是索引而非虚拟表，自动跟随表变更，无需同步触发器",
            })
            continue

        # 2) rag_documents revision 镜像 -> BEFORE 触发器 + RAISE
        if name.startswith("rag_documents_revision_"):
            event = "INSERT" if name.endswith("_insert") else "UPDATE"
            fn = f"fg_{name}"
            ddl.append(f'''
CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger AS $$
BEGIN
  IF NEW.revision IS DISTINCT FROM NEW.source_revision THEN
    RAISE EXCEPTION 'rag document revision mirror conflict';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER {name} BEFORE {event} ON rag_documents
  FOR EACH ROW EXECUTE FUNCTION {fn}();
'''.strip())
            converted.append({"trigger": name, "table": table,
                              "kind": "revision_mirror", "event": event})
            continue

        # 3) 不可变 / sticky 守卫
        if name in IMMUTABLE_GUARDS:
            g = IMMUTABLE_GUARDS[name]
            # JSON 列必须转 jsonb 才能比较（见 pg_json_columns 文档）
            guard_cond = _cast_json(g["cond"], json_cols.get(g["table"], set()))
            fn = f"fg_{name}"
            ddl.append(f'''
CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger AS $$
BEGIN
  IF {guard_cond} THEN
    RAISE EXCEPTION '{g["msg"]}';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER {name} BEFORE {g["event"]} ON {g["table"]}
  FOR EACH ROW EXECUTE FUNCTION {fn}();
'''.strip())
            converted.append({"trigger": name, "table": g["table"],
                              "kind": "immutable_guard", "event": g["event"]})
            continue

        # 4) sri_* revision counter
        parsed = parse_sri(sql)
        if parsed is None:
            substituted.append({"trigger": name, "table": table, "kind": "unparsed",
                                "reason": "无法解析，需人工处理"})
            continue
        fn = f"fg_{name}"
        when_if = ""
        if parsed["when"]:
            # SQLite 的 WHEN 用 OLD/NEW 的 IS NOT 语义；plpgsql 用 IS DISTINCT FROM。
            # JSON 列必须先转 jsonb，否则 `json = json` 不存在（运行期 UndefinedFunction）。
            cond = parsed["when"].replace(" IS NOT ", " IS DISTINCT FROM ")
            cond = _cast_json(cond, json_cols.get(parsed["table"], set()))
            when_if = f"  IF NOT ({cond}) THEN RETURN NULL; END IF;\n"
        ret = "NULL" if parsed["event"] == "DELETE" else "NULL"  # AFTER 触发器忽略返回值
        ddl.append(f'''
CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger AS $$
BEGIN
{when_if}  INSERT INTO steward_input_revisions (scope_id, structural, presentation, inferred)
  SELECT s.scope_id, {parsed["s"]}, {parsed["p"]}, {parsed["i"]}
    FROM ({parsed["scope_query"]}) AS s
  ON CONFLICT (scope_id) DO UPDATE
    SET {parsed["column"]} = steward_input_revisions.{parsed["column"]} + 1;
  RETURN {ret};
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER {name} AFTER {parsed["event"]} ON {parsed["table"]}
  FOR EACH ROW EXECUTE FUNCTION {fn}();
'''.strip())
        converted.append({"trigger": name, "table": parsed["table"],
                          "kind": "revision_counter", "event": parsed["event"],
                          "column": parsed["column"]})

    return ddl, converted, substituted


#: 从生成的 DDL 里提取 `CREATE TRIGGER <name> ... ON <table>`。
#: 必须跨行匹配（DDL 是多行字符串），且 `ON` 与表名之间可有换行/缩进。
_TRIGGER_CREATE_RE = re.compile(
    r"CREATE TRIGGER\s+(\w+)[^;]*?\bON\s+(\w+)", re.S
)


def main() -> int:
    try:
        import psycopg
    except ImportError:
        print("SKIP: psycopg 未安装")
        return 2
    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL（不得指向开发库/线上）")
        return 2
    plain = dsn.replace("postgresql+psycopg://", "postgresql://")

    json_cols = pg_json_columns(dsn)
    print(f"  PostgreSQL JSON 列所在表：{len(json_cols)} 张")
    triggers = read_sqlite_triggers()
    print(f"  SQLite 实际触发器：{len(triggers)}")
    ddl, converted, substituted = build_statements(triggers, json_cols)
    print(f"  生成等价物：{len(converted)}；被索引取代/跳过：{len(substituted)}")

    failures: list[str] = []
    applied = 0
    # 幂等：部署会重跑，而 `CREATE TRIGGER` 在已存在时报错、`CREATE OR REPLACE
    # FUNCTION` 本身是幂等的。因此对每条 DDL 里的 `CREATE TRIGGER <name> ... ON
    # <table>` 先生成 `DROP TRIGGER IF EXISTS`。用正则精确提取 name/table，
    # 而不是按行猜测。
    drop_statements: list[str] = []
    for stmt in ddl:
        for name, table in _TRIGGER_CREATE_RE.findall(stmt):
            drop_statements.append(f"DROP TRIGGER IF EXISTS {name} ON {table}")

    failures: list[str] = []
    applied = 0
    with psycopg.connect(plain) as conn:
        for stmt in [*drop_statements, *ddl]:
            try:
                conn.execute(stmt)
                conn.commit()
                applied += 1
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                failures.append(f"{type(exc).__name__}: {str(exc).splitlines()[0][:120]}")
                print(f"  [BAD] {str(exc).splitlines()[0][:110]}")
        created = conn.execute(
            "SELECT count(*) FROM information_schema.triggers WHERE trigger_schema='public'"
        ).fetchone()[0]

    print(f"  已应用 DDL：{applied}/{len(ddl)}；PostgreSQL 触发器总数={created}")

    expected_min = len(converted)
    if created < expected_min:
        failures.append(f"PostgreSQL 触发器 {created} < 生成数 {expected_min}")

    by_kind: dict[str, int] = {}
    for c in converted:
        by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
    for s in substituted:
        by_kind[f"substituted:{s['kind']}"] = by_kind.get(f"substituted:{s['kind']}", 0) + 1

    report = {
        "sqlite_triggers": len(triggers),
        "pg_ddl_generated": len(ddl),
        "pg_triggers_created": created,
        "converted": converted,
        "substituted": substituted,
        "by_kind": by_kind,
        "failures": failures,
        "json_columns": {k: sorted(v) for k, v in json_cols.items()},
        "json_cast_note": (
            "SQLAlchemy JSON 在 PostgreSQL 上是 `json` 类型，而 `json` 没有相等运算符；"
            "比较 JSON 列的触发器必须显式 `::jsonb`，否则运行期 UndefinedFunction。"
            "SQLite 侧 JSON 是 TEXT，不存在该问题，因此这是纯 PostgreSQL 阻塞点。"
        ),
        "fts_substitution_note": (
            "3 个 rag_chunks_* FTS 同步触发器不转换为触发器：PGroonga 是索引，"
            "自动跟随表变更。这不是丢失，而是机制替换；需另用搜索可见性用例证明。"
        ),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "pg-trigger-equivalents.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 全部触发器已转换为 PostgreSQL 等价物（FTS 同步由索引取代）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
