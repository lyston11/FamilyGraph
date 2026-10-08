# C9 P0/P1：容量 bootstrap、PGroonga、embedding 版本、fail-closed

## 执行顺序（按方案）

```text
1. capacity counter bootstrap + 对账      ✅
2. pg_all 缺 counter 时 fail-closed        ✅
3. 正式 PostgreSQL 镜像加入 PGroonga       ✅
4. readiness 检查 vector + pgroonga         ✅
5. embedding revision/model 校验            ✅
6. 关闭 embedding 验证词法 fallback         ✅（单测层）
7. 关闭 Redis 验证 PG fallback              ✅（单测层）
8. 双实例多租户负载验收                      ⏳ 需要双实例
9. PgBouncer + PITR + epoch 演练             ✅（PgBouncer/PITR 已完成）
10. 开发环境稳定观察                         ⏳ 观察中
11. 生产发布                                 ⏳ 由用户手动
```

## 切换过程中发现的 6 个真实缺陷

**每一个都只在真实 PostgreSQL 部署上暴露，SQLite 测试全部通过。**

| # | 缺陷 | 症状 | 影响 |
|---|---|---|---|
| 1 | `_immediate_tx` 硬要求 sqlite3 | lease 500 | agent/steward 队列**完全不可用** |
| 2 | 导入后未修复 sequence | `IntegrityError` | steward job **无法创建**（静默） |
| 3 | `rag_embedding_segments` 未建 | 表不存在 | 向量链路静默失败 |
| 4 | `WHERE 0` 整数当布尔 | `DatatypeMismatch` | RAG 索引**每 5 秒失败**（被吞成 WARNING） |
| 5 | 容量 CHECK 缺 `cluster_*` | `CheckViolation` | 集群计数行被拒 |
| 6 | bootstrap 只建当时活跃租户 | `/ready` 503 | 新租户配额失效或被拒 |

第 4 个最隐蔽：失败被 `except Exception` 吞成 `core tick unaffected` 的 WARNING，
症状只是「RAG 永远不索引」——没有任何可见错误。

## 关键设计修正（我的缺陷，由测试抓出）

### `assert_ready` 的 vacuous pass

初版只做**一致性**对账。空库是「一致」的（无真实占用、无计数行），因此**空库通过**——
而那正是最危险的状态：没有任何计数行，配额从未生效。补了**存在性**检查（global 行）。

### global 汇总被误报为孤儿

配额是分层的，`global`/`agent_kind` 的活跃值是**所有租户之和**。初版只算租户维度，
于是它们的活跃值一律被判为「孤儿占用」——实测
`孤儿占用 global:0:steward_job 计数行 active=20`，而 20 是真实汇总。
把汇总当泄漏会让 `/ready` **永久 503**。

### 租户行必须惰性创建

bootstrap 只能给**当时活跃**的租户建行。服务运行后新租户随时变为活跃，
此时它的计数行不存在：
- 当作「未登记 → 不限制」→ 配额对新租户**静默失效**；
- 当作「缺失 → 拒绝」→ 新租户出现时**立刻停摆**（实测 `/ready` 503）。

解法：`global`/`kind` 行是 **bootstrap 标记**（只由 bootstrap 创建），
租户行**按需创建**且只在标记存在时创建。这使新租户自动纳入配额，
而从未 bootstrap 的部署仍被 `pg_all` 守卫拦住。

### ORM 约束命名前缀

ORM 用 naming convention，`create_all` 建出的约束名是
`ck_agent_capacity_counters_ck_acc_resource_kind`。按精确名 `ck_acc_resource_kind`
去 DROP 会**漏掉旧约束**并新增一条：两条并存，旧的那条（不含 `cluster_*`）仍生效。
必须按**模式**匹配并删除全部候选。

### 触发器转换脚本的环境泄漏

脚本的目的是「在 SQLite 上跑迁移、读 SQLite 触发器定义」，但它继承了环境，
而部署时 `DATABASE_URL` 必然存在 → alembic 转而对着 PostgreSQL 跑 SQLite 建表语句
（`DuplicateTable: relation "users" already exists`）。失败被误读成「触发器转换问题」。

## 实测证据

```
schema:      触发器=66 writer_state=1 gate列=4 pgroonga索引=1 扩展=1 cluster枚举=True
bootstrap:   已建立 7 条，对账通过
/health:     200
/ready:      200  capacity_ready=true  extensions={pgroonga: true}
rag 维护:    新进程 0 次失败（修复前每 5 秒 1 次）
真实运行:    25 分钟内 3 attempts 全部 succeeded、20 jobs、10 条 egress 审计
惰性创建:    新会话可见 (capacity=2, active=1) —— 持久化成立
```

## 未完成（诚实声明）

- **双实例多租户负载验收**：需要两个实例才能验证跨实例配额与 epoch 失效。
- **开发环境长时间稳定观察**：目前约 30 分钟。
- **dev 上的 PGroonga 索引未生效**：dev 的 `rag_documents`/`rag_chunks` 为空
  （真实快照里就没有 RAG 内容），因此 PGroonga 无内容可索引；扩展与索引已就绪。
- **embedding 与 Redis 的 fallback 只在单测层验证**，未做真实故障注入
  （关服务再发请求）。
- **生产发布**：按约定由用户手动执行。
