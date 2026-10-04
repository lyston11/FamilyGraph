# PostgreSQL 迁移方案决策（2026-10-04）

## 结论

不能把 SQLite 的 `BEGIN IMMEDIATE`、FTS5 或简单 `FOR UPDATE SKIP LOCKED` 逐字翻译到 PostgreSQL。正确方案是把它们拆成三种不同的并发/检索合同：

1. **状态转换**：条件 `UPDATE`/版本 CAS；不先 SELECT 再写。
2. **自然键创建与跨行不变量**：锁定已有协调行；没有协调行时用事务级 advisory lock 创建它，随后由唯一约束兜底。
3. **租约调度**：先锁定持久化 capacity counter，再用 `SKIP LOCKED` 取候选；`SKIP LOCKED` 只解决候选行争抢，不负责租户配额。

RAG 采用：

- PostgreSQL 主表继续保存 source/document/chunk/revision/scope/visibility/citation；
- 中文/日文词法检索不以 `tsvector` 或 `pg_trgm` 单独替代 FTS5 trigram；第一候选是 PostgreSQL 的 PGroonga 扩展；
- pgvector 负责语义候选，不能替代词法检索；最终查询做授权过滤后的 hybrid retrieval；
- 如果部署不能接受 PGroonga，再实现应用维护的 Unicode n-gram 倒排表作为可移植后备，而不是直接降低为无索引 `ILIKE`。

## 一、18 处 `BEGIN IMMEDIATE` 的处理分类

### A. 单行状态转换：改为 CAS

适用于 lease 状态、取消、过期、版本推进等已有唯一行：

```sql
UPDATE ...
SET status = :new_status, version = version + 1
WHERE id = :id
  AND status = :expected_status
  AND version = :expected_version;
```

检查 affected rows；0 行表示竞争失败，重新读取并按既有终态优先级裁决。无需 `SERIALIZABLE`，也不要用 SELECT 结果在事务外继续写。

### B. 已有父资源上的 check-then-act：锁父/协调行

空间、账户、session、transfer 等已有稳定父行先：

```sql
SELECT id FROM resource WHERE id = :id FOR UPDATE;
```

然后在同一短事务中读资格、写关系/状态。锁顺序固定为 `global → kind → account/space → child`，避免死锁。网络、模型流和工具外部执行不进入事务。

### C. 自然键可能尚不存在：advisory lock + 唯一约束

对 `(space_id, relation_type, target_id)` 等没有可锁父行的自然键：

1. 对规范化后的自然键计算固定 64-bit hash；
2. `pg_advisory_xact_lock(namespace, hash)`；
3. 再查/插入；
4. 最终由唯一索引兜底。

advisory lock 只作为并发窗口保护，不能代替唯一约束，也不能存放业务终态。事务结束自动释放。

### D. 复杂多表不变量：优先协调行，最后才用 SERIALIZABLE

配额、租约、跨表授权这种可表示为资源计数的场景用持久化 counter row；不能安全分解的极少数事务才使用 `SERIALIZABLE` + 有界重试。不能把所有 18 处统一提高隔离级别，因为会制造无界 serialization failure 和吞吐下降。

## 二、租约与租户并发上限

### 错误方案

```sql
SELECT candidate
FROM attempts
WHERE status = 'reserved'
  AND tenant_active_count < tenant_limit
FOR UPDATE SKIP LOCKED;
```

在 READ COMMITTED 下，多个事务可以看到同一个旧计数并同时通过；实测 5 个 worker 将同一租户上限 2 扩大到 5。`SKIP LOCKED` 只避免同一 candidate 行重复，不保护租户计数。

### 正确方案：持久化 capacity counter

新增/原型化：

```text
agent_capacity_counters
  scope_kind: global | agent_kind | account | space | upstream
  scope_id
  resource_kind: assistant | steward | tool | provider
  capacity
  active_count
  version
```

租约事务：

1. 按固定顺序 `SELECT ... FOR UPDATE` 锁 global/kind/tenant counter；
2. 检查并递增 `active_count`；
3. `SELECT ... FOR UPDATE SKIP LOCKED` 取一个候选 attempt/run；
4. 候选不存在则回滚 counter 增量；
5. 写入 lease owner/until/status，提交；
6. settle、cancel、recovery 只在同一 counter 合同下递减一次，使用 lease/grant id 幂等。

`SKIP LOCKED` 负责多 worker 取不同候选；counter row 负责每租户/global 配额。租户之间只锁各自 counter，不因空间 A 的锁阻塞空间 B。若全球 counter 成为瓶颈，后续再按 shard 分片，但不能删除持久配额真相。

### 公平性

不能依赖进程内 aging 队列作为最终实现。PostgreSQL 真源后，ready attempt 持久化 `tenant_key`、`ready_at`、`priority` 和公平调度字段；调度器在短事务中选择有效等待时间最长或 virtual-finish 最小的租户，再在该租户内取候选。计数锁与候选锁顺序固定，并用多租户并发测试证明既不重复租赁也不饿死。

