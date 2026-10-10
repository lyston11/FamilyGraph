# 技术设计：RAG 授权语料矩阵与四级可见性对齐

## 0. 设计立场

本任务**不是**「把四层可见的信息都灌进 RAG」。相反，它的第一个产出是一个**可能为空的矩阵**：逐格判断某个 `(source_type, scope)` 该不该被索引。判断标准是三个不可回避的问题：

1. **写入方能提供什么形状的正文？** 自由文本还是结构化字段。
2. **读取方是谁，是否逐次变化？** 同一份索引文本能否服务所有读者。
3. **字段级授权在写入时能否已经完成？** 若不能，就必须在检索侧重建 mask——那是本任务明确拒绝的成本。

答案不同的格子，处理方式必须不同。把它们一律塞进同一张索引表，就是把「逐读者求值」压成「一份共享文本」，结果只能是组合爆炸或静默泄漏。

## D1 `profile` 类**不**进 RAG 索引（核心决策）

### 论证

`visibility.evaluate(viewer, target, purpose)` 的输出**依赖 viewer**：同一条档案对不同 viewer 得到不同的 level 与不同的字段 mask（`CONTENT_FIELDS`、披露类别、未成年人 overlay、`HIGH_RISK_CATEGORIES`）。而 `RAGChunk` 是**一份共享文本**，被多个 viewer 命中。两者形状不兼容。

三个可选路线：

| 方案 | 做法 | 成本 |
|---|---|---|
| (a) 按 (viewer, target) 生成文档 | 每个组合一份文档 | `N × M` 组合爆炸；每次披露偏好变化要重建全量 |
| (b) 按 level 分档生成 | 每 target 生成 self/household/lineage 三档，检索时按 viewer 选档 | `N × 3`；但 level 内还受 disclosure 类别与 minor overlay 影响，档位数不定；任一新披露类别都要重算档位 |
| (c) **不索引**，在 `ContextBuilder` 按 viewer 现算结构化投影 | 复用 `familygraph.get_profile_summary` 已有的 `VisibilityPolicy` 投影 | 无索引；代价是 profile 不能按语义检索 |

**选 (c)。** 理由：

- profile 是**小规模、强结构化、逐 viewer 求值**的数据。它的正确访问方式是「按 purpose 现算」，而不是「检索一段共享文本」。`get_profile_summary` 已经是这条路径的既有实现，重复造一份索引是纯增量复杂度。
- (a) 与 (b) 都会在**披露偏好变更**时产生「索引里的旧档位仍在」的窗口——而披露变更正是最不能有窗口的操作。要闭合这个窗口就得引入「披露 revision 进索引并逐查询复核」，那已经退化成 (c) 加上一层缓存，却多了一份可泄漏的物化。
- profile 的字段数量决定了它的价值主要在**结构化问答**（「外婆生日是哪天」），而不是语义相似度。把它塞进向量索引不会提升这类回答，只会让回答多一个可能过期的来源。

### Wrong vs Correct

错误：把 `profile` 当成第五类语料接进 `index_memory` 式的写入路径，靠 `sensitivity` 表达敏感性；用 `scope` 表达「能读到哪些字段」。

正确：`profile` 明确登记为**不索引**，理由写入 `UNINDEXED_SOURCE_TYPES`；档案读取继续走 `get_profile_summary` 的 `VisibilityPolicy` 投影；`RAG_SOURCE_TYPES` 里的 `profile` 保留为**声明位**（不删枚举，避免整表重建迁移），但它的「无写入方」是显式决定并带测试断言的状态。

### 后果与残留问题

`SOURCE_TIER_FRACTIONS['profile'] = 0.2` 与 `_SOURCE_TYPE_RANK_WEIGHT['profile'] = 1` 成为**预留位**。设计上保留它们（份额集合的语义是「各类别各自上限」，删除会改变结构性保证），但必须在矩阵文档中标注「预留、非活跃」，并由 R2 的断言把「活跃集」与「声明集」分开，防止把预留份额当成已实现能力。

## D2 `public` 的定义是「不含个人数据」，不是可见性层级

`visibility.py` 的四级是 `self_private / household_detail / lineage_summary / none`；`RAGDocument.scope` 的四值是 `private / household / lineage / public`。两个集合**不是同构的**：

- `scope` 没有 `none`（不可见的东西不索引，这条由写入侧保证）；
- `visibility` 没有 `public`（系统里没有「对所有人公开的档案字段」这个概念）。

因此 `public` **不能**被理解成「比 lineage 更宽的一层可见性」——那会推导出「把 household 档案投影到 public」这种灾难性结论。它的正确语义是：

> `scope='public'` 是写入方的**声明**：本文档不含任何个人数据，因此不需要可见性判定。

