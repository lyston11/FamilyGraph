# Steward 端到端发布门禁与可观测性

> 父任务：[09-11-steward-complete-hardening](../09-11-steward-complete-hardening/prd.md)
> 状态：规划完成待实施评审；本轮不启动；优先级 P2。

## Goal

用隔离测试和可审查运行证据证明从真实领域操作到后台重算、建议通知、用户确认及后续失效的完整路径可用；发布后能诊断开关、积压、失败和模型降级。

## 已核实依据

本任务负责 [F01](../09-11-steward-complete-hardening/research/findings.md#f01), [F16](../09-11-steward-complete-hardening/research/findings.md#f16), [F20](../09-11-steward-complete-hardening/research/findings.md#f20), [F21](../09-11-steward-complete-hardening/research/findings.md#f21)。严重性、原始代码锚点、已实现基线与不确定性见共享 findings；这些条目是范围内证据，不声称覆盖全仓所有安全问题。

## Dependencies

`09-11-steward-production-ops`, `09-11-steward-assist-execution`, `09-11-steward-projection-consistency`, `09-11-steward-quality-security`, `09-11-steward-candidate-review`。Trellis parent/children 不自动执行依赖，开始前按本节检查。

## Requirements

### R1 完整业务验收

临时数据库从 migration head 启动，走注册/认领/建档/确认关系 API，观察异步 job、PFV、卡片、通知、建议审批和最终重算；不能只直接调用 run_steward_job 算完整 E2E。

### R2 运行可观测

汇总 core 与 auxiliary 各自队列/耗时/失败、scan/worker heartbeat、最老 queued age、PFV stale/failed 和安全降级；状态来自真实 DB/worker，不只响应 health 200。

### R3 日志与审计

结构化日志只含关联 ID、计数、安全错误码，不含 repr(exc)、SQL 参数、模型 payload、姓名/PIN/token/key。新后台视图仅元数据；家庭内容只由已授权家庭 API 读取。

### R4 容量与告警

记录 50/200 人合成空间 full pair 重算用时与查询量；可配置阈值超限有告警/排队，不默默改算法或换数据库。首版使用现有日志与后台状态，无外部监控平台依赖。

### R5 发布及恢复

验证配置链、迁移、关闭/重开、故障恢复、备份恢复；生成 runbook、脱敏结果和对缺失外部证据的明确说明。真实 provider 成功/降级单列，不以 stub 替代。

## Acceptance Criteria

| ID | 需求 | 可观察验收 |
|---|---|---|
| AC-1 | R1 | 正常 E2E 覆盖 API→event→job→PFV/card→通知→人工提案/确认→重算，另测撤权/失败/重启，保存 ID/status/count 而非个人内容。 |
| AC-2 | R2 | worker 已停但 HTTP 存活时状态为 degraded；core succeeded + assist failed 可分别观察；列出原因而非“全部成功”。 |
| AC-3 | R3 | 注入含测试 token/姓名的异常后，应用日志/后台响应无原文；关系请求、建议审阅审计能关联实际 actor。 |
| AC-4 | R4 | 合成规模测试有明确硬件/数据量/耗时记录；超过扫描周期连续两个窗口未进展触发可见告警。 |
| AC-5 | R5 | 空库和有旧数据迁移往返、disable→enable、进程中断恢复通过；真实 provider 未运行时门禁报告 partial，不写 completed。 |

## Out of scope

不重建已存在的 core、ActionCard 通知或认领推荐；不恢复 generic Steward AgentRun/AgentJob，不做独立聊天人格、自动事实确认、自动发申请/入空间、私有会话读取、任意工具执行。其他子任务的模块由其负责，本任务只遵守接口。

## 决策状态

用户已授权生成规划，并接受确定性核心、可选模型辅助、用户确认和站内通知路线。工程参数为本方案初值；任何权限/产品范围改变需回到规划。
