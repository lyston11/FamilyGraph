# 技术设计：运行时多租户资源隔离与重试预算

## 1. 目标与非目标

本子任务只处理运行时资源和调度，不迁移数据库、不引入 Redis/向量库。目标是在当前 backend/sidecar 上先建立可证明的资源隔离合同，随后可无语义变化迁移到 PostgreSQL。

保持不变：run token/fence、Assistant/Steward kind、Steward 不进入通用 Assistant queue、Provider gateway 唯一 egress、egress 一次一审计、settle/recovery 和取消/撤权优先级。

## 2. 资源模型

```text
control-plane reserve
  ├─ heartbeat / lease / context / renewal / cancel / settle / health
execution
  ├─ assistant: global → account_id
  ├─ steward: global → space_id
  ├─ tool: kind + account/space + tool class
  └─ background: RAG/index + maintenance + space_id
provider
  └─ upstream/profile + kind + tenant key
```

每一层定义 `capacity`, `queue_limit`, `max_wait`, `deadline`, `cancel`, `release`。Admission grant 必须记录安全的 resource key、队列等待和预算版本；不记录 prompt、正文、凭据或 SQL 参数。

同一用户跨空间的 Assistant 预算按 account 聚合；Steward 预算按 space 分开，不能因 viewer/account 变化共享或扩大空间配额。global 和 kind cap 防止大量小租户合计打满系统，control reserve 永远不参与普通执行借用。

## 3. 调度与公平

优先级顺序：撤权/取消/settle > heartbeat/lease renewal > context/lease > interactive Assistant > Steward attempt > tool > background index。公平队列在同一优先级内按 tenant round-robin/aging 选择，设置单 tenant burst 上限和最大排队长度。超过队列上限立即返回可诊断的 busy/deferred，不把等待伪装成运行中。

取消必须从队列移除且不泄漏名额；worker 真正结束后才释放正在执行的 grant。租约到期、进程退出和 PostgreSQL recovery 必须能收回 grant，不依赖 sidecar 自报。

## 4. RetryBudget

每个 run 取得一个不可扩大的 budget：`absolute_deadline`、`max_provider_attempts`、`max_session_turns`、`max_total_retry_seconds`、按 error class 的子预算、upstream circuit key。provider 层和 session 层共用同一个剩余预算，不能各自重试后相乘。

- `upstream_rejected`、policy block、cancel、revoked、invalid output：不重试。
- connect/transport failure：短次数、短总时限，预算耗尽立即失败。
- 408/409/425/429/5xx：按 upstream circuit 和 run budget 有界退避。
- stream 已发送但结果未知：按现有 `sent`/计费合同处理，不自动把未知结果当作未发送。

## 5. Sidecar 与 backend

第一阶段同进程实现显式 Assistant、Steward、control、tool、background limiter；sidecar `KindAdapter` 只接收 grant，不自行推导另一 kind 的预算。长 provider stream 不占 control 名额，不持 DB 事务。backend 的 internal listener 在 control pool 中执行，模型/工具不得占满其 worker。

第二阶段基于压测决定是否将 assistant/steward sidecar 拆成不同 deployment；拆分时保留相同 internal 协议、token claims、lease recovery 和 graceful shutdown，不允许 sidecar 访问业务 DB。

## 6. 失败语义

资源拒绝是明确的可重试/不可重试分类；不创建无意义的 run 或 attempt。Redis/PostgreSQL 尚未完成前，进程内 limiter 只能保护当前实例，不能宣称跨实例全局一致；这项限制写入过渡模式诊断。

## 7. 验证重点

使用两个以上 account、两个以上 space，同时施加 Assistant 长流、Steward tool burst、provider retry、RAG background 和控制请求，证明 control p95/p99 有界、tenant queue 不饥饿、单 tenant 不能耗尽其他 kind 的保留容量。移除任一 limiter/reserve/retry budget 的 mutation 必须让回归失败。
