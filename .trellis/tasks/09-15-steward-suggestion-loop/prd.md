# 管家建议闭环：过期与提醒

> 父任务：09-15-agent-audit-remediation。优先级 P1。

## 背景

远端库 535 条 `steward_suggestions` 全部 `proposed`、`expires_at` 全为 NULL、0 条被 accept/dismiss；21 张 action card 全 `pending`；通知 20/21 未读。`STEWARD_SUGGESTION_TTL_DAYS=30` 配置存在但过期时间未落到行上，过期清理对 NULL 不生效，建议只增不减。用户观感"管家没起作用"的根因是产出物沉淀且无提醒闭环。

## Requirements

- R1：核实建议创建路径（`steward_suggestions.py` / delivery 投影）为何 `expires_at` 为 NULL；修正为创建时写入 `now + STEWARD_SUGGESTION_TTL_DAYS`。
- R2：核实 `steward_gc` 是否清理过期 proposed 建议；不足则补：过期建议置 superseded/expired，并级联通知状态。
- R3：提醒聚合：对未读建议/卡片做温和再触达（如每 N 天聚合一条通知，避免 535 条逐条轰炸），复用现有 notifications 通道；具体形态先小（聚合摘要通知）。
- R4：存量数据修复：迁移脚本或维护批为现有 535 条 proposed 建议补 `expires_at`（自修复时刻起算 TTL，不溯及既往创建时间以免立即全部过期）。
- R5：测试：TTL 写入、GC 收敛、聚合通知不重复。

## Acceptance Criteria

1. 新建议创建即带 `expires_at`。
2. 远端存量 proposed 建议补齐 `expires_at`；GC 运行后超期建议收敛。
3. 用户侧能收到聚合提醒（前端通知列表可见），且不逐条刷屏。
4. steward 主循环（job/generation/publication）行为不回退。

## 回滚

GC/提醒增量可 flag 关闭；`expires_at` 回填为幂等维护操作，可保留。
