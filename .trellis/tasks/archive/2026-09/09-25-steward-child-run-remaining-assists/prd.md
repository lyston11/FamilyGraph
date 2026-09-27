# S4 candidate/ranking/explanation 迁移到 child run

## Goal

其余三类辅助逐个迁移，各自独立开关与差分测试

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

三类逐个走 Pi 载体时**未改任何生产代码**——E3 建立的链路与服务端校验器已经通用，换 kind 只改
一个配置值。这正是 E1 重构的目的。

每 kind 的围栏经 Pi 路径断言（变异测试验证）：

- **candidate**：只落原子 `SOURCE_FACT_TYPES`；证据摘要在发送与写回之间变化即退休为 `skipped`；
- **ranking**：严格排列，部分排列**整体拒绝**（`degraded`，不部分采纳）；
- **explanation**：只可引用给定证据（编造 fact id → `degraded`），渲染文本来自服务端模板而非模型正文。

等价性按 settle 状态、原因码与产物结构比较（标识符与文本按类型归一，因为每次迭代建自己的空间）。

验收：`backend/tests/test_steward_pi_carrier_remaining_kinds.py`（7 例）。
