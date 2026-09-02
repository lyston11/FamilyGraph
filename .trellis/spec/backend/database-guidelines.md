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
