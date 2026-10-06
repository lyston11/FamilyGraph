# 数据库规范（初始规范 v0）

引擎 SQLite，WAL 模式。权威契约见 ../architecture.md §5。

- 启动时统一执行 PRAGMA：foreign_keys=ON, journal_mode=WAL, busy_timeout=5000, synchronous=NORMAL。
- 全部 schema 变更走 Alembic 迁移，禁止 create_all 裸奔到生产。
- FK 必须显式声明 ON DELETE 行为（见 architecture.md CASCADE 清单），禁止隐式。
- 写操作一律事务包裹（session.begin / unit of work），关系写入 = 环检测 + FSM 校验 + 插入同事务原子完成。
- 枚举用 CHECK 约束兜底（dir_class、status 等）+ Pydantic 双重校验。
- 防重复非终态关系：partial unique index（WHERE status IN ('pending','active')）。
- **唯一性无法落索引时用 `BEGIN IMMEDIATE`，不要只靠 service 层 SELECT**：两个事务会各自
  通过检查然后都插入。`command_transaction(session, immediate=True)` 在事务起点取写锁
  （SQLite 单写者），消除"检查 → 插入"的竞态窗口；`services/agent_queue.py` 的并发约束与
  建档去重门禁（architecture.md §0.9）都用它。这类保证必须有并发回归用例——去掉写锁后
  用例必须失败，否则它没在守护任何东西。
- 查询默认带索引意识：relations(from_user), relations(to_user), space_members(space_id) 必建索引。
- 时间统一存 UTC ISO8601 文本或 datetime；生卒日期按 `StructuredDate`
  `{cal_type, date, is_leap_month, original_text, mirror_date}` 结构化列存储（见下）。

## 结构化日期（StructuredDate）存储约定（2026-09-01）

- **`date` 恒为 ISO `YYYY-MM-DD`，不分历别。** `cal_type='lunar'` 时这三个数字是农历
  年/月/日，不是公历——不得直接拿去做时序比较或 `date.fromisoformat` 之后当公历用。
- **闰月只由 `is_leap_month: bool` 表达。** `lunar-python` 内部用负数月份标闰月，该表示
  **不得离开 `services/lunar.py`**：ISO 容器存不下负月，一旦外泄就只能 `abs()` 折叠，
  导致闰二月十五与平二月十五在存储上不可区分。
- **`is_leap_month` 恒描述农历那一侧**（`date` 与 `mirror_date` 中的农历者，二者必有且
  仅有一个是农历）。故公历行的农历镜像若落在闰月，该键同样为 true。
- `mirror_date` 是**派生**的对历镜像，由 `enrich_structured_date` 在写入边界补齐；
  `cal_type='none'` 时无镜像。它只存在于 DB 的 JSON 列，不是 `StructuredDate` 的字段，
  故不参与模型往返——消费方（如 `person_identity.canonical_birth`）读不到时须能现算降级。
- 换算口径的唯一真源是 `services/lunar.py`；禁止在别处另写一套历法换算。

## 系统管理员与空间管理员约束（2026-08-31）

- `system_admins`/`system_admin_accounts` 是独立主体表；禁止用 `users.is_admin` 或 `platform_role_assignments` 作为运行时家庭/后台主体判定。
- `space_members` 规范角色只有 `space_admin|member`，并以 CHECK 约束拒绝其他值、以 partial unique index `space_id WHERE role='space_admin' AND status='active'` 保证每空间最多一个 active 管理员；创建/交接命令保证正常终态恰好一个。角色收窄迁移必须先验证不存在待处置的历史角色行，再重建 SQLite 约束；不得在迁移中静默转换或删除成员关系。
- `owner_id` 仅为迁移期兼容镜像，不能参与授权；旧 `owner` 输入必须在写入边界归一化为 `space_admin`。
- 系统后台查询使用显式列和专用 schema；家庭端点必须使用 `require_authenticated_user`，拒绝 `system_admin` 主体。

## 管理员密码凭据 Schema（2026-09-04，0028）

- `system_admins` 唯一登录标识是 `username`（唯一索引）；`system_admin_accounts` 持有 `password_hash/password_must_change/password_version/failed_attempts/locked_until/status`。PIN 语义（`pin_hash/pin_must_change`）已从管理员侧删除，家庭 `Account` 的 PIN 不受影响。
- 迁移 0028 对含旧行数据的库 fail-closed（RuntimeError 中止 upgrade，schema 不变）；禁止把旧管理员 PIN 静默转换为密码哈希。downgrade 必须按 0022 原结构逐约束重建（约束名对齐），并通过 upgrade/downgrade 循环测试。

