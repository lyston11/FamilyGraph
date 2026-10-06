"""为每个真实事务调用点生成 contract card，并强制分类完整性。

分类表以 (path, line) 为键；未出现在表中的调用点会让脚本**失败**，
避免新增调用点被静默漏掉（这正是 Gate 1/2 要防的失败模式）。
"""
from __future__ import annotations
import json, subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        cwd=Path(__file__).parent).strip())


ROOT = _repo_root()
EV = ROOT / ".trellis/tasks/10-05-migration-proof-gates/research/evidence"

# helper 定义本身不是调用点
HELPERS = {
    ("backend/app/commands/context.py", 68),
    ("backend/app/services/agent_queue.py", 75),
    ("backend/app/services/steward.py", 170),
    ("backend/app/services/steward_pipeline.py", 83),
}

# (path, line) -> (category, invariant, lock, order_position, evidence_level, note)
# 类别：A=CAS  B=lease+counter  C=parent-row lock  D=advisory+unique
C: dict[tuple[str, int], tuple[str, str, str, str, str, str]] = {
 ("backend/app/api/action_cards.py", 491): ("C","卡状态/证据/成员资格读取后建空间，不得并发建两个","ActionCard 行","parent","L0","docstring 自述并发会各建一个空间"),
 ("backend/app/api/admin_steward.py", 526): ("C","重试意图与审计同事务","delivery intent 行","parent","L0",""),
 ("backend/app/commands/bindings.py", 102): ("C","binding+person 读取后改状态","binding 行","parent","L0",""),
 ("backend/app/commands/members.py", 414): ("C","建档去重门禁：同一人不得并发建两个 profile","space 行","parent","L0","architecture §0.9"),
 ("backend/app/commands/members.py", 472): ("C","建档+绑定不得并发重复","space 行","parent","L0",""),
 ("backend/app/commands/members.py", 1125): ("C","双 profile 合并：两侧不得同时被合并","两个 user 行（固定顺序）","parent","L0","需固定顺序避免互锁"),
 ("backend/app/commands/ownership.py", 137): ("A","双接受恰好一个赢","条件 UPDATE","n/a","L0","已有 CAS，BEGIN IMMEDIATE 非必需"),
 ("backend/app/commands/registration.py", 65): ("D","用户名唯一（行可能不存在）","唯一索引 + ON CONFLICT","n/a","L0",""),
 ("backend/app/commands/registration.py", 173): ("D","邀请码唯一（行可能不存在）","唯一约束兜底","n/a","L0","已有 savepoint 重试"),
 ("backend/app/commands/registration.py", 206): ("A","核销恰好一个赢","条件 UPDATE","n/a","L0",""),
 ("backend/app/commands/spaces.py", 548): ("C","复用双方已同属的 household，不得建第二个","两个 user 行","parent","L0",""),
 ("backend/app/dev_seed.py", 453): ("D","固定清单收敛比对","advisory lock","n/a","L0","开发脚本，低风险"),
 ("backend/app/services/admin_bootstrap.py", 171): ("D","无 admin 时创建唯一 admin","advisory lock + 唯一约束","n/a","L0",""),
 ("backend/app/services/agent_queue.py", 109): ("C","每 session 一个 active run + 每账户 N 并发","session 行 + counter","tenant","L0","**含配额**，需要 counter"),
 ("backend/app/services/agent_queue.py", 205): ("C","幂等键查重后插入","唯一约束 + ON CONFLICT","n/a","L0",""),
 ("backend/app/services/agent_queue.py", 311): ("B","选 queued job 改 leased","SKIP LOCKED + CAS","candidate","L3","见 pg-control-proof.md"),
 ("backend/app/services/agent_queue.py", 349): ("A","续租：仅租约持有者可续","条件 UPDATE (lease_owner)","n/a","L0",""),
 ("backend/app/services/agent_queue.py", 433): ("A","终态不可复活","条件 UPDATE (status)","n/a","L0",""),
 ("backend/app/services/agent_queue.py", 529): ("A","取消标记幂等","条件 UPDATE","n/a","L0",""),
 ("backend/app/services/agent_queue.py", 577): ("C","选 stale 后写终态","SKIP LOCKED + CAS","candidate","L0",""),
 ("backend/app/services/agent_queue.py", 718): ("A","条件删除","单语句","n/a","L0","无需锁"),
 ("backend/app/services/notifications.py", 364): ("A","首个 read_at 胜出","条件 UPDATE","n/a","L0",""),
 ("backend/app/services/notifications.py", 382): ("A","批量标记幂等","条件 UPDATE","n/a","L0",""),
 ("backend/app/services/steward.py", 492): ("C","每空间至多一个 active job","space 行 + partial unique","parent","L0",""),
 ("backend/app/services/steward.py", 621): ("B","**含 active_count 每空间上限**","counter 行 + SKIP LOCKED","tenant","L3","朴素移植实测越限 5/2"),
 ("backend/app/services/steward.py", 707): ("C","generation 校验后写终态","job 行","parent","L0",""),
 ("backend/app/services/steward.py", 791): ("C","选 stale 后写终态","SKIP LOCKED + CAS","candidate","L0",""),
 ("backend/app/services/steward.py", 877): ("C","读全部空间+schedule 后 upsert","ON CONFLICT DO UPDATE","n/a","L0",""),
 ("backend/app/services/steward.py", 1259): ("C","重建空间派生投影","space 行","parent","L0",""),
 ("backend/app/services/steward_assist.py", 1359): ("B","**含 _in_flight_for_space 上限**","counter 行 + SKIP LOCKED","tenant","L3","朴素移植实测越限 5/2"),
 ("backend/app/services/steward_assist.py", 1671): ("C","写回栅栏后改状态","attempt 行","attempt","L0","counter 归还必须**先于** fence"),
 ("backend/app/services/steward_assist.py", 1804): ("C","幂等应用产物（applied_at 为门）","attempt 行","attempt","L0",""),
 ("backend/app/services/steward_assist.py", 2104): ("C","续 run+attempt 租约","run 行 + attempt 行","run","L0",""),
 ("backend/app/services/steward_assist.py", 2133): ("C","建 run 并绑定 attempt","唯一约束 (run_id UNIQUE)","run","L0",""),
 ("backend/app/services/steward_assist.py", 2197): ("C","崩溃点③收敛 + 归还 counter","attempt 行","attempt","L0","跨事务，需独立验证"),
 ("backend/app/services/steward_assist.py", 2269): ("C","child run 自行收敛为 expired","run 行","run","L0",""),
 ("backend/app/services/steward_inferred.py", 470): ("C","读 active 推断后改状态","space/edge 行","parent","L0",""),
 ("backend/app/services/steward_inferred.py", 666): ("C","同上","space/edge 行","parent","L0",""),
 ("backend/app/services/steward_inferred.py", 698): ("C","同上","space/edge 行","parent","L0",""),
 ("backend/app/services/steward_pipeline.py", 83): ("C","包住 pipeline 写事务","视内部操作","parent","L0","内部写入集未逐行核对"),
 ("backend/app/services/steward_suggestions.py", 1342): ("C","读 active 建议后改状态","space/建议 行","parent","L0",""),
 ("backend/app/services/steward_suggestions.py", 1420): ("C","同上","space/建议 行","parent","L0",""),
 ("backend/app/services/steward_suggestions.py", 1587): ("C","同上","space/建议 行","parent","L0",""),
}


