# C2：持久化容量计数（counter）

## 交付物

- 迁移 `0056_agent_capacity_counters`（幂等 + refusal guard，双方言通过）
- 模型 `AgentCapacityCounter`（含 `CHECK (active BETWEEN 0 AND capacity)`）
- 服务 `app/services/capacity.py`（`ensure_counter` / `acquire` / `release` / `snapshot`）
- 测试 `backend/tests/test_capacity_counters.py`（7 项，SQLite 语义）
- 探针 `scripts/migration-proof/pg_capacity_concurrency.py`（真实 PG 并发）

## 为什么需要持久化 counter（反证实测）

同一并发形状（10 worker、2 租户、每租户容量 2）两种实现的对比：

| 实现 | leased 总数 | 每租户 in-flight | 越限 |
|---|---|---|---|
| **持久化 counter（正确）** | 4 | {'1': 2, '2': 2} | 无 |
| 计数子查询（反证） | 10 | {'1': 5, '2': 5} | **[1, 2]** |

容量为 2，反证形态却租出 **10** 个（每租户 5 个）。
这不是理论风险：`FOR UPDATE SKIP LOCKED` 只保证不同 worker 取到不同**候选行**，
READ COMMITTED 下计数子查询读不到并发事务尚未提交的 `in_flight` 行。

**反证是必需的**：没有它，「counter 形态通过」也可能只是因为用例没构造出竞争窗口。
本类探针此前两次假通过（资源不相交 / 两个都反向），因此每条都要求反例真的失败。

## counter.active 与实际占用一致

```
{
  "1": {
    "active": 2,
    "capacity": 2
  },
  "2": {
    "active": 2,
    "capacity": 2
  }
}
```

`active` 恰好等于实际 `in_flight` 数——不存在「计数漂移」或「名额泄漏」。

## 归还恰好一次

```
{
  "first": 1,
  "second": 1,
  "third": 0,
  "final_active": 0,
  "ok": true
}
```

门（「恰好一次」）在调用方：settle 用 `status` 条件更新，recovery 用 `applied_at IS NULL`。
`release` 只保证不为负，并在已为 0 时返回 0 暴露重复归还——而不是静默吸收。

## 锁序承重

反向锁序（candidate → counter）实测死锁：`['A']`。
这证明「counter 永远先于 run/attempt 行锁」不是风格问题：
`_settle` 经 `fence_execution → acquire_run_writer` 先取 run 行锁，
因此 counter 归还**必须在 fence 之前**完成，否则构成反向并真实死锁。

## 方言差异（为什么 SQLite 测试不够）

| | SQLite | PostgreSQL |
|---|---|---|
| 串行化 | `BEGIN IMMEDIATE` 全库写锁 | 逐行 `FOR UPDATE` |
| 配额越限可否复现 | **不能**（写事务天然串行） | 能（反证实测 5/2） |
| `FOR UPDATE` 语法 | 不支持 | 必需 |

因此并发正确性只在真实 PostgreSQL 上证明；SQLite 侧只测 API 语义。
这个分工是刻意的：避免「SQLite 通过」被误读成「配额成立」。

## 变异验证（两类）

| 变异 | 期望 | 实测 |
|---|---|---|
| 删除 `acquire` 的容量检查 | 用例失败 | **2 个用例失败** |
| 删除 PostgreSQL 路径的 `FOR UPDATE` | 越限或 CHECK 兜底报错 | 出现 `CheckViolation`（fail-loud） |

第二项说明 `CHECK (active <= capacity)` 是**真实兜底**：即使应用逻辑被破坏，
数据库也拒绝超额而不是静默超发。

## 过程中修掉的缺陷

1. **迁移用了 `information_schema`** → SQLite 上 `no such table`。改用 SQLAlchemy inspector。
2. **测试夹具未清空新表** → `active` 残留导致「容量 2 但第二次 acquire 失败」。
   新表必须加入 `tests/conftest.py` 的 `_TABLES`。

## 未闭合（不得当作完成）

- counter 尚未接入真实入口（`lease_next` / `lease_next_steward_job` / `lease_attempt`）；
  本步只交付并证明机制本身。
- 四类归还路径（settle / cancel / lease 过期恢复 / 栅栏退休）尚未逐一接入并验证恰好一次。
- `capacity` 的取值来源（配置项）尚未接线。
