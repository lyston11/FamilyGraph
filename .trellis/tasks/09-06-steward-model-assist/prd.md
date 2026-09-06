# Steward 模型辅助层（候选/排序/解释）

> 父任务：`09-06-agent-model-config-system`（D8/D9 决策见父 PRD）。前置：`09-06-agent-provider-admin-migration` 交付的 agent_kind 解析与配置底座。

## Goal

为 Steward（事件驱动、按空间分区、长期运行的底层引擎 Agent）接入模型辅助能力，兑现 09-01 决策记录中用户确认的产品意图："在两个家庭、家族成员之间的关系确认；给刚注册的用户推荐家族用户；为每一个用户规划出他独有的家族树、家族亲属关系称谓等，都需要一个底层运行的 agent"。模型只做**候选生成、排序、解释**三类辅助；确定性关系/权限内核与全部写入红线保留。

## 权威依据

- `archive/2026-09/09-01-agent-runtime-assistant-only/notes.md`：双 Agent、两种运行边界；"未来模型只能辅助候选、排序和解释"、"未来模型 child run/context 审计**另立任务**"；generic `AgentJob(kind="steward")` 已移除，Steward 正式执行单元是 `StewardJob(space_id, job_id, policy_version)`。
- `archive/2026-08/08-26-v2-agent-system/prd.md` R1/R3：LLM 负责自然语言理解、歧义处理、编排与解释；确定性关系计算、授权与状态机不得交给 LLM；LLM 自报置信度仅用于排序。
- `services/steward.py` 现行确定性内核（dirty 重算、冲突/缺失检测、推荐资格矩阵、checkpoint 幂等）——本任务在其上叠加，不重写。

## Requirements

### R1. 模型辅助点（三类，各自 feature flag）

- **候选**：关系候选补全（如可能的称谓/分支建议）、给刚注册用户的家族推荐候选扩充；输入仅为该空间已确认/授权共享数据。
- **排序**：对确定性矩阵产出的合格候选做重排；LLM 自报置信度仅用于排序，不改变资格判定结果。
- **解释**：ActionCard 原因/隐私影响的自然语言解释、PersonalFamilyView 关系路径说明；模板文案为 fallback。
- 每类独立开关（平台级 + 空间级），默认全关；关闭时行为与当前 V2.4 确定性形态逐字节等价。

### R2. 运行与审计边界（09-01 "另立任务"的直接兑现）

- Steward 模型调用使用 child run / child context 审计模型（09-01 明确要求的独立设计），与 Assistant 的 generic AgentSession/AgentRun 隔离；不伪造 generic AgentRun。
- 继续绑定 `space_id + job_id + policy_version`；只读本空间 confirmed/authorized shared 数据；不读私人 Session/Memory；不继承任何管理员可见性。
- Provider 解析消费子任务 A 的 `resolve_for_space(kind="steward")`；敏感判定强制本地的合同同样适用。

### R3. 写入红线（D9，全部保留并有测试）

- 绝不自动确认 SourceFact、不创建成员、不扩大可见权、不发送申请、不合并空间、不保存自由形式隐藏长期记忆。
- LLM 产物只能落在：候选池（内部）、卡片解释文案、报告；凡要落为正式数据（如关系修正）必须经 owner 显式确认（确认动作复用 ActionCard accepted 后端重校验机制）。
- checkpoint 幂等语义不变：同 cursor 重放零副作用；模型调用失败不得阻断确定性流水线（降级为模板/跳过排序，有明确日志与事件）。

### R4. 质量与评测

- 排序/候选质量有基线评测集（至少覆盖 ST-5 推荐矩阵各行）与回归门槛；解释文案有格式与幻觉抽检（不得引用未确认事实）。
- token/预算：每 job 模型调用次数与 token 上限可配置，超限降级。

## Acceptance Criteria

- [ ] AC-1：三类辅助点默认关闭时，Steward 全部既有测试不改一行通过（行为等价）。
- [ ] AC-2：开启候选/排序后，资格判定结果仍由确定性矩阵决定；LLM 重排仅改变呈现顺序，且有测试证明越权候选（未确档/proposed/disputed/friend）不因排序进入卡片。
- [ ] AC-3：child run/context 审计记录每次模型调用的空间、job、policy_version、prompt 摘要与 token 用量；与 Assistant 会话数据隔离有测试。
- [ ] AC-4：LLM 建议的关系修正仅在 owner 确认并经后端重校验后落 SourceFact；确认前不产生任何正式写入。
- [ ] AC-5：模型不可用/超时/超预算时确定性流水线照常产出，降级路径有事件与日志。
- [ ] AC-6：空间 steward 模型设置（子任务 A 的 UI）选择/停用对 Steward 行为生效；无静默 fallback。

## Out Of Scope

- Steward 成为会话式聊天 agent（09-01 边界不变）。
- LLM 参与授权、状态机、可见性判定。
- 自主工具规划/执行（V2.4 确定性触发器仍是唯一 job 来源；agent 自主规划如需，另立任务评审）。

## Notes

- 实施前必须先产出 `design.md`（child run 审计模型、prompt 合同、降级矩阵、评测方案），通过评审后再进入实现。
