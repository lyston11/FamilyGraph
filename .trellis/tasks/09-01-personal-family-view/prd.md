# 用户个人家族视图与多家族树连接

## Goal

FamilyGraph 需要在服务器内部维护一个可容纳多个家族、家庭和断开关系分量的人物关系图，并由 Steward 为每个用户持续规划独属于该用户的个人家族视图（PersonalFamilyView）。

PersonalFamilyView 不是“全平台公共家谱”，也不是每个用户各自维护一套互相矛盾的关系事实。它是在共享、可追溯的正式关系事实之上，结合当前用户拥有的空间授权、人物关联、显式桥接、可见性策略和事实状态，派生出的用户专属家族树投影。

目标是让系统能够准确回答：

- 哪些人物属于当前用户的个人家族树；
- 哪些人物虽然存在于同一内部关系图或治理空间，但不属于当前用户的树；
- 当前用户可以看到人物的哪些字段和关系；
- 当前用户与每个人之间采用哪条关系路径和什么亲属称谓；
- 哪些人物仅是待确认候选，哪些人物可以进入后续推荐集合；
- 当关系事实、权限、空间或桥接发生变化时，哪些个人视图需要失效和重算。

该能力由 Steward 这一事件驱动、按空间分区、长期运行的底层引擎 Agent 负责规划和维护。Assistant 只负责向用户解释结果、展示证据并收集明确操作，不能取代 Steward 计算个人家族视图。

## Product model

### 1. 内部全局关系图

- 系统内部可以保存多个家族、家庭和人物关系分量。
- 不同分量可能共享人物或通过确认关系发生连接，也可能完全没有任何关系。
- “全局”只表示服务器内部身份和事实真源，不表示存在一个用户可加入、浏览或枚举的公共大空间。
- 普通用户、空间管理员、系统管理员和平台角色都不能仅凭角色枚举与其无关的家族分量或家庭档案。
- 所有用户可见结果仍受 HouseholdSpace、LineageSpace、显式桥接、VisibilityPolicy、字段级披露和事实状态约束。

### 2. 多棵可重叠的个人家族树

- 每个用户都需要一个独立计算的 PersonalFamilyView。
- 两个用户的视图可以共享父母、祖辈、子女或其他共同人物。
- 每个用户也可以拥有对方没有的配偶、子女、姻亲或其他亲属分支。
- 两棵视图可以通过某个共同人物、配偶关系或其他确认关系相连。
- 两个用户即使存在于同一数据库或同一治理空间，也可能没有可证明关系，因此彼此不进入对方的个人家族树。
- 用户之间的视图差异来源于事实路径、空间授权、桥接、可见性和事实状态，不是复制并分叉出多套 SourceFact。

### 3. 共享事实与个人投影

```text
共享、可追溯的 SourceFact / 已确认关系图
                  │
                  ├── 空间授权与显式桥接
                  ├── VisibilityPolicy 与字段披露
                  ├── 事实状态与来源证据
                  └── Steward 关系路径和称谓计算
                              │
                              ├── 用户 A 的 PersonalFamilyView
                              ├── 用户 B 的 PersonalFamilyView
                              └── 用户 C 的 PersonalFamilyView
```

- SourceFact 和确认关系是正式真源；PersonalFamilyView、DerivedFact、称谓和推荐资格都是可重建派生结果。
- 不同用户可以看到不同节点、不同关系深度、不同字段、不同主路径和不同称谓，但不得因此产生彼此矛盾的正式关系事实。
- 每个派生节点和边必须能追溯到事实来源、关系路径、空间/桥接依据、policy version 和计算版本。

## 第一轮正式规划决策

用户确认：

- PersonalFamilyView 权威键采用 `viewer_account + root_person + space`；多空间首页只能聚合多个独立视图，不能形成跨空间授权上下文。
- 采用 Steward 事件驱动物化投影 + 查询时授权复核的混合模式；权限撤销即时失效，普通事实变化通过异步重算收敛。
- 跨空间桥接由两个相关用户本人双向同意；两侧空间管理员只收到通知，不得否决、修改或干涉桥接授权。
- 路径和关系类型采用推荐的 confirmed 结构事实口径；partner 不自动延伸，SocialRelation 不进入个人家族树；工程有硬深度上限但不作为产品亲属范围。
- `none` 节点完全省略；`lineage_summary` 仅返回最小基线且不可继续展开。
- 状态采用 `never_computed → queued → running → current → stale → failed`；撤权即时隐藏，事实变化异步重算。
- 首版新增 PersonalFamilyView 专用服务端 API，前端先接收授权投影契约，不在本任务重做复杂树/图布局。
- 关系纠正必须走领域确认流并改变 SourceFact 后重算；称谓纠正进入用户偏好/使用信号；Assistant 不能直接替用户确认关系。

