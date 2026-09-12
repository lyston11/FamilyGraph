# Steward 模型辅助安全修复与质量评测

> 父任务：[09-11-steward-complete-hardening](../09-11-steward-complete-hardening/prd.md)
> 状态：规划完成待实施评审；本轮不启动；优先级 P1。

## Goal

修补 Steward 模型出站政策与输出约束，建立可重复的质量及对抗回归，使模型只能改善候选、排序和解释，不能泄漏授权外内容或误导用户确认。

## 已核实依据

本任务负责 [F07](../09-11-steward-complete-hardening/research/findings.md#f07), [F08](../09-11-steward-complete-hardening/research/findings.md#f08), [F18](../09-11-steward-complete-hardening/research/findings.md#f18), [F19](../09-11-steward-complete-hardening/research/findings.md#f19), [F20](../09-11-steward-complete-hardening/research/findings.md#f20)。严重性、原始代码锚点、已实现基线与不确定性见共享 findings；这些条目是范围内证据，不声称覆盖全仓所有安全问题。

## Dependencies

`09-11-steward-assist-execution`, `09-11-steward-projection-consistency`。Trellis parent/children 不自动执行依赖，开始前按本节检查。

## Requirements

### R1 安全输入与出站

使用明确的 Steward space consumer 投影，不直接把 User.name 等原始字段当可外发数据；先 scope/字段过滤，再结构化 prompt，再 policy_guard 最终检查。敏感必须本地，不可用则降级，不自动切云。

### R2 输出约束

模型输出使用封闭 schema、证据 ID、允许 kind、严格排列和受众范围验证。解释中未经证据支持的人物/事实/承诺不可作为卡片主文案显示；保留确定性原因与隐私影响，失败用模板。

### R3 候选资格与数据最小化

模型候选只是线索，不能增加正式卡片集合或节点。候选端点必须存在于本次授权输入；排序以 recipient 分组；不把另一个收件人的文案作为用户的推荐理由。

### R4 评测与报告

建立版本化 fixture 与 expected 输出，覆盖 ST-5 各关系矩阵行、无路径/多路径、称谓、撤权、注入、未成年人、预算/超时。报告结构性安全结果与质量分开，真实模型与 fake transport 证据分开。

### R5 发布阈值

所有安全/授权/事实约束用例 100% 通过为硬门槛；排序必须集合不变；固定正例候选召回率不少于 90%、错误事实解释零容忍。新策略不通过基线则不开放相应模型开关。

## Acceptance Criteria

| ID | 需求 | 可观察验收 |
|---|---|---|
| AC-1 | R1 | 在姓名/模型上下文中植入测试 token/手机号/注入句、masked 值、私人 RAG，捕获出站 JSON 并断言不外发；云同意撤销和本地不可用按策略降级。 |
| AC-2 | R2 | fake 模型编造亲生关系、隐藏人物、自动入族承诺、任意 JSON kind、超长文本均被丢弃/模板替代；不会仅截断 500 字就通过。 |
| AC-3 | R3 | 排序混入其他收件人、bool id、重复/遗漏 id 拒绝；未确认候选只在审核池，SourceFact/PFV/会员行无写入。 |
| AC-4 | R4 | 每个 fixture 有 ID、来源证据、预期允许/拒绝及 metric；离线报告记录 fixture/prompt/model 版本，不能伪造真实 provider 质量分数。 |
| AC-5 | R5 | 安全失败即整体门禁失败；质量不达标仅该辅助点保持关闭。两种协议 adapter 与降级路径都有回归。 |

## Out of scope

不重建已存在的 core、ActionCard 通知或认领推荐；不恢复 generic Steward AgentRun/AgentJob，不做独立聊天人格、自动事实确认、自动发申请/入空间、私有会话读取、任意工具执行。其他子任务的模块由其负责，本任务只遵守接口。

## 决策状态

用户已授权生成规划，并接受确定性核心、可选模型辅助、用户确认和站内通知路线。工程参数为本方案初值；任何权限/产品范围改变需回到规划。
