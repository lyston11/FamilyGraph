# PersonalFamilyView 技术设计

## 1. 边界与目标

PersonalFamilyView 是按 `viewer_account + root_person + space` 隔离的、可重建的授权投影。它不替代 SourceFact、SpaceMember、SpaceProfileRef、DerivedFact 或推荐集合，也不创建公共 FamilySpace。

后端负责事实、权限、投影、状态和审计；Steward 负责按 `space_id` 触发与执行重算；浏览器只读取服务端投影。前端本任务接入 API 类型与 Pinia store，不重做 Vue Flow 的树/图布局。

保留旧 `/api/graph/me` 兼容接口和既有 `Relation` 语义。新接口不得在旧 payload 上隐式扩展 PersonalFamilyView 语义。

## 2. 数据模型

### 2.1 View snapshot

新增 `personal_family_views`：

- `id`
- `viewer_account_id`（FK accounts，级联）
- `root_user_id`（FK users，级联）
- `space_id`（FK family_spaces，级联）
- `status`：`never_computed|queued|running|current|stale|failed`
- `view_version`（单调整数）
- `input_hash`、`policy_version`、`computation_version`
- `computed_at`、`invalidated_at`、`failed_reason`
- 唯一键 `(viewer_account_id, root_user_id, space_id)`

viewer account 与 root profile 必须由服务端从认证身份解析并验证 1:1；客户端不能指定别人的 viewer/root。

### 2.2 View rows

新增 `personal_family_view_nodes` 和 `personal_family_view_edges`，均以 `view_id` 为分区键，保留投影结果但不成为事实真源。

节点至少包含：`user_id`、安全显示字段、`visibility_level`、`inclusion_reason_code`、`source_fact_ids_json`、`authorization_basis_json`、`policy_version`、`computation_version`。

边至少包含：`from_user_id`、`to_user_id`、`edge_kind`、`source_fact_id`、`path_json`、`path_class`、`concept_code`、`term`、`inclusion_reason_code`、`authorization_basis_json`、版本字段。

所有 JSON 证据只保存 fact id/type/revision、空间/桥接引用、policy/computation version 和安全枚举；不保存 private session、原始 Memory、模型自由文本或隐藏目标信息。

### 2.3 Bridge authorization

新增 `personal_family_bridges`：

- 两侧 `lineage_space_id`、两侧 anchor `user_id`
- 发起账号、双方同意账号/时间、状态 `pending|active|revoked|expired|rejected`
- `scope_json`（只允许最小 anchor 范围及明确的 confirmed 路径扩展规则）
- `revision`、`expires_at`、`revoked_at`、created/updated 时间
- 唯一约束覆盖无序双方 `(space_a,anchor_a,space_b,anchor_b)` 的规范化键

桥接不是 SourceFact；active 前必须有双方已认领账号本人明确同意。空间管理员只接收不含家庭敏感内容的通知，不参与审批、修改和撤销。未认领任一方只能保留 pending。

- 桥接通知以 append-only `DomainEvent` 记录为权威，并接入现有实时传递/轮询边界；如用户通知投影尚不存在，只增加最小的只读通知投影。通知不含家庭敏感内容，不提供审批、修改、撤销或读取对方空间的能力。

## 3. 投影计算与授权

### 3.1 输入

Steward 在一个 `space_id + job_id + policy_version` 上下文内读取：confirmed SourceFact、有效 SpaceMember/SpaceProfileRef、有效桥接导出、VisibilityPolicy、TermRegistry 和必要的 DerivedFact。禁止读取 private Session/Memory/RAG 或无桥接的其他空间原始事实。

### 3.2 图遍历

复用 `relationship_graph.load_graph`、`relationship_resolver` 的确定性路径原语和现有 `MAX_PATH_DEPTH=12`、`MAX_SIMPLE_PATHS=128`、替代路径最多 3 条。只遍历 confirmed 结构关系；SocialRelation 不入图。

`spouse`/`partner` 本人可直接纳入；仅有配偶边不得自动穿透配偶侧分支。Household 内已有合法授权可继续按正常路径计算；跨 LineageSpace 只通过 active bridge 的 anchor 范围进入。partner 不继续扩展。

节点和边进入投影前调用 `visibility.evaluate`，目的使用 graph/agent 对应安全上限；任何 `none` 节点、端点或路径都整体丢弃。`lineage_summary` 只保存最小基线字段且不可作为继续遍历入口。

对当前用户看不到的对象不建立可查询的投影行，也不在 API 返回其 target ID、空间 ID、节点数量或阻断路径。内部审计可以保留安全枚举原因，但不得回传身份信息。

### 3.3 主路径

同一 viewer/root/target 的路径使用现有 resolver 的全序：边数、非血缘步数、姻亲步数、节点 ID 序列。保存主路径和最多 3 条替代路径。用户偏好只能影响称谓显示，不影响事实主路径。

### 3.4 原子重建

