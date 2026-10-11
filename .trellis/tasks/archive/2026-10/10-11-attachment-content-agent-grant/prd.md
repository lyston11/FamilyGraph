# 附件内容版本与 Agent 授权入库

## Goal

让用户能够把自己档案附件中**已确认的文字内容**，明确授权给某个 household 空间的 Assistant 使用，并安全进入 `authorized_document` RAG：内容有不可变版本、授权有明确主体、检索和引用动态校验、撤销立即阻断。

## User value

用户可以把图片说明、链接说明等有用资料交给 Assistant 查询，同时保留原始附件、内容来源、版本和授权边界。允许 Assistant 使用不增加任何人的档案查看权，也不让撤销后的新检索继续读取旧授权内容。

## Confirmed repository facts

- `Attachment` 当前只有 `image`、`link` 两个实际生产类型；`location` 只是约束枚举预留。字段是 `user_id`、`type`、`url_or_path`、`title`、`description`、`uploaded_by`、`created_at`，没有内容版本或 Agent 授权字段。
- 图片上传会做扩展名、大小、magic bytes、Pillow 解码、像素上限和 PNG 重编码，重编码剥离元数据；链接只保存 URL/标题/描述，服务端不抓取外链。
- 附件查看权限已经按当前 `space_id`、`photos`/`attachments` 类型披露和 active membership 统一；本任务必须复用该读取边界，不另造一个“可看附件”判定。
- `attachments.invalidated` 已在删除、身份合并等路径产生，但目前没有 authorized-document 消费者。
- `RAGDocument`/`RAGChunk` 是可重建派生物；`ingest_authorized_document` 已提供通用显式授权文档写入接缝，但 `authorized_document` 当前没有生产写入方。
- `search_rag(source_types=None)` 代表所有已声明类别；Assistant 的 `familygraph.search_memory` 已显式限制为 `("memory",)`，新增资料必须显式扩大目的白名单，不能依赖默认全开。
- 现有空间成员状态为 `pending`/`active`/`removed` 等；空间与附件归属是两个独立概念，附件继续归属个人档案。

## MVP scope

### In scope

1. 为现有图片/链接建立不可变 `AttachmentContentVersion`。
2. 第一阶段只支持用户确认的手工文字：图片的标题/描述、链接的标题/描述；不自动抓取外链，不调用外部 OCR，不支持 PDF/文档上传。
3. 内容版本状态：draft → ready/confirmed，拒绝和被替代版本保留审计但不可授权。
4. 为已认领档案本人增加 Assistant 使用授权；第一阶段只支持：
   - household 空间；
   - `agent_kind=assistant`；
   - `purpose=rag`；
   - 整份已确认内容；
   - 明示授权者；
   - 可撤销。
5. 授权内容进入 `authorized_document` RAG，并生成可回溯到附件、内容版本和授权记录的 citation。
6. 检索前、返回模型前、用户打开引用时都重新检查有效授权与当前空间成员资格。
7. 附件删除、内容版本替代、授权撤销、空间删除/成员退出后，后续读取立即失效；后台再清理 RAG 派生物。
8. 补齐 Assistant 的 `source_types=("memory", "authorized_document")` 显式目的白名单。

### Out of scope

- 外部 OCR、图片自动理解、将图片发送给外部 Provider。
- 服务端自动抓取链接、网页正文快照和 controlled-web 授权联动。
- PDF/Office/其它文档上传与解析。
- lineage 空间授权。
- Steward 使用 authorized documents。
- 选段/段落级授权、编辑器、片段选择 UI。
- 自动把附件内容提升为 Memory；Memory 和文档来源保持独立。
- 修改附件的个人归属或把 `space_id` 添加到 `attachments`。

## Requirements

### R1 内容版本是真源派生，不改写附件

- 新增内容版本表，正文不可原地修改；每次文字变化建立新 revision。
- 至少保存 attachment、revision、content_kind、text、content_sha256、extractor name/version、状态、产生者和时间。
- `manual_text` 是唯一 MVP 提取方式；生产路径须由用户明确提交/确认，而不是上传后自动授权。
- 内容版本的有效状态必须有明确值域；`superseded`、`rejected`、`revoked` 版本不能新建授权。
- 内容版本变更不能自动继承旧 Agent 授权。

### R2 授权主体与边界

- 新增独立授权记录，绑定具体 content_version、household space、assistant、rag、授权人和授权状态。
- 只有已认领档案本人可以作为 MVP grantor；上传者/代管编辑权不自动等于模型处理同意。
- 授权只增加 Agent 使用权，不增加附件浏览权；有效使用权必须同时满足：当前 viewer 能在该 space 查看附件、内容已确认、grant active、space member active、agent/purpose 白名单允许。
- 同一内容版本/空间/agent/purpose 的 active grant 必须幂等；撤销后不能静默复活旧授权。
- household 是 MVP 唯一允许 scope；lineage、public、Steward 必须被拒绝。