## 三、FTS5 trigram 的迁移决策

现有实现不是普通英文全文搜索，而是：

```text
SQLite FTS5 virtual table
tokenize='trigram'
MATCH
bm25
```

它还配有 `ILIKE`/LIKE 后备和固定候选预算。PostgreSQL `tsvector` 不能无损替换中文字符级 trigram；`pg_trgm` 的 GIN/GiST similarity/LIKE 语义也不是 FTS5 trigram + bm25 的同一合同。尤其不能把中文质量问题隐藏在 schema migration 中。

### 首选：PGroonga 扩展

在 PostgreSQL 集群安装 PGroonga，建立独立的 `pgroonga` lexical index。它仍然以 PostgreSQL 表为事实来源，不是第二数据库；索引可重建，授权和 citation 仍由 SQL 主查询裁决。

查询抽象保留 `RAGSearchProvider` 接口：

```text
authorized source/chunk filter
  → lexical candidate (PGroonga)
  → vector candidate (pgvector, optional)
  → union + deterministic rerank
  → final visibility/scope/revision/citation validation
```

PGroonga 是否作为默认部署能力，必须通过中文/英文/混合文本基准、撤权、更新和备份恢复验证。不能因为扩展安装方便就跳过质量基准。

### 可移植后备：应用维护 Unicode n-gram 倒排表

如果不能依赖 PGroonga，维护：

```text
rag_chunk_ngrams(chunk_id, index_version, ngram, positions/count)
```

对 NFKC 后的 Unicode code point 生成 2/3-gram，查询把输入切成 n-gram，按命中覆盖度/位置计算确定性分数。`ngram` 建 B-tree/Hash 索引，chunk 的 revision/status/scope 仍由主表过滤。它的成本是写放大和索引膨胀，必须用规模基准决定是否可接受。

短文本不得直接退化为全表 `ILIKE`；无可用索引时应返回 bounded lexical fallback 或安全的“检索不可用”，不能在生产隐式扫描整个家庭数据集。

### pgvector 的位置

pgvector 只增加语义候选，不取代 lexical path：

- embedding row 绑定 chunk/revision/index_version/scope/visibility policy；
- 查询先确定授权过滤，再执行向量候选；必要时 over-fetch 后做最终授权过滤；
- lexical 与 vector 结果 union 后按确定性规则 rerank；
- RAG 关闭、embedding 失败、索引不可用时回退既有 lexical/确定性路径，不伪造向量命中。

## 四、迁移路线调整

不执行“把全部历史 Alembic 逐个在空 PostgreSQL 上重放”的机械路线，也不执行无停机双写作为第一步。SQLite 迁移应采用：

1. **PG baseline schema**：审查后的显式 PostgreSQL DDL，涵盖全部表、FK、CHECK、唯一索引、partial index、agent counter、RAG search provider 元数据；不使用 `create_all` 代替迁移。
2. **隔离快照**：按项目备份命令生成静态 SQLite snapshot，停止该 snapshot 的写入；不复制运行中的主库文件。
3. **staging/import**：保留 ID、时间、revision、状态和 FK；先导入 staging，再按依赖顺序 COPY/INSERT 到正式表，最后 setval sequence。
4. **拒绝式对账**：行数、每表摘要、关键唯一键、授权 scope、run↔attempt、lease 终态、egress 一次一审计、RAG revision/citation、FTS/vector 可重建状态全部核对；任何差异不切 writer。
5. **开发停机切换**：停止 dev API/agent，做最终备份与短写冻结，导入差异/核对，切换 `DATABASE_URL`，启动并观察；不让 SQLite 与 PG 同时裁决 lease/settle。
6. **shadow read 后再扩大**：先只读对照关键 projection/search/agent control，再切 internal control-plane，最后切家庭读写；每阶段保留旧 SQLite snapshot 和 PG backup。
7. **PG 成为唯一 writer 后**：SQLite 只保留导出/回滚窗口，禁止回写 SQLite 形成双主；回滚只回流量和配置，不把 PG 新状态盲目写回 SQLite。

## 五、必须先做的验证

1. 18 处 `BEGIN IMMEDIATE` 逐项映射表：每一处标记为 CAS、父行锁、自然键 advisory lock、counter row 或 SERIALIZABLE+retry，并为每处写并发回归。
2. counter + candidate lease 的真实 PostgreSQL 多连接测试：不同租户并发、同租户竞争、租约取消、进程崩溃、重复 recovery。
3. FTS5 golden corpus：中文、英文、混合、短词、同义/substring、标点、NFKC、撤权、revision 更新；对比 PGroonga、pg_trgm/ILIKE、Unicode n-gram 和 pgvector hybrid 的 recall@k、precision@k、p50/p95、索引大小、写入成本。
4. PG baseline 的迁移往返、备份恢复、sequence/FK/CHECK/partial index、run/attempt/fence/settle/RAG 对账。

在这些验证完成前，不切开发 writer，不宣布 SQLite 已退出，也不引入独立向量数据库。
