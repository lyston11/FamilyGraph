# PostgreSQL 租约 + 持久化 capacity counter 原型（2026-10-04）

在隔离 `postgres:16-alpine`（开发服务器，独立端口/库）上真实并发验证。未接触开发库或线上库。

## 为什么需要 counter

`lease-prototype.md` 已实测证伪朴素移植：`FOR UPDATE SKIP LOCKED` + 计数子查询在
READ COMMITTED 下**把每租户上限 2 放成 5**——因为 `SKIP LOCKED` 只防止同一**候选行**
被重复领取，不防止同一**租户**超额；计数子查询在各事务快照里读不到彼此未提交的
`in_flight`。

受影响的真实位置（`begin-immediate-classification.md` 类别 B）：

- `services/steward_assist.py::lease_attempt`（`_in_flight_for_space`，per-space 上限）
- `services/steward.py::lease_next_steward_job`（`active_count`，每空间 active 上限）
- `services/agent_queue.py::lease_next` / `enqueue_run`（每账户 N 个 assistant 并发）

## 原型形态

```sql
CREATE TABLE counters (
  scope_kind text, scope_id integer, resource text,
  capacity integer NOT NULL,
  active   integer NOT NULL DEFAULT 0,
  version  bigint  NOT NULL DEFAULT 0,
  PRIMARY KEY (scope_kind, scope_id, resource),
  CONSTRAINT ck_counter_active CHECK (active >= 0 AND active <= capacity)
);
```

租约事务（**两条语句**，psycopg3 带参数 execute 不接受多语句）：

```sql
-- 1. 占名额：行锁保持到提交，因此同租户的并发领取在此串行
SELECT bump_counter('space', :space, 'steward_assist', 1);

-- 2. 取候选：SKIP LOCKED 只负责候选行去重
WITH picked AS (
  SELECT id FROM attempts
   WHERE space_id = :space AND status = 'reserved' AND next_attempt_at <= now()
   ORDER BY next_attempt_at, id
   FOR UPDATE SKIP LOCKED LIMIT 1
)
UPDATE attempts a SET status='in_flight', lease_owner=:owner,
       lease_until = now() + interval '60 seconds'
  FROM picked WHERE a.id = picked.id
RETURNING a.id;
```

候选为空时必须归还名额（`bump_counter(..., -1)`），否则名额永久泄漏。

## 实测结果（12 个并发 worker，2 个空间 × 上限 2）

```
领取成功 4 个，唯一 4 个
每空间 in_flight: [{space_id:1, n:2}, {space_id:2, n:2}]
counter 状态:    [{scope_id:1, active:2, capacity:2}, {scope_id:2, active:2, capacity:2}]
✅ 每空间上限在并发下真正生效，且 counter 与实际 in_flight 一致
✅ 无候选路径不泄漏名额（反复尝试 3 次后 active=0）
```

对比朴素移植的 `[{tenant:1, n:5}, {tenant:2, n:3}]`：**上限真正生效**。

## 设计约束（原型暴露的，不是理论）

### 1. 名额满时不能靠异常做流程控制

`bump_counter` 在 `active + delta > capacity` 时抛 `CheckViolation`。若调用方直接尝试
然后捕获异常，**整个事务被中止**，候选选择与后续写入都无法继续。正确做法是
**先读容量再决定**（`SELECT capacity, active FROM counters WHERE ... FOR UPDATE`），
满则跳过该空间看下一个候选。

这直接对应现有 `lease_attempt` 的「服务端选一个有容量且有到期工作的空间」逻辑：
迁移后该选择必须基于 counter 行的锁内读取，而不是子查询计数。

### 2. counter 必须与真实 `in_flight` 一致

原型断言了 `active == count(in_flight)`。迁移时必须保证：

- 每个进入 `in_flight` 的 attempt 恰好 `+1`；
- 每个离开 `in_flight`（settle / cancel / 崩溃恢复 / 栅栏退休）恰好 `-1`；
- 重复结算不得重复 `-1`（用 `applied_at` / 状态转换做幂等门）。

**这是迁移中最容易出错的地方**：现有 `recover_stuck_attempts`、发送门退休、
`recover_stuck_child_runs` 都会改变 attempt 状态，每条路径都要配一次归还。

### 3. 锁顺序固定

`global → kind → account/space → candidate`。counter 行锁与候选行锁的顺序固定，
否则两个租户交叉取锁会死锁。

### 4. 不同租户不互相阻塞

counter 主键含 `scope_id`，空间 1 的行锁不阻塞空间 2。实测 12 个 worker 跨 2 个空间
并发完成，无串行化。

## 尚未验证（后续必须补）

- **结算/恢复路径的归还**：本次只验证了领取与「无候选归还」，未验证 settle、
  cancel、租约过期恢复、栅栏退休四条路径各自归还且不重复归还。
- **崩溃场景**：进程在占名额后、写 attempt 前崩溃时 counter 是否泄漏。原型依赖
  事务原子性（同事务内占名额 + 写状态），因此理论上不会；但**跨事务**的恢复路径
  需要独立验证。
- **`SERIALIZABLE` 对照**：未比较与可串行化隔离的吞吐差异；当前方案不需要它。
- **与 ORM/Session 的集成**：原型用裸 psycopg；实际实现要接 SQLAlchemy Session，
  需确认 `bump_counter` 的异常如何与 Session 事务边界交互。

## 结论

持久化 counter 行 + `SKIP LOCKED` 取候选是**唯一**满足「每租户并发上限」的形态。
不能把 `BEGIN IMMEDIATE` 机械替换成 `SKIP LOCKED`；这三处必须同时引入 counter 表、
固定锁顺序、以及四条归还路径的幂等保证。
