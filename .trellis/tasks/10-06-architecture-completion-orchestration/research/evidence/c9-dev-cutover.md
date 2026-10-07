# C9：开发环境切换到 PostgreSQL + 向量检索（L4 证据）

日期：2026-10-07
目标环境：开发（`/home/ubuntu/projects/FamilyGraph`，systemd user units）

## 最终状态

| 项 | 值 |
|---|---|
| `writer_stage` | `pg_all`（`/api/ready` 报告 `postgres_authoritative: true`） |
| 数据库 | PostgreSQL 16 + pgvector（容器 `fg-dev-pg`，命名卷 `fg-dev-pgdata`） |
| 绑定 | `127.0.0.1:55450`（不对外），`--cpus=2.0 --memory=2g` |
| 数据 | 51 users / 1169 runs / 32429 events / 22954 domain_events（与 SQLite 一致） |
| embedding | 容器 `fg-embed`，`127.0.0.1:8091`，1 核 / 1 GiB，202 MiB 实测 |
| 健康 | api 200 / agent 200 / maintenance tick 失败 **0** |
| SQLite | 保留为回滚材料（`backend/data/db/app.db`，未删除） |

## L4 端到端证据（真实部署 + 真实模型）

```text
0) embedding 服务: 启用，维度 512
1) 已建 document
2) 索引报告: {'indexed': 1, 'segments_written': 1, 'failed': 0}
3) 分段向量行数: 1
4) 查询向量: 获得
   向量候选 1 条，命中测试文档: True
     - 我的叔父是一位中学教师，住在杭州。      <- 查询是「爸爸的弟弟做什么工作」
5) 测试数据已清理，残留: 0
```

查询与文档**零字面重合**，仍然命中 —— 这是向量检索在真实部署上生效的直接证据。

## 切换过程中发现的 4 个真实缺陷（全部修复）

| # | 缺陷 | 后果 | 修法 |
|---|---|---|---|
| 1 | `_immediate_tx` 硬要求 sqlite3 连接 | **切换后 agent/steward 队列完全不可用**（lease 500） | 方言分派：SQLite 保留 `BEGIN IMMEDIATE`，PostgreSQL 用 C2 的 counter 行锁 |
| 2 | 导入后未修复 sequence | maintenance tick 每轮 `IntegrityError`，**steward 作业无法创建**；健康检查仍 200（静默） | 导入后 `setval` 对齐 47 个序列；已纳入导入脚本 |
| 3 | `rag_embedding_segments` 未在 baseline 创建 | 向量索引整条链路静默失败（表不存在） | 纳入 `pg_baseline_build.py`（与 PGroonga 索引同类问题） |
| 4 | 导入脚本用硬编码 venv 路径 | baseline 步骤 exit 2 且只报「baseline 建立失败」，看不出原因 | 改用 `sys.executable` |

**#2 值得单独强调**：它是**静默**的。健康检查 200、API 正常、用户无感，
只有维护日志里有 `maintenance tick failed`。若不做端到端验证，这个缺陷会在
生产上表现为「steward 永远不工作」。

## 一次失败尝试与回滚

第一次切换**失败**（缺陷 #1），我立即：
1. 从 `env.bak-cutover-*` 回滚环境变量；
2. 重启服务，确认 api 200；
3. 验证 SQLite 数据完好（51/1169/32429/22954）；
4. 确认无 embedding env 残留。

**回滚是干净的**，开发环境在修复期间始终可用。

## 与 SQLite 的关系（不删除）

SQLite 保留为**回滚材料**，但**不再是 writer**：`DATABASE_URL` 已指向 PostgreSQL。
按设计（`design.md` §8），回滚只回退路由与 epoch，不把 PostgreSQL 新状态盲写回 SQLite。

## 未完成（诚实声明）

| 项 | 说明 |
|---|---|
| 真实多租户负载 p95/p99 | 需要真实多实例；本切换只验证了功能可用 |
| PGroonga 在 dev 安装 | dev PostgreSQL 用的是 pgvector 镜像，**无 PGroonga**；词法检索在 dev 上退化为 LIKE。词法质量已在带 PGroonga 的隔离实例上单独验证（0/8） |
| 长时间稳定性观察 | 只观察了约 10 分钟 |
| HA / failover | 单实例，无故障转移 |
| `capacity` counter bootstrap | dev 未登记 counter（未登记=不限制，符合渐进引入设计）；启用配额需显式 bootstrap |
| 线上发布 | **未操作**，按 AGENTS.md 由用户手动执行 |
