# 撤销成员资格后 Run 不收敛：reaper 必须区分永久失效与暂时失租

## 状态与目标

planning。父任务 `09-17-dual-agent-latency-result-integrity`。发现者：`09-19-dual-agent-acceptance-gap-closure` 的 F-R4「失权」浏览器格。

目标：成员资格被永久撤销后，在途 Run 必须**直接收敛为终态**，不再被 reaper 当作可重试的租约丢失回队。

## 背景

真实链路复现（`evidence/defect-revocation-no-convergence.note.md` 有完整时间线）：

撤权后服务端授权边界是对的——每次内部请求都被 `_authorize_run` 拒（403 `AGENT_TOKEN_SCOPE_MISMATCH`，`reason=active_membership_missing`），worker 也正确停止（22 条审计、零工具调用、零业务写回、零新模型请求）。但**没有任何一方写终态**：

1. 心跳 403 → sidecar `markLeaseLost` → abort，不续租也不结算；
2. reaper 到期后按 `attempt(1) < max_attempts(3)` 判定**可重试** → `outcome="queued"` 回队；
3. 新 attempt 重新 lease → `getRunContext` 立刻 403 → 再次失租 → 再等一个租约周期；
4. 三次耗尽后（约 `3 × 300s` = **900s**）才 `expired`。

用户在这 15 分钟里看着一个永远不会完成的「生成中…」；`expired` 还把「授权永久失效」记成「租约超时」，污染失败分母。

这与已修的 D-F1（取消被当成普通冲突）同族：**非可重试条件被当作可重试**。

## 需求与验收

| ID | 需求 | 验收结果 |
| --- | --- | --- |
| R1/AC1 | 永久失效直接收敛 | 租约到期且执行身份（成员资格）已永久失效时，reaper 直接写终态，**不回队**；断言 `attempt` 不增加、终态 `settled_at` 有值。 |
| R2/AC2 | 状态与错误码诚实 | 终态与错误码必须区分「授权失效」与「租约超时」，不得复用 `AGENT_LEASE_EXPIRED`（否则失败分母混淆）。前端有对应文案，不显示为「助手服务暂时不可用」。 |
| R3/AC3 | 暂时失租仍可重试 | 成员资格**仍有效**的普通租约过期保持既有回队语义（`attempt < max` 回队、耗尽 `expired`）。不得因本修复把可重试路径也终态化。 |
| R4/AC4 | 查询失败不得误杀 | 判定依赖的查询若本身失败（DB 抖动），tick 整体失败并在下一 tick 重试；不得把「读不到」当作「已失效」而终态化健康 Run。 |
| R5/AC5 | 浏览器侧不再假进行中 | 补验格 UI2-9 转绿：撤权后 Run 收敛为终态且临时气泡不再渲染「生成中…」，无需等待租约自然过期。 |

## 非目标

- 不改 `_authorize_run` 的授权判定（它已正确）。
- 不改 `cancel_requested` 路径（D-F1 已闭合）。
- 不缩短 `AGENT_LEASE_TTL_SECONDS` / `AGENT_MAX_ATTEMPTS`（用调参掩盖不收敛是错误修法）。
- 不新增 SSE 推送机制；已打开的流靠既有轮询/终态检查收口。