重算在同一数据库事务中生成新的 view version：先依据 view key 加载输入快照，再写入临时/新 version 行，最后切换当前版本；失败时保留上一个投影但查询状态为 stale/failed，并且查询层重新执行授权，不能暴露已撤权节点。

重复 DomainEvent 以 `input_hash + policy_version + computation_version` 幂等；job checkpoint 保存 cursor、view key、input/policy/computation version 和结果签名。只有真正受影响的 viewer/root/space fan-out 重算。

## 4. DomainEvent 与状态

关系确认/撤销/删除/supersede、profile identity merge/split、space membership/profile-ref、bridge consent/revoke/expire、disclosure/policy、term 变化都触发对应空间的 view invalidation/job。

权限收紧事件在同一事务中将投影标为 stale/invalidated；PersonalFamilyView 查询再次校验当前 active membership、bridge、VisibilityPolicy 和每个节点/边，撤权后下一次读取立即隐藏。

普通事实变化进入 `queued → running → current`；失败进入 `failed`，记录安全机器原因和可重试水位。状态序列：`never_computed → queued → running → current ↔ stale → failed`；成功重算可从 stale/failed 回 current。

## 5. API 合同

新增浏览器端点：

`GET /api/personal-family-view?space_id=<positive integer>`

认证账号固定 viewer/root；服务端验证该账号对空间有 active 访问或其他明确桥接授权。不存在、无权和不可见上下文统一为安全的 `404 PERSONAL_FAMILY_VIEW_NOT_FOUND`，不得枚举空间或其他用户。

响应 `PersonalFamilyViewOut`：

- `space_id`
- `status`
- `view_version`
- `computed_at`
- `input_hash` 不直接下发原始证据，可下发版本摘要
- `nodes`
- `edges`
- `truncated`/`next_cursor`（首版受控上限；快照分页必须绑定同一 view_version）
- `stale_reason`（安全枚举，可选）

响应节点/边不包含未授权对象；节点字段遵守 `self_private|household_detail|lineage_summary`。视图版本生成 ETag；`If-None-Match` 命中返回 304。API 层只做 schema/认证参数校验，授权和投影查询由 service 完成。

错误采用既有统一 envelope；状态未准备好不是静默空图，返回带 `status` 的安全投影或明确 409/503 机器码，由最终实现按现有 API 约定固定。

## 6. 前端接入

新增 `frontend/src/api/personalFamilyView.ts`、`frontend/src/types/api.ts` 的共享类型/运行时守卫和 `frontend/src/stores/personalFamilyView.ts`。store 以 `space_id` 为缓存键，切换空间/登出/401 清空旧视图，加载时不做乐观更新。

首版只提供服务端投影读取和状态展示所需数据契约；现有 `graph` store 与 FamilySpaceView 不删除、不改作 PersonalFamilyView 真源。后续 UI 可将投影注入现有布局组件，但画布组件不直接请求 API。

## 7. Bridge command/API

桥接写入必须是显式领域命令，分别校验：两侧都是 LineageSpace、anchor 身份和空间授权、双方账号 claimed、不能是同一主体、confirmed 关系依据、无重复 active bridge、scope 最小化和过期时间。

发起请求只产生 pending bridge，不授予读取权；双方本人 consent 使用 CAS revision。第二方同意后在同一事务中置 active、写领域事件并通知两侧管理员。任一方本人可撤销，写 revoke 事件并令受影响视图下一次读取失效。管理员通知失败不回滚桥接授权。

## 8. Migration / compatibility / rollback

使用新的 Alembic migration，明确 FK on-delete、CHECK、索引和版本唯一性；不得修改旧 `relations` 或把 bridge 伪装成 `Relation`。升级创建空投影表，旧 `/graph/me` 不受影响；降级只能在无投影/桥接数据或经过显式运维清理时执行，拒绝静默丢数据。

投影功能和桥接入口受独立 feature flag 控制，默认关闭；关闭时不删除事件/投影，读取返回统一 disabled 响应。回滚优先关闭 flag 并保留事实，必要时再按 migration 约束降级。

## 9. 测试策略

后端单测覆盖：唯一键和状态机、双本人 consent、管理员仅通知、未认领 pending、bridge scope 越权、共同/独有分支、配偶不穿透、partner 不延伸、同空间无关系、断开分量、masked/none 防枚举、主路径、替代路径、撤权即时隐藏、事件 fan-out、幂等重算、失败恢复、ETag/分页和统一错误。

前端测试覆盖：schema runtime guard、store space cache、空间切换清理、401 清理、stale/failed 状态和不把候选/遮罩节点渲染为确认事实；响应类型与 backend Pydantic 一一对应。

必须运行 backend 全量授权矩阵、migration upgrade/downgrade/upgrade、mypy/ruff/pytest，以及 frontend type-check/lint/test/build。真实 Compose internal protocol E2E 不是本任务 PersonalFamilyView 的替代验证，若触及 Agent internal contract 仍按既有规范执行。