## Scenario: 收窄空间角色枚举（2026-09-02）

### 1. Scope / Trigger

当产品删除 `SpaceMember.role` 的角色值时，数据库 CHECK、ORM 常量、Pydantic schema、后端授权分支和前端共享类型必须在同一任务中收敛；禁止只删 UI 选项或应用层分支。

### 2. Signatures

- DB：`space_members.role VARCHAR(16) CHECK(role IN ('space_admin','member'))`。
- ORM：`SPACE_MEMBER_ROLES = ("space_admin", "member")`。
- API：`SpaceMemberOut.role: Literal["space_admin", "member"]`。
- 迁移：`0026_remove_guest_role.upgrade()` 收紧 CHECK；`downgrade()` 仅恢复旧三值结构。

### 3. Contracts

- active `space_admin` 与 active `member` 均属于有效空间成员；邀请、household 可见性和 controlled-web 不再存在第三种角色特判。
- 角色收窄迁移只改变 schema，不自动转换或删除成员关系。
- SQLite 重建 `space_members` 时必须保留 `id/space_id/user_id/added_by/role/status/created_at/updated_at`、`uq_space_member_pair`、成员查询索引、active 管理员 partial unique index，以及 `CASCADE/CASCADE/SET NULL` 三条 FK 删除动作。

### 4. Validation & Error Matrix

- upgrade 前发现 `role` 不在新枚举中 → 迁移抛出 `RuntimeError` 并在重建表前中止，原 schema 和数据保持不变。
- upgrade 后写入已删除角色 → SQLite `IntegrityError`。
- 非 active membership（pending/rejected/withdrawn/removed）→ 仍按非有效成员拒绝，不因角色枚举变少而放宽。
- downgrade → 恢复旧 CHECK 结构；不创建、不恢复任何历史成员行。

### 5. Good/Base/Bad Cases

- Good：无已删除角色数据时 upgrade 成功，普通 active `member` 可使用成员能力，guest 写入被 CHECK 拒绝。
- Base：downgrade 后旧三值 CHECK 可再次接受 guest，仅作为结构回滚能力。
- Bad：迁移中把未知角色静默升级为 member 或直接删除成员关系；这会在未经产品决策时改变访问权。

### 6. Tests Required

- 迁移测试：含 guest 行时 upgrade 抛错且 `sqlite_master` 中旧表结构仍存在。
- 迁移测试：无 guest 行时 upgrade/downgrade 成功，并断言索引集合、`PRAGMA foreign_key_list` 删除动作和角色 CHECK。
- 授权测试：普通 active `member` 的邀请、household 可见性和 controlled-web 正向通过；pending/non-member 负向拒绝。
- 跨层门禁：后端 pytest/Ruff/mypy 与前端 type-check/lint/test/build 全部通过，并对非历史代码执行 guest 残留搜索。

### 7. Wrong vs Correct

#### Wrong

```sql
-- 静默改变已有用户的访问权，且应用代码与 DB 合同可能漂移。
UPDATE space_members SET role = 'member' WHERE role = 'guest';
```

#### Correct

```python
guest_count = connection.scalar(
    text("SELECT COUNT(*) FROM space_members WHERE role = 'guest'")
)
if guest_count:
    raise RuntimeError("role migration requires an explicit data decision")
# 确认无待处置行后再重建 SQLite CHECK，并恢复所有索引/FK。
```

## SQLite 读事务升级与并发用例陷阱（2026-09-02）

来源：修复 `accept_transfer` 双接受并发死锁（09-01-ownership-transfer-test-deadlock）。

