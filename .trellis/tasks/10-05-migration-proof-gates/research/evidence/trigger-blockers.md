# Gate 1：全部 69 个迁移触发器使用 SQLite 专属语法（实测阻塞）

## 计数口径修正（重要）

初版用 `grep 'CREATE TRIGGER'` 数源码行，得到 **14**。但其中 4 处位于 `for` 循环内，
会按配置矩阵展开。对隔离 `DATA_DIR` 跑 `alembic upgrade head` 后统计 `sqlite_master`：

```
实际触发器总数: 69
   60  sri_* (steward input revision 计数器)
    3  rag_chunks_*
    2  rag_documents_*
    1  trg_agent_sessions_scope_immutable
    1  trg_raw_relation_inputs_immutable
    1  trg_scev_immutable
    1  trg_slc_internal_sticky
```

**按源码行计数会漏掉 55 个**（全部是 `sri_*`，保护 steward 投影新鲜度不变量）。
可复跑：`scripts/migration-proof/build_trigger_inventory.py`（同时记录两种口径与循环展开点）。

## 结论

仓库的**全部 69 个触发器**定义在 Alembic 迁移里，全部使用 SQLite 语法：

- `CREATE TRIGGER ... BEFORE UPDATE ON t WHEN <cond> BEGIN SELECT RAISE(ABORT, 'msg'); END;`
- `CREATE TRIGGER ... AFTER UPDATE ON t BEGIN INSERT ... ON CONFLICT ... END;`

PostgreSQL **不支持**这种 `BEGIN ... END` 触发器体，也没有 `RAISE(ABORT, ...)`：
它要求 `CREATE FUNCTION ... LANGUAGE plpgsql` + `CREATE TRIGGER ... EXECUTE FUNCTION`。

## 实测（`scripts/migration-proof/pg_replay_probe.py`，隔离 PostgreSQL，可复跑）

```
[OK ] 0009 scope-immutable trigger (SQLite RAISE/ABORT)
      -> SyntaxError: syntax error at or near "OLD"
[OK ] 0045 revision-counter trigger (SQLite upsert in body)
      -> SyntaxError: syntax error at or near "BEGIN"
[OK ] PostgreSQL form: function + trigger (control) -> succeeded
```

第三行是**对照**：同一实例上 PostgreSQL 原生形态（`plpgsql` 函数 + `EXECUTE FUNCTION`）
建立成功。说明失败来自 SQLite 语法本身，不是权限或配置。

## 源码位点（14 处，来自 10 个迁移文件；展开后 69 个实际触发器）

| 迁移 | 行 | 保护的不变量 |
|---|---|---|
| `0009_agent_runtime.py` | 38 | `agent_sessions` scope 不可变 |
| `0010_relationship_facts.py` | 40 | relationship facts 不可变 |
| `0014_memory_rag.py` | 251 / 258 / 264 | RAG 相关不可变 / 计数 |
| `0024_agent_runtime_assistant_only.py` | 19 | assistant-only 约束 |
| `0040_agent_session_title.py` | 23 | session title 规则 |
| `0045_steward_staged_publication.py` | 189 | steward input revision 计数 |
| `0047_rag_lifecycle_integrity.py` | 93 | RAG 生命周期完整性 |
| `0048_steward_terminology_publication.py` | 117 / 152 | terminology 发布不可变 |
| `0049_steward_candidate_evidence.py` | 104 / 111 | candidate evidence 不可变 |
| `0055_steward_assist_execution_unit.py` | 499 | candidate evidence 不可变（E1） |

## 为什么这比 CHECK 更危险

CHECK 表达式被 ORM 元数据重新声明，因此 `create_all` 路径能看到它们（并已被
`DialectCheck` 修复）。但**触发器只存在于迁移里**——ORM 元数据里没有对应声明。

后果：

1. **ORM metadata 建表完全看不到这 14 个触发器**，因此 `pg-schema-feasibility.md`
   的「87/87 表可建」与它们无关；
2. 走 PostgreSQL baseline 时，这些**不可变性与计数不变量会静默消失**——
   不是报错，而是数据库不再拒绝非法 UPDATE；
3. 它们保护的正是审计/不可变语义（scope、evidence、revision 计数），
   丢失属于**安全与账本完整性**问题，不是风格问题。

## 对后续 Gate 的强制要求

- PostgreSQL baseline 必须为每个触发器提供 `plpgsql` 等价实现，并逐条写**负向用例**
  （非法 UPDATE 必须被拒绝）；
- 不能用「应用层已经检查」替代：触发器存在的原因正是应用层路径可能被绕过；
- 必须有一条结构性断言：迁移中出现的每个 SQLite 触发器都有一个 PG 等价物，
  且两者语义逐条对应。

## 证据等级

**L2**（真实隔离 PostgreSQL 执行，可复跑：`PGTEST_DSN=... pg_replay_probe.py`）。