def main() -> None:
    inv = json.loads((EV / "tx-inventory.json").read_text())
    sites = [x for x in inv["items"] if (x["path"], x["line"]) not in HELPERS]
    missing = [(x["path"], x["line"]) for x in sites if (x["path"], x["line"]) not in C]
    stale = [k for k in C if k not in {(x["path"], x["line"]) for x in sites}]
    if missing:
        raise SystemExit(f"未分类的事务调用点（必须显式分类）：{missing}")
    if stale:
        raise SystemExit(f"分类表指向已不存在的调用点：{stale}")

    cards = []
    for x in sites:
        cat, inv_text, lock, pos, level, note = C[(x["path"], x["line"])]
        cards.append({
            "id": f"TX-{x['path']}:{x['line']}", "path": x["path"], "line": x["line"],
            "function": x["function"], "expression": x["expression"],
            "category": cat, "invariant": inv_text, "lock_object": lock,
            "lock_order_position": pos, "evidence_level": level, "note": note,
            "status": "classified" if level != "L3" else "proven-l3",
        })
    by_cat: dict[str, int] = {}
    by_level: dict[str, int] = {}
    for c in cards:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
        by_level[c["evidence_level"]] = by_level.get(c["evidence_level"], 0) + 1
    out = {
        "total_sites": len(sites), "helpers_excluded": len(HELPERS),
        "by_category": by_cat, "by_evidence_level": by_level, "cards": cards,
    }
    (EV / "tx-contracts.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("total_sites","helpers_excluded","by_category","by_evidence_level")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
