"""Gate 3：PostgreSQL baseline prototype + 触发器等价物 + 负向用例。

## 为什么需要它

69 个触发器**只存在于 Alembic 迁移里**，ORM 元数据看不到它们。走 PostgreSQL
baseline 时若不显式重写，这些不变量会**静默消失**——不是报错，而是数据库不再拒绝
非法写入。本脚本为四类触发器各建一个 plpgsql 等价物，并用**负向用例**证明它们
真的在拒绝非法操作。

四类（按语义，不是按数量）：

| 类别 | 数量 | 语义 |
|---|---|---|
| scope-immutable | 1 | 会话的 account/space/kind 不可变 |
| append-only | 1 | raw_relation_inputs 不可 UPDATE |
| conditional-immutable | 1 | candidate evidence 在特定条件下不可变 |
| sticky-status | 1 | 内部 attribution_status 不可从 versioned 退回 |
| revision-counter | 60 | 表变更时递增 steward_input_revisions 计数 |

用法：

    PGTEST_DSN=postgresql://... python3 research/tools/pg_baseline_prototype.py

退出码 0 = 全部负向用例按预期被拒绝、正向用例被接受。
"""
from __future__ import annotations

import os
import sys

DDL = """
DROP TABLE IF EXISTS bp_sessions, bp_raw, bp_evidence, bp_candidates,
                     bp_sources, bp_revisions CASCADE;
DROP FUNCTION IF EXISTS bp_guard_session_scope() CASCADE;
DROP FUNCTION IF EXISTS bp_guard_raw_append_only() CASCADE;
DROP FUNCTION IF EXISTS bp_guard_evidence_immutable() CASCADE;
DROP FUNCTION IF EXISTS bp_guard_status_sticky() CASCADE;
DROP FUNCTION IF EXISTS bp_bump_revision() CASCADE;

CREATE TABLE bp_sessions (
  id int PRIMARY KEY, account_id int NOT NULL, space_id int NOT NULL, agent_kind text NOT NULL
);
CREATE TABLE bp_raw (id int PRIMARY KEY, payload text NOT NULL);
CREATE TABLE bp_evidence (
  id int PRIMARY KEY, content_sha256 text, source_job_id int, projection_job_id int
);
CREATE TABLE bp_candidates (id int PRIMARY KEY, attribution_status text NOT NULL);

CREATE TABLE bp_sources (id int PRIMARY KEY, scope_id int NOT NULL, title text);
CREATE TABLE bp_revisions (
  scope_id int PRIMARY KEY, structural int NOT NULL DEFAULT 0,
  presentation int NOT NULL DEFAULT 0, inferred int NOT NULL DEFAULT 0
);

-- 1) scope-immutable：对应 trg_agent_sessions_scope_immutable
CREATE FUNCTION bp_guard_session_scope() RETURNS trigger AS $$
BEGIN
  IF OLD.account_id IS DISTINCT FROM NEW.account_id
     OR OLD.space_id IS DISTINCT FROM NEW.space_id
     OR OLD.agent_kind IS DISTINCT FROM NEW.agent_kind THEN
    RAISE EXCEPTION 'agent_sessions scope is immutable';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_bp_session_scope BEFORE UPDATE ON bp_sessions
  FOR EACH ROW EXECUTE FUNCTION bp_guard_session_scope();

-- 2) append-only：对应 trg_raw_relation_inputs_immutable
CREATE FUNCTION bp_guard_raw_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'raw_relation_inputs is append-only';
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_bp_raw_append_only BEFORE UPDATE ON bp_raw
  FOR EACH ROW EXECUTE FUNCTION bp_guard_raw_append_only();

-- 3) conditional-immutable：对应 trg_scev_immutable
CREATE FUNCTION bp_guard_evidence_immutable() RETURNS trigger AS $$
BEGIN
  IF OLD.content_sha256 IS NOT NULL AND OLD.content_sha256 IS DISTINCT FROM NEW.content_sha256 THEN
    RAISE EXCEPTION 'candidate evidence is immutable';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_bp_evidence_immutable BEFORE UPDATE ON bp_evidence
  FOR EACH ROW EXECUTE FUNCTION bp_guard_evidence_immutable();

-- 4) sticky-status：对应 trg_slc_internal_sticky
CREATE FUNCTION bp_guard_status_sticky() RETURNS trigger AS $$
BEGIN
  IF OLD.attribution_status = 'versioned' AND NEW.attribution_status <> 'versioned' THEN
    RAISE EXCEPTION 'candidate internal mode is sticky';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_bp_status_sticky BEFORE UPDATE ON bp_candidates
  FOR EACH ROW EXECUTE FUNCTION bp_guard_status_sticky();

-- 5) revision-counter：对应 60 个 sri_* 触发器
CREATE FUNCTION bp_bump_revision() RETURNS trigger AS $$
BEGIN
  INSERT INTO bp_revisions (scope_id, structural) VALUES (NEW.scope_id, 1)
  ON CONFLICT (scope_id) DO UPDATE SET structural = bp_revisions.structural + 1;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_bp_bump_revision AFTER INSERT OR UPDATE OR DELETE ON bp_sources
  FOR EACH ROW EXECUTE FUNCTION bp_bump_revision();
"""