- pysqlite legacy autocommit 模式下 SELECT 不持有持久快照：条件 UPDATE（CAS）总是对最新已提交状态求值。所以"条件 UPDATE 单独使用"天然单赢家；`BEGIN IMMEDIATE` 对 CAS 本身的正确性不是必需的。
- `BEGIN IMMEDIATE` 的真正价值是覆盖**跨读取的多步 check-then-act 窗口**——授权读取 → 资格复核 → 多表写入（如 `commands/ownership.py::accept_transfer` 的 load_actor + transfer 检查 + 条件更新 + 角色翻转）。不要因为 CAS 单独看是安全的就省掉它。
- 不能用"去掉写锁后 outcome 测试仍通过"来否定写锁约束：仅含条件 UPDATE 的竞态，结果断言在 pysqlite 上测不出锁缺失。写锁守护的是读阶段窗口，第三方并发者（如并发移除成员资格）只能靠它挡住。
- 并发回归用例自身禁止无限等待，否则一次竞态会把全量 pytest 挂死：`barrier.wait`/`thread.join` 必须带统一超时（参照 `tests/test_person_dedupe.py` 的 `_SYNC_TIMEOUT` 模式）；worker 闭包只捕获标量 ID 和独立 `SessionLocal`，禁止跨线程共享 ORM 实例或 Session；worker 必须捕获 `HTTPException` 与普通 `Exception` 并写入受 `Lock` 保护的结果列表，最终断言从主线程 Session 重查数据库。

## SQLite 迁移连接状态边界（2026-09-03）

- Alembic 迁移重建 SQLite 表时，不得在迁移函数内无条件切换连接级 `PRAGMA foreign_keys`。该设置在已有事务中可能被 SQLite 忽略，迁移结束后还可能改变调用方连接的预期状态；迁移应直接依赖项目启动时已经配置好的外键开关，并通过显式 FK 定义保持删除动作。
- 迁移回归必须覆盖连接原本 `foreign_keys=ON` 与 `OFF` 两种状态，确认 upgrade/downgrade 不会替调用方改变该状态；同时断言重建后的索引、唯一约束和每条 FK 的 `ON DELETE` 行为。

## Scenario: 平台级 Memory / RAG 开关（2026-09-13）

### 1. Scope / Trigger

当用户端暴露 Memory 或 RAG 能力、但能力需要平台统一治理时，不能只依赖不可发现的环境变量或让家庭端承受裸 503；应提供独立系统管理员配置入口，同时保留部署级 fail-closed 兜底。

### 2. Signatures

- DB：`platform_feature_configs(id=1, memory_enabled, rag_enabled, updated_at, updated_by_system_admin_id)`；迁移创建空表，不插入默认行。
- 家庭 API：`GET /api/platform-features` → `{memory_enabled: bool, rag_enabled: bool}`。
- Admin API：`GET/PUT /admin-api/v1/platform-features`；PUT 请求必须完整包含两个布尔字段，`extra='forbid'`。
- 服务：`get_platform_feature_state(db)`、`is_memory_enabled(db)`、`is_rag_enabled(db)`、`set_platform_feature_state(...)`。

### 3. Contracts

- 无配置行时分别回退 `MEMORY_ENABLED` / `RAG_ENABLED`；有配置行时以平台值为产品开关。
- 环境变量仍是 hard-off：对应环境值为 false 时有效状态必为 false，Admin 响应以 `memory_source` / `rag_source='deployment'` 标识，不能在 UI 强行打开。
- Admin 响应只包含两个状态、两个来源和更新时间；不得返回家庭记忆、候选原文、RAG 文本、秘密或部署路径。
- 只有独立 `system_admin` 认证可写；家庭 listener 不挂载写端点。

### 4. Validation & Error Matrix

- 未认证或家庭 JWT → admin API 认证失败；家庭 PUT 路径不存在/不允许。
- PUT 额外字段或非布尔字段 → 422，数据库不变。
- Memory 关闭 → Memory 业务 API 保持 503 fail-closed；RAG 可独立工作。
- RAG 关闭 → RAG 搜索/索引 API 保持 503 fail-closed；Memory 可独立工作。
- 状态端点网络/服务错误 → 前端显示“状态无法确认”，不得伪报为 disabled。

### 5. Good/Base/Bad Cases

- Good：四种开关组合均能独立读取和门禁；Admin 保存后重新读取仍与数据库一致。
- Base：迁移后空表继续使用旧环境配置；首次 Admin 写入才建立单例。
- Bad：把环境变量当成前端状态、把任意 503 当成未启用，或让空间管理员写平台开关。

### 6. Tests Required

- 迁移 upgrade/downgrade 与空表 bootstrap。
- 服务层环境回退、平台值优先级、hard-off 与四种组合。
- 家庭状态安全投影、Admin 认证/严格 schema/审计及家庭写拒绝。
- Memory/RAG 业务门禁独立性；家庭端关闭态操作隐藏和错误分类；Admin 页面保存失败回弹、刷新真源和移动端可达性。

