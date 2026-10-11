# 实施计划

## Phase 1：迁移与内容版本

- [ ] 读取 database/migration 规范，设计 `attachment_content_versions` 的 CHECK、唯一索引、FK 和 refusal guard。
- [ ] 新增模型、迁移和领域事件常量；不回填历史附件，不自动把 description 当已确认内容。
- [ ] 新增内容版本命令/API：创建手工正文、确认、拒绝/替代；只允许已认领档案本人确认。
- [ ] API 输入长度、hash、revision、状态转换和安全 404/403 测试。

## Phase 2：AgentContentGrant

- [ ] 新增 grant 模型、迁移和 partial unique active index。
- [ ] 实现 household-only、assistant-only、rag-only、whole-only 的命令/API。
- [ ] 校验 grantor 是附件所属已认领档案本人；空间成员/空间类型/内容确认状态在同一事务复核。
- [ ] 实现不可恢复撤销；授权 revision、审计和事件不记录正文。
- [ ] 测试跨 household/lineage、非本人/代管、pending/removed、重复 active grant 和撤销重授。

## Phase 3：authorized_document RAG

- [ ] 扩展 source registry/Assistant purpose whitelist：明确加入 `authorized_document`，保持 Steward 不加入。
- [ ] 新增 grant → RAGDocument 的唯一生产入口，复用现有 canonical/materialize/lifecycle 路径。
- [ ] 扩展 eligibility：候选形成前检查 grant active、confirmed version、attachment 有效、household space、active member、assistant/rag。
- [ ] citation handle 回查 attachment/version/grant；撤销或删除后安全失败。
- [ ] 处理 `attachments.invalidated`、version superseded/revoked、grant revoked 的幂等失效；不让成员退出全局撤销 grant，依赖实时 membership predicate。
- [ ] 测试 filter-then-ANN/FTS、无授权不可召回、授权后召回、撤销立即不可见、重复事件不复活。

## Phase 4：前端确认和授权 UX

- [ ] 在现有附件查看权限基础上，增加手工内容创建/预览/确认界面。
- [ ] 增加 household + Assistant + whole grant UI；明确授权范围和用途，不展示不存在的 OCR/PDF/lineage/Steward 选项。
- [ ] 提供撤销状态和不可用引用反馈；不在 UI 或日志展示无权正文。
- [ ] 组件测试覆盖版本确认、授权失败、安全失败和空间切换。

## Phase 5：质量门与发布

- [ ] 隔离 DATA_DIR 执行 refusal guard、`alembic upgrade head`、migration proof；验证 downgrade 不破坏数据。
- [ ] 受影响 backend pytest、ruff、format、mypy；frontend lint/type-check/Vitest。
- [ ] 检查 Agent tool source whitelist、Steward whitelist、引用回查和 provider sensitivity 边界。
- [ ] 关闭 feature flag 回归：旧附件上传/查看/删除、Memory RAG、无新 grant 写入。
- [ ] 在独立部署顺序中记录后端先于前端的兼容策略；未运行的高成本检查写入交付说明。

## 风险检查点

- 不得把通用 `ingest_authorized_document` 暴露成客户端任意写入入口。
- 不得让 grant 绕过附件当前 viewer 权限。
- 不得把 confirmed 内容自动升级为 Memory。
- 不得让 `source_types=None` 重新成为 Assistant 新资料的隐式放开。
- 不得用异步清理完成与否决定当前授权结果。
- 迁移 refusal guard 必须在任何 DDL/Alembic version move 前执行。

## 回滚

关闭 authorized_document feature flag 后拒绝新 content grant/RAG ingest；现有 grant 查询可由服务端硬门拒绝。保留 content versions/grants/audit 以供审计，不删除或降级破坏正文历史。数据库 downgrade 仅在迁移证明确认无新数据依赖时执行。