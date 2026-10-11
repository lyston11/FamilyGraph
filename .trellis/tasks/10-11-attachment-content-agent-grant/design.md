# 技术设计：附件内容版本与 Agent 授权入库

## 1. Boundary model

保持三种事实分层：

```text
Attachment（个人档案原始资源）
  └── AttachmentContentVersion（用户确认的可引用文字版本）
        └── AgentContentGrant（某空间/某 Agent/某用途的明示许可）
              └── RAGDocument / RAGChunk（可重建搜索派生物）
```

四层不可互换：

- Attachment 查看权继续由现有 `visibility.evaluate(viewer, target, space_context)` 和按类型披露控制。
- ContentVersion 证明“哪段文字从哪个附件版本产生、用户确认过什么”。
- Grant 证明“谁允许哪个 Agent 在哪个 household 用哪一版文字”。
- RAG 只提供检索，不承担授权真源。

不向 `attachments` 添加 `space_id`。授权空间是 Grant 的属性，附件仍可被多个空间分别授权；空间授权绝不反向提升浏览器附件访问权限。

## 2. ContentVersion schema

建议新增 `attachment_content_versions`：

```text
id                  PK
attachment_id       FK attachments RESTRICT/CASCADE（按既有删除合同选择显式失效后删除）
revision            positive int, unique(attachment_id, revision)
content_kind        manual_text（MVP；预留 ocr_text/link_metadata/web_snapshot/document_text）
text                Text, non-empty, bounded by API/service max
content_sha256      String(64), required
extractor_name      String(64), e.g. manual
extractor_version   String(32), e.g. v1
status              draft|confirmed|rejected|superseded|revoked
created_by          FK users SET NULL
confirmed_by        FK users SET NULL
created_at          DateTime
confirmed_at        DateTime nullable
superseded_at       DateTime nullable
revoked_at          DateTime nullable
```

重要约束：

- `(attachment_id, revision)` 唯一；revision 由命令层在写锁/事务内分配，不能由客户端任意指定。
- 正文不可原地更新。修订是新行；旧版本保留给历史引用和审计。
- `confirmed` 只能由附件归属的已认领本人确认；MVP 不让上传者、代管者或空间管理员代替确认。
- 图片/链接 MVP 的正文来自用户显式提交的手工文字；不要后台自动从 link 抓正文，不要默认 OCR。
- `description` 变化不会隐式生成版本；必须走显式 create/confirm content-version 命令，避免把普通档案编辑误当作模型授权。

若项目已有统一 domain event/audit 约定，内容版本事件使用 `attachment.content_version.created/confirmed/superseded`；正文、URL 和敏感文本不能放 event payload，只放实体 ID、revision、actor。

## 3. AgentContentGrant schema

建议新增 `agent_content_grants`：

```text
id                  PK
authorization_revision positive int
content_version_id  FK attachment_content_versions
space_id            FK family_spaces
agent_kind          assistant（CHECK；Steward 不在 MVP）
purpose             rag（CHECK）
grant_scope         whole（CHECK；selected 后续）
grantor_user_id     FK users RESTRICT/SET NULL（撤销后保留审计）
grantor_account_id  FK accounts SET NULL
status              active|revoked|expired
created_at          DateTime
revoked_at         DateTime nullable
revoke_reason      String(64) nullable
```

约束与索引：

- `space_id` 必须指向 `FamilySpace.kind='household'`；SQLite CHECK 不能跨表时由命令/API refusal guard + 测试保证，必要时使用触发器/迁移前校验。
- `content_version.status='confirmed'` 才能创建 active grant；数据库不能跨表强制时由事务内重新读取并加状态条件。
- grantor 必须是 content version 所属已认领档案本人；授权者必须在 grant 创建时仍能编辑/确认该档案，且不能由空间管理员替代。
- active 唯一性：`(content_version_id, space_id, agent_kind, purpose, grant_scope)` partial unique index；撤销历史可保留多条，但不能有两个 active grant。
- `authorization_revision` 单调递增并参与引用校验；revoked 不可重新 active，重新授权新建 grant。

## 4. State transitions

```text
ContentVersion:
  draft -> confirmed | rejected
  confirmed -> superseded | revoked
  draft/rejected/superseded/revoked 不可 grant

Grant:
  active -> revoked | expired
  revoked/expired 不可恢复

RAGDocument:
  active -> invalidated/revoked（由 grant/source 失效）
```

内容版本替换不更新旧 grant 的 foreign key；新版本必须重新确认、重新 grant。删除附件先通过事件把相关版本/grant/RAG 变成不可读，再按既有附件删除合同清除附件/文件。

