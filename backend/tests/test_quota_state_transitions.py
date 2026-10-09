"""配额承载模型的状态转换必须逐条显式分类（结构性回归）。

## 为什么需要这个

迁移到 PostgreSQL 的持久化 capacity counter 后，正确性依赖一条容易被无声破坏的
不变量：

> 每一条「进入活跃态」的边恰好 `+1`，每一条「离开活跃态」的边恰好 `-1`。

但状态是通过直接赋值 `row.status = "..."` 改变的（`app/` 中有 50+ 处），
`status` 与「是否计入配额」并**不是**一对一：

- `StewardModelCall` 的配额只数 `in_flight`——发送门把 `reserved` 改成 `skipped`
  时**从未计入**，不能归还；而写回栅栏把 `in_flight` 改成 `skipped` 时**必须**归还。
  两处代码长得几乎一样，目标状态也相同。
- `StewardJob` 的配额把 `queued` 也算进去，所以 `+1` 发生在**入队**而不是租约。
- `steward.reaper_pass` 会把未耗尽的 job 送回 `queued`——那是活跃态**内部**转换，
  既不 `+1` 也不 `-1`。
- `StewardGeneration` 根本不是配额承载模型，它的 status 与 counter 无关。

靠人工 review 无法保证这一点。本用例扫描这四个文件里的**全部** status 写入，
要求每一处都出现在下面这张显式分类表中；新增未分类的写入即失败，强制作者回答
「这影响配额吗」。分类表本身就是完整清单，而不是抽样。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

QUOTA_FILES = (
    "services/steward_assist.py",
    "services/steward.py",
    "services/agent_queue.py",
    "services/agent_events.py",
)

# 分类语义：
#   enter       从非活跃进入活跃        -> counter +1
#   leave       从活跃进入非活跃        -> counter -1
#   internal    活跃态内部转换          -> 不动 counter
#   pre_active  从未计入的状态出发      -> 不动 counter
#   post_active 已离开活跃态之后的改写  -> 不动 counter
#   not_quota   不属于配额承载模型      -> 不动 counter
#
# 键为 (相对 app/ 的路径, 行号)。行号变化时用例会失败并提示更新——这是刻意的：
# 移动代码时应重新确认分类，而不是让它悄悄沿用旧标签。
CLASSIFICATION: dict[tuple[str, int], tuple[str, str]] = {
    # ---- StewardModelCall：配额只数 in_flight ----
    ("services/steward_assist.py", 1588): ("enter", "lease_attempt 取得租约 -> in_flight"),
    ("services/steward_assist.py", 1552): ("pre_active", "发送门：plan 缺失，reserved 从未计入"),
    ("services/steward_assist.py", 1563): ("pre_active", "发送门：栅栏拒绝，reserved 从未计入"),
    ("services/steward_assist.py", 1577): ("pre_active", "发送门：预算不足，reserved 从未计入"),
    ("services/steward_assist.py", 2518): ("pre_active", "schedule_due_attempt 计划阶段"),
    ("services/steward_assist.py", 2036): ("leave", "_settle_attempt 成功 -> succeeded"),
    ("services/steward_assist.py", 2055): ("leave", "_settle_attempt 组上下文失效 -> degraded"),
    ("services/steward_assist.py", 2070): ("leave", "_settle_attempt 校验未过 -> degraded"),
    ("services/steward_assist.py", 2116): ("leave", "_settle_attempt_failure 分类结果"),
    ("services/steward_assist.py", 2119): ("leave", "_settle_attempt_failure -> failed"),
    ("services/steward_assist.py", 1948): ("leave", "record_attempt_outcome 写回栅栏 -> skipped"),
    ("services/steward_assist.py", 2416): ("leave", "recover_stuck_attempts 崩溃点③ -> unknown"),
    ("services/steward_assist.py", 2390): (
        "post_active",
        "recover_stuck_attempts 崩溃点④：改写已结算行，不重复归还",
    ),
    # ---- AgentRun（steward child run 的收敛也在本文件）----
    ("services/steward_assist.py", 2466): ("leave", "recover_stuck_child_runs -> expired"),
    # ---- StewardModelCall 的构造点：全部在计划阶段，尚未计入 ----
    ("services/steward_assist.py", 1183): ("pre_active", "_reserve_attempt 计划期跳过（预算耗尽）"),
    ("services/steward_assist.py", 1189): (
        "pre_active",
        "_reserve_attempt 计划期跳过（token 不足）",
    ),
    ("services/steward_assist.py", 1193): (
        "pre_active",
        "_reserve_attempt 计划期跳过（prompt 过大）",
    ),
    ("services/steward_assist.py", 1197): (
        "pre_active",
        "_reserve_attempt 预留：reserved 不计入 assist 配额（只数 in_flight）",
    ),
    # ---- steward child run 进入 AgentRun 活跃态 ----
    # 注意：steward child run 是否消耗「每账户 assistant 并发」取决于 counter 是否按
    # kind 分桶。设计结论是**不消耗**（它由 assist 的 per-space 配额治理），因此迁移时
    # counter 必须按 kind 分桶，否则 steward 会挤占 assistant 的账户配额。
    ("services/steward_assist.py", 2317): ("enter", "open_child_run 直接建 leased run"),
    # ---- AgentRun/AgentJob 构造点 ----
    ("services/agent_queue.py", 136): ("enter", "_create_run_and_job 建 queued run"),
    ("services/agent_queue.py", 151): ("enter", "_create_run_and_job 建 queued job"),
    # ---- StewardJob：配额含 queued，+1 发生在入队 ----
    ("services/steward.py", 447): ("enter", "事件触发入队，status='queued' 占用配额"),
    ("services/steward.py", 546): ("enter", "入队（幂等路径），status='queued' 占用配额"),
    ("services/steward.py", 649): ("internal", "queued -> leased，仍在活跃态"),
    ("services/steward.py", 755): ("leave", "settle_steward_job 写终态"),
    ("services/steward.py", 822): ("internal", "reaper 可能回到 queued（未耗尽），不动 counter"),
    ("services/steward.py", 757): ("not_quota", "StewardGeneration 不是配额承载模型"),
    ("services/steward.py", 817): ("not_quota", "StewardGeneration 不是配额承载模型"),
    # ---- AgentRun / AgentJob ----
    ("services/agent_queue.py", 346): ("internal", "job queued -> leased"),
    ("services/agent_queue.py", 354): ("internal", "run queued -> leased"),
    ("services/agent_queue.py", 500): ("leave", "_settle 写 run 终态"),
    ("services/agent_queue.py", 507): (
        "leave",
        "_settle 同步写 job 终态（与 run 同事务，不重复归还）",
    ),
    ("services/agent_queue.py", 662): (
        "internal",
        "reaper_pass 可能回到 queued（未耗尽），job 与 run 状态不同步",
    ),
    ("services/agent_queue.py", 668): ("leave", "reaper_pass 写 run 终态"),
    ("services/agent_events.py", 502): ("internal", "run leased -> running"),
    ("services/agent_events.py", 506): ("internal", "job leased -> running"),
}


def _status_writes() -> list[tuple[str, int, str, str]]:
    """返回 (相对 app/ 的路径, 行号, 描述, 值描述)。

    覆盖两种形态：

    1. 属性赋值 `row.status = <字面量或变量>`
    2. 构造关键字 `StewardJob(..., status="queued")`

    第二种容易漏——`enter` 恰恰发生在这里（入队即占配额），所以必须一起扫。
    """
    found: list[tuple[str, int, str, str]] = []
    for rel in QUOTA_FILES:
        path = APP_DIR / rel
        src = path.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            # 形态 1：属性赋值
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr == "status":
                        found.append(
                            (rel, node.lineno, ast.unparse(target), ast.unparse(node.value))
                        )
            # 形态 2：构造关键字 status=
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name not in {"StewardJob", "StewardModelCall", "AgentRun", "AgentJob"}:
                    continue
                for kw in node.keywords:
                    if kw.arg == "status":
                        found.append((rel, node.lineno, f"{name}(status=)", ast.unparse(kw.value)))
    return sorted(set(found))


ALL_WRITES = _status_writes()


def test_the_scanner_finds_the_expected_surface():
    """盘点基线：扫描面不能悄悄缩小（否则未分类检查会变成空操作）。"""
    assert len(ALL_WRITES) >= 25, (
        f"只扫描到 {len(ALL_WRITES)} 处状态写入，远少于预期——扫描逻辑可能已失效：\n"
        + "\n".join(f"  {r}:{ln} {d}" for r, ln, d, _v in ALL_WRITES)
    )
    files = {r for r, _l, _d, _v in ALL_WRITES}
    assert files == set(QUOTA_FILES), f"扫描覆盖的文件集变化：{sorted(files)}"


def test_classification_table_has_no_stale_entries():
    """分类表不得指向已不存在的位置（防止表变成没人维护的噪声）。"""
    actual = {(rel, line) for rel, line, _d, _v in ALL_WRITES}
    stale = sorted(set(CLASSIFICATION) - actual)
    assert not stale, (
        "分类表包含已不存在的状态写入位置（代码移动后需重新确认分类）：\n"
        + "\n".join(f"  {rel}:{line}" for rel, line in stale)
    )


def test_every_status_write_is_classified():
    """配额相关文件里的每一处状态写入都必须被分类。"""
    unclassified = [
        f"{rel}:{line}  {desc} = {value}"
        for rel, line, desc, value in ALL_WRITES
        if (rel, line) not in CLASSIFICATION
    ]
    assert not unclassified, (
        "发现未分类的状态写入。每一处都必须显式回答「是否影响 capacity counter」"
        "（enter/leave/internal/pre_active/post_active/not_quota），"
        "否则名额可能被静默泄漏或重复归还：\n" + "\n".join(f"  {u}" for u in unclassified)
    )


def test_every_quota_model_has_both_enter_and_leave():
    """每个有 `enter` 的模型都必须有 `leave`，否则名额会被永久泄漏。"""
    per_file: dict[str, dict[str, int]] = {}
    for (rel, _line), (kind, _why) in CLASSIFICATION.items():
        if kind == "not_quota":
            continue
        per_file.setdefault(rel, {}).setdefault(kind, 0)
        per_file[rel][kind] += 1
    for rel, kinds in per_file.items():
        if kinds.get("enter"):
            assert kinds.get("leave"), (
                f"{rel} 有 {kinds['enter']} 条进入配额的路径但没有任何归还路径——"
                "名额会被永久泄漏（该租户再也租不到，且不会报错）"
            )


@pytest.mark.parametrize(
    "kind", ["enter", "leave", "internal", "pre_active", "post_active", "not_quota"]
)
def test_every_classification_kind_is_used(kind):
    """六种分类都必须实际被用到，防止分类退化为单一标签。"""
    kinds = {k for k, _ in CLASSIFICATION.values()}
    assert kind in kinds, f"分类 {kind} 未被使用，分类表可能已退化"


def test_the_two_skipped_paths_are_distinguished():
    """`skipped` 的两类来源必须被区分：发送门（未计入）vs 写回栅栏（须归还）。

    这是本用例存在的最直接理由——两处代码几乎相同、目标状态相同，但配额语义相反。
    把发送门错标成 leave 会**多归还**名额（上限被静默放宽）；把写回栅栏错标成
    pre_active 会**少归还**（名额永久泄漏）。
    """
    send_gate = [
        k for k, (kind, why) in CLASSIFICATION.items() if kind == "pre_active" and "发送门" in why
    ]
    write_back = [
        k for k, (kind, why) in CLASSIFICATION.items() if kind == "leave" and "写回栅栏" in why
    ]
    assert len(send_gate) == 3, f"发送门的三条 pre_active 分类缺失：{send_gate}"
    assert len(write_back) == 1, f"写回栅栏的 leave 分类缺失：{write_back}"
