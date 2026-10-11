# 统一知识管线：授权模型、检索入口、写入合同与维护循环

## Goal

把当前**各自独立建立的 5 条读路径与 4 条写路径**收敛为**一条知识管线**，使"谁能读什么"只有一个决策点、"什么能进知识库"只有一个写入合同、"上下文怎么组装"只有一个预算策略、"知识怎么保持正确"有一个自发维护循环。

## Background（已核对代码的事实）

### 读路径（5 条，各有各的授权）

| # | 路径 | 授权依据 |
|---|---|---|
| ① | `memory_rag.search_rag`（记忆 / RAG） | `scope` 扁平枚举 + 空间成员 + `source_types` 目的限定 |
| ② | `family_projection` / `personal_family_view`（图谱） | `visibility.evaluate` + 关系路径 + 空间成员 |
| ③ | `steward_tools` 投影工具（6 个只读） | `space_id` + 发布视图 + viewer claim |
| ④ | `get_profile_summary`（`VisibilityPolicy` 投影） | 四级阶梯 + 字段级 mask |
| ⑤ | `agent_query`（`search_space` / `resolve_free_text_relation`） | 空间 + 可见性 |

### 写路径（4 条，各有各的形状）

| source_type | 现状 |
|---|---|
| `memory` | ✅ 完整：候选 → 用户确认 → `Memory` → `index_memory` |
| `public_kinship` | ⚠️ 称谓包**直接 seed**，无候选门（10-10 语料矩阵任务实现） |
| `authorized_document` | 🔨 合同已定、**无生产写入方**（`10-11-attachment-content-agent-grant` 正在补） |
| `family_story` | ❌ 合同已定、**连真源表都没有** |
| `profile` | ❌ **明确排除**，改走 `get_profile_summary` |

### 两套授权模型不对齐（**核心缺陷**）

| | 域 | 形状 |
|---|---|---|
| `services/visibility.py` | 档案 | 四级阶梯 `self_private > household_detail > lineage_summary > none` + **字段级 mask**（`CONTENT_FIELDS`、未成年人 overlay、`HIGH_RISK_CATEGORIES`）；purpose 只能收紧 |
| `RAGDocument.scope` | 记忆 / 知识 | 扁平枚举 private/household/lineage/public + 空间成员判据；敏感度仅 `normal/sensitive/high/local_required` |

`memory_sources._author_visible` 确实调用 `visibility.evaluate(purpose=PURPOSE_RAG)`，但**只当"作者可见/不可见"一道门**用；四级阶梯与字段级 mask **没有落到 chunk 文本上**。因此未成年人 overlay 与 `HIGH_RISK_CATEGORIES`（health/address/school/contact/private_notes）在 RAG 侧**没有等价机制**；`sensitivity='high'` 只是把可读 scope 收紧到 private，**不是遮蔽字段**。

**紧迫性**：`10-11-attachment-content-agent-grant` 正在建 `authorized_document` 的第一个真实写入方。它的设计已写明"复用现有读取边界"与"`for_model=True` 仍执行现有 sensitivity/local restrictions"，但**没有处理字段掩码落到文本**。不先统一授权模型，同一缺陷将被复制到第二个 source_type。

## 统一后的形状

```
① Ingestion        ② Knowledge(真源)       ③ Index(派生)       ④ Retrieval(授权)     ⑤ Assembly(预算)      ⑥ Maintenance
源 → 候选           Memory / SourceFact      RAGDocument         authorized hits       context_blocks       verify/retire
                    AttachmentContentVer     RAGChunk            filter-then-ANN       tier budget          dedup/merge
                    TermEntry(system)                            + 词法 hybrid         citation             rebuild
```

**RAG 不是并列的第五个东西，它是 ③④ 两层**；记忆只是 `source_type='memory'` 的一个来源。

## 六个统一点

### U1 一套授权模型（地基，必须最先）

- `visibility.py` 四级阶梯成为**唯一**授权真源；`RAGDocument.scope` 是它的**投影**，不是第二个模型。
- **字段级掩码在 chunk 物化时施加**（`_materialize_chunks` 之前），而不是只在入口当一道门。
- `sensitivity='high'` 语义改为"**遮蔽字段**"，而不是"收紧 scope"。
- `PURPOSE_RAG` 上限 `lineage_summary` 保持。

### U2 一个检索入口

- 所有消费者（assistant / steward / PFV / graph）用**同一 execution identity** 调**同一**授权检索服务。
- `search_rag` 已有的 `scope_allowlist`（集合）、`private_reader_account_id`（显式读者）、`source_types`（目的限定）是统一接口的雏形。
- 保持**两层各自独立挡住**（`_ELIGIBILITY_SQL` SQL 层 + `_can_read_document` 复核层）—— 已实测的承重设计，不合并成一层。
- `filter-then-ANN` 是硬约束（实测：post-filter 在 1/10 空间选择性下只返回 1 条而精确应有 10 条，且**无法区分"无相关内容"与"被授权过滤掉"**）。

### U3 一套预算策略

