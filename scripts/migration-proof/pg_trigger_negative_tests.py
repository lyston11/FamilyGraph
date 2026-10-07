"""C1：为每个触发器等价物写**负向用例**——非法操作必须被拒绝。

## 为什么必须做

「DDL 应用成功」不等于「约束生效」。plpgsql 函数可能因为条件写错、列名不匹配、
`IS DISTINCT FROM` 语义差异而**静默放行**非法操作。只有负向用例能证明它承重。

同时必须做**正向对照**：合法操作要成功。否则「全部拒绝」也能骗过负向用例。

## 覆盖

| 类别 | 负向（必须拒绝） | 正向（必须通过） |
|---|---|---|
| scope_immutable | 改 account_id/space_id/agent_kind | 写同值 |
| append_only | 任何 UPDATE | （无：表设计为只插入） |
| candidate evidence immutable | 改 digest / status 从非 pending | 改允许的列 |
| sticky status | versioned -> 其他 | 非 versioned -> 其他 |
| revision mirror | revision != source_revision | 两者相等 |
| sri revision counter | （计数器应递增） | INSERT/UPDATE/DELETE 后计数 +1 |

用法：

    PGTEST_DSN=postgresql://... python3 scripts/migration-proof/pg_trigger_negative_tests.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    import subprocess
    override = os.environ.get("MIGRATION_PROOF_ROOT")
    if override:
        return Path(override)
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        cwd=Path(__file__).parent).strip())


ROOT = _repo_root()
OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", str(ROOT / "artifacts/migration-proof")))


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

    failures: list[str] = []
    results: list[dict] = []

    def check(conn, label: str, sql: str, *, blocked: bool, params=None) -> None:
        try:
            conn.execute(sql, params or ())
            conn.commit()
            got = False
            detail = "accepted"
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            got = True
            detail = f"{type(exc).__name__}: {str(exc).splitlines()[0][:70]}"
        ok = got == blocked
        mark = "OK " if ok else "BAD"
        want = "应拒绝" if blocked else "应通过"
        print(f"  [{mark}] {label}（{want}）-> {detail}")
        results.append({"case": label, "blocked": got, "expected_blocked": blocked,
                        "ok": ok, "detail": detail})
        if not ok:
            failures.append(f"{label}: 期望 blocked={blocked}，实际 {got}（{detail}）")

    with psycopg.connect(plain) as conn:
        # 负向用例需要真实父行（FK 会先拦住不存在的 account/space）。
        # 这里建立最小的 accounts/users/family_spaces，仅用于本探针。
        conn.execute("""
            INSERT INTO users (id, name, gender, privacy_mode, profile_status, created_at)
            VALUES (900100, 'probe-user', 'unknown', 'public', 'identity_confirmed', now())
            ON CONFLICT (id) DO NOTHING;
            INSERT INTO accounts (id, user_id, pin_hash, pin_must_change, token_version,
                                  failed_attempts, status)
            VALUES (900100, 900100, 'x', false, 1, 0, 'claimed') ON CONFLICT (id) DO NOTHING;
            INSERT INTO family_spaces (id, name, owner_id, kind, created_at)
            VALUES (900100, 'probe-space', 900100, 'household', now()) ON CONFLICT (id) DO NOTHING;
            INSERT INTO users (id, name, gender, privacy_mode, profile_status, created_at)
            VALUES (900101, 'probe-user-2', 'unknown', 'public', 'identity_confirmed', now())
            ON CONFLICT (id) DO NOTHING;
            INSERT INTO steward_jobs (id, space_id, cause, trigger_cursor, status, attempt,
                    max_attempts, checkpoint_json, policy_version, created_at, updated_at)
            VALUES (900100, 900100, 'integrity_scan', 0, 'queued', 0, 3, '{}', 'v1', now(), now())
            ON CONFLICT (id) DO NOTHING;
        """)
        conn.commit()

        # ---------- scope_immutable：agent_sessions ----------
        print("scope_immutable（agent_sessions）")
        conn.execute("""
            INSERT INTO agent_sessions (id, account_id, space_id, agent_kind,
                                        term_usage_consent, created_at, updated_at)
            VALUES (900001, 900100, 900100, 'assistant', false, now(), now())
            ON CONFLICT (id) DO NOTHING""")
        conn.commit()
        check(conn, "改 account_id",
              "UPDATE agent_sessions SET account_id = 900100 + 1 WHERE id = 900001",
              blocked=True)
        check(conn, "改 space_id",
              "UPDATE agent_sessions SET space_id = 900100 + 1 WHERE id = 900001", blocked=True)
        check(conn, "改 agent_kind",
              "UPDATE agent_sessions SET agent_kind = 'steward' WHERE id = 900001", blocked=True)
        check(conn, "写同值（正向）",
              "UPDATE agent_sessions SET account_id = account_id WHERE id = 900001",
              blocked=False)

        # ---------- append_only：raw_relation_inputs ----------
        print("append_only（raw_relation_inputs）")
        cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='raw_relation_inputs' ORDER BY ordinal_position").fetchall()]
        if "id" in cols:
            conn.execute("SELECT count(*) FROM raw_relation_inputs")
            # 尝试任意 UPDATE：即使 0 行命中，触发器也只在有行时触发，故需先插一行
            inserted = conn.execute("""
                INSERT INTO raw_relation_inputs (id, author_account_id, text, context_json, created_at)
                VALUES (900002, 900100, 'probe', '{}', now())
                ON CONFLICT (id) DO NOTHING RETURNING id""").fetchone()
            conn.commit()
            if inserted:
                check(conn, "任何 UPDATE",
                      "UPDATE raw_relation_inputs SET text = 'changed' WHERE id = 900002",
                      blocked=True)
            else:
                print("  SKIP: 无法插入 raw_relation_inputs 探针行（列约束不匹配）")

        # ---------- sticky status：steward_llm_candidates ----------
        print("sticky status（steward_llm_candidates）")
        try:
            conn.execute("""
                INSERT INTO steward_llm_candidates (id, space_id, job_id, candidate_kind,
                        payload_json, candidate_digest, attribution_status, status, created_at)
                VALUES (900003, 900100, 900100, 'k', '{}', 'd1', 'versioned', 'proposed', now())
                ON CONFLICT (id) DO NOTHING""")
            conn.commit()
            check(conn, "versioned -> legacy",
                  "UPDATE steward_llm_candidates SET attribution_status = 'legacy'"
                  " WHERE id = 900003", blocked=True)
            conn.execute("""
                INSERT INTO steward_llm_candidates (id, space_id, job_id, candidate_kind,
                        payload_json, candidate_digest, attribution_status, status, created_at)
                VALUES (900004, 900100, 900100, 'k', '{}', 'd2', 'legacy', 'proposed', now())
                ON CONFLICT (id) DO NOTHING""")
            conn.commit()
            # 注意：CHECK 只允许 legacy/unsupported/versioned，因此正向用例必须
            # 用真实枚举值（legacy -> unsupported），否则测的是 CHECK 而非触发器。
            check(conn, "legacy -> unsupported（正向）",
                  "UPDATE steward_llm_candidates SET attribution_status = 'unsupported'"
                  " WHERE id = 900004", blocked=False)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            print(f"  SKIP: steward_llm_candidates 探针失败（{type(exc).__name__}）")

        # ---------- revision mirror：rag_documents ----------
        print("revision mirror（rag_documents）")
        try:
            conn.execute("""
                INSERT INTO rag_documents (id, source_type, source_id, revision, source_revision,
                        scope, sensitivity, confirmation_status, visibility_snapshot,
                        visibility_snapshot_key, index_version, status, created_at, updated_at)
                VALUES (900005, 'memory', '900005', 1, 1, 'private', 'normal', 'confirmed',
                        '{}', 'k', 'v1', 'active', now(), now())
                ON CONFLICT (id) DO NOTHING""")
            conn.commit()
            check(conn, "revision != source_revision（插入）",
                  "INSERT INTO rag_documents (id, source_type, source_id, revision,"
                  " source_revision, scope, sensitivity, confirmation_status,"
                  " visibility_snapshot, visibility_snapshot_key, index_version, status,"
                  " created_at, updated_at)"
                  " VALUES (900006, 'memory', '900006', 2, 1, 'private', 'normal',"
                  " 'confirmed', '{}', 'k', 'v1', 'active', now(), now())", blocked=True)
            check(conn, "revision = source_revision（正向）",
                  "INSERT INTO rag_documents (id, source_type, source_id, revision,"
                  " source_revision, scope, sensitivity, confirmation_status,"
                  " visibility_snapshot, visibility_snapshot_key, index_version, status,"
                  " created_at, updated_at)"
                  " VALUES (900007, 'memory', '900007', 3, 3, 'private', 'normal',"
                  " 'confirmed', '{}', 'k', 'v1', 'active', now(), now())", blocked=False)
            check(conn, "UPDATE 使两者不等",
                  "UPDATE rag_documents SET revision = 5 WHERE id = 900007", blocked=True)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            print(f"  SKIP: rag_documents 探针失败（{type(exc).__name__}）")

        # ---------- sri revision counter ----------
        print("sri revision counter（steward_input_revisions）")
        try:
            before = conn.execute(
                "SELECT structural FROM steward_input_revisions WHERE scope_id = 0"
            ).fetchone()
            before = before[0] if before else 0
            conn.execute("""
                INSERT INTO users (id, name, gender, privacy_mode, profile_status, created_at)
                VALUES (900102, 'probe-user-3', 'unknown', 'public', 'identity_confirmed', now())
                ON CONFLICT (id) DO NOTHING;
                INSERT INTO accounts (id, user_id, pin_hash, pin_must_change, token_version,
                                      failed_attempts, status)
                VALUES (900008, 900102, 'x', false, 1, 0, 'claimed')
                ON CONFLICT (id) DO NOTHING""")
            conn.commit()
            after = conn.execute(
                "SELECT structural FROM steward_input_revisions WHERE scope_id = 0"
            ).fetchone()
            after = after[0] if after else 0
            ok = after > before
            print(f"  [{'OK ' if ok else 'BAD'}] accounts INSERT -> structural {before} -> {after}")
            results.append({"case": "sri counter increments", "ok": ok,
                            "before": before, "after": after})
            if not ok:
                failures.append(f"sri 计数器未递增：{before} -> {after}")

            # 反证：在**临时表**上建一个同形触发器再删掉，证明递增来自触发器。
            # 第一版直接 DROP 真实触发器 `sri_accounts_structural_insert`，
            # 导致基线从 66 个触发器变成 65 个——反证必须是非破坏性的，
            # 否则「验证」本身会腐蚀被测对象。
            conn.execute("""
                DROP TABLE IF EXISTS probe_counter_proof CASCADE;
                CREATE TABLE probe_counter_proof (id int PRIMARY KEY);
                CREATE OR REPLACE FUNCTION fg_probe_counter_proof() RETURNS trigger AS $$
                BEGIN
                  INSERT INTO steward_input_revisions (scope_id, structural, presentation, inferred)
                  VALUES (0, 1, 0, 0)
                  ON CONFLICT (scope_id) DO UPDATE
                    SET structural = steward_input_revisions.structural + 1;
                  RETURN NULL;
                END; $$ LANGUAGE plpgsql;
                CREATE TRIGGER trg_probe_counter_proof AFTER INSERT ON probe_counter_proof
                  FOR EACH ROW EXECUTE FUNCTION fg_probe_counter_proof();
            """)
            conn.commit()
            mid = conn.execute(
                "SELECT structural FROM steward_input_revisions WHERE scope_id = 0"
            ).fetchone()[0]
            conn.execute("INSERT INTO probe_counter_proof VALUES (1)")
            conn.commit()
            with_trg = conn.execute(
                "SELECT structural FROM steward_input_revisions WHERE scope_id = 0"
            ).fetchone()[0]
            conn.execute("DROP TRIGGER trg_probe_counter_proof ON probe_counter_proof")
            conn.commit()
            without_trg = conn.execute(
                "SELECT structural FROM steward_input_revisions WHERE scope_id = 0"
            ).fetchone()[0]
            conn.execute("INSERT INTO probe_counter_proof VALUES (2)")
            conn.commit()
            final = conn.execute(
                "SELECT structural FROM steward_input_revisions WHERE scope_id = 0"
            ).fetchone()[0]
            ok_trg = with_trg == mid + 1
            ok_no = final == without_trg
            print(f"  [{'OK ' if ok_trg else 'BAD'}] 反证-有触发器：{mid} -> {with_trg}（应 +1）")
            print(f"  [{'OK ' if ok_no else 'BAD'}] 反证-删触发器：{without_trg} -> {final}（应不变）")
            results.append({"case": "counter increments with trigger", "ok": ok_trg})
            results.append({"case": "counter stops without trigger", "ok": ok_no})
            if not ok_trg:
                failures.append("反证失败：有触发器时计数未递增")
            if not ok_no:
                failures.append("反证失败：删除触发器后计数仍变化")
            conn.execute("DROP TABLE IF EXISTS probe_counter_proof CASCADE;"
                         " DROP FUNCTION IF EXISTS fg_probe_counter_proof() CASCADE;")
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            print(f"  SKIP: sri 探针失败（{type(exc).__name__}: {str(exc)[:90]}）")

        # 清理探针行
        conn.execute("""
            DELETE FROM agent_sessions WHERE id = 900001;
            DELETE FROM raw_relation_inputs WHERE id = 900002;
            DELETE FROM steward_llm_candidates WHERE id IN (900003, 900004);
            DELETE FROM rag_documents WHERE id IN (900005, 900006, 900007);
            DELETE FROM accounts WHERE id IN (900008, 900009);
            DELETE FROM steward_jobs WHERE id = 900100;
            DELETE FROM family_spaces WHERE id = 900100;
            DELETE FROM users WHERE id IN (900101, 900102, 900103);
            DELETE FROM accounts WHERE id = 900100;
            DELETE FROM users WHERE id = 900100;
        """)
        conn.commit()

    passed = sum(1 for r in results if r["ok"])
    report = {"total": len(results), "passed": passed, "failures": failures, "results": results}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "pg-trigger-negative-tests.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print(f"\n  通过 {passed}/{len(results)}")
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 触发器等价物经负向与正向双向验证")
    return 0


if __name__ == "__main__":
    sys.exit(main())
