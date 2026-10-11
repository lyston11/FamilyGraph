# 父任务整合记录

## 子任务结果

| 子任务 | 覆盖 | 提交 | 结果 |
|---|---|---|---|
| `10-11-rag-authorization-model-unification` | U1 | `b30d1ff5` | ✅ |
| `10-11-unified-authorized-retrieval-entry` | U2 | `d3a2a2c5` | ✅ |
| `10-11-memory-background-extraction-pass` | U4 | `3168f667` | ✅ |
| `10-11-retrieval-budget-importance-decay` | U3 | `b92dd87c` | ✅ |
| `10-11-stable-prefix-context-split` | U6 | `31fab599` | ✅ |
| `10-11-knowledge-maintenance-loop` | U5 | `c8bbdd65` | ✅ |

## 各统一点的实际交付

### U1 授权模型统一

`RAGDocument.visibility_snapshot`（此前恒为 `{}`）承载「读我至少需要哪些类别是清的」声明：

```json
{"subject_user_id": 42, "required_categories": ["dates"], "key": "profile-v1"}
```

- `subject_user_id` 是 **opt-in 标记**：`NULL` → 维持 scope 裁决（`memory` / `public_kinship`
  逐字不变）；非 `NULL` → 主体阶梯裁决（字段掩码 + 未成年人 overlay + HIGH_RISK 自限）。
- 判据只有一处：`visibility.required_clearance_satisfied`。RAG 层不复制阶梯、不复制
  `HIGH_RISK_CATEGORIES`、不复制 overlay 字段。
- 施加点是**投影复核层**（`memory_sources._profile_clearance_ok`），不是 SQL 层：`evaluate`
  需要读关系、成员与披露记录，塞进 SQL 会把细判降级成粗筛。
- **实施中发现**：RAG 的字段清空实际来自**披露**而非成员身份（`_PURPOSE_LEVEL_CAP[PURPOSE_RAG]`
  封在 `lineage_summary`，该层只清 `disclosed_categories` 映射的字段）。这不是障碍，是正确的
  语义——空间成员身份本身不足以读别人档案派生的内容。披露系统由此第一次接到语料文本上。
- 无迁移（复用既有列）。mutation：去掉 HIGH_RISK 自限 → 2 failed；去掉复核层判据 → 5 failed。

### U2 统一授权检索入口

`services/knowledge_retrieval.py`：`RetrievalIdentity`（身份）→ `resolve_authorization`（派生
允许集与私有读者）→ `retrieve`（唯一入口）。

- 未知 kind **fail closed**（422），不退回「读全部」。
- 可读集为空是**授权结论**：直接返回空 outcome，不退化成全开。
- `RetrievalOutcome.filtered_out` 把「没有相关内容」与「被授权过滤掉」分开。
- 5 个消费者全部迁移（`agent_tools` / `steward_tools` / `context_builder` / `rag` /
  `build_run_context`）。迁移前有 3 个依赖默认全开，其中 `build_run_context` 对 steward run
  也会拿到 assistant 的允许集。
- **结构性守卫（AST）**：`search_rag(` 只允许出现在 `memory_rag.py` 与
  `knowledge_retrieval.py`。

### U3 预算与衰减

- 份额增加消费方维度 `(consumer_kind, source_type)`；只登记 `steward`，assistant 取值逐字不变。
- `lex-v3` 增加新鲜度（年龄衰减），位置在**来源类别之后**；按天分桶以保证可复现；
  **不是默认**（排序是回归与评测基准）。
- **顺带修复一个我引入的缺陷**：`budgeted` 未进入被比较的 policy，且 `_replay` 无条件重算
  分层分配 → steward 的 `budgeted=False` 构建重放时必然误报 `tier_budget_changed`。

### U4 后台片段抽取

`memory_extractor.extract_session_segments`：读最近 20 条用户消息组成的片段。

- 只用确定性规则；产物仍是 pending 候选（确认门不变）。
- **幂等**：去重看事实本身（同账号同 summary 的候选或 `Memory.content`），不看来源消息。
- **只读用户消息**（assistant 回复可能是模型猜测）。
- **来源归因**：quote 跨消息时归给被它完整包含的最长那条消息并换成该消息全文
  （`propose_candidate` 对 `agent_message` 来源做全等校验）；无来源即丢弃。