### 7. Wrong vs Correct

#### Wrong

```python
if config.MEMORY_ENABLED is False:
    # 前端继续渲染所有写操作，并把任意请求失败都显示成同一条黄色提示
    pass
```

#### Correct

```python
state = get_platform_feature_state(db)
if not state.memory_enabled:
    raise_api_error(503, MEMORY_DISABLED, "Memory 功能未开启")
```

家庭端先读取只读状态并隐藏必然失败的操作；平台开关写入只允许独立系统管理员完成。

## Scenario: 局部唯一索引的跨方言可移植性（2026-10-04）

### 1. Scope / Trigger

新增或修改任何 `Index(..., unique=True, ...)` 局部索引、`app/models/indexes.py`，
或在 PostgreSQL 上建表/导入数据前必读。

### 2. Signatures

- 唯一允许的构造方式：`app/models/indexes.py::partial_unique_index(name, *columns, where="...")`。
- 它把**同一个**谓词字符串同时用于 `sqlite_where` 与 `postgresql_where`，因此两方言
  不可能漂移。
- 回归：`tests/test_partial_index_portability.py`。

### 3. Contracts

- **不得**在模型层手写 `sqlite_where=` 或 `postgresql_where=`。结构性用例会失败。
- 局部唯一索引的**数量**被钉在 16；增删都必须显式改断言，避免唯一性被无声削弱。
- 谓词为 `X IS NOT NULL` 的索引是**显式例外**（PostgreSQL 唯一索引默认
  `NULLS DISTINCT`，含 NULL 行本就可重复，语义与 SQLite 的 `WHERE X IS NOT NULL`
  等价）。例外集合逐条列出，且用例会断言它们**仍然**是 `IS NOT NULL` 形状——
  不允许用模式匹配放过其他索引。若将来改为 `NULLS NOT DISTINCT` 或给列加非空默认值，
  这些索引会立刻变成真缺陷。

### 4. Validation & Error Matrix

| 情况 | SQLite | 退化后的 PostgreSQL | 正确 PostgreSQL |
|---|---|---|---|
| 历史终态行 + 当前 active 行（如同一 session 两个 run） | 接受 | **拒绝**（UniqueViolation） | 接受 |
| 第二条 active 行 | 拒绝 | 拒绝 | 拒绝 |
| `revoked` 关系 + 新的 `active` 关系 | 接受 | **拒绝** | 接受 |

退化形态下 `UNIQUE(session_id)` 意味着一个 session **一生只能有一行**，历史行会直接
撞约束；数据导入会在第一条历史行上失败。

### 5. Good/Base/Bad Cases

- Good：用 `partial_unique_index` 声明；两个方言渲染出相同谓词；历史行与 active 行共存。
- Base：新增局部索引时同步更新数量断言。
- Bad：手写 `sqlite_where` 只声明一个方言（SQLAlchemy 在另一方言上**静默**丢弃谓词，
  不报错、不警告）。

### 6. Tests Required

- 结构性：`app/models` 中不存在单方言谓词；局部唯一索引数量 == 16。
- 渲染性：每个索引在 SQLite 与 PostgreSQL 下都含 `WHERE`，且两方言谓词字符串相同。
- 语义性（真实 PostgreSQL）：历史 + active 共存；第二条 active 被拒；`revoked` 后重建被允许。
- 变异验证：删除 `postgresql_where` 必须让用例失败（实测 13 个用例失败）。

### 7. Wrong vs Correct

#### Wrong

```python
# PostgreSQL 上谓词被静默丢弃 -> UNIQUE(session_id) -> 一个 session 一生只能有一个 run
Index("uq_agent_runs_session_active", "session_id", unique=True,
      sqlite_where=sa.text("status IN ('queued','leased','running')"))
```

#### Correct

```python
partial_unique_index(
    "uq_agent_runs_session_active", "session_id",
    where="status IN ('queued','leased','running')",
)
```

## Scenario: PostgreSQL 租约的租户并发上限（2026-10-04）

### 1. Scope / Trigger

把 `services/steward_assist.py::lease_attempt`、`services/steward.py::lease_next_steward_job`、
`services/agent_queue.py::lease_next` 或 `enqueue_run` 迁移到 PostgreSQL 前必读；
任何涉及「每租户并发上限」的选行逻辑同样适用。

