# Assistant 记忆与 RAG 执行合同记录

2026-09-14，记录 B 修复后的跨层实现与回归入口。本文是代码合同记录，不替代根目录 AGENTS.md 或任务授权，不将历史 spec 索引重新设为工作流门禁。索引物化、算法换版及维护租约由 D 的独立合同负责。

## 1. 适用范围

适用于 Assistant 的 run token、ContextBuilder、事件追加、公开引用投影和 sidecar 材料包。目标是让实际执行身份、原片段和当前读取权限始终对应，避免只在 HTTP 入口鉴权、只按来源 ID 认证或只在 mock 中对齐字段。

Steward 未接入私有聊天 RAG；词法查询不构成 embedding 或通用语义理解。RAG 子预算不等于整个模型请求窗口。

## 2. 签名与持久字段

- `ExecutionIdentity.from_claims(claims)` 捕获签名的 `run_id/job_id/attempt/account_id/space_id/agent_kind/tool_allowlist`。`fence_execution(db, identity, ...)` 在短 SQLite writer 内读取 Run、Job、Session、Account 和会员资格，比较两侧 attempt、scope、状态、取消和租约有效时间。
- `ContextBuilder.build(..., run_id, attempt, execution, recent_messages, provider_decision)` 为同一 `(run_id, attempt)` 复用一个构建。`context_builds` 的 `policy_json/invalidated_at/invalidation_reason` 由迁移 `0046_context_execution_contract` 添加。
- `rollback_preserving_invalidation(db, execution)` 仅从签名执行的唯一 build 捕获服务端已观察到的失效标量，回滚事件批次后按相同 scope/id 条件保留；不读取 sidecar 自报理由，不重建已删除的 build。
- `ExactChunkRef` 包含 `document_id/chunk_id/source_type/source_id/source_revision/index_version/chunk_index/content_hash`。它由服务端命中生成并保存于 item/已认证消息，客户端不能自报 hash 以获得认证。
- `POST /internal/agent/runs/{run_id}/events/append` 的事件可以携带独立于 `public_payload` 的 `context_reference`。
- `GET /api/agent/runs/{run_id}/events/{seq}/citations` 通过所属 session、assistant role 和服务端消息 key 定位完整引用；不是任意句柄查询端点。

## 3. 请求、响应及边界

```json
{
  "seq": 2,
  "type": "message.assistant_added",
  "public_payload": {"role": "assistant", "text": "合成回答 [rag:42:r1:c3]"},
  "context_reference": {"build_id": 7, "attempt": 1, "used_handles": ["rag:42:r1:c3"]}
}
```

`build_id/attempt` 为严格正整数；`used_handles` 最多 20 个，每个 1～255 字符，不可重复。只有已完成的 assistant 消息能携带 reference。sidecar 从完成文本与本次 included 句柄的交集生成列表；不从流式半句或材料列表推断已使用。

服务端先按原始请求生成 v2 指纹：`run_id/attempt/seq/type/public_payload/context_reference` 全部参与，再认证和裁剪。已提交的同请求只确认 duplicate，不因后续撤权改写原消息或指纹。没有 reference 的旧 sidecar 保留正文/web，不猜测认证引用。

`agent_citations` 统一历史、SSE/重连和固定补取的引用投影。公开字段 `citations/unavailable_citation_count/citations_complete` 由服务端生成；失权后不输出受限来源定位和摘录。内部历史只下发允许的 user/assistant 正文。既有正文的留存不等于结构化引用仍有权限。

整个 `public_payload` 按 `json.dumps(..., ensure_ascii=False).encode('utf-8')` 测量，上限 16384 字节。引用装不下时保持正文/web，固定补取完整引用；前端显示可重试的加载状态。不要截断已合法的正文，也不要抬高事件限额。

`rag_budget.render_context_appendix` 与 `agent/src/context.ts` 使用同一材料包；估算器 `utf8-half-envelope-v1` 为 UTF-8 字节数除二向上取整，包含标记、句柄、换行和引用指令。默认子预算 2000，整块纳入/排除。共享冻结包络 fixture 验证两种语言逐字一致。