## 第二轮正式规划决策

用户确认全部采用推荐方案：

- 桥接绑定两侧具体空间和两个 anchor，只授予最小跨 anchor 范围；不授予整棵空间树。
- 未认领人物不能由代管人或空间管理员代替本人同意；可保存待确认请求，但在双方本人同意前不生效。
- 配偶本人可进入；仅有配偶边不自动穿透；同一 HouseholdSpace 内已有授权可继续计算，跨 LineageSpace 必须显式桥接；partner 不延伸。
- 主路径复用现有 `relationship_resolver` 确定性排序，最多 3 条替代路径；用户不能手动钉死正式事实路径。
- 浏览器只收到安全枚举型排除理由；不可见目标不返回 target ID、身份、空间、路径或存在性信息。
- 首版采用当前账号固定 viewer/root 的 PersonalFamilyView 专用 GET API，返回一致性快照、版本、状态、节点、边、路径和安全理由。
- 撤权在下一次读取时立即生效；普通事实变化异步、幂等、可见失败、最终收敛，不承诺固定秒级 SLA。

## Requirements

### R1. 严格区分三种“属于”

系统必须分别表达以下概念，不能复用一个 membership 标记：

1. **属于治理空间**：由邀请、接受、管理员流程、`SpaceMember`、`SpaceProfileRef` 或显式桥接产生；这是授权和治理关系。
2. **属于某用户的个人家族树**：由 Steward 根据已确认事实、当前用户授权和可见性规则派生；不创建新的空间成员资格。
3. **属于推荐集合**：在个人视图和授权范围之上进一步满足推荐资格、路径、冷却、拒绝和隐私规则；推荐资格不能提升可见权。

必须成立：

```text
属于同一治理空间
    ≠ 必然属于我的 PersonalFamilyView
    ≠ 必然可以推荐给我
```

### R2. Steward 负责规划个人视图

- Steward 是正式底层 Agent，不是 Assistant 的工具函数，也不是只有被 Assistant 调用时才运行。
- Steward 通过 `DomainEvent → StewardJob → maintenance/engine` 持续更新个人视图。
- Steward 决定派生层面的节点纳入/排除、主关系路径、亲属称谓、候选状态、推荐资格和失效原因。
- Steward 的确定性核心负责关系遍历、证据链、权限、事实状态、称谓规则和资格门禁。
- 未来可以让受限模型辅助自然语言归类、候选排序或解释，但模型输出只能是候选，不能改变正式事实、权限或成员资格。
- Assistant 可以解释 PersonalFamilyView 和收集用户确认，但不能自行遍历全局图绕过 Steward/服务端策略重新生成另一套真相。

### R3. 个人树纳入必须有可证明依据

人物进入某用户的正式 PersonalFamilyView，至少需要同时满足：

- 存在由 confirmed SourceFact 或等价已确认关系组成的可解释路径；
- 路径使用的数据位于当前用户有权消费的 Household/Lineage 范围，或经过有效显式桥接；
- 路径上的人物、边和字段通过 VisibilityPolicy 与披露规则；
- 事实没有被撤销、删除、取代、标记 disputed，且使用当前有效 revision；
- 纳入不会仅依赖“同一空间”“同一姓氏”“模型猜测”或未确认候选。

每个纳入结果必须记录或能够重建：viewer/root、target、主路径、纳入理由、来源事实、空间/桥接依据、policy version、计算版本和更新时间。

### R4. 排除必须明确且可解释

以下情况不得进入正式个人家族树：

- 仅处于同一空间但没有已确认关系路径；
- 仅存在 proposed、pending、disputed 或模型推测关系；
- 路径需要穿越当前用户无权访问的空间或未授权桥接；
- 关键节点或关系被 VisibilityPolicy 遮罩到无法证明该路径；
- 事实已撤销、删除、过期、被 supersede 或版本失效；
- 位于完全无关的断开图分量。

系统应保留结构化排除原因，供审计、重算和用户可理解的解释使用；排除原因不得泄漏被遮罩人物或其他空间的存在。

### R5. 配偶连接不自动扩权

- 已确认的 `spouse/partner` 本人可以进入双方各自的 PersonalFamilyView。
- 配偶边可以表达两棵个人家族树之间存在连接。
- 结婚或伴侣关系不能自动合并双方的 LineageSpace。
- 当前用户不能仅凭配偶边自动穿透查看配偶的父母、兄弟姐妹或完整家族档案。
- 只有显式桥接、共同 Household、双方空间授权，或当前用户本身已拥有两侧合法访问权时，Steward 才能将配偶侧分支投影进来。
- 即使某个配偶侧分支因独立授权而可见，后续“可能认识的血亲”推荐仍必须排除任何包含 `spouse/partner` 的路径。

