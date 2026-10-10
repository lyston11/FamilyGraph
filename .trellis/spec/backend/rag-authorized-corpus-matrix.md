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
| `authorized_document` | `attachments` + 授权记录 | private / household / lineage | 用户对某附件显式授权 | 附件 revision + 授权记录 revision | **仅登记** |
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
INDEXED_SOURCE_TYPES   = ("memory", "public_kinship")          # 有真实写入方
UNINDEXED_SOURCE_TYPES = {"family_story": "<理由>", "authorized_document": "<理由>", "profile": "<理由>"}
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

这条约束直接决定 `authorized_document` 的合同形状：附件授权若只能整篇授权，等价于把原文
交给该空间的所有 active 成员。因此它需要**段落级授权记录**，那是独立任务。

## 7. 分层预算的预留位

`SOURCE_TIER_FRACTIONS` 保留五类份额，但**未索引类别的份额是预留位**，不是已实现能力。
不删预留份额：删掉会让未登记类别重新落到 `DEFAULT_TIER_FRACTIONS` 的静默行为，而那正是
`test_context_tier_budget.py` 的断言要防的。

份额集合的结构性保证（任何单一类别不可能占满预算、空类别不借出份额）**不由活跃类别数决定**，
因此不需要随 `INDEXED_SOURCE_TYPES` 调整。

## 8. steward 可读集是二维的，且新增类别默认不可读

`steward_memory.STEWARD_READABLE_SOURCE_TYPES` 是显式枚举（当前 `("memory",)`）。
**不从 `scope` 推导**：放开一个 scope 时静默放开该 scope 下的全部类别，默认方向是
**放开**——那是错的方向。

两层各自独立挡住 `public`：

1. `scope_allowlist` 的值域是 `MEMORY_SCOPES`（private/household/lineage），
   `public` **无法被表达**（写入即 422）；
2. `_ELIGIBILITY_SQL` 的 public 分支由 `:is_assistant = 1` 控制。

`public` 分支继续不对 steward 开放（无限制公开材料不对管家开放）。要求 steward 读公共
称谓知识是一次**独立的边界变更**，必须重新论证这个门，不得顺手放宽。

## 9. Required validation

```bash
cd backend && pytest tests/test_rag_source_type_registry.py tests/test_rag_public_kinship.py \
    tests/test_steward_memory_tool.py tests/test_steward_memory_scopes.py \
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
| 公共索引在**生产路径**上被建立 | 删掉 `terms.seed_builtin_packs` 的调用 |

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

# 5. 类别是显式枚举，新增类别默认不可读
STEWARD_READABLE_SOURCE_TYPES: tuple[str, ...] = ("memory",)
```
