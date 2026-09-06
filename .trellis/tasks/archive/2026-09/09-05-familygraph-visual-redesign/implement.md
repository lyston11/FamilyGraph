# Implement: FamilyGraph 双前端高级视觉重设计

## Checklist

- [ ] 更新家庭端 token、玻璃 fallback、全局星空背景。
- [ ] 更新家庭端 AppShell 壳层材质与导航层次。
- [ ] 在 HouseholdCardView 增加家庭空间 hero 大卡片并精修现有卡片布局。
- [ ] 将 FamilyTreeView 画布和相关控制器改为专用星空 token。
- [ ] 将 system-admin-frontend 全局色板和玻璃层切换为深色运营台。
- [ ] 运行两端 lint、type-check、test、build，并复核源级颜色/依赖/模块边界。
- [ ] 更新任务上下文和开发日志，提交变更。

## Validation

```bash
cd frontend && npm run lint && npm run type-check && npm run test && npm run build
cd ../system-admin-frontend && npm run lint && npm run type-check && npm run test && npm run build
```

## Risky Files

- `frontend/src/styles/tokens.ts`: 双主题变量完整性和现有主题测试。
- `frontend/src/views/HouseholdCardView.vue`: 模板新增结构，必须保留现有 `data-test` 和空/错/加载态。
- `frontend/src/views/FamilyTreeView.vue`: Vue Flow 容器层级与深色背景可读性。
- `system-admin-frontend/src/styles/main.css`: 后台全局颜色，需保证所有语义状态可辨识。
