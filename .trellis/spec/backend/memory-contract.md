# Memory 来源、响应和前端缓存合同

本文件记录 2026-09-13 A 任务的实现合同，用于维护真实接口；不将其他历史 spec 重新提升为开发门禁。授权与任务边界仍以 AGENTS.md 和当前任务为准。验证入口：[A 任务](../../tasks/09-13-memory-contract-repair/prd.md)。

## 1. Scope / Trigger

修改 Memory 候选、确认、管理列表、RAG 保存、旧来源验证或前端投影缓存时适用。HTTP 字段、数据库来源证据和各读取面的授权必须一起验证，前端 mock 成功不足以证明真实创建成功。

## 2. Signatures

- `POST /api/memory-candidates`：创建待确认候选，201。
- `GET /api/memory-candidates?include_decided=...`：本人候选。
- `POST /api/memory-candidates/{id}/confirm`：`{scope, retention_days?}`，返回 Memory。
- `POST /api/memory-candidates/{id}/dismiss`、`POST /api/memories/{id}/revoke`、`DELETE /api/memories/{id}`：管理自身记录。
- `GET /api/memories?space_id=...`、`GET /api/rag/search?space_id=...&q=...`：当前获权投影。
- `memory_sources.source_lifecycle / memory_materializable`：不含读者的来源有效性；`document_readable / source_access / memory_access`：当前读者/空间授权。
- `verify_legacy_source(db, row, account=...)`：只恢复可验证来源，检查既存 Memory 范围；不确认、不扩大范围、不索引。
- DB：candidate 的 `(author_account_id, idempotency_key)` 唯一，Memory 的 `source_candidate_id` 唯一；来源证据同时存入 Memory，不能只依赖 candidate/message FK。

## 3. Contracts

新客户端创建请求必须显式来源，并为一个用户操作保持同一 `idempotency_key`（8～128 字符）：

```json
{"source":{"kind":"manual"},"raw_quote":"本人此次输入","summary":"整理后的摘要","purpose":"用途","suggested_scope":"private","sensitivity":"normal","idempotency_key":"operation-unique-key"}
```

`source` 为以下三种之一，额外字段拒绝：

- `manual`：原文必填；HTTP raw_quote 最多 12,000 字符。
- `agent_message`：附正整数 `message_id`，仅本人当前获权会话中的原始 user 全文；不接受 assistant/tool/派生消息作独立快照。
- `rag_chunk`：附 `document_id/chunk_id/revision/index_version/space_id`；space_id 是当前搜索空间，服务端精确回读原文。客户端原文可省略，提供时必须完全相同。

summary 最多 20,000 字符，purpose 最多 120 字符。非 manual 的原文在 UI 只读，RAG 保存保持源敏感度；候选不是可检索 Memory。确认 scope 使用 `private` 或 `household:N/lineage:N`，选项为服务端 `allowed_scopes` 与当前空间的交集。

旧无来源请求不能推断 manual；旧 source_message_id 只适配合法原始 user。任意 source_document_ref 不是授权；非空客户端 source_span 拒绝，来源证据由服务端生成。

输出保留 HTTP `raw_quote`（候选 ORM 字段仍是 `source_quote`），新增 source_kind、source_status、allowed_scopes。`unavailable/unverified` 时原文/摘要/用途和源消息 ID/ref/span 均隐藏；本人可管理元数据，其他失权读者不返回记录。已确认本人 user 快照在消息删除后为 `deleted_snapshot`；RAG 副本仍依赖根来源，不能扩大原空间或敏感度范围。

写入顺序固定为 mutation → flush → 构造 DTO / model_dump_json → commit → 返回已验证 DTO。创建请求 fingerprint 与归一化输入绑定；确认 fingerprint 保留原 retention_days，不用重算后的到期时间比较重试。取得写锁后重验来源、目标 scope 与有效 Memory 开关。

前端所有写同一投影的读请求共享请求顺序，写回需匹配账户 generation、分区对象和请求序号。clear/reset 不仅禁止旧回写，还禁止旧 mutation 继续发起新身份的刷新。能力刷新清除旧 RAG 结果；读取失败清除相应旧正文并显示错误，不伪装操作成功。

## 4. Validation & Error Matrix

| 情况 | 结果 |
|---|---|
| 缺失/冲突/伪造来源、原文不匹配 | 422，不落候选 |
| 无权、跨空间、来源撤销或不可验证 | 403 / 隐藏投影，不能检索 |
| 同 key、同输入 | 返回同候选；仍重验当前授权 |
| 同 key、异输入；同候选异确认参数 | 409 MEMORY_STATE_CONFLICT |
| 重复确认、同参数 | 同一 Memory / 同一 retention_until |
| 存量 scope 超过可验证来源 | 拒绝恢复，原行保持 unverified |
| Memory 关闭 | 管理 API 503 MEMORY_DISABLED；独立 RAG 读取仍按其开关 |
| 功能状态或写后刷新失败 | 前端解释错误；保留重试键，不插入假成功结果 |

## 5. Good / Base / Bad Cases

- Good：显式手工创建 → 列表序列化 → 确认 → 真实搜索命中；相同请求重试不重复。
- Base：旧合法本人 user 来源继续适配；旧无引用的 Assistant 消息展示合同不变。
- Bad：将共享片段保存为 private 后退出原空间，副本仍读出正文；恢复旧来源时把 A 空间原始 user 认证到 B 空间。两者都必须拒绝/隐藏。

## 6. Tests Required

- 真实迁移临时库中的 API 创建/列表/确认/检索/撤销；提交前 DTO 检验失败时无提交，重试与并发确认只有一条 Memory。
- 三种合法来源和非法角色/跨空间/撤权/过期/错误 revision/sensitivity、删除聊天 FK、legacy 迁移与恢复反例。
- 真实 Axios serializer 的来源 payload、只读字段、同内容重试键、异内容新键、四开关组合及失败提示。
- 受控 Promise 顺序的旧响应/退出/空间清理/能力切换回归，断言受限正文不被恢复且无旧链额外 GET。
- linked worktree 共享依赖时，Python 验证显式 `PYTHONPATH=.` 并运行 `.venv/bin/python -m pytest`；先确认 app.__file__ 来自当前 worktree。真实 listener smoke 为三个端口分别分配独立动态地址。

## 7. Wrong vs Correct

错误：收到无 source 的请求自动视为 manual；提交后才发现 DTO 缺 raw_quote；仅用搜索结果或旧快照认定当前授权；按整段文本去重用户操作。

正确：显式/可验证来源、服务端原文回读、提交前响应校验、每个读写面持续检查来源；按用户操作 key 与请求 fingerprint 处理重试。新切分与批量索引生命周期由 B/D 继续实现，不能用 A 通过宣称它们已经完成。
