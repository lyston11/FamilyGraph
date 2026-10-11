# RAG 授权语料矩阵（(source_type × scope) 合同）

2026-10-10 建立。本文是**新增 RAG `source_type` 的唯一入口**：任何类别在进入索引前，
必须在下面的矩阵里有一行，且该行的四要素齐全。

Assistant 的执行身份、引用读取与分层预算见 [执行合同](memory-rag-execution-contract.md)；
索引物化、换版与撤权见 [索引生命周期合同](rag-index-lifecycle-contract.md)。

## 1. 适用范围

适用于新增/删除 `RAG_SOURCE_TYPES`、改变某个类别的 `scope` 值域、给某类别接写入方、
或调整 steward 可读的记忆类别。**不适用于** assistant 的记忆确认流程本身（见
[memory-contract.md](memory-contract.md)）。

## 2. 矩阵（四要素缺一不可）

| source_type | 真源 | 允许 scope | 写入触发 | 生命周期锚点 | 状态 |
|---|---|---|---|---|---|
| `memory` | `memories` | private / household / lineage | 候选确认（`confirm_candidate`） | `Memory.revision`、`superseded_by_id`、`valid_to`、`status` | 活跃（既有） |
| `public_kinship` | `term_entries` 的 `system`/`locale` 级 | public | 内置称谓包 seed / 维护循环补建 | 包内容的 sha256 派生正整数 | 活跃（2026-10-10） |
| `authorized_document` | `attachment_content_versions` + `agent_content_grants` | household | 附件本人在某家庭空间显式授权 | 内容版本 `revision` + 授权 `authorization_revision` | 活跃（2026-10-11，见 §6.1） |
| `family_story` | 尚不存在 | household / lineage | 待产品定义 | 待定 | **仅登记** |
| `profile` | `users` 档案字段 | ——（不索引） | —— | —— | **明确排除** |

### 2.1 四要素的定义

- **真源**：正文来自哪张表。索引是**可重建派生物**，真源状态是授权与可见性的唯一依据。
- **允许 scope**：该类别可以写哪些 `scope` 值。值域外一律 422，不静默纠正。
- **写入触发**：生产执行路径上的哪个函数/命令会调用写入方。**只在测试里被调用等于没接**——
  `ingest_authorized_document` 的现状就是这类缺陷的样本。
- **生命周期锚点**：`source_revision` 从真源的哪个字段来。锚点必须**随内容变化且只随内容变化**：
  用 `count(*)` / `max(updated_at)` 会让版本号与内容无关地抖动，触发无意义的重建与冲突。

## 3. `scope` 与可见性四级阶梯的对应

`RAGDocument.scope` 与 `visibility` 的四级**不是同构集合**：

| RAG `scope` | `visibility` 层级 | 判据 |
|---|---|---|
| `private` | `self_private` | `d.author_account_id = :private_reader_account_id`（**读者**，不是 kind） |
| `household` | `household_detail` | 空间 active 成员 + `:allow_household` |
| `lineage` | `lineage_summary` | 空间 active 成员 + `:allow_lineage`（`PURPOSE_RAG` 的上限即此层） |
| `public` | **无对应层级** | `:is_assistant = 1`（不是可见性判定） |

两个集合的差集由两条规则闭合：

1. `scope` 没有 `none`：不可见的东西**不索引**（写入侧保证，检索侧不重建这个语义）；
2. `visibility` 没有 `public`：**含个人数据的类别不得写 `scope='public'`**。

### 3.1 `public` 的定义是「不含个人数据」

`public` **不是**「比 `lineage` 更宽的一层可见性」。按后者理解会直接推导出「把
`household` 档案投影到 `public`」这种灾难性结论。它的正确语义是写入方的**声明**：

> 本文档不含任何个人数据，因此不需要可见性判定。

这条声明必须是**可测的**，而不是约定。`public` 文档必须满足：

- `space_id IS NULL`（`ck_rag_documents_scope_space` 已强制）；
- `author_account_id IS NULL` 且 `owner_user_id IS NULL`（没有任何可归属的读者身份）；
- 正文**只由受控模板产出**，不含任何自由文本字段——测试用「字符允许集」断言，
  而不是「不含某个具体字符串」。