这条声明必须是**可测的**，而不是约定：`public` 文档的 `author_account_id`、`owner_user_id` 必须为 NULL，`space_id` 必须为 NULL（`ck_rag_documents_scope_space` 已强制），且正文不得含用户姓名或账号 id。测试对 `public_kinship` 生成的文档逐项断言这一点。

**推论（承重）**：任何含个人数据的 source_type **不得**写 `scope='public'`。这条规则闭合了两个集合的差集——否则 `public` 就会变成一个绕过 `visibility.evaluate` 的旁路。

## D3 「声明了但没人写」必须是显式且可测的决策

现状是四类 `source_type` 声明了却没有写入方，这是**偶然状态**：它既没有测试保护，也没有文档说明，因此任何人加一类 `source_type` 都会得到一个静默空转的枚举值，而 `SOURCE_TIER_FRACTIONS` 的断言（「所有 `RAG_SOURCE_TYPES` 都已登记」）会**通过**——它只检查份额存在，不检查写入方存在。

修法是最小且可测的：

```python
#: 有真实写入方的 source_type（生产执行路径上会被创建）。
INDEXED_SOURCE_TYPES: tuple[str, ...] = ("memory", "public_kinship")

#: 声明但无写入方的类别 → 理由。差集必须逐条给出理由：
#: 「声明了但没人写」是显式决定，不是偶然状态。
UNINDEXED_SOURCE_TYPES: dict[str, str] = {
    "family_story": "依赖尚不存在的家族故事写入功能；本任务只登记合同",
    "authorized_document": "附件授权 token 语义未定；本任务只登记合同",
    "profile": "逐 viewer 求值，走 get_profile_summary 结构化投影，不落 RAG 索引",
}
```

断言：`set(INDEXED_SOURCE_TYPES) ∩ set(UNINDEXED_SOURCE_TYPES) == ∅`，且并集 `== set(RAG_SOURCE_TYPES)`。

**反证要求**：新增一个假类别到 `RAG_SOURCE_TYPES` 而不更新两侧 → 测试必须失败。没有这条反证，断言就只是同义反复。

不删枚举值：`RAG_SOURCE_TYPES` 参与 CHECK 约束，删除需要 SQLite 整表重建（复制全表）。收益（少四个枚举值）远不抵迁移风险，且会破坏历史数据的可读性。

## D4 `public_kinship` 是唯一真实接入的纵向切片

选它的理由按权重排序：

1. **无个人数据**：`term_entries` 的 `system` / `locale` 级是内置称谓包（`BUILTIN_SYSTEM_TERMS`、`BUILTIN_LOCALES`），不含任何 `user_id` / `account_id`。因此它不会触碰 D2 的硬约束，是唯一可以零隐私风险接入的类别。
2. **闭合一个真实的空洞**：`scope='public'` 在生产恒为空。接入后该分支第一次有真实数据，`_ELIGIBILITY_SQL` 的 public 分支从「永不命中」变成「可验证」。
3. **验证完整合同路径**：新 `source_type` 需要走 chunk 物化、`index_version`、FTS 写入、revision 冲突、撤权、`rebuild_index`——这条路径目前只有 `memory` 走过。用一个无风险类别先把它走通，再谈 `authorized_document` 这类有授权语义的类别。
4. **走生产执行路径**：必须在 seed 或称谓包版本升级命令里被调用。只在测试里调用等于复制 `ingest_authorized_document` 的现状。

**诚实标注**：本切片的**检索质量价值有限**——assistant 已有 `get_term_alternatives` 与确定性称谓解析器，称谓知识主要是确定性查表，不是语义检索问题。它的价值在于**闭合 `public` scope 与验证接入合同**。不得把「`public_kinship` 召回率提升」写成检索能力的提升。

**`public` 继续不对 steward 开放**：`_ELIGIBILITY_SQL` 的 public 分支由 `:is_assistant = 1` 控制。steward 的称谓能力由 `steward_terminology` + 确定性解析承担（见 project memory #330），不需要 RAG 侧的公共知识。本任务**不改**这个边界，并在测试里对「把 `public_kinship` 加入 steward 可读集后仍不可读」做反向断言。

## D5 steward 可读集二维化，且新增类别默认拒绝

现状：steward 可读集是一维 `scope` 集合（平台 ∩ 空间 ∩ env 上界）。一旦 `public_kinship` 或 `authorized_document` 落地，「允许 `household`」就会同时放开该 scope 下的**所有** source_type——粒度不对，且默认是**放开**。

改为 `(scope, source_type)` 二维集合，并写死默认方向：

- 默认值**显式枚举**，不得由「`scope` 放行了就把该 scope 下所有类别放行」推导；
- 新增 `source_type` 的默认值是**不可读**；
- 有效集 = env 上界 ∩ 平台列 ∩ 空间列（交集，任一为空即整体为空，保持既有语义）；
- 空集时工具不广告、不注册进 run allowlist；检索入口返回 **403**，不是空列表。

