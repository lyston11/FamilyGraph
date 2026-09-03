# 实施清单：移除 guest 空间角色

## 1. 实施前确认

- [ ] 读取任务上下文、PRD、设计和相关 backend/frontend 规范。
- [ ] 检查工作树，避开 `RelationshipDetailPanel.vue`、`FamilyTreeView.vue` 等现有无关改动。
- [ ] 确认当前 Alembic head，并核对 `space_members` 约束名称与 SQLite batch migration 模式。
- [ ] 在目标测试数据库确认不存在 `role='guest'`；若发现数据，停止实施并回到规划。

## 2. 数据库与后端

- [ ] 新增 Alembic 迁移，将 `space_members.role` CHECK 收紧为 `space_admin|member`，提供结构 downgrade。
- [ ] 更新模型角色常量、CHECK、schema Literal 和注释。
- [ ] 删除邀请、管理员申请、visibility、controlled-web 和 Steward 中的 guest 专属分支/参数。
- [ ] 搜索后端运行时代码，确认没有新的角色分支遗漏。

## 3. 后端测试

- [ ] 调整授权矩阵、controlled-web、管理员申请、空间引用和邀请测试，保留 member 正向与非成员负向覆盖。
- [ ] 增加或调整迁移/模型约束测试，证明 guest 写入被拒绝且 upgrade/downgrade 可执行。
- [ ] 运行定向测试后运行：`cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m mypy app && .venv/bin/python -m pytest -q`。
- [ ] 若 ownership-transfer deadlock 仍触发，仅在能证明与本任务无关时单独记录；其他失败必须修复。

## 4. 前端与测试

- [ ] 更新 `SpaceRole`、spaces store、治理面板、系统管理员展示、邀请文案和守卫/组件 fixtures。
- [ ] 搜索前端源码与测试，确认不再消费 guest 角色。
- [ ] 运行：`cd frontend && npm run lint && npm run type-check && npm run test && npm run build`。

## 5. 规范与全局检查

- [ ] 更新 `.trellis/spec/architecture.md` 和 `.trellis/spec/backend/database-guidelines.md` 的权威角色合同及 guest 限定。
- [ ] 对 backend、frontend、当前规范进行受控 `guest|Guest|GUEST|访客` 搜索；历史迁移、归档任务和历史说明可保留，但运行时合同不得残留。
- [ ] 使用 `trellis-check` 做最终质量复核，修复发现的问题。

## 风险与停止点

- 数据库中发现 guest 行：停止，不自行升级或删除数据。
- 现有用户改动与本任务重叠：保留用户改动，必要时报告冲突。
- SQLite 约束重建影响索引/FK：迁移测试必须核对现有索引和外键仍在。