反例（必须失败）：给 `public` 文档写一个 `author_account_id`，或让 `render_*` 拼进一个姓名。

## 4. 「声明集」与「活跃索引集」必须分开登记

```python
# app/models/rag.py
RAG_SOURCE_TYPES       = ("memory", "family_story", "authorized_document", "profile", "public_kinship")
INDEXED_SOURCE_TYPES   = ("memory", "authorized_document", "public_kinship")   # 有真实写入方
UNINDEXED_SOURCE_TYPES = {"family_story": "<理由>", "profile": "<理由>"}
```

为什么需要这份重复登记：`SOURCE_TIER_FRACTIONS` 的既有断言（「所有 `RAG_SOURCE_TYPES`
都已登记」）只检查份额存在，**不检查写入方存在**。因此四类空转既无文档也无测试保护，
任何人加一个 `source_type` 都会得到一个静默空转的枚举值。

断言（`tests/test_rag_source_type_registry.py`）：两侧互斥、并集等于 `RAG_SOURCE_TYPES`、
每个未索引类别有非空理由、活跃类别有 tier 份额。**必须有反证**（构造一个未登记的类别，
断言失败），否则一致性断言只是同义反复。

**不删枚举值**：`RAG_SOURCE_TYPES` 参与 `ck_rag_documents_source_type` 的 CHECK 约束，
删除需要 SQLite 整表重建（复制全表），收益不抵迁移风险，且会让历史数据的值不可读。

## 5. `profile` 为什么**不**进 RAG 索引

`visibility.evaluate(viewer, target, purpose)` 的输出**依赖 viewer**（level 与字段 mask
都随读者变化），而 `RAGChunk` 是**一份共享文本**。两者形状不兼容。

| 方案 | 成本 |
|---|---|
| 按 (viewer, target) 生成 | `N × M` 组合爆炸；披露偏好变更要重建全量 |
| 按 level 分档生成 | `N × 3`，但披露类别与未成年人 overlay 还会再分档；档位数不定 |
| **不索引**，按 viewer 现算 | 无索引；代价是 profile 不能按语义检索 |

**取第三种。** profile 是**小规模、强结构化、逐 viewer 求值**的数据，它的正确访问方式
是按 purpose 现算（`familygraph.get_profile_summary` 已是这条路径的实现）。前两种方案
都会在**披露偏好变更**时留下「索引里的旧档位仍可见」的窗口，而披露变更正是最不能有
窗口的操作。

## 6. 字段级 mask 无法作用于自由文本（硬约束）

`memory` 类的正文是用户确认的自由文本，可能含健康、地址、学校、联系人——而
`HIGH_RISK_CATEGORIES` 在 RAG 侧**没有对应机制**：`sensitivity` 只有四档，`high` 的作用是
把可读 scope 收紧到 `private`，不是遮蔽字段。

> **含敏感字段的数据不得以未遮蔽的自由文本进入 RAG。** 若某类来源的正文可能含敏感字段，
> 其写入方必须在**写入时**完成遮蔽，或在授权时提供段落/字段级粒度。

这条约束对 `authorized_document` 的推论有两半，**不要只记一半**：

1. **授权粒度必须由用户明示，不能由系统推断**——所以真源不是附件本身，而是一张独立的
   授权记录（`agent_content_grants`）。用户授权的是**某一份已确认的内容版本**，不是「这张
   照片」这个模糊对象。
2. **整份授权是合法的 MVP 形态，不是缺陷**。早期本文把「段落级授权」写成所有文档的强制
   前置，那是错的：它会让功能不可用，而用户对一份自己刚预览过的文字选择「整份允许」是
   完全明示的决定。段落级是**后续扩展**，且一旦支持就必须绑定不可变片段（见 §6.2）。

代价必须如实说明：整份授权 = 该内容对该空间所有 active 成员可检索。因此 MVP 只允许
`household`（不允许 `lineage`），因为 `household_detail` 的可见性形状与 RAG 的空间成员
过滤同构；`lineage` 还要叠加披露类别、直系结构边、pending 最小互见与未成年人 overlay，
把它转成检索前可执行的 SQL 授权集合是独立工作量。

