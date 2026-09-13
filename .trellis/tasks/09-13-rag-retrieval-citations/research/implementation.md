# B 实施与验证记录

日期：2026-09-13。基线：`merge` 继承 A（d1f43a5）+ C（2baf7a8/470b362）链后为 97675c7。工作分支 `feat/09-13-rag-retrieval-citations`，worktree `/Users/lyston/PycharmProjects/fg-09-13-rag-retrieval-citations`。

## 改动边界

- `backend/app/services/rag_query.py`（新增）：QueryPlan lex-v1。NFKC 规范化、受控别名表 v1（过年→春节、聚会→聚餐）、疑问词停用、CJK 二字窗口、500 字符/8 词项上限、降级原因记录；日志只出版本/计数。
- `backend/app/services/memory_rag.py`：index_version 升 `fts5-trigram-v2`；句/段优先 + 有界重叠分块（`_chunk_text` 重写）；保守 UTF-8 token 估算（`utf8_bytes//2`，替换 `len//4`）；`_materialize_chunks` 按 (document, chunk_index) upsert——同版本重试 chunk ID 稳定；`search_rag` 改为 plan 驱动：单条 MATCH 内 OR（短语 + ≥3 字词项），两字词走同 eligibility 的参数化 LIKE 后备（扫描上限 200），秩融合按支路顺序、不再跨支路比 bm25 原始分；`build_context` 增加 attempt 绑定 + `blocks_json` 持久化 + 同 (run, attempt) 幂等重放（来源失权 → `AGENT_CONTEXT_INVALIDATED`）。
- `backend/app/services/context_builder.py`：同样实现 (run, attempt) 幂等重放；`blocks_json` 落库。
- `backend/app/services/agent_tokens.py`：run token 必含 `attempt` claim；缺 claim 一律 AgentTokenError（旧 token 不自动补当前值）。
- `backend/app/api/internal_agent.py`：lease 签发 attempt；`_authorize_run` 比对 `claims.attempt == run.attempt`；context 传入 attempt。
- `backend/app/services/agent_events.py`：`EventEntry.request_fingerprint`（type + 首次收到的候选 payload 规范化 sha256）；幂等比较指纹（旧事件无指纹退化为 payload 全等）；`message.assistant_added` 提交时 `_authenticate_citations`：从回答文本提取句柄，逐一核验属本 attempt build 的 included item + `document_readable` + 句柄 revision 与 document.revision 一致，才生成 citations；`context_reference_json`（build_id/attempt/used_handles）落事件行、不进 public_payload。
- `backend/app/api/agent.py`：`_message_out` 改为服务端授权投影（citations 只含当前可读来源、受限项计数为 `unavailable_citation_count`，content_json 不再回显原始 citations）；新增 `GET /runs/{run_id}/events/{seq}/citations` 固定后备读取。
- `backend/app/schemas/agent.py`：`CitationOut`（六字段）、`AgentMessageOut` 增 citations/unavailable_citation_count、`RunEventCitationsOut`。
- 迁移 `0044_rag_citation_contract`：合并既有双 head（0042_memory_source_contract、0043_platform_steward_assist_switches）；agent_run_events.request_fingerprint/context_reference_json；context_builds.attempt/blocks_json + (run_id, attempt) 唯一索引。
- sidecar：client 保留并校验 `context_build_id`（此前 parser 丢弃）；worker 在有 context 时附句柄引用指引（服务端认证，句柄伪造不生效）。
- frontend：历史走顶层 citations 投影 + unavailable 提示「部分来源已不可用」；SSE assistant_added 无引用时按 (run_id, seq) 调 `fetchRunEventCitations` 补取，失败正文不受影响。

## 测试

新增 `backend/tests/test_rag_retrieval_citations.py`（35 条）：核心中文 15 参数化正例 + Q13 花生片段 + EN 英文回归；N01/N03/N04/N06/N08/N09 反例；别名非硬编码；同版本 chunk ID 稳定；attempt 幂等/过期 token 403；指纹幂等与冲突；引用认证/伪造不认证/撤销遮罩/补取端点；web 引用并存与 16KiB 边界；plan 形状。

既有破坏面修复：run token 测试与伪造 token 用例补 attempt；A 的迁移链测试 head 更新为 0044（downgrade 守卫断言改为停在 0042）；legacy 隔离测试 fixture 索引版本升 v2；agent/前端 mock 补 `fetchRunEventCitations`。

## 检查结果（本 worktree）

- backend：`ruff check .` / `ruff format --check .` / `mypy app` 通过；`pytest -q` 1114 passed / 3 skipped（含新增 35 条）。
- agent：`npm run lint` / `type-check` / `test`（109 passed）/ `build` 通过。
- frontend：`npm run lint` / `type-check` / `test`（620 passed）/ `build` 通过。
- 迁移在隔离临时库 `alembic upgrade head` 通过（合并双 head）。

## 机械格式化说明

`ruff check --fix` / `format` 对仓库内既有测试文件与 `0042_steward_inferred_edges.py` 做了 import 顺序/换行等纯格式修复（与 A 对 0041 的机械格式化同类）；AST 与断言未变，`test_steward_assist_platform_governance.py` 仅格式。未改业务断言。

## 边界与交接 D

- 只解 RAG 子预算与中文词法召回；全请求总预算、真实模型质量、embedding/rerank 均未实现/未测（E 边界不变）。
- index_version `fts5-trigram-v2` 为当前唯一活动版本；存量 v1 文档的批量迁移、防复活、维护触发归 D（其fixture/迁移需消费 `_materialize_chunks` 的 upsert 语义与 document.index_version）。
- 语义忠实度未证明：引用认证只证明来源绑定，不证明每句话被材料支持。
