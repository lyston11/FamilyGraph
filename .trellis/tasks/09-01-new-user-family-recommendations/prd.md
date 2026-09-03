# 新认领用户的个人家族初始化与亲属推荐

## Goal

新用户完成认领（`managed → claimed`）时，由 Steward 保证其个人家族视图（PersonalFamilyView）在所属空间进入可计算/已计算状态，并基于**当前有效**的 PersonalFamilyView 生成三类推荐：可见血亲与亲属称谓、待确认关系线索。推荐是只读的候选投影，不创建成员资格、不提升可见权、不反向修改个人树。

本任务落实 `09-01-personal-family-view` 的 R10/AC-15 移交项："新用户初始化和亲属推荐由独立任务规划；推荐必须以当前有效 PersonalFamilyView、合法可见范围和可解释关系路径为输入"。

## Background / Confirmed Facts

- **触发点已决策（2026-09-02，用户确认）**：采用 `managed → claimed`（认领）作为新用户初始化与首轮推荐的唯一触发事件。`09-01-new-user-family-recommendations/notes.md` 中其余三个候选（首次获得空间访问权、未来自助注册、已有用户首次形成可计算树）全部推迟。
- 认领有**两个转换点**：`commands/identity.py:29` `claim_and_confirm_own_identity`（「这是我」合并转换，已发 `account.claimed` 事件）；`commands/members.py:477` `change_own_pin`（首登强制改 PIN 完成认领，**目前不发任何领域事件**）。触发合同必须覆盖两个入口。
- **初始化的懒语义已存在**：`personal_family_view.get_view` 对 `never_computed` 视图自动重建（`backend/app/services/personal_family_view.py:194-195`）；Steward 空间作业的 `rebuild_space_views` 会重建 `never_computed/queued/stale/failed` 视图。`StewardJob.cause` 词表已含 `"claim"`（`models/steward.py:41-50`），但 `account.claimed` 事件（无 `space_id`）目前不会派生任何空间作业。
- **可见血亲推荐的数据基础已就绪**：`load_graph` 的可见集合 = active 成员 ∪ active `SpaceProfileRef` ∪ 本人（经 `visibility.evaluate` 剪枝）∪ bridge 用户；PersonalFamilyView 只保留有 confirmed 路径的节点，边携带主路径、`path_class`、`concept_code` 和称谓 `term`（`relationship_resolver`）。同空间无确认路径的人不进入视图——这天然满足"同空间不等于亲属推荐"边界。
- 现有 `recommendation_matrix.py` 是 V2.4 ST-5 的**建档/建空间推荐矩阵**（`create_household`/`request_lineage` ActionCard），与本任务的"可见血亲推荐"语义不同，不得混用；只复用其纯函数、fail-closed、机器可读原因码的模式。
- 冷却与行为投影基础设施已存在：`BehaviorProjection`（`UNIQUE(space_id, account_id, projection_key)`，键前缀白名单 `PROJECTION_KEY_PREFIXES`）、`STEWARD_COOLDOWN_DAYS`（默认 7 天）。
- 待确认关系的确认流已存在：proposed `SourceFact` → relation FSM 确认命令；本任务不新建确认机制，只做线索汇聚与展示。

## Requirements

### R1. 认领触发合同

- `account.claimed` 是新用户初始化的唯一触发领域事件；**两个认领转换点都必须发出该事件**（为 `change_own_pin` 补发缺失的事件，payload 含 `user_id`）。
- 事件消费：为该用户每个 active 成员资格/引用的空间派生一个 `StewardJob`（`cause="claim"`），复用既有空间单活、lease、checkpoint 机制；不引入泛化的全局作业。
- 触发幂等：重复 `account.claimed`（理论不可达，防御性）与认领前已有视图的场景不得产生重复作业或重复推荐状态；空间单活索引兜底。

### R2. 个人家族初始化

- 认领后，用户在其每个空间的 PersonalFamilyView 必须进入可计算管道（`never_computed → queued → running → current`，复用既有状态机与 Steward 空间作业重建）。
- 初始化不创建任何 SourceFact、SpaceMember、可见权或桥接；只保证投影可计算。
- 认领前已由代管人维护的视图数据不迁移、不复制——视图本就是从共享真源派生的，认领只改变"本人可读"这一事实。

### R3. 亲属推荐投影（只读）

新增服务端推荐投影（读时从当前 PersonalFamilyView 派生，不物化为事实）：

1. **可见血亲与称谓**：当前视图中与 viewer 存在 confirmed 结构路径的节点，按既有主路径返回称谓（`term`/`concept_code`）、`path_class` 与安全路径摘要；主路径含 `spouse`/`partner` 步骤的血亲推荐一律排除（personal-family-view AC-6 延续）。
2. **待确认关系线索**：viewer 所在空间内、与其存在 proposed/pending 关系事实或确档清单待议项的可见人物，汇聚为待确认候选；候选只携带安全枚举理由与最小元数据，不渲染为已确认关系。
3. **推荐解释**：每条推荐必须带机器可读原因码与可解释文案；不得通过姓名、称谓或解释泄漏当前用户无权查看的人物或空间。

