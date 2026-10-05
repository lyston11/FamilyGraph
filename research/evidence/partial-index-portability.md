# 局部唯一索引在 PostgreSQL 上的静默失效（2026-10-04）

## 结论（决定性，改变迁移方案）

仓库有 **16 个** `Index(..., unique=True, sqlite_where=...)`，**0 个** `postgresql_where`。
SQLAlchemy 只在匹配的方言下渲染方言专属谓词，因此这 16 个局部唯一索引在
PostgreSQL 上会**退化为全表唯一索引**。已用真实编译验证：

```python
Index("uq_agent_runs_session_active", t.c.session_id, unique=True,
      sqlite_where=text("status IN ('queued','leased','running')"))
```

```sql
-- SQLite（正确）
CREATE UNIQUE INDEX uq_agent_runs_session_active ON agent_runs (session_id)
  WHERE status IN ('queued','leased','running')

-- PostgreSQL（谓词消失）
CREATE UNIQUE INDEX uq_agent_runs_session_active ON agent_runs (session_id)
```

后果不是「索引少了一个」而是**唯一性范围被放大到整个表**：

| 索引 | 谓词 | PG 上退化为 | 后果 |
|---|---|---|---|
| `uq_agent_runs_session_active` | `status IN (queued,leased,running)` | `UNIQUE(session_id)` | 一个 session 一生只能有一个 run |
| `uq_steward_jobs_space_active` | active 状态 | `UNIQUE(space_id)` | 一个空间一生只能有一个 steward job |
| `uq_space_active_admin` | `role='space_admin' AND status='active'` | `UNIQUE(space_id)` | 一个空间一生只能有一个成员行 |
| `uq_relations_pair_fwd` | `status IN (pending,active)` | `UNIQUE(from_user,to_user)` | 撤销过的关系永远无法重建 |
| `uq_relations_pair_rev` | 同上 | `UNIQUE(to_user,from_user)` | 同上 |
| `uq_source_facts_active` | `state != 'revoked'` | `UNIQUE(subject,object,fact_type,COALESCE(space_id,-1))` | 撤销过的事实永远无法重建 |
| `uq_action_cards_active_dedupe` | active 状态 | 全列唯一 | 同一证据版本的历史卡永久阻止新卡 |
| `uq_steward_suggestions_active_dedupe` | active 状态 | 全列唯一 | 同上 |
| `uq_sie_active_triple` | active 状态 | 全列唯一 | 驳回过的推断永远无法重建 |
| `uq_term_entries_personal_active` | `level='personal' AND status='active'` | `UNIQUE(owner_account_id,concept_code)` | 个人称谓无法跨 level 重建 |
| `uq_term_entries_space_active` | `level='space' AND status='active'` | `UNIQUE(space_id,concept_code,term)` | 同上 |
| `uq_space_manager_application_pending` | `status='pending'` | 全列唯一 | 被拒后无法再次申请 |
| `uq_ownership_transfer_active` | `status='pending'` | `UNIQUE(space_id)` | 空间一生只能有一次转移 |

**12 个是真缺陷**（`uq_agent_messages_session_key` / `uq_agent_tool_calls_run_call` /
`uq_notifications_suggestion` / `uq_ownership_transfer_active` 需按列判断）。

## 为什么 SQLite 上没有暴露

谓词里的 `status`/`state`/`role` 不是列的一部分，所以 SQLite 的局部索引允许同一
`session_id` 在多行不同 `status` 下共存（仅 active 状态互斥）。PG 上谓词消失后，
这些「历史行 + 当前行」会直接撞唯一约束。

**这不是理论风险**：历史行（终态 run、revoked 关系、dismissed 建议）在真实库中大量存在。

## 修复

`sqlite_where` 与 `postgresql_where` 必须**成对声明同一谓词**：

```python
Index("uq_agent_runs_session_active", "session_id", unique=True,
      sqlite_where=sa.text("status IN ('queued','leased','running')"),
      postgresql_where=sa.text("status IN ('queued','leased','running')"))
```

已实测修复后 PG 渲染出正确谓词。

## 不需要修复的 4 个（NULL 列）

`uq_agent_messages_session_key`、`uq_agent_tool_calls_run_call`、
`uq_notifications_suggestion` 的谓词是 `X IS NOT NULL`。PostgreSQL 唯一索引默认
`NULLS DISTINCT`，含 NULL 的行可重复，因此这三者在 PG 上语义等价，**不必**加
`postgresql_where`。但迁移时必须**验证**而不是假设：若将来改为 `NULLS NOT DISTINCT`
或引入非空默认值，它们会立即变成真缺陷。

## 对迁移方案的影响

1. **PG baseline schema 不能从 ORM 元数据生成**。必须逐条审查 16 个局部索引，
   显式写出两方言谓词，并断言生成的 DDL 与 SQLite 语义一致。
2. 需要一条**结构性回归**：遍历所有 `sqlite_where` 索引，断言存在同谓词的
   `postgresql_where`（或该索引属于上述 4 个 NULL 例外）。
3. 需要一条**语义回归**：对每个局部索引，构造「历史终态行 + 当前 active 行」并断言
   PG 接受；这是迁移前必须先在隔离 PG 上跑过的用例。
4. 数据导入顺序因此受影响：必须先确认目标 schema 已带正确谓词，再导入历史行，
   否则导入会在第一条历史行上因唯一冲突失败。

这条发现也解释了为什么「在隔离 PG 上重放历史 Alembic」不是安全路线：那些迁移用
`batch_alter_table` 重建 SQLite 表并重新声明索引，在 PG 上不会复现同样的局部性。