- 接入维护 tick（`memory_segment_*`）。

### U5 维护循环

`rag_maintenance.retire_unreachable_documents`：真源不可达的派生文档退役。

- 查询**直接选出需要退役的文档**（`LEFT JOIN memories` + 不可达条件），因此**自排空**、
  不需要游标、不需要迁移。
- 只动派生层（`Memory` 一行不改）；三种理由写进 `invalidation_reason`。
- 与物化共用租约但不碰其游标；顺序为 物化 → 校验 → 退役；计数进 `rag_index_retired`。
- **已有的部分无需重做**：`verify`（revision 对账）由既有物化批的
  `_memory_projection_complete`（比对 revision + 内容哈希 + chunks + FTS）覆盖；`rebuild`
  由既有 `stage_index_version` 覆盖。

### U6 稳定前缀

- 投影与 digest **不随知识写入变化**（新增并确认一条记忆后逐字节不变）。
- **结构性守卫（AST）**：`steward_assist.py` 内不得出现对 `retrieve` / `search_rag` 的调用。
  mutation：插入 `knowledge_retrieval.retrieve(...)` 后守卫失败并指出行号。
- 易变增量经 `familygraph.steward.search_memory` 工具进入上下文。

## 父级验收：逐条结论（含未达成项）

| 验收项 | 结论 |
|---|---|
| 所有消费者读知识只经过一个入口，且可区分「无内容」与「被过滤」 | ✅ 已达成，AST 守卫 |
| 知识库能自发发现失效并退役 | ✅ 已达成（退役扫描）；`verify`/`rebuild` 由既有机制覆盖 |
| 上下文的稳定部分与易变部分分离，`prompt_digest` 不抖动 | ✅ 已达成，测试 + AST 守卫 |
| `(scope, source_type)` 授权判据只有一份真源 | ✅ 已达成（判据全在 `visibility.py`） |
| **字段级掩码对每个 source_type 生效** | ⚠️ **部分达成**：对**主体派生内容**（声明 `subject_user_id`）生效；`memory` / `public_kinship` **刻意不施加**（scope 是主体自己选的可见性，叠加掩码等于推翻主体的选择） |
| **新增 source_type 只需一处登记 + 一处写入方** | ⚠️ **未完全达成**：仍需登记 `RAG_SOURCE_TYPES` **与** `SOURCE_TIER_FRACTIONS`（后者有测试强制，不会静默漏），且需一个 ingest 写入方。`family_story` / `profile` 仍是「声明但无写入方」 |

## 明确未做（需要产品定义，不自行发明）

| 项 | 为什么不做 |
|---|---|
| `curate`（记忆去重 / 合并 / 低价值归档） | 「低价值」没有定义。合并两条用户确认过的记忆涉及数据改写语义（保留哪条？合并后 revision 怎么走？），需要产品决策 |
| `classify`（importance 评分） | 「重要」没有定义，且需要新列（迁移）与它影响什么（排序？预算？注入？）的决定。U3 的新鲜度是**可从既有数据推导**的那一半，已交付 |
| `family_story` 的写入方 | 真源表尚不存在；`UNINDEXED_SOURCE_TYPES` 已显式登记理由 |
| `profile` 的索引化 | 逐 viewer 求值（依赖读者），走 `get_profile_summary` 结构化投影是**刻意的**，不索引 |

## 验证

| 检查 | 结果 |
|---|---|
| `pytest`（全部子任务合并后） | **2449 passed, 38 skipped** |
| `ruff check` / `ruff format --check` | 通过 |
| `mypy app` | No issues found |
| `alembic heads` | 单一 head `0060_steward_memory_scopes`（本父任务**未新增迁移**） |
| 新增结构性守卫 | 47 项通过（SQL 可移植性、统一入口、稳定前缀、退役、预算衰减、档案清空、片段抽取） |
| mutation 验证 | U1 ×2、U2（AST 守卫）、U6（AST 守卫）、PG boolean（SQL 守卫） |

## 未运行的检查

`frontend` 与 `system-admin-frontend` 的构建与 smoke：本次改动全在后端服务层，未触碰前端代码
与 API 契约形状（新增字段是 additive 的）。