### 6.1 附件（`attachments`）如何进入 RAG（2026-10-11 起）

早期结论是「附件不进 RAG」。2026-10-11 逐项核实后**修正**为：附件**不直接**进 RAG，
而是通过「内容版本 + 显式授权」两个真源进入。当初登记的三条阻塞点，处理方式各不相同：

| # | 当初的事实 | 现在的处理 |
|---|---|---|
| 1 | 附件没有 `space_id` 列 | **不加**。空间属于**授权**，不属于附件：同一张照片可以在空间 A 被授权、在空间 B 不被授权，照片仍然只有一份 |
| 2 | 没有资源级授权记录 | 新增 `agent_content_grants`（内容版本 × 空间 × Agent × 用途 × 授权人 × 撤销） |
| 3 | 没有可索引正文 | 新增不可变的 `attachment_content_versions`；MVP 只收**用户明确提交并确认的手工文字** |

**为什么不能把附件直接投成 `RAGDocument`**：附件的读取判据是
`visibility.evaluate(viewer, target)`——它依赖**谁在问**（层级 + 归属者披露开关 + 未成年人
overlay）。一份共享 chunk 不能承载逐 viewer 求值的授权。这与 `profile` 是同一个问题，
而解法也一致：真源保存**已确认文本**，授权在检索时逐请求复核（见 §6.3）。

**MVP 边界（刻意收窄）**：`household` 空间 / `assistant` / 整份内容 / 已认领档案本人授权。
不支持：`lineage`、Steward、PDF、自动 OCR、外链抓取、选段。

### 6.2 内容版本不可变，且授权不自动继承

`attachment_content_versions` 的正文**一旦产生就不可原地修改**：改文字产生新 `revision`，
旧版本标 `superseded`。理由是 citation 指向 `(attachment, version, grant)` 三元组，原地改写
会让旧引用指向新内容。

**旧版本的授权不自动转移到新版本**：新版本可能包含更多内容，也可能把用户刚删掉的敏感
文字带回来。因此流程固定为「生成 → 预览 → 确认 → 另行授权」，三段互不蕴含。

将来支持选段时，必须绑定**不可变片段**（`start_offset`/`end_offset` + 该片段文本的哈希），
不能只存「第 3～5 段」这种会随正文漂移的范围。

### 6.3 检索期的授权是三重交集，且撤销先于清理

```text
可被 Agent 检索
= 当前 viewer 在当前空间可查看该附件（复用 attachment_access 投影）
∩ 内容版本 status = confirmed
∩ grant 仍 active 且 (agent_kind, purpose, space_id) 匹配
```

三条同时成立，缺一不可。实现上：

- **SQL 层**：`_ELIGIBILITY_SQL` 增加 `authorized_document` 的 `EXISTS(grants ...)` 分支，
  与 `memory` 分支并列。因此**候选收集阶段**就已经排除未授权文档——不是先全库召回再在
  应用层隐藏（后者会让候选数、排序与 RAG 预算受未授权内容影响）。
- **投影层**：`memory_sources._authorized_document_readable` 再复核一次 grant、内容版本、
  附件与当前 viewer 的附件可见性。两层各自独立挡住，任何一层失效都不得放行。
- **撤销**：`revoke_grant` / 内容被取代 / 附件删除都先 `invalidate_source` 打墓碑
  （查询立即不再匹配），再等维护循环物理清理。**不能等清理完成才生效。**

已经进入模型上下文的内容无法收回，产品不得承诺「撤销后模型从未看到过」。

### 6.4 为什么 `MEMORY_TOOL_SOURCE_TYPES` 必须显式扩大

助手工具默认只读 `memory`。接入 `authorized_document` 是一次**显式的代码改动**
（`("memory", "authorized_document")`），不是让 `source_types=None` 的「读全部」默认生效。
Steward 仍为 `("memory",)`。

## 7. 分层预算的预留位

`SOURCE_TIER_FRACTIONS` 保留五类份额，但**未索引类别的份额是预留位**，不是已实现能力。
不删预留份额：删掉会让未登记类别重新落到 `DEFAULT_TIER_FRACTIONS` 的静默行为，而那正是
`test_context_tier_budget.py` 的断言要防的。

