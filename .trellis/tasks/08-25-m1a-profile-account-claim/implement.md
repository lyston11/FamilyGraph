# m1a 实施计划

## Checklist

1. [ ] Alembic 0003：users 增量列（见 design 数据契约）+ downgrade 可回滚
2. [ ] ClaimState 接线：change_pin 成功翻转 pin_must_change 时同事务置 claimed（含测试：managed→claimed 唯一转换点）
3. [ ] challenge candidates 补 created_by_name（design 兼容项）
4. [ ] services/custody.py：resolve_relation / assert_can_edit —— 权限矩阵全分支单测
5. [ ] api/users.py 扩展：POST 建档（一次性 PIN）/ GET / PATCH / disclosure / DELETE(confirm_name)
   - 测试：权限矩阵×5 主体、handover 认领后创建者写 403、删除级联+audit 快照、confirm 不符 409、disclosure 键集合校验
6. [ ] 前端：MemberCreateWizard（三步+预留插槽）、OneTimePinDialog、ProfileDrawer（查看/编辑/披露开关组/删除输入名字确认）、HomeView 档案列表
   - 组件测试：向导流程、一次性 PIN 弹窗不可回看、删除确认流、无权编辑态
7. [ ] 门禁全绿 + 手工旅程：建四类亲人→各拿一次性 PIN→登录认领→创建者被降权→删除档案

## 验证命令

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app/ && .venv/bin/python -m pytest -q
cd frontend && npm run lint && npm run type-check && npm run test && npm run build
```

## 审查门禁

- custody 矩阵与 architecture §1/D5 逐格核对
- 删除服务与 architecture §7 六条级联/审计要求逐条核对

## 回滚点

- 单 feature 分支；0003 迁移 downgrade 干净；前端组件独立可 revert