### 2. Contracts

- `FOR UPDATE SKIP LOCKED` **只**保证不同 worker 取到不同**候选行**。它**不**保证
  每租户配额：实测 READ COMMITTED 下计数子查询读不到彼此未提交的 `in_flight`，
  上限 2 被放成 5。因此**不能**把 `BEGIN IMMEDIATE` 机械替换为 `SKIP LOCKED`。
- 配额真相是持久化的 `counters` 行（`scope_kind/scope_id/resource/capacity/active/version`），
  带 `CHECK (active BETWEEN 0 AND capacity)`。
- 锁顺序固定：`global → kind → account/space → candidate`。
- 候选为空时必须归还名额，否则名额永久泄漏、空间永久无法再租。

### 3. Validation & Error Matrix

| 情况 | 期望 |
|---|---|
| 并发 worker 数 > 每租户配额 | 实际并发数 == 配额，不越限 |
| 名额已满 | **跳过该空间看下一个候选**，不得靠捕获异常控流（异常会中止整个事务） |
| 该空间无到期候选 | 归还名额，`active` 回到原值 |

### 4. Tests Required

- 并发领取：`count(in_flight) <= capacity` 逐租户成立，且 `active == count(in_flight)`。
- 无候选路径反复调用后 `active` 不变（不泄漏）。
- 四条归还路径（settle / cancel / 租约过期恢复 / 栅栏退休）各自归还且**不重复**归还。
- 变异验证：把 counter 换回计数子查询必须让并发用例失败。

### 5. Wrong vs Correct

#### Wrong

```sql
-- READ COMMITTED 下计数子查询看不到并发事务未提交的 in_flight：上限失效。
SELECT id FROM attempts
 WHERE space_id = :space AND status = 'reserved'
   AND (SELECT count(*) FROM attempts x
         WHERE x.space_id = attempts.space_id AND x.status = 'in_flight') < :cap
 FOR UPDATE SKIP LOCKED LIMIT 1;
```

#### Correct

```sql
-- 1) 先占持久化名额（行锁保持到提交，同租户领取在此串行）
SELECT bump_counter('space', :space, 'steward_assist', 1);
-- 2) 再取候选（SKIP LOCKED 只负责候选行去重）
WITH picked AS (
  SELECT id FROM attempts
   WHERE space_id = :space AND status = 'reserved' AND next_attempt_at <= now()
   ORDER BY next_attempt_at, id FOR UPDATE SKIP LOCKED LIMIT 1
) UPDATE attempts a SET status='in_flight', lease_owner=:owner
    FROM picked WHERE a.id = picked.id RETURNING a.id;
-- 3) 无候选则归还：SELECT bump_counter('space', :space, 'steward_assist', -1);
```

## Scenario: 锁顺序（PostgreSQL 迁移新增的约束，2026-10-04）

### 1. Scope / Trigger

引入任何**行锁**（`FOR UPDATE`、no-op `UPDATE ... WHERE id=`、counter 行）或把
`BEGIN IMMEDIATE` 换成行锁前必读。

### 2. Contracts

SQLite 的 `BEGIN IMMEDIATE` 是**全库**写锁——没有「部分顺序」，因此锁序问题在 SQLite
上**永远测不出来**。迁移到行锁后，锁序成为必须显式设计的约束。

固定顺序：

```text
global counter → kind counter → account/space counter → run 行 → attempt 行
```

**counter 永远先于 run 行**。两条实现要求：

1. 租约路径：先取 counter，再进 fence（fence 内部会取 run 行锁）。
2. 结算路径：必须在 `fence_*_execution`（或任何取 run 行锁的函数）**之前**归还
   counter。否则结算是 `run → counter`，与租约相反。

第 2 条最易漏：`record_attempt_outcome` 在调用方事务内运行，而调用方
（`settle_run` 的 `on_settled`）之前已经过 `fence_execution`。

### 3. Validation & Error Matrix

实测交叉顺序（两事务各持一锁后互等）：

```
死锁被检测到: ['A']       ← PostgreSQL DeadlockDetected 中止其一
```

### 4. Verified mechanism

`agent_execution.acquire_run_writer` 的 no-op UPDATE 在 PostgreSQL 上**确实**提供
行级串行化：

