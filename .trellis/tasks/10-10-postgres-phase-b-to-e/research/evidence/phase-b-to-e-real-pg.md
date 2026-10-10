# Phase B–E 在真实 PostgreSQL 上的验证结果（2026-10-10）

## 环境

服务器 `lyston` 上运行中的生产 PG 容器（`familygraph-prod-postgres-1`，
PostgreSQL 16.15 + pgvector + pgroonga），用**独立数据库**做验证，
不触碰生产库 `familygraph`：

```text
隔离库：fg_migration_proof / fg_iso_* / fg_diag*
DSN：postgresql+psycopg://...@172.28.0.5:5432/<隔离库>
schema：pg_schema_apply.py（94 表 / 260 索引 / 121 CHECK / 213 FK / 64 触发器）
```

## 探针结果（18 PASS / 0 FAIL）

| 探针 | 结论 |
|---|---|
| `pg_trigger_equivalents` | 69 个 SQLite 触发器的 PG 等价物全部建立（66 个触发器 + 函数） |
| `pg_trigger_negative_tests` | **13/13**：正向、负向、反证（删触发器后行为改变）双向验证 |
| `pg_baseline_prototype` | 四类语义等价物（scope-immutable/append-only/conditional/sticky/counter）+ 反证 |
| `pg_baseline_guards_probe` | refusal 顺序、中断回滚、重复幂等、审计 exactly-once |
| `pg_schema_apply` | 完整 schema 幂等应用（含元数据外对象：writer_state / gate 列 / PGroonga 索引） |
| `pg_baseline_build` | 94 表 / 260 索引 / 121 CHECK / 213 FK / 16 局部索引（谓词保留） |
| `pg_capacity_concurrency` | **反证成立**：朴素计数子查询越限 5/5，counter 版 2/2 正确；归还恰好一次；反向锁序真死锁 |
| `multitenant_acceptance` | 租户/集群/流三层配额守恒，归还不泄漏 |
| `cross_instance_capacity` | 集群级上限跨实例生效 |
| `pg_fault_injection` | 六类故障（提交前/后断连、重复 settle、cancel vs settle、崩溃恢复、序列化冲突）全部按预期收敛 |
| `pg_three_lock_probe` | 四把锁按冻结顺序无死锁；**反向顺序确实死锁**（证明顺序承重）；跨租户不阻塞 |
| `pg_deadlock_probe` | 反向锁序真死锁，正确锁序无死锁 |
| `pg_deadlock_timeout_probe` | 一次真实死锁让被中止方额外等 ~0.70s（deadlock_timeout 1s）；可会话级调至 200ms |
| `pg_connection_budget_probe` | 连接预算可计算；上限行为可观测 |
| `pgroonga_revocation_probe` | 撤权后带过滤查询不可见；**反证**：去掉过滤就能查到撤权内容 |
| `pgroonga_branch_probe` | PGroonga 分支在真实 PG 可执行，授权过滤承重 |
| `pgvector_lifecycle_probe` | 撤权/删除/REINDEX 语义符合「索引是派生物」；重建后结果一致（无半切换） |
| `pgvector_pipeline_probe` | **filter-then-ANN + 反证 + RRF 确定性**（含撤权 chunk 不出现、无过滤时出现） |
| `rag_schema_e2e_probe` | 真实 91 表 schema 上 PGroonga 检索可用，授权过滤承重 |
| `pg_replay_probe` | 历史 Alembic 在 PG 上确实阻塞（json_extract / last_insert_rowid / FTS5 / SQLite 触发器语法） |

## 生产库直接对账（Phase C 的真正验收）

`reconcile_real_schema.py` 直接对生产库 `familygraph` 运行（只读）：

```text
agent_runs: 262    steward_model_calls: 334    memories: 2
rag_documents: 0   rag_chunks: 0               audit_log: 1032
agent_runs.status: {succeeded: 217, failed: 44, expired: 1}
memories.scope: {household/space_id=set: 2}     ← 与 CHECK 约束一致
rag_documents revision 镜像: 一致
attempt/run 孤儿: 无                            ← 排除合法 NULL FK 后
egress 审计: 262 个 run，尝试数 min=1 p50=3 max=8
egress sent=false 均带 error_class: 一致
对账通过: 无差异
```

`egress` 尝试数 min=1 p50=3 max=8 与 `spec/backend/agent-runtime.md` 记录的生产实测
（成功 run p50=3、p90=7、p99=11、max=17）**方向一致**，交叉验证了「每次真实出站尝试
写恰好一条审计」的语义。

## 在真实数据库上才发现的 4 个真实缺陷

| 缺陷 | 性质 | 修正 |
|---|---|---|
| `pgvector_pipeline_probe` 缺 `model` 参数 | `build_vector_candidates` 在 f1de7502 加了必填 kwarg，探针未同步 → TypeError，**pgvector 证明已失效** | 补参数 + 补绑定值 |
| `reconcile_real_schema` 把合法 NULL FK 报成孤儿 | `run_id` 是 `ON DELETE SET NULL`，NULL 是合法态；原查询报 75「孤儿」而实际是 0 | 排除 `run_id IS NULL` |
| 同脚本断言「每个 run 恰好一条 egress 审计」 | 合同是「**每次出站尝试**一条」，失败 run 可达 24 次；断言本身是错的 | 改为报分布 + 检验 `sent=false ⇒ error_class` |
| `scope_distribution` 用 `dict()` 包三元组 | `scope, space_id IS NULL, count(*)` 是三列，运行时 ValueError | 显式构造 + 键写成 `scope/space_id=NULL\|set` |

**共同点**：四个都只在真实数据库上执行才暴露。纯静态检查、CI（无 PGTEST_DSN）
都看不到。这验证了 `10-05` 探针纪律里的一条：**探针必须在真实数据库上执行才算验证**。

## 仍未验证（诚实声明）

- `import_sequence_probe` 的 backup/restore 演练：容器内无 `pg_dump`/`pg_restore`（环境阻塞）。
- `real_snapshot_import`：需要一个 app.backup 产出的 SQLite 快照；生产库已是 PG，
  SQLite 快照是 10-08 的旧库，不具备「迁移源」语义。
- `pg_cutover_stage`：已在隔离库上验证到 `pg_all`（epoch 0→3，持久化确认），
  但那是**阶段机本身**的验证，不是真实流量灰度。
- Phase D 的真实流量灰度：需要真实前端/agent 流量切换，未做。
- Phase E 的多租户压力矩阵：`multitenant_acceptance` 验证了配额守恒，
  但真实 p95/p99 延迟、真实 DERP/provider 故障、真实 sidecar 多实例未测。
