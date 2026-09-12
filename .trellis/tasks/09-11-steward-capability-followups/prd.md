# Steward 后续能力：授权知识、个人路径解释与地区称谓

> 父任务：[09-11-steward-complete-hardening](../09-11-steward-complete-hardening/prd.md)
> 状态：延期研究，未授权实现；优先级 P3。

## Goal

为已经讨论但尚未有完整产品闭环的可选能力保留可执行研究与准入方案，避免未来重复发现或为了“像 Agent”增加无用模块。

## 已核实依据

本任务负责 [F22](../09-11-steward-complete-hardening/research/findings.md#f22)。严重性、原始代码锚点、已实现基线与不确定性见共享 findings；这些条目是范围内证据，不声称覆盖全仓所有安全问题。

## Dependencies

`09-11-steward-release-observability`。Trellis parent/children 不自动执行依赖，开始前按本节检查。

## Requirements

### R1 共享知识辅助

研究在当前空间 confirmed shared RAG 上生成有来源的解释/候选；不读取 private memory/session，不用检索文本覆盖结构事实。先给出相对纯结构输入的增益再启用。

### R2 个人路径解释

补模型对 PersonalFamilyView 已授权路径的解释候选；单独绑定 viewer/root/space/input version，复用安全辅助批次，不能把空间模型输出作为每个用户的共同真相。

### R3 地区称谓内容

保留现有四级词典和原文，盘点 locale 资源缺口；明确地区覆盖、同义歧义、两人共用空间别名门槛。后续新增地区必须有具体用例和词表来源。

### R4 条件性能工作

仅在发布任务容量样本证明 full-pair 重算超过预算后设计受影响子图重算、独立 worker 或批次公平性；先有基线再选择组件。

## Acceptance Criteria

| ID | 需求 | 可观察验收 |
|---|---|---|
| AC-1 | R1 | 研究报告列出可检索类别/权限/确认/撤销矩阵与至少 6 个合成问答对，证明 shared-only 且事实优先。 |
| AC-2 | R2 | 两个 viewer 同空间不同授权路径的解释互不串线；确定性模板仍有效，报告是否值得启用。 |
| AC-3 | R3 | 交付 locale coverage 表与证据来源，未知词可解释降级；不把任意模型生成词升为全局标准。 |
| AC-4 | R4 | 列明触发性能重构的观测条件和保序/幂等/权限等价测试；没有超限证据则保留现状。 |

## Out of scope

不重建已存在的 core、ActionCard 通知或认领推荐；不恢复 generic Steward AgentRun/AgentJob，不做独立聊天人格、自动事实确认、自动发申请/入空间、私有会话读取、任意工具执行。其他子任务的模块由其负责，本任务只遵守接口。

## 决策状态

研究范围已登记；实施决策尚未收敛，具体问题见 design.md。不会阻塞本轮六个修复任务。
