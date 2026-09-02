# 移除 guest 空间角色

## Goal

移除 `guest` 这一空间角色，使系统只保留 `space_admin` 与 `member`，并让数据库、后端授权、前端类型/展示、测试与架构规范使用同一套角色合同。

## Background / Confirmed Facts

- 现有角色合同为 `space_admin`、`member`、`guest`；`guest` 在 household 可见性、邀请、管理员申请和 controlled-web 上有额外限制。
- 现有实施计划已定位后端模型/schema/API/命令/服务/迁移、前端类型/store/组件/守卫、测试和架构文档中的 guest 引用。
- 数据库约束通过 `SpaceMember` ORM CHECK 与 Alembic 迁移共同约束角色；角色删除必须通过新迁移完成，不能只改应用代码。
- 当前工作树已有其他未提交改动；本任务实施时必须避免覆盖无关修改。

## Requirements

1. `SpaceMember.role` 的持久化与 API 输出只允许 `space_admin`、`member`；任何新写入的 `guest` 值都必须被数据库和 schema 拒绝。
2. 删除所有仅为 guest 存在的授权分支：active 成员统一按 member 规则参与 household 可见性、邀请、管理员申请和 controlled-web；Steward 的成员筛选不再携带 guest 特殊参数。
3. 前端共享角色类型、空间 store、治理面板、系统管理员展示和路由测试不得再依赖 guest。
4. 为历史 guest 数据提供明确、可逆约束变更但不伪造角色恢复的迁移路径；迁移行为必须有测试覆盖。
5. 更新受影响的后端/前端测试与架构/代码注释，确保实现、测试、文档无遗留 guest 语义（归档任务与历史记录除外）。

## Acceptance Criteria

- [ ] Alembic upgrade 在空库和现有数据库上成功；升级前验证不存在历史 guest 行，升级后角色 CHECK 只接受 `space_admin|member`。
- [ ] Alembic downgrade 能恢复旧 CHECK 约束；由于没有历史 guest 数据，不涉及成员数据恢复。
- [ ] 后端模型、schema、授权服务/命令和 API 不再包含 guest 专属分支，相关授权回归测试通过。
- [ ] 前端 `SpaceRole` 及相关 UI/store/守卫不再包含 guest，类型检查、lint、测试和构建通过。
- [ ] 后端质量门禁（ruff、format、mypy、pytest）通过；已知 ownership-transfer deadlock 若仍存在，单独记录而不掩盖其他失败。
- [ ] 架构规范和代码注释与两角色合同一致；受控搜索确认没有非归档 guest 引用。

## Compatibility Decision

- 已确认历史数据库中不存在 `guest` 成员数据，因此迁移无需转换或删除存量成员关系。
- 新迁移只需将角色 CHECK 约束收紧为 `space_admin|member`；downgrade 恢复旧约束仅用于结构回滚，不承诺恢复不存在的历史数据。

## Out of Scope

- 不新增任何替代访客/只读角色。
- 不改变 `space_admin`、`member` 的其他业务语义或 owner 移交流程。
- 不清理 git 历史、归档任务或历史迁移文件中的 guest 文本。

## Notes

- 计划来源：`.trellis/plans/remove-guest-role-plan.md`。
- 本任务跨越数据库、后端、前端、测试和规范文档，因此需要 `design.md` 与 `implement.md` 后再激活。
