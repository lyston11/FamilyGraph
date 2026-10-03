# 实施计划：运行时多租户资源隔离与重试预算

## Phase A：基线

- [ ] 记录 sidecar event loop、kind slot、backend AnyIO worker、DB pool、tool admission、maintenance 和 control endpoint 的现状。
- [ ] 建立按 account/space/kind 的 queue wait、run duration、provider retries、tool wait、heartbeat latency 基线。
- [ ] 先写并发回归：单 account/space burst、跨 kind 保留容量、control-plane 在过载下仍可用。

## Phase B：控制面与资源合同

- [ ] 定义 resource key、grant、queue state、拒绝码、取消/回收和诊断字段。
- [ ] 建立 control-plane reserve 和独立 limiter；验证 heartbeat/lease/context/settle/cancel 不取 tool/model/background 名额。
- [ ] 建立 Assistant account quota、Steward space quota、kind/global cap 与公平队列。
- [ ] 在队列等待、worker 取消、租约过期、进程停止和 recovery 中验证名额无泄漏。

## Phase C：RetryBudget

- [x] 统一 provider/session retry 的预算消费和 error class 映射。
      run 级 `RunRetryBudget` 在传输层封顶（`AGENT_RUN_MAX_PROVIDER_ATTEMPTS`=8 /
      `AGENT_RUN_MAX_TOTAL_RETRY_MS`=120000），拒绝形态为合成永久 4xx；
      证据见 `research/evidence/retry-multiplication-baseline.md`。
- [ ] 加入 upstream/profile circuit key，避免一个 DERP/provider 故障熔断全部租户。
      （run 级封顶已完成；circuit 仍待做）
- [ ] 故障注入 connect timeout、5xx、永久 4xx、stream interruption、cancel，确认总尝试数和墙钟上界。

## Phase D：sidecar/backend 分池

- [ ] 在 sidecar 中分离 Assistant、Steward、control 的槽位，tool/background 不得借用 control。
- [ ] 在 backend 中分离 control/model/tool/background 的 worker/limiter，继续保持 sidecar 无 DB/外网。
- [ ] 评估分进程部署；若实现，先 backend 后 sidecar 发布并测试优雅退出与 lease recovery。

## Phase E：验收

- [ ] 运行多 account×space 矩阵：同用户跨空间、同空间多用户、Assistant+Steward、tool burst、slow/failing provider、RAG/index、撤权/取消/重启。
- [ ] 记录 control p95/p99、tenant queue wait、retry 次数、run deadline、DB wait 和用户可见终态。
- [ ] 运行 backend/agent 相关检查和 mutation；未达到父任务 AC-2/3/6 时不得完成。

## 回滚

R0 仅保留诊断；R1 可关闭新公平队列但保留 control reserve；R2 可回到同进程分池但禁止回到无界 retry；分进程失败不改变 token/fence/settle 合同。
