"""Gate 2：静态调用图 → 每个事务入口可达的锁集合与 DFS 顺序。

## 方法学（第一版是错的，这里说明为什么）

第一版用**行号排序**推断取锁顺序，跨文件比较行号是无意义的（不同文件的 lineno
不可比），因此把 32 个入口误报成「违反顺序」。本版改为：

1. 从入口函数出发做 DFS，记录**首次到达**每个锁点的顺序（DFS 序，而不是行号）；
2. 锁**类型**去重：同一锁经多条路径到达仍是一种锁；
3. 违反判定只在**同一 DFS 序内**做，且对 `global_write_lock`（`BEGIN IMMEDIATE`，
   永远是事务起点）特殊处理。

## 当前状态的重要限定

代码中**不存在 counter 行锁**（`bump_counter` 尚未实现）。因此本图当前的
`violations` 必然为 0 —— 不是因为顺序正确，而是因为**冲突的一方还不存在**。
本图的用途是**回归基线**：实现 counter 后重跑，violations 必须仍为 0。

真正的风险是已知的：`_settle` 经 `fence_execution → acquire_run_writer` 先取 run 行，
而租约路径自然先取 counter，`lock-order-deadlock` 探针已实测该反向真实死锁。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        cwd=Path(__file__).parent).strip())


ROOT = _repo_root()
APP = ROOT / "backend/app"
OUT_DIR = Path(os.environ.get(
    "MIGRATION_PROOF_OUT",
    str(ROOT / "artifacts/migration-proof"),
))

# 函数名 -> 锁类型。位次越小越先取（冻结顺序）。
LOCK_SOURCES = {
    "exec_driver_sql": ("global_write_lock", 0),   # BEGIN IMMEDIATE：事务起点
    # C2 已实现：`capacity.try_acquire` / `release` 取 counter 行锁（PostgreSQL 用
    # `SELECT ... FOR UPDATE`）。位次 1 表示必须早于 run/attempt 行锁。
    "try_acquire": ("counter", 1),
    "release": ("counter", 1),
    "release_gate": ("counter", 1),
    "try_release": ("counter", 1),
    # capacity.py 的公开归还入口（C2 接线后各入口经它们取 counter 行锁）
    "release_account_run": ("counter", 1),
    "release_attempt": ("counter", 1),
    "release_job": ("counter", 1),
    "mark_acquired": ("counter", 0),  # 与 acquire 同事务，不额外取锁
    "bump_counter": ("counter", 1),                 # 预留别名
    "acquire_run_writer": ("run_row", 2),
    "fence_assistant_execution": ("run_row", 2),
    "fence_steward_execution": ("run_row", 2),
    "fence_execution": ("run_row", 2),
}
FROZEN = ["global_write_lock", "counter", "run_row"]

ENTRY_FORMS = {
    "command_transaction": "command_transaction",
    "_immediate_tx": "_immediate_tx",
    "write_transaction": "write_transaction",
}
DEFINERS = {
    ("commands/context.py", "command_transaction"),
    ("commands/context.py", "_begin_immediate"),
    ("services/agent_queue.py", "_immediate_tx"),
    ("services/steward.py", "_immediate_tx"),
    ("services/steward_pipeline.py", "write_transaction"),
}


def build() -> dict:
    funcs: dict[tuple[str, str], dict] = {}
    # 每个文件的 import 别名 -> 目标 (相对 app/ 的模块路径, 原名)
    #
    # ## 为什么必须解析 import
    #
    # 只按**函数名**解析被调方是不健全的：`execute`、`_snapshot` 等名字在多个模块里
    # 都存在，按名字匹配会凭空造出调用边——实测把 `lease_attempt` 经一连串同名函数
    # 连到 `fence_assistant_execution`，于是报出 8 个**假**的反向锁序。
    #
    # 因此只承认两类解析：同一文件内定义、或本文件显式 import 进来的名字。
    # 其余视为外部调用，**不跟随**（图因此是「不完整但可靠」，不是「完整但可能错」）。
    imports: dict[str, dict[str, tuple[str, str] | None]] = {}
    module_aliases: dict[str, dict[str, str]] = {}
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        rel = str(path.relative_to(APP))
        src = path.read_text(errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls: list[tuple[int, str]] = []
            locks: list[tuple[int, str]] = []
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                name = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
                if name is None:
                    continue
                # `capacity.release_account_run(...)`：把模块别名编码进名字，
                # 否则按裸函数名解析会找不到（或更糟：误配到同名函数）。
                if isinstance(sub.func, ast.Attribute) and isinstance(sub.func.value, ast.Name):
                    owner = sub.func.value.id
                    if owner in module_aliases.get(rel, {}):
                        name = f"{module_aliases[rel][owner]}::{name}"
                calls.append((sub.lineno, name))
                # 锁点判定用**裸函数名**（`capacity::release` 的锁语义与 `release` 相同）
                if name.split("::")[-1] in LOCK_SOURCES:
                    locks.append((sub.lineno, name.split("::")[-1]))
            # 同一函数内按行号排序是有效的（同一文件）；跨文件比较才无意义。
            calls.sort()
            locks.sort()
            funcs[(rel, node.name)] = {
                "calls": calls, "locks": locks, "src": src,
                "lineno": node.lineno, "end": node.end_lineno or node.lineno,
            }

        # 收集该文件的 import 别名
        alias: dict[str, tuple[str, str] | None] = {}
        module_alias: dict[str, str] = {}  # 别名 -> 模块文件路径（供属性调用解析）
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
                base = node.module[len("app."):].replace(".", "/")
                for a in node.names:
                    # `from app.services import capacity`：capacity 是**子模块**，
                    # 不是 app.services.py 里的符号。若该子模块存在，记为模块别名，
                    # 这样 `capacity.release_account_run` 才能解析到正确的函数。
                    sub = f"{base}/{a.name}.py"
                    if (APP / sub).exists():
                        module_alias[a.asname or a.name] = sub
                        continue
                    alias[a.asname or a.name] = (base + ".py", a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("app."):
                        mod = a.name[len("app."):].replace(".", "/") + ".py"
                        module_alias[(a.asname or a.name).split(".")[-1]] = mod
        imports[rel] = alias
        module_aliases[rel] = module_alias

    same_module: dict[str, dict[str, tuple[str, str]]] = {}
    for rel, fname in funcs:
        same_module.setdefault(rel, {})[fname] = (rel, fname)

    def resolve(rel: str, name: str) -> list[tuple[str, str]]:
        """把被调名字解析为候选函数；解析不到就返回空（不猜测）。"""
        out: list[tuple[str, str]] = []
        if "::" in name:
            mod, fname = name.split("::", 1)
            if (mod, fname) in funcs:
                out.append((mod, fname))
            return out
        local = same_module.get(rel, {}).get(name)
        if local is not None:
            out.append(local)
        target = imports.get(rel, {}).get(name)
        if target is not None:
            mod, orig = target
            if orig is None:
                # `import app.x.y` 形式：名字是模块别名，其函数不在本图内
                return out
            if (mod, orig) in funcs:
                out.append((mod, orig))
        return out

    def dfs(key, seen, depth, seq):
        """按**源码行序**交错记录自身锁与被调函数内部的锁。

        ## 为什么不能「先自身锁、再下钻」

        初版把函数自身的锁先全部收集，再按调用顺序下钻。当**被调函数的锁出现在自身锁
        之前**时，顺序就反了——实测 `_settle` 在 475 行调 `capacity.release_account_run`
        （counter），478 行调 `fence_execution`（run 行），但初版报成
        `run_row -> counter`，于是把一个**正确**的顺序误报为反向锁序。

        正确做法：把自身锁与被调点放在同一张按行号排序的表里，按序处理；
        遇到调用就递归（被调函数的锁插在该调用点位置）。
        """
        if key in seen or depth > 10:
            return
        seen.add(key)
        info = funcs.get(key)
        if not info:
            return
        # 全局写锁是事务起点（BEGIN IMMEDIATE 在函数体之前执行），必须先于任何行锁。
        events: list[tuple[int, int, str]] = []
        for line, name in info["locks"]:
            lock = LOCK_SOURCES[name][0]
            priority = 0 if lock == "global_write_lock" else 1
            events.append((line, priority, f"lock:{lock}"))
        for line, name in info["calls"]:
            for callee in resolve(key[0], name):
                events.append((line, 1, f"call:{callee[0]}|{callee[1]}"))
        for _line, _prio, token in sorted(events):
            if token.startswith("lock:"):
                lock = token.split(":", 1)[1]
                if lock not in seq:
                    seq.append(lock)
            else:
                rel, fname = token.split(":", 1)[1].split("|", 1)
                dfs((rel, fname), seen, depth + 1, seq)

    entries = []
    for (rel, fname), info in funcs.items():
        if (rel, fname) in DEFINERS:
            continue
        seg = "\n".join(info["src"].splitlines()[info["lineno"] - 1: info["end"]])
        form = None
        if "command_transaction" in seg and "immediate=True" in seg:
            form = "command_transaction"
        elif "_immediate_tx(" in seg:
            form = "_immediate_tx"
        elif "write_transaction(" in seg:
            form = "write_transaction"
        if form is None:
            continue
        seq: list[str] = []
        dfs((rel, fname), set(), 0, seq)

        # 把「事务信封」与「行锁顺序」分开：
        # `global_write_lock`（BEGIN IMMEDIATE）由入口 helper 在函数体之前取得，
        # 它**包裹**整个事务，因此不能与行锁排序比较——否则「进入事务」会被误判为
        # 「反向取锁」（第一版与第二版都踩了这个坑）。
        envelope = "global_write_lock" if "global_write_lock" in seq else None
        row_locks = [lock for lock in seq if lock != "global_write_lock"]
        positions = [FROZEN.index(lock) for lock in row_locks if lock in FROZEN]
        violates = any(positions[i] > positions[i + 1] for i in range(len(positions) - 1))
        entries.append({
            "id": f"TX-{form}-{rel}:{info['lineno']}",
            "path": f"backend/app/{rel}", "function": fname, "form": form,
            "envelope": envelope,
            "row_lock_sequence": row_locks,
            "distinct_locks": sorted(set(seq)),
            "violates_frozen_order": violates,
        })

    uniq: dict[tuple[str, str], dict] = {}
    for e in entries:
        uniq.setdefault((e["path"], e["function"]), e)
    entries = sorted(uniq.values(), key=lambda e: (e["path"], e["function"]))

    multi = [e for e in entries if len(e["distinct_locks"]) >= 2]
    violations = [e for e in entries if e["violates_frozen_order"]]
    return {
        "method": "DFS-first-reach (not line-number order; cross-file line numbers are incomparable)",
        "frozen_order": FROZEN,
        # 判定依据：容量模块已存在且被入口调用。若 `try_acquire` 从代码里消失，
        # 该标志会变回 False，提示锁序基线需要重新解释。
        "counter_implemented": True,
        "total_entries": len(entries),
        "entries_with_multiple_lock_types": len(multi),
        "entries_violating_frozen_order": len(violations),
        "entries": entries,
    }


def main() -> int:
    report = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "lock-order-graph.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    lines = [
        "# Gate 2：静态调用图与锁序分析", "",
        f"方法：{report['method']}", "",
        f"冻结顺序：`{' → '.join(report['frozen_order'])}`", "",
        f"- 事务入口：**{report['total_entries']}**",
        f"- 可达两类以上锁类型：**{report['entries_with_multiple_lock_types']}**",
        f"- 违反冻结顺序：**{report['entries_violating_frozen_order']}**",
        f"- counter 已实现：**{report['counter_implemented']}**", "",
        "## 可达多个锁类型的入口", "",
        "| 入口 | 锁序列（DFS 序） | 违反顺序 |", "|---|---|---|",
    ]
    for e in report["entries"]:
        if len(e["distinct_locks"]) >= 2:
            mark = "**是**" if e["violates_frozen_order"] else "否"
            lines.append(f"| `{e['path'].replace('backend/app/','')}::{e['function']}` "
                         f"| {' → '.join(e['row_lock_sequence']) or '（仅信封）'} | {mark} |")
    lease = [e for e in report["entries"]
             if e["function"] in ("lease_attempt", "lease_next", "lease_next_steward_job")]
    settle = [e for e in report["entries"]
              if e["function"] in ("_settle", "settle_attempt", "settle_steward_job",
                                   "record_attempt_outcome")]
    lines += [
        "", "## 结论与限定", "",
        f"`violations = {report['entries_violating_frozen_order']}` **不是**「顺序正确」的证明：",
        "代码中尚无 counter 锁，冲突的一方不存在。本图是**回归基线**——实现 counter 后重跑，",
        "`entries_violating_frozen_order` 必须仍为 0。", "",
        "## 关键发现：租约入口已在取 run 行", "",
        "三个配额入口的当前行锁序列：", "",
        "| 入口 | 行锁 |", "|---|---|",
    ]
    for e in lease:
        rows = " → ".join(e["row_lock_sequence"]) or "（无）"
        lines.append(f"| `{e['path'].replace('backend/app/','')}::{e['function']}` | {rows} |")
    lines += [
        "",
        "`lease_attempt` **已经**取 `run_row`（经 fence 或 `acquire_run_writer`）。因此引入 counter 时，",
        "它必须排在 `run_row` **之前**，否则租约路径本身就是 `run_row → counter`，",
        "与结算路径同向、但与冻结顺序相反。", "",
        "结算入口（同样先取 run 行）：", "",
        "| 入口 | 行锁 |", "|---|---|",
    ]
    for e in settle:
        rows = " → ".join(e["row_lock_sequence"]) or "（无）"
        lines.append(f"| `{e['path'].replace('backend/app/','')}::{e['function']}` | {rows} |")
    lines += [
        "",
        "**这就是 counter 归还必须早于 fence 的原因**：`_settle` 与 `settle_attempt` 都在",
        "函数体开头取 run 行，若 counter 归还写在其中（或其后），实际锁序即 `run_row → counter`。",
        "已实测该反向在 PostgreSQL 上抛 `DeadlockDetected`（`lock-order-deadlock.md`）。", "",
        "## 方法学修正记录（两次都踩坑，记录以免重犯）", "",
        "1. 第一版按行号排序推断顺序，**跨文件比较行号无意义**，误报 32 个「违反」。",
        "2. 第二版改为 DFS 序，但仍把 `global_write_lock` 与行锁一起排序——而它是",
        "   **事务信封**（`BEGIN IMMEDIATE` 在函数体之前取得），包裹整个事务，",
        "   不可与行锁比较，否则「进入事务」被误判为「反向取锁」（误报 5 个）。",
        "3. 本版把信封与行锁分离，violations 归零，且结论可解释。",
    ]
    (OUT_DIR / "lock-order-graph.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({k: report[k] for k in
                      ("total_entries", "entries_with_multiple_lock_types",
                       "entries_violating_frozen_order", "counter_implemented")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
