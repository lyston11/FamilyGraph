# C7：writer epoch 与 migration health

## 为什么这是切换安全的前提

切换 SQLite → PostgreSQL 的最大风险不是「数据搬不过去」，而是**双主**：两个 writer
同时裁决 lease/settle/counter，产生两个真相，且**无法事后对账修复**——两边都可能
已经对外产生了结果。

## 交付

`app/services/writer_epoch.py` + 迁移 `0058_writer_state`：

```text
writer_state(id=1, stage, epoch, updated_at, updated_by)
```

| 阶段 | 含义 | PG 是否真源 |
|---|---|---|
| `sqlite` | 迁移前 | 否 |
| `shadow` | 只读对照 | **否**（这是关键：shadow 期间不能停止写 SQLite） |
| `pg_control` | control-plane 写 PG | control 面是 |
| `pg_all` | 全部写 PG | 是 |

### 为什么用数据库单行而不是 env 配置

配置无法在运行时安全变更：改 env 需要**逐实例重启**，重启期间新旧实例并存——
那正是双主窗口。放进数据库后，一次 `UPDATE` 同时完成「切换 + 让旧实例失效」。

### epoch 的作用

每次阶段变化 epoch +1。持有旧 epoch 的实例在写前核对失败并**拒绝写入**
（`WriterEpochMismatch`，是**安全**异常而非可重试故障）。这使切换**不需要逐实例
重启**，也就消除了双主窗口。

### 回滚语义

回滚 = 相邻退一级 + epoch 递增。断言：退回后 `postgres_is_authoritative` 变回 False，
且旧 epoch 立即失效。

**禁止**把 PostgreSQL 的新状态盲写回 SQLite——那会让 SQLite 变成「落后但仍在被写」
的第二真相源。回滚只回退**路由**，数据留在 PostgreSQL。

### 只能逐级移动

跳级会让「哪些面已经切过」不可知，从而无法安全回滚。`advance` 强制
`abs(delta) == 1`，跳级抛 `ValueError`。

## 数据库侧兜底

三条 CHECK 落在数据库上（应用层判断可被运维手工 UPDATE 绕过）：

```sql
CHECK (stage IN ('sqlite','shadow','pg_control','pg_all'))
CHECK (epoch >= 0)          -- 负值会让「递增后仍小于旧值」的实例误判自己仍是 writer
CHECK (id = 1)              -- 单例：两行状态就是两个真相
```

## migration health 端点

`GET /api/ready`（家庭 listener）与 `/admin-api/ready`（admin listener），只返回
**治理元数据**：阶段、epoch、更新时间、actor、合法阶段列表。

与 `/health` **分开**是刻意的：存活探针回答「进程还在吗」（不查数据库——否则数据库
抖动会触发不必要的重启），就绪探针回答「依赖与迁移阶段是否就绪」。

## 本次踩到的三个真实问题

1. **SQLite 把 DATETIME 存成 TEXT 并原样返回字符串**，PostgreSQL 返回 `datetime`。
   直接 `.isoformat()` 在 SQLite 上抛 `AttributeError`（health 直接 500）。
   已抽出 `_serialize_timestamp` 同时处理两种。
2. **admin listener 的路由白名单测试**（`test_admin_app_registers_only_admin_api_routes`）
   拒绝 `/admin-api/ready`。判断：管理员需要在不登录家庭面的情况下看迁移状态，
   因此把它**显式列为只读治理探针例外**，而不是从路由里去掉——去掉会让迁移期间
   无人能看到切换状态。
3. **`test_deep_downgrade_honours_parent_refusal` 依赖过时列名**（该用例插入的
   `agent_run_events` 列已不存在），且断言了某个具体消息文本（而消息由 0052 的
   helper 决定）。改为断言**承重性质**：拒绝发生 + 本迁移未被降级（DDL=0、列仍在）。

## 变异验证

移除 0058 的 `run_ancestor_preflight` → 深层降级用例失败。
即该 preflight 承重（缺它就会先 DROP 再被祖先拒绝，留下半降级 schema）。

## 未完成（诚实声明）

1. ~~写路径尚未接入 `check_epoch`~~ → **已闭合**：三个事务入口
   （`command_transaction`、`agent_queue._immediate_tx`、`steward._immediate_tx`）
   在取写锁**之前**调用 `writer_epoch.guard`。守卫在事务起点，epoch 过期时连写锁
   都不取——取了就说明已开始参与写入竞争。`steward_pipeline.write_transaction`
   经 `steward._immediate_tx` 继承守卫，无需单独接线。
2. **PITR / WAL archive / HA / failover 未实现**：属运维实施，需要真实 PostgreSQL
   集群与归档存储。
3. **连接预算与 PgBouncer 兼容性未验证**：C3 已实现集群级名额，但 PgBouncer 的
   transaction pooling 对 `FOR UPDATE` 与 advisory lock 的影响未实测。
4. **备份恢复演练未做**（`10-05` 只做了原型）。

## 证据等级

writer epoch 与 migration health：**L1**（单测 + 迁移往返 + 变异）。
真实双实例切换未演练。


## 写路径接线（后续更新）

| 入口 | 守卫位置 | 说明 |
|---|---|---|
| `commands/context.command_transaction` | 事务起点，`_begin_immediate` 之前 | 覆盖全部领域命令 |
| `agent_queue._immediate_tx` | `BEGIN IMMEDIATE` 之前 | 覆盖队列/租约/结算 |
| `steward._immediate_tx` | `BEGIN IMMEDIATE` 之前 | 覆盖 steward 内核 |
| `steward_pipeline.write_transaction` | 经 `steward._immediate_tx` | 继承，无需单独接线 |

### 首次采纳 vs 之后比对

- **首次**调用采纳数据库当前 epoch（刚启动的进程持有的就是当前值；此时拒绝会让
  每次启动后的第一个写失败）；
- **之后**每次比对，不同即拒绝。这是「切换让旧实例失效」的机制。

### `advance` 同步本进程 epoch

执行切换的实例**就是**当前 writer，因此 `advance` 必须同步采纳新 epoch，
否则它会把自己锁在门外。已用测试守护。

### 可关闭

`FG_WRITER_EPOCH_GUARD=0` 在迁移完成且不再计划变更阶段后关闭守卫（省掉每次写事务
的一次单行查询）。**默认开启**：默认关闭会让「忘了打开」变成静默的双主风险。