### R4. 推荐资格与排除（notes.md 已确认边界的固化）

- 同一治理空间但没有确认关系路径的人，不得仅凭同空间进入推荐（数据上由视图口径天然保证，测试必须守住）。
- 已拒绝（dismissed）、处于冷却期、事实失效（revoked/superseded/disputed）、不可见或被遮罩的推荐不得重复出现。
- 推荐不得反向把候选人物加入正式个人树、不得提升可见性、不得创建 SpaceMember、不得自动发送/接受任何申请。
- 用户对推荐的显式操作（确认关系）必须走既有领域命令（relation FSM 确认/确档清单决议），命令层重新校验权限、事实版本与当前状态；推荐层不承载执行动作。

### R5. 拒绝与冷却记忆

- 对血亲推荐条目的"不再推荐"记忆落在 `BehaviorProjection`（`UNIQUE(space_id, account_id, projection_key)`），键前缀加入白名单（如 `kinship_recommendation_dismissed:`），value 记时间戳；冷却期沿用 `STEWARD_COOLDOWN_DAYS`。
- 待确认关系线索的关闭语义沿用其既有确认流状态（confirmed/disputed 终态），不重复建冷却。

### R6. API 与前端最小合同

- 新增 `GET /api/family-recommendations?space_id=<positive integer>`：认证账号固定 viewer；无权上下文统一安全 404；返回 `{space_id, generated_from_view_version, items, truncated}`，item 含类别、目标最小元数据、称谓/路径摘要、原因码。
- 响应只消费当前有效（`current`）视图；视图 `stale/failed/never_computed` 时返回带状态的安全空集或状态码，不得静默返回旧内容冒充 current。
- 前端新增 API decoder + Pinia store（按空间缓存、切换清理、401/logout 清理、非乐观），首版最小展示（候选列表 + 理由 + 不再推荐入口）；画布组件不得直接请求；候选不得渲染为已确认关系。

## Acceptance Criteria

- [ ] **AC-1 触发覆盖**：`claim_and_confirm_own_identity` 与 `change_own_pin` 两条认领路径都产生 `account.claimed` 事件；事件为用户每个 active 空间派生至多一个 `StewardJob(cause="claim")`。
- [ ] **AC-2 初始化收敛**：认领后每个空间的 PersonalFamilyView 经 Steward 作业达到 `current`；重复事件不产生重复作业。
- [ ] **AC-3 血亲推荐正确性**：共享父母/祖辈的用户在推荐中可见血亲与称谓；主路径含 `spouse/partner` 的目标不出现在血亲推荐。
- [ ] **AC-4 同空间无路径排除**：同空间但无确认路径的人不出现在任何推荐中（Scenario C 回归）。
- [ ] **AC-5 拒绝/冷却排除**：dismissed 条目在冷却期内不再出现；冷却期满可重现；跨空间互不影响。
- [ ] **AC-6 待确认线索边界**：proposed/pending 线索只以候选形式出现，确认/争议走既有命令后从推荐中消失；推荐层无执行端点。
- [ ] **AC-7 无权不泄漏**：无权空间 404；推荐条目不包含 visibility `none`/masked 目标的身份、空间、路径或存在性信息。
- [ ] **AC-8 只读消费**：推荐只读 current 视图；推荐链路不写 SourceFact/SpaceMember/视图版本，personal-family-view AC-15 的合同测试落地。
- [ ] **AC-9 前端合同**：decoder/store 与后端 schema 一一对应；空间切换/登出/401 清理；候选不渲染为确认事实。
- [ ] **AC-10 质量门禁**：backend 定向 + 全量 pytest、ruff、mypy，frontend type-check/lint/test/build 通过；如涉及迁移按临时 `DATA_DIR` 往返。

## Out of scope

- 其余触发点（首次获得空间访问权、自助注册、已有用户首次成树）——notes.md 记录在案，后续任务再议。
- ActionCard 承载推荐（扩 `CARD_KINDS` 需迁移与卡片合同变更，首版推荐是只读投影）。
- 推荐排序的模型参与、跨空间"可能认识的人"发现、桥接自动推荐。
- 触发 `profile.identity_confirmed` 而未认领的路径（不存在该顺序：确认与认领合并转换或由认领先行）。
- 人物去重（`09-01-person-identity-dedupe`）与 PFV 失效合同的收口（`09-02-personal-family-view-followup`）。

## Dependencies

- `09-01-personal-family-view`（已完成）：`get_view`/`view_payload` 只读消费合同、视图状态机、AC-15。
- `09-01-person-identity-dedupe`：避免重复人物产生错误推荐（血亲推荐以唯一人物真源为前提；两任务无文件冲突，顺序无硬依赖，建议先行）。
- `09-02-personal-family-view-followup`（in_progress）：PFV 失效与状态回归；本任务的状态语义测试与其共享口径。
- 既有 Steward/ActionCard/BehaviorProjection 链路：作业派生、冷却与行为投影存储。
