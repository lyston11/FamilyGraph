# Steward 模型调用事务隔离、预算与崩溃恢复

> 父任务：[09-11-steward-complete-hardening](../09-11-steward-complete-hardening/prd.md)
> 状态：实现回归已通过（2026-09-12）；发布门禁 PARTIAL——真实 provider E2E 未运行，不得标记 completed（审计结论见 ../09-11-steward-release-observability/release-evidence.md §8）。

## Goal

把核心结果的成功与辅助模型的成败分开，使慢模型不占用 SQLite 写锁；调用失败、崩溃和预算耗尽可追溯且不会无限付费或回写陈旧内容。

## 已核实依据

本任务负责 [F04](../09-11-steward-complete-hardening/research/findings.md#f04), [F05](../09-11-steward-complete-hardening/research/findings.md#f05), [F06](../09-11-steward-complete-hardening/research/findings.md#f06), [F16](../09-11-steward-complete-hardening/research/findings.md#f16), [F19](../09-11-steward-complete-hardening/research/findings.md#f19)。严重性、原始代码锚点、已实现基线与不确定性见共享 findings；这些条目是范围内证据，不声称覆盖全仓所有安全问题。

## Dependencies

`09-11-steward-production-ops`。Trellis parent/children 不自动执行依赖，开始前按本节检查。

## Requirements

### R1 事务隔离

core 提交 DerivedFact/PFV/卡片/checkpoint 后才可调用网络；任何 HTTP 调用期间均无业务 DB 写事务。辅助失败不回滚已提交的 core，且不阻塞其他空间 core 调度。

### R2 持久化辅助阶段

模型辅助是 canonical job 的受限子阶段，有独立状态、输入证据版本、attempt 和审计；不恢复 generic AgentJob/AgentRun，不伪造 Assistant 会话。

### R3 预算与不确定结果

发请求前预留调用次数、输入/输出 token 上界和 wall-time；failed/degraded/invalid-output 也消耗尝试预算。缺失/负数 usage 不当成免费；崩溃后的未知结果不能盲目无限重发。

### R4 写回栅栏

模型返回后重查空间设置、provider revision、policy、源事实 revision、卡片状态/revision、候选受众与 lease。任何变化都跳过或 supersede；禁用辅助不允许旧请求回来恢复文案。

### R5 限量与关闭

限制 prompt/响应字节、单批候选数、单批卡片数、请求并发、总耗时；关闭 worker/空间开关时取消或停止后继调用；优雅退出不会无限等 httpx。

## Acceptance Criteria

| ID | 需求 | 可观察验收 |
|---|---|---|
| AC-1 | R1 | 用事件屏障挂起 fake transport，另一个 SQLite 连接仍可提交领域写入；core 结果已可读。取消/杀死辅助进程不回滚 core。 |
| AC-2 | R2 | core 提交后、发送前、发送后审计前、写回前四个崩溃点均能恢复；辅助行不会出现在 Assistant 三表。 |
| AC-3 | R3 | 总预算为 2 次时三类辅助最多发送 2 次，即使两次均超时/格式错误；剩余 token 不足即跳过；usage 缺失/负数/部分字段有保守计费。 |
| AC-4 | R4 | 调用期间撤权、改事实、换 provider、关闭开关或卡片终态，返回内容全部不应用，审计有安全原因码。 |
| AC-5 | R5 | 超大 prompt/响应、HTTP 30 秒超时、进程退出触发 deadline；后续空间仍能完成确定性 job。 |

## Out of scope

不重建已存在的 core、ActionCard 通知或认领推荐；不恢复 generic Steward AgentRun/AgentJob，不做独立聊天人格、自动事实确认、自动发申请/入空间、私有会话读取、任意工具执行。其他子任务的模块由其负责，本任务只遵守接口。

## 决策状态

用户已授权生成规划，并接受确定性核心、可选模型辅助、用户确认和站内通知路线。工程参数为本方案初值；任何权限/产品范围改变需回到规划。