## 5. Commands/API

第一阶段 API 最小化为：

```text
POST /attachments/{attachment_id}/content-versions
POST /attachment-content-versions/{version_id}/confirm
POST /attachment-content-versions/{version_id}/grants
POST /attachment-grants/{grant_id}/revoke
GET  /attachments/{attachment_id}/content-versions       # 仅 owner/编辑者
GET  /attachment-grants?space_id=...                     # 仅授权主体/必要的本人审计
```

所有 API：

- 使用现有 authenticated identity + custody/attachment owner 检查；
- 创建/确认/授权/撤销均走 command transaction；
- 无权对象统一安全 404/403，不能暴露版本、grant 或正文是否存在；
- 不返回原始 URL 给 Agent grant API；前端展示授权前预览使用已有附件读取权限；
- grant 创建时强制 `space_id`，验证 household active membership 和档案本人确认资格；
- 默认不允许客户端指定 `revision`、hash、extractor、agent_kind、purpose、scope。

## 6. RAG integration

### 6.1 canonical source identity

```text
source_type = authorized_document
source_id = attachment:{attachment_id}:version:{content_version_id}:grant:{grant_id}
revision = content_version.revision
scope = household
space_id = grant.space_id
confirmation_status = authorized
owner_user_id = attachment.user_id
author_account_id = grantor account（仅审计/metadata，不替代 grant）
content_sha256 = content_version.content_sha256
```

调用现有 `ingest_authorized_document` 前，服务层在同一事务重新验证 content/grant/attachment 状态。不得从客户端文本直接调用通用 ingest。RAG document 的 metadata 可保留 grant ID，但正文引用读取必须回查授权真源。

### 6.2 activation and invalidation

- grant active/确认完成后调用统一物化路径；若 embedding/FTS 物化失败，grant 仍记录但查询不可见，维护任务可重试，不能把 grant 回滚成“未授权”。
- 查询 eligibility 增加 authorized-document 分支：source type、space、active grant、confirmed version、有效 attachment、当前 space active membership、`agent_kind/purpose` 全部匹配。
- `search_rag` 继续 filter-then-ANN；任何 candidate 进入排序前已经过 grant predicate。
- Assistant 目的白名单由 `("memory",)` 改为 `("memory", "authorized_document")`，并在 tool test 中证明 `family_story/profile/public_kinship` 不被放入。
- Steward 的 `STEWARD_READABLE_SOURCE_TYPES` 保持 `("memory",)`。
- 引用打开端点通过 `citation_handle -> RAGChunk -> RAGDocument -> grant/content/attachment` 逐层回查；失效返回安全不可用，不返回正文片段。

### 6.3 event propagation

统一消费：

```text
attachments.invalidated
attachment_content_version.superseded/revoked
agent_content_grant.revoked
space.deleted / member.removed（查询门立即失效，后台可标记派生物）
```

事件处理幂等：按 grant/document ID 条件更新；重复事件不报错、不复活。成员退出不应全局撤销 grant，因为同一 grant 对其他 active household members 仍可有效；查询层以当前成员状态实时过滤，后台清理是优化而非授权条件。

## 7. Privacy and provider boundary

- `for_model=True` 仍执行现有 sensitivity/local restrictions；`authorized_document` 不获得 provider 绕过权。
- 手工文字按敏感正文处理，不写日志、任务文件、审计 detail 或错误 payload。
- content preview 只给有附件查看权和版本管理权的用户；grant API 不成为正文泄露入口。
- 只把确认内容交给 RAG；未确认草稿、被替代版本、撤销授权正文不进入新的模型上下文。
- 文档正文可能包含 prompt injection；作为引用资料进入 context 时必须标记为不可信资料，不能改变工具授权或系统规则。

## 8. Migration and rollout

迁移先做 refusal guards：检查现有附件/历史数据是否有需要新约束的冲突，任何 DDL 前拒绝并报告 ID/计数，不输出正文。新增表原则上无旧数据回填，不给已有附件自动建立 content version 或 grant。

部署顺序：

1. 后端 schema + model + content/grant commands/API（新表为空，不影响旧附件）。
2. 后端 RAG eligibility、citation recheck、event invalidation 与 Assistant allowlist。
3. 前端内容确认和授权界面。
4. 通过隔离数据库 migration proof 后，按 feature flag 开启 authorized_document ingest。

回滚：关闭 feature flag 后停止新 grant/RAG 写入；已有 grant 的查询可由后端硬门全部拒绝；不删除审计表或已确认内容版本，不通过 downgrade 破坏数据。