查询计划 `lex-v1`：NFKC、500 字符、8 词项、受控别名。只在明确指代且最近 4 条同会话允许文字内有唯一锚点时扩展；任一历史消息超过 500 字符则保守降级。短词 LIKE 使用参数绑定和显式 `ESCAPE '!'`，转义 `!/%/_` 以保留查询字面含义。FTS 和两字后备共享 200 个 SQL 返回候选预算、每页最多 32；这是候选预算，不是 SQLite 内部实际扫描行数上界。

## 4. 校验与错误矩阵

| 条件 | 行为 |
|---|---|
| 签名 attempt 缺失、bool 或非正整数 | token 拒绝，不补成当前 attempt |
| 入口鉴权后发生真实 reaper/lease 换代 | writer/admission 拒绝 `AGENT_TOKEN_SCOPE_MISMATCH`，无旧执行副作用 |
| Run/Job 不可执行、取消或租约过期 | 409，保持现有终态/取消优先级 |
| 同 attempt 构建来源、策略或预算已失效 | 409 `AGENT_CONTEXT_INVALIDATED`，失效状态不可恢复；新 attempt 可重建 |
| reference 错 build/attempt、句柄未 included 或正文未使用 | 409 `AGENT_EVENT_INVALID`，无认证引用 |
| 原片段缺失/改文/改版本/改 revision 或当前失权 | 不认证该片段，不改指向新搜索结果 |
| 同 seq 异原请求 | 409 `AGENT_EVENT_SEQ_CONFLICT`，整批事件/消息/指纹回滚 |
| public payload 16385 字节 | 422 `AGENT_EVENT_INVALID` |
| 0046 存在新 policy/失效证据时尝试降级 | 第一项 DROP 前拒绝，保留证据 |

已准入的 Provider/工具在途操作允许按现有合同完成；准入在网络 I/O 前提交，不持有网络长事务。工具 `ToolRunScope` 固定原 attempt，持久结果和审计不得随 ORM 自动刷新漂移到新 attempt。网页日期输出先统一成 JSON，首次与重放同样经过结果策略。

## 5. 正常、兼容与反例

- 正常：同 attempt 并发 GET 返回同一构建；实际回答使用的合法句柄在 SSE、历史及补取一致。
- 兼容：旧消息没有 citations 正常渲染；RAG 关闭时建立的空构建在同 attempt 再开启后仍为空，不偷偷换材料。
- 反例：合法 context 已因关闭 RAG 或来源失效被服务端判无效，不能因随后事件批次冲突回滚而丢掉这一安全状态；事件事务和失效状态分别保持各自语义。

## 6. 回归入口及断言

- `test_rag_acceptance_contract.py`、`test_rag_acceptance_bindings.py`：真实独立 Session 换租；context 并发；精确定位与持续失效；伪造字段、各读取出口；16384/+1；原请求重放；已准入工具归属和真实 gateway 网络点可写。
- `test_rag_query_context.py`：相同问题配不同唯一历史得到不同锚点，歧义/无前文/越窗口不猜；授权补足跨页且总候选受限。
- `test_context_execution_migration.py`、`test_memory_source_migration.py`：证据保留与真实迁移 head；分支降级不能用 scalar 任取 Alembic version 当唯一 head。
- `agent/test/context.test.ts`、events/worker tests：真实包络、完成文本引用列表、attempt/build 接线和 C 的压缩恢复。
- `scripts/smoke/run_agent_memory_smoke.py`：隔离三 listener、真实 Pi/HTTP、撤权/丢响应/重连/补取与原始内部历史。模型流为合成，不代表真实模型忠实度或线上性能。

## 7. 错误做法与对应实现

错误：从可变 ORM `run.attempt` 重建期望身份，在入口 SELECT 后直接写入；引用只核对 `source_id`；认证后 payload 用来判断原请求幂等；把 failed 事件批次回滚当成可以恢复旧 context 的理由。

正确：签名身份贯穿短 writer/admission；服务端保存精确片段描述符并按当前权限重新读取；指纹在认证/裁剪前计算；同 attempt 失效标记单调保留，事件本身仍保持整批原子性。
