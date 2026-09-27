# S2 Steward terminology 迁移到 child run

## Goal

terminology 一类走 Pi child run；差分等价 + egress 审计 + 崩溃收敛 + 写回栅栏回归

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

---

## 交付结论（2026-09-27）

**本任务的交付物已由 `09-26-steward-sidecar-adapters` 完成**，本任务归档，不再单独实施。

理由：E1 把执行单元改成 attempt 之后，两处协议修复（租约端点改名、settle 携带产物）只有把
Pi 载体真正跑起来才能证明，因此该任务把 terminology 的载体迁移与验收一并做了。

实际交付（见 `.trellis/tasks/archive/2026-09/09-26-steward-sidecar-adapters/implement.md`）：

- terminology 走 `STEWARD_ASSIST_TERMINOLOGY_CARRIER=pi`，与 inproc 载体**结构等价**（同一输入 →
  同一 settle 状态、原因码与产物形状）；
- egress 审计以 child run id 为 `target_id`（经网关是 child run 唯一能拿到审计的路径）；
- 崩溃点⑤收敛（child run 卡住 → run `expired` + attempt `unknown`，无第二次出站）；
- 撤权在内部请求层即被拒（比写回栅栏更早），且一个维护 tick 内收敛；
- 写回栅栏回归全绿。

`prompt_version` 的跨层单源在本任务中的实际形态与本文初稿不同：prompt **文本**改为服务端拥有
（投影下发 `steward_instructions`），而不是让 `steward_terminology.PROMPT_VERSION` 去引用
`steward_assist.STEWARD_PROMPT_VERSION`——因为进程内载体发送的是 `_PROMPTS[kind]`，
`prompt_digest` 覆盖它，两侧必须发同一段文本。详见 spec §8。
