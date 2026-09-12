# Agent Note: Steward 候选走有状态建议审核闭环，模型永不直接写正式关系

Status: implemented

## Problem

模型产生的血缘候选只是内部池里的裸 JSON（kind 任意截断、digest 含模型措辞、只有 proposed/dismissed 两态、无审核 API）：相同建议换措辞即可绕过去重；owner 可以一键把建议变成正式关系；确定性冲突没有人类可处理的站内待办。产品要求"模型仅辅助、当事人确认"，但执行链没有承接点。

## Decision

迁移 0038 引入受控投影 `StewardSuggestion` + 收件人表 `StewardSuggestionRecipient`，`services/steward_suggestions.py` 承接：

- kind 闭合为 `relation_proposal | term_preference | identity_duplicate | missing_information`；evidence_json 只存允许的 ID/revision；去重键 = space/kind/有向端点/结构化值/证据哈希，**不含模型措辞**；证据变化产生关联新建议（supersede），旧建议不复活；
- 受众与操作矩阵：列表/详情只对"active 成员且证据端点对其可见"者开放；owner（非端点）只能 submit 生成提案、永远不能代确认；确认人 = 端点本人 ∪ 合法代管（custody 能力矩阵），`commands/relationship_proposals.py` 做两端授权、expected revision、环检查与 SOURCE_FACT_TYPES 白名单，路由禁止直调 `transition_source_fact`；
- submit 幂等（Idempotency-Key + CAS revision + evidence_hash 预检）：关系建议 202 返回 `{suggestion, linked_proposal, pending_confirmations}`，绝不显示为已确认；term_preference 仅本人提交并复用既有个人词命令；identity_duplicate/missing_information 首版只 open_details/dismiss 转既有资料流程；
- 通知复用现有 Notification（suggestion_id 引用、唯一 recipient×space×suggestion、固定模板标题、状态实时投影），不建第二套通知系统。

## Alternatives considered

- **把每个候选直接物化为 ActionCard** — 复用面最小，但 ActionCard 是"建议+确认状态"而非待审核线索，会把模型输出当授权凭据，且无法表达按收件人的独立驳回/冷却；确定性推荐卡语义也会被污染。
- **复用 connections 命令并把 elder/younger 模糊映射到收养/继亲** — 命令面零新增，但语义不等价，会把非生物关系写成 biological_parent 级别的事实，已列为明确禁止的映射。
- **不做审核闭环，候选继续留在内部池** — 安全上最保守，但 F07/F14/F15 的产品缺口（用户无法看理由、驳回、补资料）原样保留，与"最终由有权人员确认"的目标相悖。

## Consequences

- **收益**：从建议到正式关系全程有权限校验与审计；相同结构证据只产生一个待办；驳回冷却按收件人×证据版本隔离；已提交提案随领域对象终局化（submitted ≠ confirmed）。
- **代价与已知上限**：v1 的 term_preference 没有生成器（只有提交/展示路径）；identity_duplicate/missing_information 尚无 submit，需人工在既有流程处理；建议与候选是两层存储，模型措辞仅存于内部池。

## Verification

`backend/tests/test_steward_suggestions.py`（去重、受众 404、owner 不可代确认、并发 submit 单副作用、幂等重放、路由级 JSON 序列化回归）；`scripts/steward_e2e.py` 走完 建议提交 → 端点确认 → 图内生效 → 后续重算。

Note: 候选→正式关系的唯一合法路径 — 见 .agent-notes/implemented/feature/2026-09-11-steward-suggestion-review-loop.md
