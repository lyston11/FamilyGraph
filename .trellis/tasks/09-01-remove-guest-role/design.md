# 技术设计：移除 guest 空间角色

## 边界与数据流

角色值由数据库 CHECK 约束和 ORM 常量定义，经 SQLAlchemy 模型 → Pydantic 空间 schema → FastAPI 授权/命令 → 前端 API 类型与 Pinia store → 组件展示消费。所有边界统一收敛到 `space_admin | member`，不在前端自行推导额外角色。

## 数据库

新增下一序号 Alembic 迁移，重建 `space_members` 的角色 CHECK 约束为 `space_admin|member`。SQLite 迁移按现有项目模式处理约束重建。由于已确认无历史 guest 行，不执行数据变换；downgrade 仅恢复三值 CHECK。

## 后端

- `app/models/space.py`：更新角色常量、CHECK 和注释。
- `app/schemas/space.py`：角色 Literal 收敛为两值。
- `app/commands/spaces.py`：active 成员统一可邀请，删除 guest 排除分支。
- `app/commands/manager_applications.py`：删除 guest 专属拒绝函数及调用，保留 member/身份确认/空间类型校验。
- `app/services/visibility.py`：移除 guest baseline 分支；household active 成员按普通成员可见性计算。
- `app/api/controlled_web.py`、`app/services/controlled_web.py`：删除 guest 禁止分支，使用统一成员授权。
- `app/services/steward.py`：移除 `include_guest` 参数及调用点。
- 同步更新相关注释，避免授权语义残留。

## 前端

- `src/types/api.ts`：`SpaceRole` 只保留两值。
- `src/stores/spaces.ts`：`canInvite` 以当前 active membership 存在为准。
- `SpaceGovernancePanel.vue`、`SystemAdminView.vue`：删除 guest 标签、文案、分支和样式。
- `InviteMemberDialog.vue` 及路由守卫测试：清理 guest-only 说明和测试数据。

## 测试与兼容

后端保留普通 member 的正向授权测试，并删除只验证 guest 限制的测试；新增/调整迁移测试确认两值约束拒绝 guest。前端移除 guest fixtures/循环并运行类型、lint、单测、构建。归档任务、历史迁移和 git 历史中的 guest 文本不属于运行时残留。

## 回滚

应用代码回滚与 Alembic downgrade 可恢复旧三值约束；本任务没有存量 guest 数据，因此无需数据回滚。若未来发现实际存在 guest 行，应在迁移前停止并重新评审兼容策略。
