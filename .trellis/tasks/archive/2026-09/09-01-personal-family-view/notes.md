# 用户个人家族视图与多家族树连接：决策记录

## 2026-09-01 · 需求来源

用户明确指出，FamilyGraph 不能把一个空间里的全部人物简单当成每个人相同的家族树：

> 因为每个人的家族空间不一样，用户之间可能存在共同的家族成员，但是还有他单独的亲属，所以对于每一个用户都需要单独为他呈现出独属于他的家族树。

用户进一步定义：

> 可以看作是一个大空间中的多个家族树，有些可能是通过某人（无论是配偶还是什么）之间有联系；有些则没有任何联系；但是对于每一个用户，他们能看到的视角都是仅限于他们的家族空间。这就需要 Steward 去规划哪些用户是属于的，哪些是不属于的，后续的用户推荐也是基于此的。

这里的“大空间”经讨论后解释为服务器内部全局人物关系图，而不是所有用户可以浏览的公共 FamilySpace。

## 已确认的产品决定

### D1. 两个正式 Agent

- Assistant 是会话式 LLM Agent，负责理解、解释和收集用户明确操作。
- Steward 是事件驱动、按空间分区、长期运行的底层引擎 Agent。
- Steward 负责个人家族树、亲属称谓、用户/关系候选和一致性审计；它不是普通定时函数，也不是 Assistant 工具。
- Steward 当前采用确定性核心；未来模型只能辅助候选、排序或解释。

### D2. 共享真源，个人投影

- 系统内部有共享人物和 SourceFact 真源。
- 每个用户看到的是基于自身事实路径和权限计算的 PersonalFamilyView。
- 不为每个用户复制一套可独立冲突的正式家谱。
- 多个视图可共享人物，也可拥有独有分支或完全断开。

### D3. 三种归属分开

- `SpaceMember`/治理归属是授权关系。
- PersonalFamilyView 纳入是 Steward 派生关系。
- 推荐资格是比视图纳入更窄的资格判断。
- 三者不能互相自动升级。

### D4. 同空间不自动成为亲属

- 同空间但没有确认关系路径的人不进入彼此正式个人树。
- 同空间本身也不产生亲属推荐资格。
- 管理目录中的最小元数据不能等同家庭档案可见性。

### D5. 配偶连接但不穿透

- 已确认配偶进入双方视图，并可以成为两棵树的连接点。
- 配偶关系不自动合并双方 LineageSpace。
- 不能仅凭配偶关系查看配偶的父母、兄弟姐妹和完整家族。
- 只有独立空间授权、共同 Household 或显式桥接允许投影配偶侧分支。
- 血亲推荐始终排除含 `spouse/partner` 的路径。

### D6. Steward 规划派生结果，不创造权利

- Steward 可以决定个人视图的纳入/排除、主路径、称谓和候选资格。
- Steward 不能创建成员、扩大可见权、确认 SourceFact、发送申请、合并空间或代表用户接受建议。
- 新的直接关系只能成为候选/ActionCard，需授权主体确认。

### D7. 输入和隐私边界

- 正式视图使用 confirmed SourceFact 和 authorized shared knowledge。
- proposed/pending/disputed 只能生成候选。
- Steward 不能读取 private Session/Memory、无权空间或依靠平台管理员全局权限。
- 用户明确同意记录的称谓使用可以作为个性化输入，但不能改变关系事实。

### D8. 新用户推荐后续规划

- 新用户推荐依赖 PersonalFamilyView，不与本任务混做。
- “刚注册/managed→claimed/未来自助注册”哪个事件触发初始化仍暂定。
- 已建立 `09-01-new-user-family-recommendations`。

## 术语纠偏

- “全局图”不是公共空间，也不授予发现能力。
- “个人家族树”不是一份新的 SourceFact 真源，而是用户专属、可重建、带来源的投影。
- “属于某人的树”不等于“成为某空间成员”。
- “可以推荐”不等于“已经属于个人树”，更不等于“获得可见权限”。
- “Steward 不走 Assistant/Pi generic Runtime”不等于“Steward 不是 Agent”。

