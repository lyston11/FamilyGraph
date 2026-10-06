# Gate 1：RAG source/revision/scope/visibility/citation 与 lexical/vector 索引边界卡

## 为什么这是迁移的硬边界

RAG 的授权、来源身份与引用完整性**不能**随存储迁移而改变。当前实现把权威判据放在
SQL 主查询里（scope/status/confirmation/sensitivity 过滤 + `VisibilityPolicy`），
索引（FTS5 / 未来 PGroonga / pgvector）只是**可重建的派生物**。

迁移必须保持这条分层；任何「索引先查、授权后补」的改写都会把授权降级为事后过滤。

## 1. 权威表与不变量（来自 `app/models/rag.py`）

### `rag_documents`

| 列 | 约束 | 迁移影响 |
|---|---|---|
| `source_type` | CHECK IN (`memory`,`family_story`,`authorized_document`,`profile`,`public_kinship`) | 枚举必须逐字保留；PG CHECK 等价 |
| `status` | CHECK IN (`active`,`revoked`,`deleted`,`invalidated`) | 授权过滤依赖此列 |
| `sensitivity` | CHECK IN (`normal`,`sensitive`,`high`,`local_required`) | provider 策略依赖此列 |
| `confirmation_status` | CHECK IN (`confirmed`,`authorized`) | **只有这两种可检索** |
| `scope` | CHECK IN (`private`,`household`,`lineage`,`public`) | 与 `space_id` 联动 |
| `scope` × `space_id` | CHECK：`private`/`public` → `space_id IS NULL`；`household`/`lineage` → `space_id NOT NULL` | **复合 CHECK，必须双方言渲染** |
| `revision` = `source_revision` | CHECK `ck_rag_documents_revision_mirror` | 镜像不变量 |
| `(source_type, source_id, revision)` | **唯一索引** `ix_rag_documents_source` | 来源身份的唯一性根 |
| `(space_id, scope)` | 普通索引 | 授权过滤路径 |

### `rag_chunks`

| 约束 | 含义 |
|---|---|
| `(document_id, index_version, chunk_index)` 唯一 | 同一算法版本内块序号唯一 |
| `source_revision` | 块绑定到产生它的来源版本 |
| `content_sha256` | 完整输入的证据；NULL 表示尚无完整证据 |

**块的不可变性**：已有 `id`/`text`/`revision`/`version` 不原地改写，旧版本保留供
`ExactChunkRef` 精确读取（见 `rag-index-lifecycle-contract.md`）。

## 2. 授权判据（不可迁移的部分）

```text
1. SQL 层过滤：scope / status / confirmation_status / sensitivity
2. VisibilityPolicy：当前 viewer 对 source 的可读性
3. 最终响应只含可引用的 citation_handle
```

**硬约束**：

- `private` 记忆只有 Assistant 且作者本人可检索；Steward、其他账号、其他空间、未 active
  成员一律空结果；
- 撤权/删除/过期必须在**同一事务**中 tombstone 文档与块；**不依赖索引物理清理**才停止命中；
- 检索**不得**成为发现隐藏内容的路径（memory #325）。

## 3. 索引边界（可重建派生物）

| 索引 | 现状 | 迁移归属 |
|---|---|---|
| `rag_chunks_fts`（FTS5 `tokenize='trigram'`） | SQLite 虚拟表 + `MATCH` + `bm25()` | **无 PG 对等物**；归 `10-04-lexical-search-migration` |
| 词法后备（`LIKE ... ESCAPE '!'`） | 有界候选预算 200 | 语义等价，但**不是**主路径 |
| 向量（pgvector） | 尚未实现 | 归 `10-03-pgvector-rag`，**只增语义候选** |
| 5 个 `rag_*` 触发器 | SQLite 语法，**PG 上不成立** | 必须在 PG baseline 中重写（见 `trigger-inventory.json`） |

### 索引可重建性必须成立

迁移后必须能证明：**删掉全部索引并重建，检索结果与授权判据不变**。
这是「索引是派生物」的可执行定义。

## 4. 迁移期间必须保持的接缝

| 接缝 | 要求 |
|---|---|
| `ExactChunkRef` | `document_id/chunk_id/source_type/source_id/source_revision/index_version/chunk_index/content_hash` 全部保留；客户端**不能**自报 hash 获得认证 |
| `context_builds` | `account_id` 可空（steward 投影）；`policy_json`/`invalidated_at` 保留 |
| `agent_citations` | 公开字段由服务端生成；失权后不输出受限来源定位与摘录 |
| RAG 开关 | 部署 hard-off 与平台开关共同约束；**不因迁移放宽** |

## 5. 迁移期禁止事项

1. **不得**把授权过滤下移到索引层或让索引先行；
2. **不得**在迁移中替换词法算法（`tsvector`/`pg_trgm` 未经基准不得替换 trigram）；
3. **不得**把 `rag_*` 触发器静默丢弃（否则 revision 镜像与 FTS 同步失效）；
4. **不得**让向量候选绕过 scope/visibility/revision 校验；
5. **不得**因迁移而放宽「撤权后立即零命中」。

## 6. 未验证项（诚实声明）

- 5 个 `rag_*` 触发器的**具体语义**未逐条验证（只知它们使用 SQLite 语法、在 PG 上不成立）；
- `(source_type, source_id, revision)` 唯一索引在 PG 上的行为未做真实插入验证；
- `ck_rag_documents_scope_space` 复合 CHECK 的双方言渲染未验证（它是普通 CHECK，
  理论上双方言通用，但**未实测**）；
- 索引重建不变性（第 3 节）未做实验。

以上四项必须在写 PG baseline 时逐条验证，不能假定。
