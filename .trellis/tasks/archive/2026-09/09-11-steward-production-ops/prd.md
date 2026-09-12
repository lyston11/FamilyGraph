# Steward 生产调度与运维可靠性

> 父任务：[09-11-steward-complete-hardening](../09-11-steward-complete-hardening/prd.md)
> 状态：规划完成待实施评审；本轮不启动；优先级 P1。

## Goal

让已有的空间后台引擎能在无新事件、停机重启、短暂失败和运维重跑时持续收敛；已有成功历史与当前领域数据不被重复执行覆盖。

## 已核实依据

本任务负责 [F01](../09-11-steward-complete-hardening/research/findings.md#f01), [F02](../09-11-steward-complete-hardening/research/findings.md#f02), [F03](../09-11-steward-complete-hardening/research/findings.md#f03), [F17](../09-11-steward-complete-hardening/research/findings.md#f17)。严重性、原始代码锚点、已实现基线与不确定性见共享 findings；这些条目是范围内证据，不声称覆盖全仓所有安全问题。

## Dependencies

无；是本轮首个实现任务。Trellis parent/children 不自动执行依赖，开始前按本节检查。

## Requirements

### R1 运行状态与配置

保留开发环境安全默认；明确报告 disabled / paused / running / degraded。分别解释核心、worker、关系引擎、PFV、模型总开关和空间开关的有效状态。部署明确选择启用核心和 worker；模型辅助继续默认关闭。

### R2 扫描与追补

定时扫描每个有效空间，补登记遗漏事件、未计算视图和到期卡片；首次启用、重新启用或策略版本变化时追补。扫描本身不直接改事实，持续 cursor 相同时也可以进行到期检查，不被历史 succeeded 幂等短路。

### R3 有限恢复

区分可重试的数据库锁/暂时资源错误和确定性的输入/权限错误；前者有限退避，后者明确 failed。lease 过期恢复继续保留；到达终态的历史 job 不复活，人工重跑创建关联的新 job。

### R4 调度与租约

同空间至多一个活跃 core job；扫描与事件并发合并水位。执行、heartbeat、结算都验证 job id + attempt + lease owner + lease deadline；拒绝过期执行者覆盖新 lease。多 listener 只有一个维护循环。

### R5 运维重跑

系统管理员可在独立 8002 listener 查看作业元数据、请求单空间重跑。要求原因、幂等键、审计与频控；平台身份不获得人物、候选、图谱、prompt、checkpoint 原文访问权。

## Acceptance Criteria

| ID | 需求 | 可观察验收 |
|---|---|---|
| AC-1 | R1 | 所有总开关/worker/空间辅助组合均返回可解释状态；只开模型设置不能显示引擎已运行。 |
| AC-2 | R2 | 构造停机期间事件、空闲空间到期卡、相同事件水位、重新启用四种情境，两个 tick 内登记受影响作业且不重复活跃行。 |
| AC-3 | R3 | 锁冲突可退避后成功；非法输入进入 failed 且其他空间继续。三次可重试失败耗尽后不继续自动重试。 |
| AC-4 | R4 | 旧 worker 使用旧 attempt/lease 结算被拒；多 listener 启停仍只有一个循环，最后 holder 停止后结束。 |
| AC-5 | R5 | 重放相同重跑请求仅一个关联 job；family token 无法调用 8002，8000 不暴露后台路由，响应精确字段白名单。 |

## Out of scope

不重建已存在的 core、ActionCard 通知或认领推荐；不恢复 generic Steward AgentRun/AgentJob，不做独立聊天人格、自动事实确认、自动发申请/入空间、私有会话读取、任意工具执行。其他子任务的模块由其负责，本任务只遵守接口。

## 决策状态

用户已授权生成规划，并接受确定性核心、可选模型辅助、用户确认和站内通知路线。工程参数为本方案初值；任何权限/产品范围改变需回到规划。