```sql
UPDATE agent_runs SET updated_at = updated_at WHERE id = :run_id
```

8 并发事务「取写锁 → 读 max(seq) → 插入」：成功 8、唯一冲突 0、seq 连续 `[0..7]`。
**反证**：不取写锁时同一并发形状 → 表中仅 1 条、7 次唯一冲突。因此该机制有效且用例
有判别力。

### 5. Tests Required

- 并发跑「租约」与「结算」路径，断言无 `DeadlockDetected` 且两者都完成。
- 变异验证：把 counter 归还移到 fence 之后，用例必须暴露交叉顺序。
- 三把以上锁（global + kind + tenant + run）的顺序仍需独立验证。

### 6. Wrong vs Correct

#### Wrong

```python
# 结算：先 fence（取 run 行锁），再归还 counter -> 与租约路径锁序相反
run, _, _ = fence_steward_execution(db, identity)
steward_assist.record_attempt_outcome(db, ...)   # 内部归还 counter
```

#### Correct

```python
# counter 在进入 fence 之前处理；fence 之后只做行内更新
steward_assist.release_capacity(db, ...)         # 先 counter
run, _, _ = fence_steward_execution(db, identity)
```

## Scenario: PostgreSQL 迁移执行前证明门（2026-10-05，10-05-migration-proof-gates）

### 1. Scope / Trigger

改动 PostgreSQL 迁移、方言相关模型/迁移、事务边界或锁序前必读。适用于
`app/models/`、`app/services/{agent_queue,steward,steward_assist,steward_pipeline}.py`、
`migrations/versions/`。

### 2. Signatures（可复跑的探针与扫描器）

```bash
# 扫描器（从仓库根运行，不需要数据库）
./backend/.venv/bin/python research/tools/build_tx_entries.py        # 事务入口枚举
./backend/.venv/bin/python research/tools/build_tx_contracts.py      # 分类 + 强制完整性
./backend/.venv/bin/python research/tools/build_raw_sql_inventory.py # 方言风险分类
./backend/.venv/bin/python research/tools/build_trigger_inventory.py # 触发器（含循环展开）
./backend/.venv/bin/python research/tools/build_inventory.py         # 表/索引/约束

# 探针（必须 PGTEST_DSN；未设置时 SKIP + exit 2，不会误连）
PGTEST_DSN=... ./backend/.venv/bin/python research/tools/pg_replay_probe.py
PGTEST_DSN=... ./backend/.venv/bin/python research/tools/pg_deadlock_probe.py
PGTEST_DSN=... ./backend/.venv/bin/python research/tools/pg_control_proof.py
PGTEST_DSN=... ./backend/.venv/bin/python research/tools/pg_baseline_prototype.py
PGTEST_DSN=... ./backend/.venv/bin/python research/tools/pg_fault_injection.py
PGTEST_DSN=... ./backend/.venv/bin/python research/tools/import_reconcile_probe.py
```

退出码约定：`0` = 通过；`1` = 断言不符（真缺陷）；`2` = 缺 DSN/驱动（环境阻塞，**不算通过**）。

### 3. Contracts（实测结论，不是推断）

- **局部唯一索引必须双方言**：只用 `sqlite_where` 会让 PostgreSQL 退化为**全表**唯一索引
  （`UNIQUE(session_id)` = 一个 session 一生只能有一个 run）。用
  `app/models/indexes.partial_unique_index()`，它把同一谓词喂给两个方言。
- **JSON CHECK 必须方言渲染**：`json_extract` 在 PG 上让**建表失败**。用
  `app/models/checks.DialectCheck`。SQLite 的 `json_extract(...) = 1` 是**类型敏感**的
  （字符串 `"1"` ≠ 数字 `1`），PG 侧必须用 jsonb 对 jsonb（`-> 'k') = '1'::jsonb`），
  **不能**写成 `->> ... ::int`（那会接受字符串 `"1"`，是不同约束）。
- **运行期 JSON 查询必须可移植**：`func.json_extract` 能建表、只在**执行时**失败。
  JSON 列用 `col["k"].as_string()/as_integer()`；**Text** 列用
  `app/models/json_expr.json_text_field()`（`CAST(x AS JSON)` 在 SQLite 上求值为整数 0，不可用）。