- `(consumer_kind, source_type)` 二维份额 + **importance / 年龄衰减**，替换现在分散三处的预算：`SOURCE_TIER_FRACTIONS`、steward 的 `readable_scopes` 门、memory 份额。
- 份额语义保持现有设计（**各类别各自的上限，不做容量转移**，空类别不浪费）。
- 排序权重（`rank_version` / `_SOURCE_TYPE_RANK_WEIGHT`）与**衰减**是两件事，必须分开。

### U4 一个写入合同

- 每个 `source_type` 都有写入方，都走 `propose → authorize → index` 同一形状。
- `memory` 是基准形状；`public_kinship` 无个人数据、无同意主体，**保留无候选门**但在合同里写明这是**有意的例外**。
- **补 pi 的教训**：`memory` 的 ① 阶段现在只有"单条消息命中 9 类正则"或"模型主动调工具"两条路。要加**后台 pass 读会话片段**，产出仍是 **pending 候选**（同意门原样保留）。

### U5 一个维护循环

| 任务 | FamilyGraph 对应 |
|---|---|
| `verify` | 对着**真源 revision** 复核已索引文档；不符则重建或撤下 |
| `curate` | 记忆去重 / 合并 / 收紧；低价值归档 |
| `classify` | 给记忆打 importance（喂给 U3 的衰减） |
| `retire` | 失效 → `superseded_by_id` / `valid_to` / `status`（机制已有，**缺自发触发**） |
| `rebuild` | 索引换版（`index_version` 已存在） |

**约束**：只动派生层与标记，**不动真源**；判据必须与 U1 同源（否则维护会"修好"一条本该不可见的内容）。

### U6 稳定前缀与增量上下文

- 上下文分成**冻结基线**（digest 覆盖它）与**易变增量**，而不是每次 run 全量重建。
- 对应痛点：`prompt_digest` / `input_hash` 抖动。steward 投影注释明确写了"按字节预算取确定前缀子集，使 hash 稳定，发送前 fence 不会误判"；记忆是**可变**的，塞进 `user_content` 就会抖动 → fence 拒绝率上升。
- 统一做法：**稳定部分进基线、易变部分进增量（工具或独立槽位）**，让 digest 契约与记忆时效性不再互相打架。

## 子任务与依赖

| 子任务 | 覆盖 | 依赖 | 状态 |
|---|---|---|---|
| `10-11-rag-authorization-model-unification` | U1 | 无（**最先**） | 待开始 |
| `10-11-unified-authorized-retrieval-entry` | U2 | U1 | 待开始 |
| `10-11-memory-background-extraction-pass` | U4（memory 切片） | U1（字段掩码） | 待开始 |
| `10-11-retrieval-budget-importance-decay` | U3 | U2 | 待开始 |
| `10-11-knowledge-maintenance-loop` | U5 | U1 + U2 + U3 | 待开始 |
| `10-11-stable-prefix-context-split` | U6 | U2 | 待开始 |

**父/子不是依赖系统**：上表的"依赖"是排序建议，写在各子任务的 `prd.md` / `implement.md` 里。

## 与在进行的 RAG 任务的接口（不冲突）

| 任务 | 在统一形状里的位置 | 冲突风险 |
|---|---|---|
| `10-10-rag-authorized-corpus-matrix`（已完成） | U1 + U4 的**合同基础**（矩阵、`INDEXED/UNINDEXED_SOURCE_TYPES`、`scope`↔阶梯映射、`public` 定义） | 无 |
| `10-03-pgvector-rag` | U2 的向量候选（filter-then-ANN 已定） | 无 |
| `10-04-lexical-search-migration` | U2 的词法候选（PGroonga-first / Unicode n-gram fallback，`pg_trgm` 已排除） | 无 |
| `10-11-attachment-content-agent-grant` | U4 的第二个实例 | ⚠️ **字段掩码**：U1 不定则缺陷被复制 |
| `10-03-steward-memory-scope-access`（已完成） | U3 的一个实例（可读集 + 显式读者） | 无 |

## 父任务验收（只在全部子任务落地后）

- [ ] 新增一个 `source_type` 时，只需改**一处**登记 + 一处写入方，授权与预算自动继承。
- [ ] `(scope, source_type)` 的授权判据只有**一份**真源，且字段级掩码对**每个** source_type 生效（含 mutation 证据）。
- [ ] 所有消费者读知识只经过**一个**入口，且"无相关内容"与"被授权过滤掉"可区分。
- [ ] 知识库能对着真源**自发**发现失效并退役，无需人工触发。
- [ ] 上下文的稳定部分与易变部分分离，`prompt_digest` 在记忆变化时**不抖动**。

## Constraints

- 不新建系统：把现有接缝（`search_rag` 三参数、`visibility` 阶梯、`RAGDocument` 生命周期字段）收拢，不另造第二套。
- 不放松任何既有授权边界（#307 / #321 / #584 / 语料矩阵的 `public` 定义）。
- 不改变 `memory` 的同意门（每条候选仍需用户确认）。
- 迁移序号不得与并行任务冲突（集成时检查）。
- 不在子任务中顺手重构超出该子任务范围的代码。
