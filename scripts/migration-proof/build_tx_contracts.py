"""为全部 65 个事务入口生成 contract card，并强制分类完整性。

分类表以 (path, function) 为键（比行号稳定，重命名行不漂移）。
未分类的入口会让脚本**失败**——新增入口必须显式回答「这影响哪类不变量」。
"""
from __future__ import annotations
import json, subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        cwd=Path(__file__).parent).strip())


ROOT = _repo_root()
import os as _os

# 证据输出目录：可用 MIGRATION_PROOF_OUT 覆盖（任务归档后指向持久位置）。
OUT_DIR = Path(_os.environ.get(
    "MIGRATION_PROOF_OUT",
    str(ROOT / "artifacts/migration-proof"),
))
EV = OUT_DIR

# 入口总数。任何变化都必须显式更新——共享分类键会掩盖同函数内的新增入口。
EXPECTED_ENTRIES = 65

# 同函数内存在多个事务边界的函数 -> 入口数。这些函数的一条分类要覆盖多个边界，
# 因此必须显式登记，变化时强制复核。
MULTI_ENTRY_FUNCTIONS = {
    ("backend/app/services/steward_pipeline.py", "_stage_view"): 2,
    ("backend/app/services/steward_pipeline.py", "execute"): 3,
}

# (path, function) -> (category, invariant, lock_object, order_position, evidence, note)
# A=CAS  B=lease+counter  C=parent/row lock  D=advisory+unique
C = {
 # ---- 类别 A：单行/单条件 CAS ----
 ("backend/app/commands/ownership.py","accept_transfer"):("A","双接受恰好一个赢","条件 UPDATE","n/a","L0","已有 CAS；BEGIN IMMEDIATE 非必需"),
 ("backend/app/commands/registration.py","redeem_invite_code"):("A","核销恰好一个赢","条件 UPDATE","n/a","L0",""),
 ("backend/app/services/notifications.py","mark_notification_read"):("A","首个 read_at 胜出","条件 UPDATE","n/a","L0",""),
 ("backend/app/services/notifications.py","mark_all_notifications_read"):("A","批量标记幂等","条件 UPDATE","n/a","L0",""),
 ("backend/app/services/agent_queue.py","heartbeat"):("A","续租仅租约持有者可续","条件 UPDATE(lease_owner)","n/a","L0",""),
 ("backend/app/services/agent_queue.py","request_cancel"):("A","取消标记幂等","条件 UPDATE","n/a","L0",""),
 ("backend/app/services/agent_queue.py","_settle"):("A","终态不可复活","条件 UPDATE(status)","n/a","L0","**内含 fence → run 行锁**；counter 归还须在其前"),
 ("backend/app/services/agent_queue.py","prune_finished"):("A","条件删除","单语句","n/a","L0","无需锁"),
 # ---- 类别 B：lease + counter ----
 ("backend/app/services/agent_queue.py","lease_next"):("B","选 queued job 改 leased","SKIP LOCKED + CAS","candidate","L2","**原型 L3 / 入口 L2**：pg_control_proof.py 证明的是原型形态（proof_* 表），不是该真实入口在 PG 上的行为"),
 ("backend/app/services/agent_queue.py","enqueue_run"):("C","每 session 一个 active + 每账户 N 并发","session 行 + counter","tenant","L0","**含配额**"),
 ("backend/app/services/steward.py","lease_next_steward_job"):("B","**含 active_count 每空间上限**","counter 行 + SKIP LOCKED","tenant","L2","**原型 L3 / 入口 L2**；朴素移植实测越限 5/2，但未在该真实入口上验证"),
 ("backend/app/services/steward_assist.py","lease_attempt"):("B","**含 _in_flight_for_space 上限**","counter 行 + SKIP LOCKED","tenant","L2","**原型 L3 / 入口 L2**；朴素移植实测越限 5/2，但未在该真实入口上验证"),
 # ---- 类别 C：父行/协调行锁 ----
 ("backend/app/api/action_cards.py","execute_card"):("C","不得并发建两个空间","ActionCard 行","parent","L0","docstring 自述该竞态"),
 ("backend/app/api/admin_steward.py","steward_delivery_retry"):("C","重试意图与审计同事务","intent 行","parent","L0",""),
 ("backend/app/commands/bindings.py","confirm_binding"):("C","读 binding+person 后改状态","binding 行","parent","L0",""),
 ("backend/app/commands/members.py","create_member"):("C","建档去重门禁","space 行","parent","L0","architecture §0.9"),
 ("backend/app/commands/members.py","create_managed_member"):("C","建档+绑定不重复","space 行","parent","L0",""),
 ("backend/app/commands/members.py","merge_duplicate_profile"):("C","两侧不得同时被合并","两个 user 行（固定序）","parent","L0","顺序必须固定"),
 ("backend/app/commands/spaces.py","create_shared_household"):("C","复用已同属 household","两个 user 行","parent","L0",""),
 ("backend/app/services/agent_queue.py","submit_user_message"):("C","幂等键查重后插入","唯一约束 + ON CONFLICT","n/a","L0",""),
 ("backend/app/services/agent_queue.py","reaper_pass"):("C","选 stale 后写终态","SKIP LOCKED + CAS","candidate","L0",""),
 ("backend/app/services/steward.py","enqueue_steward_job"):("C","每空间至多一个 active job","space 行 + partial unique","parent","L0",""),
 ("backend/app/services/steward.py","settle_steward_job"):("C","generation 校验后写终态","job 行","parent","L0",""),
 ("backend/app/services/steward.py","reaper_pass"):("C","选 stale 后写终态","SKIP LOCKED + CAS","candidate","L0",""),
 ("backend/app/services/steward.py","scan_due_spaces"):("C","读空间+schedule 后 upsert","ON CONFLICT DO UPDATE","n/a","L0",""),
  ("backend/app/services/steward_assist.py","settle_attempt"):("C","写回栅栏后改状态","attempt 行","attempt","L0","counter 归还必须先于 fence"),
 ("backend/app/services/steward_assist.py","apply_settled_attempt"):("C","幂等应用（applied_at 为门）","attempt 行","attempt","L0",""),
 ("backend/app/services/steward_assist.py","heartbeat_child_run"):("C","续 run+attempt 租约","run 行 + attempt 行","run","L0",""),
 ("backend/app/services/steward_assist.py","open_child_run"):("C","建 run 并绑定 attempt","唯一约束(run_id UNIQUE)","run","L0",""),
 ("backend/app/services/steward_assist.py","recover_stuck_attempts"):("C","崩溃点③收敛 + 归还 counter","attempt 行","attempt","L0","跨事务，需独立验证"),
 ("backend/app/services/steward_assist.py","recover_stuck_child_runs"):("C","child run 自行收敛 expired","run 行","run","L0",""),
 ("backend/app/services/steward_inferred.py","confirm_edge"):("C","读 active 推断后改状态","space/edge 行","parent","L0",""),
 ("backend/app/services/steward_inferred.py","dismiss_edge"):("C","同上","space/edge 行","parent","L0",""),
 ("backend/app/services/steward_inferred.py","reinstate_edge"):("C","同上","space/edge 行","parent","L0",""),
 ("backend/app/services/steward_suggestions.py","dismiss_suggestion"):("C","读 active 建议后改状态","space/建议 行","parent","L0",""),
 ("backend/app/services/steward_suggestions.py","submit_suggestion"):("C","同上","space/建议 行","parent","L0",""),
 ("backend/app/services/steward_suggestions.py","restore_term"):("C","同上","space/建议 行","parent","L0",""),
 # ---- 类别 D：自然键 + advisory/唯一约束 ----
 ("backend/app/commands/registration.py","register_user"):("D","用户名唯一（行可能不存在）","唯一索引 + ON CONFLICT","n/a","L0",""),
 ("backend/app/commands/registration.py","create_my_invite_code"):("D","邀请码唯一","唯一约束兜底","n/a","L0","已有 savepoint 重试"),
 ("backend/app/services/admin_bootstrap.py","_bootstrap_admin_if_needed"):("D","无 admin 时创建唯一 admin","advisory lock + 唯一约束","n/a","L0",""),
 ("backend/app/dev_seed.py","maybe_seed_demo_data"):("D","固定清单收敛比对","advisory lock","n/a","L0","开发脚本"),
 # ---- write_transaction 包装层的其余入口（Steward 写回路径，共 23 个）----
 ("backend/app/services/steward.py","flush_buffer"):("C","批量 apply_pair_result","space 行","parent","L0","**经 write_transaction**；内部写入集未逐行核对"),
 ("backend/app/services/steward_delivery.py","_claim_due"):("C","认领投递意图（intent+generation+job）","intent 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_delivery.py","_defer_changed_snapshot"):("C","快照变更时延后意图","intent 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_delivery.py","_record_failure"):("C","投递失败记账","intent 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_demand.py","register"):("C","需求登记幂等","space 行 + job","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_gc.py","collect"):("C","GC 收集（读候选后写删除）","intent/view 行","parent","L0","**经 write_transaction**；读-写分离"),
 ("backend/app/services/steward_overlay.py","claim_due"):("C","认领 overlay viewer","intent 行","parent","L0","**经 write_transaction**；崩溃消耗持久预算"),
 ("backend/app/services/steward_overlay.py","_heartbeat"):("C","overlay 续租","binding 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_overlay.py","execute"):("C","执行 overlay 并写投影","generation 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_overlay.py","_fail"):("C","overlay 失败记账","intent 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_pipeline.py","begin_job"):("C","job queued→running","job 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_pipeline.py","heartbeat"):("C","pipeline 续租","job/generation 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_pipeline.py","publish"):("C","发布 generation 与 view","generation 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_pipeline.py","record_failure"):("C","pipeline 失败记账","job 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_pipeline.py","execute"):("C","执行 pipeline 主流程","job/view/generation","parent","L0","**经 write_transaction**；写入集最大"),
 ("backend/app/services/steward_pipeline.py","save_target"):("C","保存 target 与 view","generation/view 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_pipeline.py","_stage_view"):("C","暂存 view","generation/view 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_pipeline.py","_reuse_view"):("C","复用已有 view","generation 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_pipeline.py","_fail_target"):("C","target 失败记账","generation/view 行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_pipeline.py","_reserve_search"):("C","预留检索预算","检索预算行","parent","L0","**经 write_transaction**"),
 ("backend/app/services/steward_delivery.py","drain"):("C","投递写回：卡片/投影/候选同事务","space 行","parent","L0","**经 write_transaction**；Steward 主要写回路径"),
 # ---- 其他 write_transaction 入口 ----
   }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    inv = json.loads((EV / "tx-entries.json").read_text())
    entries = inv["entries"]
    keys = {(e["path"], e["function"]) for e in entries}
    missing = sorted(keys - set(C))
    stale = sorted(set(C) - keys)
    if missing:
        raise SystemExit("未分类的事务入口（必须显式分类）：\n" + "\n".join(f"  {p}::{f}" for p, f in missing))
    if stale:
        raise SystemExit("分类表指向已不存在的入口：\n" + "\n".join(f"  {p}::{f}" for p, f in stale))

    # 强制机制必须覆盖**入口数**而不是**函数数**。
    #
    # 分类键是 (path, function)，因此同一函数内的第二个、第三个事务入口会共享一条
    # 分类——新增调用点会静默通过（check 用第 66 个入口实证了这一点）。
    # 这里改为断言「入口数 == 已分类入口数」：任何新增入口都会改变计数并失败，
    # 直到作者显式更新 EXPECTED_ENTRIES 并复核该函数的分类是否仍然成立。
    if len(entries) != EXPECTED_ENTRIES:
        raise SystemExit(
            f"事务入口数从 {EXPECTED_ENTRIES} 变为 {len(entries)}：\n"
            "  新增或删除了事务入口。请复核其所在函数的分类是否仍然成立，\n"
            "  然后显式更新 EXPECTED_ENTRIES。共享分类键会掩盖同函数内的新增入口，\n"
            "  因此计数断言是必要的第二道防线。\n"
            + "\n".join(f"  {e['path']}:{e['line']} {e['function']} ({e['form']})" for e in entries)
        )

    # 同函数多入口必须显式登记，避免「一个函数一条分类」掩盖差异。
    from collections import Counter
    per_fn = Counter((e["path"], e["function"]) for e in entries)
    multi = {k: n for k, n in per_fn.items() if n > 1}
    if multi != MULTI_ENTRY_FUNCTIONS:
        raise SystemExit(
            f"同函数多入口集合变化：\n  实际 {multi}\n  预期 {MULTI_ENTRY_FUNCTIONS}\n"
            "  这些函数内部有多个事务边界，分类时必须逐条确认。"
        )

    cards = []
    for e in entries:
        cat, inv_text, lock, pos, level, note = C[(e["path"], e["function"])]
        cards.append({**e, "category": cat, "invariant": inv_text, "lock_object": lock,
                      "order_position": pos, "evidence_level": level, "note": note})
    by_cat: dict[str, int] = {}
    by_form: dict[str, int] = {}
    by_level: dict[str, int] = {}
    for c in cards:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
        by_form[c["form"]] = by_form.get(c["form"], 0) + 1
        by_level[c["evidence_level"]] = by_level.get(c["evidence_level"], 0) + 1
    out = {"total": len(cards), "by_category": by_cat, "by_form": by_form,
           "by_evidence_level": by_level, "cards": cards}
    (EV / "tx-contracts.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("total", "by_category", "by_form", "by_evidence_level")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