### R6. 同空间不等于亲属关系

- 同一 HouseholdSpace 或 LineageSpace 中的两个人，如果没有确认亲属路径或有效桥接，不进入彼此的正式 PersonalFamilyView。
- 普通用户不能因同空间成员身份查看对方家庭档案。
- 空间治理角色能看到的最小成员/档案元数据必须与家庭档案内容分离，并由独立治理权限决定。
- Steward 可以针对未确认关系生成候选或 ActionCard，但不能将候选直接升级为正式树节点。

### R7. 数据可信度和输入边界

Steward 可以用于正式个人视图的输入包括：

- confirmed SourceFact 和有效确认关系；
- 当前空间 authorized shared knowledge；
- 有效 DerivedFact 及其完整来源链；
- SpaceProfileRef、显式桥接和当前空间授权；
- TermRegistry、地域称谓规则和用户明确同意记录的称谓使用信号；
- 受限 BehaviorProjection 和 Steward checkpoint。

以下数据只能生成候选或不得消费：

- proposed/pending/disputed 数据：只能生成待确认候选，不能进入正式树；
- private Session/Memory：Steward 禁止读取；
- 其他空间数据：没有桥接和授权时禁止读取；
- 模型生成文本：不能直接成为 SourceFact 或正式关系；
- 系统管理员/platform operator 身份：不能用来扩大 Steward 的家族数据权限。

### R8. Steward 不创造权利或正式事实

Steward 可以生成或维护：

- PersonalFamilyView 节点、边和版本；
- DerivedFact、关系路径和亲属称谓；
- 纳入/排除理由；
- 关系候选、冲突、缺口和重复人物审计发现；
- 推荐资格、ActionCard、冷却和 checkpoint。

Steward 不能：

- 创建或激活 `SpaceMember`；
- 自动给用户授予新的 Household/Lineage 可见权；
- 将 proposed/pending 关系自动确认为 SourceFact；
- 自动发送或接受加入申请；
- 自动合并两个 LineageSpace；
- 代表用户接受推荐或确认高影响操作。

### R9. 视图变化和可重建性

- 关系事实新增、确认、撤销、删除、supersede，人物合并/拆分，空间成员变化，桥接变化，VisibilityPolicy 版本变化和称谓规则变化，都必须使受影响视图失效或重算。
- 重算必须是幂等的，并从正式真源和 DomainEvent/checkpoint 恢复，不能把旧投影反向当成 SourceFact。
- 一个用户视图的变化不能无依据地改变其他用户视图；共享事实变化时只重算真正受影响的 viewer/root。
- 系统必须能区分“尚未计算”“计算中”“当前有效”“因输入变化失效”和“计算失败”等状态；具体状态机由技术设计固定为 `never_computed → queued → running → current ↔ stale → failed`，成功重算可从 stale/failed 回到 current。

### R10. 推荐依赖个人视图但与个人视图分离

- 新用户初始化和亲属推荐由独立任务 `09-01-new-user-family-recommendations` 规划。
- 推荐必须以当前有效 PersonalFamilyView、合法可见范围和可解释关系路径为输入。
- 推荐结果不能反向将候选人物加入正式个人树，也不能提升其可见性。
- 已拒绝、处于冷却、不可见、证据失效或路径包含 `spouse/partner` 的血亲推荐必须排除。
- “刚注册/新认领”的准确触发点暂未决定，不属于本任务。

## Required product scenarios

### Scenario A：共同亲属与独有分支

兄妹 A 与 B 共享父母和祖辈。A 另有配偶和子女，B 另有自己的配偶和子女。A、B 的 PersonalFamilyView 共享共同亲属，但分别包含自己的独有分支，不能简单返回同一棵空间树。

### Scenario B：配偶连接但不穿透

A 与 B 是已确认配偶，双方进入彼此视图。A 不能仅凭婚姻自动查看 B 的父母和兄弟姐妹；只有 A 对 B 侧空间另有合法授权或显式桥接时，相关分支才可出现。

### Scenario C：同空间但无关系

C 与 D 同属某个治理空间，但不存在确认关系路径。D 不进入 C 的正式个人树，也不能仅凭同空间成为亲属推荐；若存在未确认线索，只能产生不泄漏敏感信息的待确认任务。

### Scenario D：断开的家族分量

内部关系图存在完全断开的两个家族分量。任一分量的用户都不能发现、枚举或推断另一分量的存在。

