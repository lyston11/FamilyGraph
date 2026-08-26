# FamilyGraph V2.4 Steward 与 ActionCard：空间管家和推荐闭环

> 依赖：V2.0–V2.3 完成。

## Goal

交付一个严格按空间运行的后台 Steward：持续刷新派生关系和称谓、发现缺口/冲突、把可行动建议变成有状态 ActionCard；它可以沉淀正式领域事件和投影，但永远不能替用户写 SourceFact、发送加入申请或合并空间。

## Requirements

### ST-1 Steward 权限与上下文

- 每次运行绑定 `space_id + job_id + policy_version`，只读取该空间确认 SourceFact、有效 DerivedFact、TermRegistry、BehaviorProjection、确认共享 RAG（V2.5 后）和 Job checkpoint。
- 不读取任何用户私人 Session/Memory，不访问其他空间，不继承 platform_operator/admin 全局可见性。
- Steward 定义由系统维护；空间 owner/admin 只配置词典、允许的 Provider、知识库和功能开关。

### ST-2 触发与工作

- 由 SourceFact/claim/membership/term/disclosure/domain event、定期完整性扫描或管理员显式重跑触发 durable Job。
- 工作包括 DerivedFact dirty 重算、称谓候选刷新、冲突/缺失检测、推荐资格判断和 ActionCard 去重/失效。
- Steward checkpoint 只保存作业进度/版本，不保存自由形式隐藏长期记忆。

### ST-3 DomainEvent 与行为投影

- 建档描述解析、称谓纠正、提案确认/争议、卡片 viewed/dismissed/accepted、成员申请结果等形成目的明确的 DomainEvent。
- BehaviorProjection 可用于当前空间的词条使用、卡片冷却、纠正偏好和推荐质量；禁止键盘/鼠标/停留时长泛监控。
- Agent 摘要是投影，不能替代原始描述、用户话语或正式事实。

### ST-4 ActionCard

- 状态：pending/viewed/accepted/executed/dismissed/expired/superseded；终态不可复活。
- 唯一键至少覆盖 card_type、subject/object、space、evidence_version；相同证据不重复骚扰。
- 卡片包含用户可理解的原因、使用的确认事实/路径、将发生的动作、隐私影响、有效期与当前状态。
- accepted 后由后端重新校验 actor、space、SourceFact revision、target claim/membership、VisibilityPolicy 与冷却；成功才 executed。

### ST-5 推荐范围与资格

- 首版只在“创建者 ↔ 被创建者”之间产生空间/桥接卡片；被创建 Profile identity_confirmed 且相关 SourceFact confirmed 后才进入判断。
- no-space/household/lineage 的创建选择影响可生成的卡片，但不自动 membership。
- friend/colleague：无家庭/家族推荐；可由用户手工邀请 household guest。
- partner：双方确认且允许披露后，仅可推荐共同 HouseholdSpace；不推荐 LineageSpace。
- spouse：可推荐共同 HouseholdSpace，也可分别申请对方指定 LineageSpace；不自动通过。
- biological/adoptive/step parent-child、confirmed sibling：按创建时选择推荐 household/lineage/两者；guardian 默认 household。
- 用户点击“发起申请”才调用发送命令；卡片 accepted 可表示进入确认页，不得代替最终发送动作。

### ST-6 空间桥接

- 配偶/亲属只建立人物关系或桥接建议；双方各自 LineageSpace 不合并，父母兄弟姐妹不自动互见。
- 双方可共同新建 HouseholdSpace，共同子女可属于该 Household 并连接各自谱系。
- PersonalFamilyView 可以把有权空间投影成连续图，但底层保持分区。

## Acceptance Criteria

- [ ] AC-ST1：Steward 对另一个空间和私人 Session/RAG 的读取全部拒绝，operator 身份不能放宽。
- [ ] AC-ST2：同一 dirty event 幂等处理；Job crash/重试不重复 DerivedFact 或卡片。
- [ ] AC-ST3：相同 evidence_version 只有一张有效卡；事实/权限变更会 supersede 旧卡。
- [ ] AC-ST4：未确档 Profile、proposed/disputed Fact、friend/colleague 均不生成家族推荐。
- [ ] AC-ST5：伴侣/配偶/亲子/sibling/guardian 矩阵逐行与卡片文案、可执行动作一致。
- [ ] AC-ST6：接受卡片后篡改事实、撤权或过期时执行被拒绝；SourceFact 与申请不会被静默写/发。
- [ ] AC-ST7：称谓修改、提案与卡片行为形成 DomainEvent/Projection，不要求 Memory card，也不采集泛行为。
- [ ] AC-ST8：共同 Household 创建不合并 Lineage，不自动暴露双方父母兄弟姐妹。

## Out Of Scope

- 不做全平台陌生人候选、MatchBroker 或跨空间遍历。
- 不允许 Steward 自主接受/发送请求、修改公开范围或创建 SourceFact。
- 不把 Job checkpoint 当长期人格 Memory。

## Blocking Open Questions

无。