- **历史 Alembic 不能在 PG 上重放**：`0042`（`json_extract` CHECK）、`0022`
  （`last_insert_rowid()`）、`0014`（FTS5 虚拟表）实测分别抛 `UndefinedFunction`/
  `UndefinedFunction`/`SyntaxError`。**69 个触发器**全部使用 SQLite 语法
  （`BEGIN...END` + `RAISE(ABORT)`）并实测语法错误。因此必须使用审查后的 PG baseline。
- **ORM metadata 可建表 ≠ 迁移可重放**：`create_all` 走元数据，绕过迁移链，
  因此**看不到触发器**（触发器只存在于迁移里）。`pg-schema-feasibility` 的 87/87
  与迁移链可重放是两件事，不得互相代替。
- **锁序**：`global capacity → kind capacity → tenant capacity → run row → attempt row`。
  实测交叉顺序（租约 `counter→run` vs 结算 `run→counter`）产生真实 `DeadlockDetected`。
  `_settle` 经 `fence_execution → acquire_run_writer` 先取 run 行锁，因此 counter 归还
  **必须早于** fence，否则锁序相反。
- **SQLite 测不出该死锁**：`BEGIN IMMEDIATE` 是全库写锁，没有「部分顺序」。
  这类缺陷**只能在真实 PostgreSQL 多连接上**发现。
- **配额需要持久化 counter**：`SKIP LOCKED` + 计数子查询在 READ COMMITTED 下会静默
  违反每租户上限（实测 5/5 越限）。必须用 counter 行 + 固定锁序；名额满时**先查容量**，
  不能靠捕获异常控流（异常会中止整个事务）。
- **counter 归还恰好一次**：门是 settle 的 `status` 条件 UPDATE affected rows 与
  recovery 的 `applied_at IS NULL`。发送门退休 `reserved` attempt **从未计入**，
  写回栅栏退休 `in_flight` attempt **必须归还**——两处目标状态相同、语义相反。

### 4. Validation & Error Matrix

| 条件 | 行为 |
|---|---|
| 新增事务入口未分类 | `build_tx_contracts.py` 退出 1 |
| 入口数变化（如 65 → 66） | 退出 1，要求复核并更新 `EXPECTED_ENTRIES` |
| 探针缺 `PGTEST_DSN` | 退出 2（SKIP），**不得**视为通过 |
| `import_reconcile_probe` 目标库已有同名业务表 | 退出 3，拒绝运行（防误指真实库） |
| 对账发现差异 | 报告差异，**不自动修复**（refusal） |

### 5. 已知未闭合（不得当作通过）

- 静态调用图只覆盖 `_settle`/`settle_attempt` 一条反向路径；65 个入口中哪些同时持有
  两类锁未逐条判定。
- 三把以上锁的顺序未实测；`deadlock_timeout` 对延迟预算的影响未测量。
- 触发器只验证了**四类语义**；60 个 `sri_*` 的逐表 `scope_id` 解析、`rag_*` 的具体
  语义、列级 `UPDATE OF` 写法均未验证。
- 故障注入与对账是**原型**（`fi_*`/`proof_*` 最小模型、合成数据），不是真实业务
  schema 或真实历史库。
- `writer epoch` 与 `migration health` 未设计（归 `10-04-postgres-operations-cutover`）。

### 6. Wrong vs Correct

#### Wrong

```python
# 用 sqlite_where 声明唯一索引：PG 上谓词消失，唯一性范围被放大到整表
Index("uq_agent_runs_session_active", "session_id", unique=True,
      sqlite_where=text("status IN ('queued','leased','running')"))

# PG 侧把类型敏感的比较写成会放宽语义的形态
"coalesce((source_span_json::jsonb ->> 'version')::int = 1, false)"   # 接受了字符串 "1"

# 结算时先 fence（取 run 行锁）再归还 counter —— 与租约路径锁序相反
run, _, _ = fence_steward_execution(db, identity)
steward_assist.record_attempt_outcome(db, ...)      # 内部归还 counter
```

#### Correct

```python
from app.models.indexes import partial_unique_index

partial_unique_index("uq_agent_runs_session_active", "session_id",
                     where="status IN ('queued','leased','running')")

# 保留类型敏感语义：jsonb 对 jsonb
"coalesce((source_span_json::jsonb -> 'version') = '1'::jsonb, false)"

# counter 先于 fence
steward_assist.release_capacity(db, ...)
run, _, _ = fence_steward_execution(db, identity)
```