### Scenario E：共享人物连接两棵树

两棵原本分别维护的树通过同一个已去重人物和确认事实建立连接。Steward 应在授权允许时重算受影响用户视图，并保留连接的事实来源和版本，不能复制出第二个人物。

### Scenario F：事实或权限撤销

关系被撤销、桥接失效或用户失去某空间访问权后，受影响分支必须从个人视图中失效；缓存或旧 DerivedFact 不能继续暴露数据。

## Acceptance Criteria

- [ ] **AC-1 内部图不公开**：无关用户和角色不能枚举断开家族分量；内部全局图不会被实现为公共 FamilySpace。
- [ ] **AC-2 每用户独立视图**：系统能为共享部分亲属但拥有不同分支的两个用户生成不同 PersonalFamilyView，并保留共享 SourceFact 真源。
- [ ] **AC-3 三种归属分离**：空间成员资格、个人树纳入和推荐资格使用独立合同；改变派生视图不会自动改变 `SpaceMember` 或可见权。
- [ ] **AC-4 有证据才纳入**：正式树中每个节点/边均有确认路径、来源事实、授权依据、policy/computation version 和可解释纳入理由。
- [ ] **AC-5 同空间不自动纳入**：同空间但没有确认路径的人不会进入个人树或亲属推荐，且不会泄漏其家庭档案。
- [ ] **AC-6 配偶边界**：确认配偶可连接双方视图，但不会自动暴露配偶侧父母/兄弟姐妹；血亲推荐排除包含 `spouse/partner` 的路径。
- [ ] **AC-7 断开分量隔离**：完全无关的图分量之间没有浏览、搜索、推荐或错误解释泄漏。
- [ ] **AC-8 可信状态隔离**：proposed/pending/disputed 或模型猜测只能形成候选，不能进入正式个人树或改变 SourceFact。
- [ ] **AC-9 Steward 权限受限**：Steward shared-only、当前空间和 VisibilityPolicy 约束有自动化测试；private、其他空间和管理员全局权限不能被消费。
- [ ] **AC-10 不自动创造权利**：Steward 不能自动确认关系、创建成员、授予可见权、发送申请、合并空间或代替用户接受推荐。
- [ ] **AC-11 可重建和失效**：事实、人物身份、空间授权、桥接或 policy 变化会幂等重算受影响视图；失效缓存立即停止暴露数据。
- [ ] **AC-12 可解释输出**：纳入、排除、主路径、称谓和候选均能返回不会泄密的结构化理由和来源链。
- [ ] **AC-13 场景回归**：Scenario A–F 均有后端领域测试，并包含跨用户、跨空间、配偶边和撤权后的反例。
- [ ] **AC-14 前端只呈现授权投影**：浏览器只消费服务端 PersonalFamilyView，不自行拼接无权节点，也不把候选渲染为已确认关系。
- [ ] **AC-15 推荐依赖清晰**：新用户推荐任务只能消费当前有效个人视图，不得通过推荐反向提升树归属或数据权限。

## Out of scope

- 个人视图的最终表结构、API payload、前端接入和重算调度已在 `design.md` 与 `implement.md` 中确定；实现时不得另行扩大产品语义。
- 不在本任务决定“新注册/新认领”的准确触发点，也不实现推荐 UI；由 `09-01-new-user-family-recommendations` 负责。
- 不把 Steward 迁回 generic `AgentRun/AgentJob(kind="steward")`；其运行边界由 `09-01-agent-runtime-assistant-only` 负责。
- 不在本任务重做人物去重；唯一人物真源和重复建档审计由 `09-01-person-identity-dedupe` 负责。
- 不允许通过本任务扩大系统管理员、空间管理员或 platform operator 对家庭档案的访问权。

## Dependencies

- `09-01-agent-runtime-assistant-only`：保留 Steward 正式 Agent 身份、`StewardJob` 链路和 shared-only policy consumer。
- `09-01-person-identity-dedupe`：保证连接不同树的共同人物具有可追溯唯一身份，重复记录不会制造虚假分支。
- 已归档 V2.3 relationship intelligence：关系路径、DerivedFact、称谓与 proof 基础。
- 已归档 V2.4 Steward/ActionCard：事件驱动 StewardJob、候选和用户确认闭环。
- `09-01-new-user-family-recommendations`：消费本任务定义的当前有效个人视图和推荐资格边界。

## Planning status

产品决策已收敛；最终技术设计和执行顺序分别记录在 `design.md` 与 `implement.md`。实现阶段如发现技术细节冲突，必须回到本 PRD、architecture 和 backend/frontend spec 校正，不得扩大产品语义。
