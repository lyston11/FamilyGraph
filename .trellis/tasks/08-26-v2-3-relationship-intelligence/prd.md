# FamilyGraph V2.3 Relationship Intelligence：关系推理与称谓系统

> 依赖：Foundation、Runtime、Read-only Assistant 完成。

## Goal

用稳定原子事实和确定性路径算法，为每个查看者在当前空间生成尽量完整、可解释的亲属关系与称谓；允许用户以自然语言和地方叫法输入、纠正和积累空间习惯，但任何不确定解析都不能静默改变 SourceFact。

## Requirements

### KI-1 事实分层

- SourceFact 支持 biological/adoptive/step parent-child、guardian、spouse、partner、direct_sibling；每条含方向、scope、asserted_by、provenance、state、revision。
- SocialRelation（friend/colleague 等）单独存储，不参加血缘/姻亲路径和推荐。
- DerivedFact/DerivedConcept 由确认 SourceFact 路径计算，带 viewer、space、evidence ids/hash、algorithm/term version，可删除重建。
- direct_sibling 可在父母未知时独立确认，但不得据此创建虚构父母；已确认共同父母时 sibling 应由图自动派生。

### KI-2 确定性亲属路径

- 支持多路径、收养/继亲/监护/伴侣、共同子女、多个家庭空间，不能假设单根树或只有一次婚姻。
- 为当前查看者计算方向、世代、父系/母系/姻亲线、路径类型和标准概念；例如可从“奶奶的兄弟”确定为相应祖辈旁系概念。
- 多条等价路径按稳定规则选主路径并保留替代路径；冲突、环或事实不足不强行给唯一结论。
- LLM 不参与图遍历/世代/路径真值，只把算法结果翻译成用户听得懂的解释。

### KI-3 自由输入与推断等级

- 关系输入框接受“妈妈、老妈、母亲、舅爷爷”等自由文本；内部候选码可建议但不强制用户理解或选择。
- ProfileIntakeExtractor 输出候选概念、可能的原子事实、依据词素和等级：determined/supported/ambiguous/conflicting。
- `determined` 只自动更新 DerivedFact；`supported` 生成一句话确认提案；`ambiguous` 最多追问一次通俗问题；`conflicting` 展示冲突并保留原文。
- “爷/舅/姑”等词素与当前图可作为候选依据，但 LLM 自报置信度不能提升写入权限。

### KI-4 TermRegistry

- 四级解析/展示优先级：个人偏好 > 当前空间词典 > 地区语言包 > 系统标准称谓。
- 用户原始输入单独保存，任何词典/Agent 产物都不得覆盖。
- 普通用户可直接修改个人显示称谓；修改形成 DomainEvent/BehaviorProjection，不需要记忆卡。
- 同一空间两位不同 identity_confirmed 用户对同一 DerivedConcept 使用同一词后，自动成为该空间可推荐别名；保留 revision/使用证据，无管理员发布，不推广为全局模板。
- 同一人物进入不同空间可显示不同称谓。

### KI-5 Assistant 集成

- Assistant 可回答“某人是我的什么人、为什么、还有其他叫法吗”，展示主路径、事实状态、空间/个人称谓来源和必要的不确定性。
- 用户纠正称谓只改个人偏好或明确提交空间词使用，不直接改变结构关系。
- DerivedFact cache 失效后必须重算，不能用旧称谓/旧路径继续回答。

## Acceptance Criteria

- [ ] AC-KI1：稳定 fixture 覆盖亲生、收养、继亲、监护、配偶、伴侣、父母未知的直接兄弟姐妹、再婚与共同子女。
- [ ] AC-KI2：“奶奶的兄弟”等已确定路径能给出标准概念、主/替代路径与可解释依据；资料不足时不伪造父母或方向。
- [ ] AC-KI3：自由输入原文完整保留；Agent/词典更新不会覆盖原文或 SourceFact。
- [ ] AC-KI4：determined/supported/ambiguous/conflicting 四类各有测试，只有用户确认可写 SourceFact。
- [ ] AC-KI5：个人>空间>地区>系统优先级、跨空间不同称谓和两位确档用户规则可重复验证。
- [ ] AC-KI6：称谓纠正形成领域事件并立即影响当前视图，不需要 Memory card，也不污染其他用户/空间。
- [ ] AC-KI7：算法结果具有确定性：相同 facts/version 得到相同 concept/path；LLM 更换不改变结构结论。
- [ ] AC-KI8：删除/争议/撤权后 DerivedFact 与 Assistant 答案及时失效。

## Out Of Scope

- 不做全平台跨空间匹配、陌生亲属搜索或 MatchBroker。
- 不把所有地方叫法一次性内置；首版提供基础地区包与可扩展注册表。
- 不让空间词典覆盖系统关系语义。

## Blocking Open Questions

无；首批地区包可在实现时选择，不改变优先级和写入治理。