份额集合的结构性保证（任何单一类别不可能占满预算、空类别不借出份额）**不由活跃类别数决定**，
因此不需要随 `INDEXED_SOURCE_TYPES` 调整。

## 8. 两侧的可读集都是二维的，且新增类别对两侧都默认不可读

### 8.1 steward

`steward_memory.STEWARD_READABLE_SOURCE_TYPES` 是显式枚举（当前 `("memory",)`）。
**不从 `scope` 推导**：放开一个 scope 时静默放开该 scope 下的全部类别，默认方向是
**放开**——那是错的方向。

两层各自独立挡住 `public`：

1. `scope_allowlist` 的值域是 `MEMORY_SCOPES`（private/household/lineage），
   `public` **无法被表达**（写入即 422）；
2. `_ELIGIBILITY_SQL` 的 public 分支由 `:is_assistant = 1` 控制。

`public` 分支继续不对 steward 开放（无限制公开材料不对管家开放）。要求 steward 读公共
称谓知识是一次**独立的边界变更**，必须重新论证这个门，不得顺手放宽。

`authorized_document` 同样不在 steward 的元组里：**Steward 不读附件授权文档**。这不是
遗漏——附件授权的授权人明确选择的是「助手」，把同一份内容同时交给第二个 Agent 会让
「我授权给谁」这个问题失去意义。

### 8.2 assistant（`search_memory` 工具）

`agent_tools.MEMORY_TOOL_SOURCE_TYPES` 同样是显式枚举，当前为
`("memory", "authorized_document")`——2026-10-11 接入附件授权文档时**显式**扩大了这一行。

这一侧曾经**没有**这个维度，后果是实测到的：`_search_memory_tool` 调 `search_rag` 时未传
`source_types`，而 `source_types=None` 的语义是「读全部类别」，于是 2026-10-10 接入
`public_kinship` 后，名为「记忆」的工具开始返回空间外的公共称谓语料
（实测 citation：`rag:term-pack:zh-CN:r247349778:c5`）。`authorized_document` /
`family_story` 落地时会以同样方式静默继承。

**助手多出的能力来自工具，不是来自更宽的 RAG 读取面。** 助手另有六个只读领域工具
（`get_profile_summary` 等）与两个受控 Web 工具，但 RAG 类别维度上仍是逐类枚举。
新增 `source_type` 要放开给任一侧，都必须改该侧的类别元组——一次显式、可评审的代码
改动，而不是一次配置写入。扩大助手可读类别的**唯一**正当理由是「有对应的显式授权
真源」：`authorized_document` 有 `agent_content_grants`，`public_kinship` 没有，
所以后者对 `search_memory` 与 steward 都不可读。

**2026-10-12 起两侧的类别元组都是 §8.3 策略表的别名**；那一节说明为什么必须有这张表、
以及它修掉了一个 steward 上下文比 steward 工具读得更宽的真实缺陷。

反证：`tests/test_memory_tools.py::test_search_memory_does_not_read_other_source_types`
同时断言「限定后结果为空」与「不限定则确实召回公共语料」，证明差别承重而非同义反复。

### 8.3 消费者来源策略：唯一真源 + 按 kind 派生（2026-10-12 已解决）

**唯一真源是 `app/models/rag.py` 的 `RAG_CONSUMER_SOURCE_TYPES`。** 每个消费者显式列出
自己读哪些类别，**不从 `INDEXED_SOURCE_TYPES` 推导**：

| key | 值 | 消费者 |
|---|---|---|
| `assistant_context` | memory, authorized_document, public_kinship | assistant run 的 `ContextBuilder` |
| `steward_context` | memory | steward run 的上下文构建 |
| `assistant_memory_tool` | memory, authorized_document | `familygraph.search_memory` |
| `steward_memory_tool` | memory | `familygraph.steward.search_memory` |
| `browser_search` | memory, authorized_document, public_kinship | `GET /rag/search` |

`agent_tools.MEMORY_TOOL_SOURCE_TYPES` 与 `steward_memory.STEWARD_READABLE_SOURCE_TYPES`
是这张表的**别名**（名字保留给既有调用点与文档，值是单一真源）。

