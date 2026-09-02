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
- `space_members` 规范角色只有 `space_admin|member|guest`，并以 partial unique index `space_id WHERE role='space_admin' AND status='active'` 保证每空间最多一个 active 管理员；创建/交接命令保证正常终态恰好一个。
- `owner_id` 仅为迁移期兼容镜像，不能参与授权；旧 `owner` 输入必须在写入边界归一化为 `space_admin`。
- 系统后台查询使用显式列和专用 schema；家庭端点必须使用 `require_authenticated_user`，拒绝 `system_admin` 主体。