## 与当前实现的已知差距

- 现有 `/graph/me` 主要从当前人物做旧 `Relation` BFS；`family` 按深度，`clan` 接近整个连通分量。
- 它尚未完整表达多个授权空间、SpaceProfileRef/桥接、SourceFact/DerivedFact 版本、每用户纳入理由、个人称谓、推荐资格和失效审计。
- 现有 DerivedFact、relation proof、VisibilityPolicy 和 StewardJob 是原料，但不能被描述为完整 PersonalFamilyView 已经实现。

## 进入正式规划前必须研究

- 当前 Household/Lineage/SpaceProfileRef 与人物身份的真实数据流。
- `/graph/me`、relation proof、DerivedFact、称谓和 VisibilityPolicy 的全部调用方。
- StewardJob 的事件触发、checkpoint、重试和空间单活合同。
- 跨空间桥接已有接口、审批和撤销语义。
- 前端当前树/图的请求、缓存、切换空间和遮罩行为。
- 数据量、重算成本和 SQLite 并发约束。

本 notes 保存讨论来源和已确认决定；`prd.md` 是要求与验收的权威版本。未来技术设计若与这里冲突，应先回到用户确认的产品语义，而不是按现有 BFS 实现反推需求。

## 2026-09-01 · 第一轮规划决策补充

用户确认第一轮推荐方案，唯一调整是跨空间桥接：

- 视图键为 `viewer_account + root_person + space`，多空间只聚合独立视图。
- 采用 Steward 事件驱动物化投影 + 查询时授权复核；撤权立即失效，普通事实变化异步重算。
- 桥接由两个相关用户本人双向同意；两侧空间管理员只接收通知，不得否决或干涉。
- 采用 confirmed 结构事实路径；partner 不自动延伸，SocialRelation 不进入家族树，工程保留硬深度上限。
- `none` 完全省略，`lineage_summary` 仅返回不可展开的最小基线。
- 视图状态采用 `never_computed → queued → running → current → stale → failed`。
- 首版使用 PersonalFamilyView 专用 API，前端先消费服务端授权投影契约。
- 关系纠正走 SourceFact 确认流，称谓反馈进入偏好/使用信号，Assistant 不直接替用户确认。

下一轮需细化桥接对象与范围、managed/minor/deceased 的本人同意、配偶侧延伸、路径主次排序、隐私排除解释、API 字段和新鲜度承诺。

## 2026-09-01 · 第二轮规划决策补充

用户确认全部采用推荐方案：

- 桥接绑定两侧具体空间和两个 anchor，只授予最小跨 anchor 范围，不授予整棵空间树。
- 未认领人物不能由代管人或空间管理员代替本人同意；可以保存待确认请求，但未生效。
- 配偶本人可进入；仅有配偶边不自动穿透；同一 HouseholdSpace 内已有授权可继续计算，跨 LineageSpace 必须显式桥接；partner 不延伸。
- 主路径复用现有 `relationship_resolver` 的确定性排序，最多 3 条替代路径，用户不能手动钉死正式事实路径。
- 浏览器只收到安全枚举型排除理由；不可见目标不返回身份、空间、路径或存在性信息。
- 首版使用当前账号固定 viewer/root 的 PersonalFamilyView 专用 GET API，返回一致性快照、版本、状态和投影内容。

## 2026-09-01 · 实现进度

已实现 PersonalFamilyView/Node/Edge 与显式跨族谱 Bridge 的 ORM、Alembic 0025、schema、浏览器 API、前端 API/runtime guard/Pinia store。active bridge 已接入关系图：仅当前 viewer 为 anchor 时建立跨空间边，沿另一侧 confirmed 结构路径计算；跨空间节点使用最小 lineage_summary 投影。DomainEvent 会标记受影响视图 stale，Steward 空间作业负责重建。

已验证：PersonalFamilyView/Bridge/Resolver/Steward 定向测试 68 passed；后端全量（排除已知 ownership-transfer deadlock）619 passed、3 skipped、1 deselected；backend ruff/mypy、frontend type-check/lint/test/build、Alembic upgrade→downgrade→upgrade 均通过。