`search_rag` 已有 `scope_allowlist` 与 `source_types` 两个参数，二维化的改动落在**配置形状与默认值**上，不落在检索 SQL 上——这是选择这个改法的原因：检索层不需要新代码，只有配置与门禁需要。

**反证**：把一个新的 source_type 加入平台配置而不加入空间配置 → 有效集为空、工具不广告；加入两侧但不加入默认枚举 → 仍不可读。

## D6 字段级 mask 无法作用于自由文本（硬约束）

`memory` 类的正文是用户确认的自由文本。自由文本里可能有健康、地址、学校、联系人——而 `HIGH_RISK_CATEGORIES` 在 RAG 侧**没有对应机制**：`sensitivity` 只有四档，`high` 的作用是把可读 scope 收紧到 `private`（`_restrict_scopes`），不是遮蔽字段。

因此本任务登记一条硬约束，而不是实现一个 mask 引擎：

> 含敏感字段的数据**不得**以未遮蔽的自由文本进入 RAG。若某类来源的正文可能含敏感字段，则它的写入方必须在**写入时**完成遮蔽，或在授权时提供段落/字段级粒度。

这条约束直接决定 `authorized_document` 的合同形状：附件授权若只能整篇授权，则等价于把原文交给该空间的所有 active 成员。因此 `authorized_document` 的落地需要**段落级授权记录**，本任务只登记该要求，不实现（其授权 token 语义与 `controlled_web` 的已批准 token 类似，但面向空间成员而非单次出网，需要独立任务）。

## 与 pgvector / 词法迁移的接缝

- `10-03-pgvector-rag` 已实测：**索引不承载授权**，可见性完全由查询层过滤承担；ANN 必须 **filter-then-ANN**。本任务新增 `public_kinship` 时，它进入 `_ELIGIBILITY_SQL` 的同一段过滤，**不需要**新增过滤条件（`public` 分支已存在），但必须验证向量路径的绑定参数齐全——`§10` 已记录「漏传绑定参数 → `StatementError` → 静默回退词法」这个失败形态。
- `10-04-lexical-search-migration` 定了 PGroonga 为词法主路径。`public_kinship` 的正文是中文称谓，正好落在 CJK 词法能力上，是该迁移的天然回归样本。
- **证明门顺序**：先扩 golden set（R5），再灌数据（R3）。`memory-eval-v1` 的 fixture 里没有 `public_kinship` 用例，先灌数据等于让新来源在无基线的情况下进入上下文。

## 分层预算的标注

`SOURCE_TIER_FRACTIONS` 保持五类不动（份额集合的语义是「各类别各自上限」，且「任何单一类别不可能占满预算」是结构性保证，不由活跃类别数决定）。但必须在矩阵文档与代码注释中标注哪几条是**预留位**：`family_story` / `authorized_document` / `profile` 当前无写入方。

不把预留份额删掉：删掉会让「未登记类别落到 `DEFAULT_TIER_FRACTION`」重新变成静默行为，而那正是 `test_context_tier_budget.py` 的断言要防的。

## 测试设计要点

| 断言 | 反证（去掉断言必须失败） |
|---|---|
| `INDEXED_SOURCE_TYPES` ∪ `UNINDEXED_SOURCE_TYPES` == `RAG_SOURCE_TYPES` | 加一个假类别 → 失败 |
| `public_kinship` 文档 `author_account_id IS NULL` 且正文无账号 id | 故意写一个带 author 的 public 文档 → 失败 |
| `public_kinship` 走完 revision 冲突 / 撤权 / `rebuild_index` | 按 id 直读证明行仍在、`status='revoked'`（可见性由查询层承担） |
| steward 默认不可读 `public_kinship` | 把类别加入两侧配置但不加入默认枚举 → 仍不可读 |
| 配置不允许时 403 而非空列表 | 断言错误码，不是断言 `== []` |
| 向量路径绑定参数齐全 | 去掉 `now` → 失败（`§10` 已记录的失败形态） |

## 已知风险

1. **`public_kinship` 的 `source_revision` 锚点选择**：称谓包没有天然的 revision 列。设计取「包版本」为 `source_revision`，需在实现时确认 `term_entries` 是否有可用版本信号；若没有，必须新增一个显式的包版本常量，而**不能**用 `count(*)` 或 `max(updated_at)` 这类不稳定信号（它们会让 revision 抖动，触发不必要的重建与冲突）。
2. **`public` 分支对 steward 恒假**：`public_kinship` 落地后 steward 仍读不到它。若将来要求 steward 读公共称谓知识，那是一次**独立的边界变更**，需要重新论证 `:is_assistant = 1` 这个门——不得在本任务顺手放宽。
3. **二维配置的迁移**：既有配置值是一维 `scope` 集合，升级后语义必须**收窄而非放宽**。升级路径要保证「旧值 → 新值」不引入新的可读类别（即旧值只能映射到已显式枚举的 `(scope, source_type)` 组合）。