#### 被修掉的真实缺陷（不只是「缺一个显式声明」）

`CONTEXT_SOURCE_TYPES = None` 的语义是「全部已声明类别」，而它被**三个不同消费者复用**。
其中 `ContextBuilder.build()` 同时服务 assistant 与 steward（它有显式的 steward 分支），
却无论哪种 kind 都拿到同一份「全部」。于是 **steward 的上下文能读到 `authorized_document`**
——该类别的检索门是 grant 存在性，**没有** `:is_assistant` 门——比 steward 自己的工具策略
`("memory",)` 更宽。

这是**收窄**修正（越界内容消失），不是放宽。`tests/test_rag_context_consumer_sources.py`
同时断言 assistant 侧仍读得到，以证明「steward 读不到」不是「授权文档整体坏了」。

#### 为什么删除常量，而不是改成具体元组

`CONTEXT_SOURCE_TYPES` 这个名字描述单一消费者，却被三个消费者复用。保留一个语义含糊的
共享常量，等于把「复用错常量」这条路留着。删除后每个调用点必须写出自己是谁；上下文路径
改为 `context_source_types(agent_kind)`，未知 kind **fail closed**（不回退到最宽集合）。

#### 为什么 `browser_search` 是独立的键

它与 `assistant_context` 今天取值相同，但**消费者不同**：将来调整 agent 上下文的来源面
不应连带改变用户自己看到的搜索结果。跨消费者复用同一常量正是本节要消除的形态。

#### 入口层 fail closed

`RetrievalIdentity.source_types` 是**必填** `tuple[str, ...]`（无默认值、无 `| None`），
`retrieve()` 对运行时传入的 `None` 仍显式 422。理由：类型标注不约束运行时（未类型化的
调用点、`**kwargs` 组装、反射都能绕过），而这个默认值的失效后果是「静默读全部」。

#### 反证义务

`validate_consumer_source_types(policy)` 是对**数据字面量**的校验器；没有反证它只是同义
反复。`tests/test_rag_consumer_source_policy.py` 提供四条：

1. 伪造策略含 `family_story`（已声明但**未索引**）→ 必须失败；
2. 伪造策略含 `public_kin`（拼写错误/未声明）→ 必须失败；
3. 空 key / 空值 → 必须失败；
4. **承重**：把 `family_story` 加进 `INDEXED_SOURCE_TYPES` 后，所有消费者取值逐项不变
   ——直接证明「加进索引集不会放宽任何消费者」。若有人把策略表改成索引集的派生值，这条
   会立刻失败。

### 8.4 刻意的非对称：`public_kinship` 在上下文里、不在 `search_memory` 里

`search_memory` 的名字是「记忆」；返回空间外的通用称谓知识会误导模型把语料当成用户记忆
（2026-10-10 实测到 `term-pack:zh-CN` 出现在该工具结果里）。上下文构建是拼装材料，通用
知识作为背景是合理的。该非对称由 `test_rag_consumer_source_policy.py` 钉住。

## 9. Required validation
```bash
cd backend && pytest tests/test_rag_consumer_source_policy.py \
    tests/test_rag_context_consumer_sources.py tests/test_knowledge_retrieval_entry.py \
    tests/test_rag_source_type_registry.py tests/test_rag_public_kinship.py \
    tests/test_steward_memory_tool.py tests/test_steward_memory_scopes.py \
    tests/test_memory_tools.py \
    tests/test_rag_lifecycle_acceptance.py tests/test_context_tier_budget.py \
    tests/test_memory_eval_baseline.py
```

