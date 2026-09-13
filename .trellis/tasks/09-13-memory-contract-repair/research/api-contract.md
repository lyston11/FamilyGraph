# A 后端 / 前端接口合同

实施日期：2026-09-13。新增字段不改变 HTTP `raw_quote` 命名。后端迁移以实际 head `0041_term_pack_expansion` 为基础。

## 创建候选

`POST /api/memory-candidates` 的 `source` 必须显式为以下一种：

```typescript
type MemorySource =
  | { kind: 'manual' }
  | { kind: 'agent_message'; message_id: number }
  | { kind: 'rag_chunk'; document_id: number; chunk_id: number;
      revision: number; index_version: string; space_id: number }
```

RAG `space_id` 是当前搜索所用空间；不是客户端自行声明授权。服务端回读片段并重验原空间、作者、版本、来源链。`raw_quote` 对 manual 必填，对其他类型可省略，提交时必须与实际原文完全一致。message 仅允许本人原始 user、全文快照。

`idempotency_key` 放 JSON body，8–128 字符。每个用户操作生成一次，网络失败重试复用；正文、摘要、scope、用途、敏感度和 source 参与请求指纹。相同键异参返回 409 `MEMORY_STATE_CONFLICT`。

旧 `source_message_id` 可兼容符合新规则的 user 消息；旧无来源、任意 `source_document_ref`、新旧字段冲突或客户端伪造 `source_span` 返回 422/403，不当作 manual。

确认维持 `{scope: 'private' | 'household:N' | 'lineage:N', retention_days?: number}`。同 candidate 同确认参数重试返回同 Memory；异参 409。已经撤销或失权时不能借幂等重放读取正文。

## 候选和 Memory 输出

保留原字段，并增加：

- `source_kind`: `manual | agent_message | rag_chunk | legacy`
- `source_status`: `available | deleted_snapshot | unavailable | unverified`
- `allowed_scopes`: `string[]`，取值使用确认端点的 scope 拼写。

`raw_quote`、候选 `summary`、Memory `content`、`purpose` 为 `string | null`。`unavailable` / `unverified` 时不返回这些正文；`source_span_json` 也不泄露原定位快照。本人管理面可见状态元数据，其他失权读者不返回记录。`deleted_snapshot` 仅表示已确认的本人原始 user 快照可读、原消息已删除；未确认候选不得在原消息删除后确认。

只有 `available` 的 pending 候选可确认。允许忽略/撤销/删除本人不可用记录，但响应继续遮蔽正文。

## 搜索与开关

`RAGSearchOut` 的现有 `index_version` 返回真实 chunk 版本，新增 `space_id: number | null`（原文档空间）和 `allowed_scopes: string[]`。保存请求的 `source.space_id` 始终用当前搜索 spaceId，不能直接照搬 nullable 的结果 space_id。

Memory 关闭时所有管理和保存 API 返回 503 `MEMORY_DISABLED`；RAG 可独立搜索。用户阅读与模型外发的本地 Provider 限制分别判断，人工保存不因 Provider 未选择而拒绝合法来源。来源失效/失权会导致保存或确认拒绝；列表正文隐藏，检索与 Context 同样排除。

## 事务顺序

服务写入 → flush → 构造 DTO 并 `model_dump_json()` → commit → 返回已验证 DTO。候选创建唯一 `(author_account_id, idempotency_key)`；Memory 唯一 `source_candidate_id`，确认通过条件状态更新串行竞争。