### R3 RAG 写入与引用

- `authorized_document` 只能由有效 grant 触发生产写入；复用现有 canonical document/chunk/materialize/lifecycle 路径，不复制 `_ELIGIBILITY_SQL`。
- `RAGDocument.source_id` 必须能定位 attachment、content_version、grant；`revision` 与 content version 对齐，正文 hash 可校验。
- RAG document 使用 household scope、授权 space_id、authorized confirmation；不把 `author_account_id` 当作唯一授权依据。
- Assistant 目的白名单显式包含 authorized_document；Steward 默认不包含。
- 检索 SQL 必须在候选形成前限制 grant active、版本 confirmed、附件仍有效、space/member/agent/purpose 满足；不能全库召回后再在答案层过滤。
- citation 必须带来源引用所需的 attachment/content version/grant 信息；打开引用要再次授权核验，已撤销返回安全不可用结果。

### R4 撤销与生命周期

- grant revoke、attachment invalidated、content version superseded、space deletion/member exit 的后续查询必须立即不可见。
- 撤销传播先更新授权/来源状态，再标记或清理 RAG 派生物；不能等待异步清理完成才生效。
- RAG 派生物保留审计所需的安全元数据，但不得在已失效路径中返回正文、向量或 citation。
- 删除附件的既有 `attachments.invalidated` 事件要成为文档索引失效接缝；事件处理必须幂等。
- 已经发送给模型或用户的内容无法收回，产品不承诺回溯删除；只保证后续读取和新上下文不可继续取得。

### R5 安全与数据边界

- 手工文字也视为可能含健康、地址、学校、联系人等敏感信息；确认 UI/API 必须明确用途与 household 范围。
- 不把原始图片、外部 URL 抓取结果或未确认 OCR 结果发送给 Provider。
- Provider 为非 local 时，沿用现有 `for_model` 敏感度/本地限制；不得因为 source_type 新增而绕过 sensitivity。
- authorization/grant API 的拒绝需 fail closed，不泄露附件、正文、空间或授权是否存在给无权主体。
- 密钥、正文和原始外链不得进入日志；审计只记录实体 ID、版本、actor 和稳定错误类。

## Acceptance criteria

- [ ] 一个已认领档案本人可以为图片/链接创建手工内容版本，确认后得到不可变 revision；同 revision 修改正文被拒绝，正文 hash 可核对。
- [ ] 新内容版本不会自动继承旧 grant；旧 grant 仍精确绑定旧 revision。
- [ ] 本人可在 household A 授权 Assistant 使用一份 confirmed 内容；授权后 Assistant 能检索并得到带 attachment/content-version/grant 的 citation。
- [ ] household B、lineage、Steward、pending/removed 成员和未授权 viewer 均不能通过 RAG 或 citation 获取该正文。
- [ ] Assistant 的 memory 工具只读 `memory` 与显式授权的 `authorized_document`，不会重新放开所有 RAG source type；Steward 仍不读 authorized_document。
- [ ] grant revoke 后：RAG 查询、上下文构建、citation 打开均立即不可用；RAG 清理延迟不影响拒绝结果。
- [ ] 删除附件产生的 `attachments.invalidated` 能幂等失效相关内容版本、grant 和 RAG document；重复事件不报错、不复活。
- [ ] 新内容版本、附件删除、空间删除和成员退出不会留下可返回的旧正文/向量候选；保存的失效引用返回安全失败。
- [ ] 不支持的 OCR、外链抓取、PDF、lineage、Steward、选段输入被明确拒绝或不出现入口。
- [ ] 现有附件上传、查看、删除和 Memory RAG 回归保持通过。
- [ ] 迁移 refusal guard 在任何 DDL/版本移动前执行；隔离数据库 `alembic upgrade head` 通过；downgrade 不破坏或恢复旧种子。
- [ ] 后端 ruff/format/mypy/受影响 pytest 与前端 type-check/lint/受影响 Vitest 通过；未运行的高成本检查在交付说明列明。

## Technical decisions already approved

- 附件继续是个人档案资源，不添加 `attachments.space_id`。
- 第一阶段采用“整份已确认内容”而不是强制段落授权；选段是后续独立扩展。
- 第一阶段只支持 household + Assistant + rag；不自动开放 lineage/Steward。
- 内容版本与 RAG 派生物分离；不以 Memory 替代文档来源。
- 授权条件是当前附件读取权限与显式 Agent grant 的交集；grant 不扩大档案可见性。

## Open questions

无阻塞产品问题。OCR、网页正文、PDF、选段、lineage 和 Steward 均已明确延期，不影响 MVP 规划。
