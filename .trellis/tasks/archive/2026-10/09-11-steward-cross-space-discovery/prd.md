# Steward 跨空间亲属发现：延期设计与准入条件

> 父任务：[09-11-steward-complete-hardening](../09-11-steward-complete-hardening/prd.md)
> 状态：延期研究，未授权实现；优先级 P3。

## Goal

保留跨空间“可能认识的人”与 MatchBroker 的后续需求及风险决策，明确在当前发布中不开发、不启用，防止被当作普通推荐缺口自动扩权实现。

## 已核实依据

本任务负责 [F23](../09-11-steward-complete-hardening/research/findings.md#f23)。严重性、原始代码锚点、已实现基线与不确定性见共享 findings；这些条目是范围内证据，不声称覆盖全仓所有安全问题。

## Dependencies

`09-11-steward-release-observability`。Trellis parent/children 不自动执行依赖，开始前按本节检查。

## Requirements

### R1 范围登记

与现有授权空间内认领/亲属推荐分开。只研究不同空间是否存在自愿且最小披露的关联发现方式，不直接遍历全局家庭图。

### R2 双边同意

未来匹配必须明确双方加入候选池、最小披露、退出和冷却；推荐命中不建立成员资格、事实、bridge 或自动加入空间。

### R3 威胁模型

覆盖人物/空间存在性枚举、隐藏亲属关系推断、家庭暴力/未成年人、时间/数量侧信道、撤权和候选令牌重放。

### R4 停止条件

拿不出避免越权泄漏的设计和真实用户价值时结论应为不实施。不得借本任务生成真实人物候选、外部发送或云调用。

## Acceptance Criteria

| ID | 需求 | 可观察验收 |
|---|---|---|
| AC-1 | R1 | 明确已实现 within-scope 推荐与 cross-scope discovery 的输入输出、授权差异表，当前发布排除后者。 |
| AC-2 | R2 | 研究草案给出 opt-in/withdraw/expiry/token replay 的状态机与权限矩阵；所有推荐动作仍需领域授权。 |
| AC-3 | R3 | 至少列出 8 个攻击场景及可验证防护/不可解决项；隐藏对象响应须不可枚举。 |
| AC-4 | R4 | 交付 go/no-go 决策材料，只有后续新的用户批准才能拆实现任务；无批准时保持 deferred。 |

## Out of scope

不重建已存在的 core、ActionCard 通知或认领推荐；不恢复 generic Steward AgentRun/AgentJob，不做独立聊天人格、自动事实确认、自动发申请/入空间、私有会话读取、任意工具执行。其他子任务的模块由其负责，本任务只遵守接口。

## 决策状态

研究范围已登记；实施决策尚未收敛，具体问题见 design.md。不会阻塞本轮六个修复任务。
