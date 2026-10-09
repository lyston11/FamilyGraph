# 现状：记忆与 RAG 的实现证据与缺口

结论先行：**安全骨架完整，质量能力缺失**。缺口集中在四处——记忆无更新语义、
提取只有 7 条确定性规则、检索无重排、上下文是贪心填预算。

## 1. 现有链路（代码证据）

```text
user 消息 → settle 钩子 (agent_queue._settle)
          → memory_extractor.rule_detector   [纯规则, 7 类, 单消息上限 3]
          → MemoryCandidate (pending)
          → 用户 POST /api/memory-candidates/{id}/confirm
          → Memory (confirmed, scope/sensitivity/retention_until)
          → RAGDocument + RAGChunk (index_version=fts5-trigram-v1/v2)
          → search_rag 词法(FTS5|PGroonga) [+ 可选向量补充]
          → ContextBuilder.build  (每 run_id+attempt 一次, MAX_INCLUDED_SOURCES=20)
```

| 事实 | 证据位置 |
|---|---|
| 提取是纯确定性规则，类别集合与顺序固定 | `backend/app/services/memory_extractor.py`；`memory-contract.md` §8 |
| 提取钩子在 settle 内、savepoint 隔离、永不抛错 | `memory-contract.md` §8；`agent_queue._settle` |
| Memory 有 `retention_until`，由 `expire_due_memories` 到期处理 | `backend/app/models/memory.py`；`backend/app/services/memory.py` |
| Memory 无 `superseded_by` / 无有效区间 / 无冲突字段 | `backend/app/models/memory.py:114-175`（无此类列） |
| RAG 只支持 active/revoked/deleted/invalidated + `invalidation_reason` | `backend/app/models/rag.py:73-95` |
| 词法按方言分派；eligibility 由调用方原样拼入 SQL | `backend/app/services/rag_search_provider.py` |
| 向量候选**只增不减、不重排**，且复用 `_rows_to_hits` | `backend/app/services/memory_rag.py:1330-1370, 1390-1490` |
| 候选预算 200、页 32、limit≤100 | `memory_rag.py:1132-1133`、`1258-1259` |
| 上下文整块纳入/排除，超预算即丢 | `backend/app/services/context_builder.py:288-310` |
| 历史只用最近 4 条且每条 ≤500 字符 | `context_builder.py:224`；`rag_query.py` |
| 别名表是 closed list，仅 2 条（过年/聚会） | `backend/app/services/rag_query.py` `ALIAS_TABLE_V1` |
| 工具注册表**没有记忆类工具** | `backend/app/services/agent_tools.py:163-285`（REGISTRY 全量） |
| 索引换版有游标/租约/失败台账 | `backend/app/models/rag.py:117-165`；`rag-index-lifecycle-contract.md` |
| embedding 已具备 provider 抽象、后台索引、filter-then-ANN 查询 | `rag_embeddings.py`、`embedding_client.py`、`embedding_index.py`、`embedding_chunking.py` |

## 2. 缺口（按用户价值排序）

### G1 记忆没有更新语义（最高）

同一个人的职业、住址、称谓偏好变化后，旧 Memory 仍是 `active`：检索会同时命中新旧，
模型看到**互相矛盾的事实**，且无法判断哪个有效。`retention_until` 只表达「到期」，
不表达「被取代」。

这是家族场景的核心能力：亲属关系、住址、职业、称呼都会变，而「过时的记忆被当成当前事实」
比「检索不到」更有害。

### G2 提取面过窄

7 类确定性规则（birthday/anniversary/dietary/occupation/school/residence/preference）覆盖不了
家族场景的主要可记忆内容：亲属关系确认、家族事件、故事、称呼偏好、居住迁移史。
反向问题也存在：规则命中即产卡，没有相关性判定，候选队列容易被噪音污染。

### G3 Agent 不能主动记忆

用户说「记住这个」时，agent 只能指望 settle 后的规则碰运气。缺少受控的记忆提议工具，
而工具的缺失是**刻意的**（REGISTRY 里没有任何记忆写路径）——增量必须保持「只能提议、
不能确认」的边界。

### G4 检索无重排、查询计划过弱

词法 top-k 直接进 context。没有 RRF/特征重排，没有实体锚定（人名/称谓 → person_id），
别名表 2 条。多跳问题（「我外祖父的弟弟的女儿」）只能靠单轮词法撞运气。

### G5 上下文组装是贪心填满

`candidate_estimate > token_budget` 即丢弃，且「整块纳入/排除」：一条 800 字符 chunk
可能顶掉后面所有命中，而命中顺序是候选到达顺序，不是质量顺序。子预算默认 2000。

### G6 没有质量基线

没有 golden set、没有指标、没有回归门。任何检索/提取改动都无法证伪，也无法回答
「这次改动到底好没好」。

## 3. 不可动摇的约束（增量必须复用）

- 授权过滤先于排序；索引不承载授权；去掉过滤必须让 mutation 测试失败。
- 引用只能由服务端生成（`_rows_to_hits` / `ExactChunkRef`），客户端不能自报 hash。
- 每 `(run_id, attempt)` 一次构建，失效单调不可恢复。
- 记忆入库必须经用户确认；提取产物只是 review card。
- 模型调用只经 provider gateway（egress 审计、脱敏、重试预算）。
- 未配置/不可用时 fail closed 或回退确定性路径，**不伪造命中**。
