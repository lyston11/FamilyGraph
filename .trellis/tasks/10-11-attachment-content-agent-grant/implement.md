# 实施计划

## Phase 1：迁移与内容版本

- [x] 读取 database/migration 规范，设计 `attachment_content_versions` 的 CHECK、唯一索引、FK 和 refusal guard。
- [x] 新增模型、迁移和领域事件常量；不回填历史附件，不自动把 description 当已确认内容。
- [x] 新增内容版本命令/API：创建手工正文、确认、拒绝/替代；只允许已认领档案本人确认。
- [x] API 输入长度、hash、revision、状态转换和安全 404/403 测试。

## Phase 2：AgentContentGrant

- [x] 新增 grant 模型、迁移和 partial unique active index。
- [x] 实现 household-only、assistant-only、rag-only、whole-only 的命令/API。
- [x] 校验 grantor 是附件所属已认领档案本人；空间成员/空间类型/内容确认状态在同一事务复核。
- [x] 实现不可恢复撤销；授权 revision、审计和事件不记录正文。
- [x] 测试跨 household/lineage、非本人/代管、pending/removed、重复 active grant 和撤销重授。

## Phase 3：authorized_document RAG

- [x] 扩展 source registry/Assistant purpose whitelist：明确加入 `authorized_document`，保持 Steward 不加入。
- [x] 新增 grant → RAGDocument 的唯一生产入口，复用现有 canonical/materialize/lifecycle 路径。
- [x] 扩展 eligibility：候选形成前检查 grant active、confirmed version、attachment 有效、household space、active member、assistant/rag。
- [x] citation handle 回查 attachment/version/grant；撤销或删除后安全失败。
- [x] 处理 `attachments.invalidated`、version superseded/revoked、grant revoked 的幂等失效；不让成员退出全局撤销 grant，依赖实时 membership predicate。
- [x] 测试 filter-then-ANN/FTS、无授权不可召回、授权后召回、撤销立即不可见、重复事件不复活。

## Phase 4：前端确认和授权 UX

- [x] 在现有附件查看权限基础上，增加手工内容创建/预览/确认界面。
- [x] 增加 household + Assistant + whole grant UI；明确授权范围和用途，不展示不存在的 OCR/PDF/lineage/Steward 选项。
- [x] 提供撤销状态和不可用引用反馈；不在 UI 或日志展示无权正文。
- [x] 组件测试覆盖版本确认、授权失败、安全失败和空间切换。

## Phase 5：质量门与发布

- [x] 隔离 DATA_DIR 执行 refusal guard、`alembic upgrade head`、migration proof；验证 downgrade 不破坏数据。
- [x] 受影响 backend pytest、ruff、format、mypy；frontend lint/type-check/Vitest。
- [x] 检查 Agent tool source whitelist、Steward whitelist、引用回查和 provider sensitivity 边界。
- [x] 关闭 feature flag 回归：旧附件上传/查看/删除、Memory RAG、无新 grant 写入。
- [x] 在独立部署顺序中记录后端先于前端的兼容策略；未运行的高成本检查写入交付说明。

## 风险检查点

- 不得把通用 `ingest_authorized_document` 暴露成客户端任意写入入口。
- 不得让 grant 绕过附件当前 viewer 权限。
- 不得把 confirmed 内容自动升级为 Memory。
- 不得让 `source_types=None` 重新成为 Assistant 新资料的隐式放开。
- 不得用异步清理完成与否决定当前授权结果。
- 迁移 refusal guard 必须在任何 DDL/Alembic version move 前执行。

## 回滚

关闭 authorized_document feature flag 后拒绝新 content grant/RAG ingest；现有 grant 查询可由服务端硬门拒绝。保留 content versions/grants/audit 以供审计，不删除或降级破坏正文历史。数据库 downgrade 仅在迁移证明确认无新数据依赖时执行。

## 实施记录（2026-10-11）

### 实际交付

- 模型：`attachment_content_versions`（不可变正文版本）、`agent_content_grants`（显式授权）。
  迁移 `0061_attachment_content_agent_grant`，含 refusal guard（目标表已有数据则拒绝）。
- 命令/API：创建/列出/确认内容版本；创建/列出/撤销 grant。授权人必须是附件所属**已认领档案本人**。
- RAG：`ingest_attachment_content_grant` 是唯一生产入口，复用 `ingest_authorized_document`。
- 检索期三重交集：SQL 层 grant EXISTS 分支 + 投影层 `_authorized_document_readable`。
- 撤销先打墓碑（`invalidate_source`）再异步清理；revoke / 内容被取代 / 附件删除三条路径都接。
- 前端：`AttachmentAgentGrant.vue` 把「保存 / 确认 / 授权」做成三个独立动作。

### 与 main 的合并（本任务中途 main 前进多个提交）

按 AGENTS.md 把 `origin/main` merge 进本分支（不 rebase）。唯一冲突在
`memory_sources._can_read_document` 收尾：两边各加了一道独立的门，保留两者。

**合并中发现并修复的真实冲突**：main 的 `b30d1ff5` 用 `authorized_document` 承载
「档案派生内容」（source_id 形如 `attachment:<subject>:<categories>`，靠
`visibility_snapshot` 清空声明授权，**没有** grant）。我的 grant 分支若按
`attachment:` 前缀判别，会把这类文档一并卡死——实测 5 条 profile 清空用例全部不可读。

修法：判别式精确到完整形状 `attachment:%:version:%:grant:%`（投影层
`is_attachment_grant_source` 用同一判别式）。两类各自走自己的门，形状不完整的
附件文档 fail closed。新增
`test_grant_source_shape_is_discriminated_from_profile_derived_documents` 钉住判别式。

### 按新合同更新的旧测试

4 条测试写于 `authorized_document` 还只是占位枚举时，把「空间成员即可读」当成正确
行为（含管家可读）。现在它有了真实合同，因此：

- `test_steward_can_retrieve_current_space_shared_rag_only` →
  `test_steward_cannot_retrieve_authorized_documents`（管家读不到，两个独立理由；
  并加 assistant 反证证明不是「语料不存在」）。
- `test_steward_cannot_retrieve_public_rag_documents` → 删除：其反例
  （`scope='public'` 的 authorized_document）已不可能存在（入索引即 422）。
- `test_public_rag_requires_active_space_membership` → 载体改用 `public_kinship`
  （唯一合法的 `scope='public'` 来源），断言更强：现在测生产语料而非人造越权文档。
- 生命周期测试改用 `family_story` 作非 memory 载体（`authorized_document` 已带 grant 合同）。

### 顺带修正

- `app/models/__init__.py` 从未导入 `app.models.attachment`；此前无 FK 指向它所以没暴露，
  新 FK 让它成为必需。`test_partial_index_portability` 的基线计数 16 → 17。
- `authorized_document` 入索引时拒绝 `scope='public'`（与「含个人数据」不相容）。

### 未做（如实记录）

- 未做 OCR / 外链抓取 / PDF 解析 / 选段授权 / lineage / Steward（均为 MVP 明确排除）。
- 未部署到运行环境；未跑前端 smoke（需真实 listener，本任务未改 API 契约之外的东西）。
