# 数据库规范（初始规范 v0）

引擎 SQLite，WAL 模式。权威契约见 ../architecture.md §5。

- 启动时统一执行 PRAGMA：foreign_keys=ON, journal_mode=WAL, busy_timeout=5000, synchronous=NORMAL。
- 全部 schema 变更走 Alembic 迁移，禁止 create_all 裸奔到生产。
- FK 必须显式声明 ON DELETE 行为（见 architecture.md CASCADE 清单），禁止隐式。
- 写操作一律事务包裹（session.begin / unit of work），关系写入 = 环检测 + FSM 校验 + 插入同事务原子完成。
- 枚举用 CHECK 约束兜底（dir_class、status 等）+ Pydantic 双重校验。
- 防重复非终态关系：partial unique index（WHERE status IN ('pending','active')）。
- 查询默认带索引意识：relations(from_user), relations(to_user), space_members(space_id) 必建索引。
- 时间统一存 UTC ISO8601 文本或 datetime；生卒日期按 architecture.md 的 {cal_type,date,original_text} 结构化列存储。
- 备份只用 online backup API（python -m app.backup），禁止运行期 cp 主库文件。