| 断言 | 反证（去掉断言必须失败） |
|---|---|
| 声明集 = 活跃集 ∪ 未索引集 | 加一个未登记类别 |
| `public` 文档三列为 NULL、正文受限字符集 | 写入一个带 author 的 public 文档 |
| 同 revision 异内容 → 409 | 伪造同 revision 的不同正文 |
| 撤权后查询不可见、行仍在 | 按 id 直读证明 `status='invalidated'` |
| 已 `index_superseded` 的公共文档不重新激活 | 手工置该状态后重跑写入方 |
| steward 读不到 `public_kinship` | 只放类别维度、scope 用合法值域 |
| 消费者来源策略自洽（值 ⊆ 活跃索引集） | 伪造含 `family_story` 的策略 |
| 加进 `INDEXED_SOURCE_TYPES` 不放宽任何消费者 | 扩展索引集后断言取值逐项不变 |
| steward 上下文读不到 `authorized_document` | 同一文档在 assistant 上下文里必须读得到 |
| `CONTEXT_SOURCE_TYPES` 不存在 | 断言 `not hasattr(knowledge_retrieval, ...)` |
| assistant 的 `search_memory` 也读不到 `public_kinship` | 去掉 `source_types` 参数（实测会返回 `rag:term-pack:zh-CN:...`） |
| 公共索引在**生产路径**上被建立 | 删掉 `terms.seed_builtin_packs` 的调用 |
| 附件授权文档：无 grant 不可召回、撤销后立即不可见、行仍在 | 直读 `status='invalidated'` 证明是查询层挡住而非删除 |
| 附件授权文档：管家读不到（grant 的 `agent_kind` CHECK 只允许 assistant） | 以 assistant 身份反证同一查询能召回 |
| `attachment:%:version:%:grant:%` 判别式精确（非前缀） | `attachment:1:dates`（档案派生形状）必须判为**非** grant 形状 |

## 10. Wrong vs Correct

### Wrong

```python
# 1. 把「public」当成更宽的一层可见性，把 household 档案投影进去
metadata = {"scope": "public", "author_account_id": account.id}   # 含个人数据却声明 public

# 2. 新 source_type 自带第二条索引路径
def index_my_thing(...):
    db.add(RAGDocument(...)); db.add(RAGChunk(...))               # 可见性合同要各自再证一次

# 3. 版本号取与内容无关的信号
revision = db.scalar(select(func.count()).select_from(TermEntry)) # 增删一条就变，触发重建

# 4. 只在测试里调用写入方
#    （生产路径上没有任何调用点 → 等于没接，见 ingest_authorized_document 的现状）

# 5. 用 scope 推导 steward 可读类别
source_types = ("memory", "family_story") if "household" in scopes else ("memory",)

# 6. 靠 `source_types=None` 的「读全部」默认（名为「记忆」的工具读到了公共语料）
hits = search_rag(db, ..., query=clean, agent_kind="assistant")  # 读全部类别

# 7. 一个消费者常量被别的消费者复用
CONTEXT_SOURCE_TYPES = None                       # 名字属于「上下文」
... = build(agent_kind="steward", source_types=CONTEXT_SOURCE_TYPES)   # 却按 assistant 的语义读全部
... = rag_search(source_types=CONTEXT_SOURCE_TYPES)                    # 浏览器端点也来复用
```

### Correct

```python
# 1. public 是无个人数据的声明，且可测
assert document.author_account_id is None and document.owner_user_id is None

# 2. 复用同一物化路径，可见性完全由查询层的 _ELIGIBILITY_SQL 承担
document, created = _canonical_document(db, metadata, target_version=version)
_materialize_chunks(db, document, text_value, revision, creating=created)

# 3. 版本号由内容派生，只随内容变化
revision = 1 + int.from_bytes(sha256(canonical_entries).digest()[:4], "big") % (2**31 - 1)

# 4. 接在生产路径上（seed / 维护循环），并断言它真的建了文档

# 5. 类别是策略表的别名，新增类别对两侧默认不可读
STEWARD_READABLE_SOURCE_TYPES = RAG_CONSUMER_SOURCE_TYPES["steward_memory_tool"]
MEMORY_TOOL_SOURCE_TYPES = RAG_CONSUMER_SOURCE_TYPES["assistant_memory_tool"]

# 6. 助手的能力来自工具，不来自更宽的读取面
hits = search_rag(db, ..., query=clean, agent_kind="assistant",
                  source_types=MEMORY_TOOL_SOURCE_TYPES)

# 7. 每个消费者从策略表取自己那一行；上下文按 kind 派生
source_types = RAG_CONSUMER_SOURCE_TYPES["browser_search"]
source_types = context_source_types(agent_kind)    # assistant → 宽；steward → ("memory",)
```