SEED = """
INSERT INTO bp_sessions VALUES (1, 10, 20, 'assistant');
INSERT INTO bp_raw VALUES (1, 'p');
INSERT INTO bp_evidence VALUES (1, 'hash-a', 1, NULL);
INSERT INTO bp_candidates VALUES (1, 'versioned');
INSERT INTO bp_sources VALUES (1, 7, 't');
"""


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

    conn = psycopg.connect(dsn, connect_timeout=8)
    conn.execute(DDL)
    conn.execute(SEED)
    conn.commit()

    failures: list[str] = []

    def expect(sql: str, *, blocked: bool, label: str) -> None:
        try:
            conn.execute(sql)
            conn.commit()
            got_blocked = False
            detail = "accepted"
        except Exception as exc:  # noqa: BLE001 - 探针记录任意 PG 错误
            conn.rollback()
            got_blocked = True
            detail = f"{type(exc).__name__}: {str(exc).splitlines()[0][:70]}"
        ok = got_blocked == blocked
        print(f"  [{'OK ' if ok else 'BAD'}] {label}（{'应拒绝' if blocked else '应接受'}）-> {detail}")
        if not ok:
            failures.append(label)

    print("负向：非法操作必须被拒绝")
    expect("UPDATE bp_sessions SET account_id = 99 WHERE id = 1", blocked=True,
           label="scope-immutable 改 account_id")
    expect("UPDATE bp_sessions SET space_id = 99 WHERE id = 1", blocked=True,
           label="scope-immutable 改 space_id")
    expect("UPDATE bp_sessions SET agent_kind = 'steward' WHERE id = 1", blocked=True,
           label="scope-immutable 改 agent_kind")
    expect("UPDATE bp_raw SET payload = 'changed' WHERE id = 1", blocked=True,
           label="append-only UPDATE")
    expect("UPDATE bp_evidence SET content_sha256 = 'hash-b' WHERE id = 1", blocked=True,
           label="conditional-immutable 改 content_sha256")
    expect("UPDATE bp_candidates SET attribution_status = 'internal' WHERE id = 1", blocked=True,
           label="sticky-status versioned -> internal")

    print("正向：合法操作必须被接受")
    expect("UPDATE bp_sessions SET account_id = 10 WHERE id = 1", blocked=False,
           label="scope-immutable 写同值")
    expect("UPDATE bp_evidence SET projection_job_id = 5 WHERE id = 1", blocked=False,
           label="conditional-immutable 写非受保护列")
    # 必须用**另一行**：先前被拒的 UPDATE 已回滚，id=1 仍是 versioned，
    # 对它再改仍会被正确拒绝（那是保护生效，不是缺陷）。
    expect("INSERT INTO bp_candidates VALUES (2, 'draft')", blocked=False,
           label="sticky-status 插入非 versioned 行")
    expect("UPDATE bp_candidates SET attribution_status = 'internal' WHERE id = 2",
           blocked=False, label="sticky-status 从非 versioned 出发可改")
    expect("UPDATE bp_sources SET title = 't2' WHERE id = 1", blocked=False,
           label="revision-counter 正常更新")

    row = conn.execute("SELECT structural FROM bp_revisions WHERE scope_id = 7").fetchone()
    print(f"  revision 计数 = {row[0]}（期望 2：seed INSERT + 一次 UPDATE）")
    if row[0] != 2:
        failures.append(f"revision 计数应为 2，实际 {row[0]}")

    # 反证：删掉一个保护函数体后，非法操作必须变成可接受
    conn.execute("DROP TRIGGER trg_bp_raw_append_only ON bp_raw")
    conn.commit()
    expect("UPDATE bp_raw SET payload = 'changed' WHERE id = 1", blocked=False,
           label="反证：移除 append-only 触发器后 UPDATE 被接受")

    conn.execute("DROP TABLE IF EXISTS bp_sessions, bp_raw, bp_evidence, bp_candidates,"
                 " bp_sources, bp_revisions CASCADE")
    conn.commit()
    conn.close()

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 四类触发器等价物均按预期工作（含反证）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
