# 家庭端导航/资料编辑/披露策略修复与种子补全 Implementation Plan

- [x] 1. 导航（R1）
  - [x] 1.1 HouseholdCardView.openMember / FamilyTreeView.onNodeSelect 携 state.fgBackTo
  - [x] 1.2 PersonProfileView 返回按钮 context-aware（标签+目标+兜底 home）
  - [x] 1.3 SettingsView 返回修复（push home + 标签「← 返回我的家庭」）
- [x] 2. 资料编辑（R2）
  - [x] 2.1 api 层 updateMemberProfile（PATCH /members/{id}）
  - [x] 2.2 SettingsView 基础资料表单（性别/出生/逝世/简介）+ 保存回显
- [x] 3. 披露放开（R4/R5）
  - [x] 3.1 backend disclosure 服务：高敏感可落行；is_minor→422（新错误码）；审计
  - [x] 3.2 frontend DisclosureMatrix：成年人可开 + 强确认 Modal + 文案更新
- [x] 4. 种子（R3/R6）
  - [x] 4.1 lineage 空间「王氏家族」+ 成员挂入 + birth 日期
  - [x] 4.2 基础五类披露默认开（高敏感关）
  - [x] 4.3 test_dev_seed.py 断言更新
- [x] 5. 文档与验证
  - [x] 5.1 docs/DEV-DATA-SEEDING.md §5 同步
  - [x] 5.2 backend 门禁全绿；frontend 门禁全绿
  - [x] 5.3 部署清库重播种 + 浏览器端到端验证（主会话）

## 验证指令
```bash
cd backend && .venv/bin/python -m ruff check app tests && .venv/bin/python -m ruff format --check app tests && .venv/bin/python -m mypy app && .venv/bin/python -m pytest -q
cd frontend && npm run lint && npm run type-check && npm run test && npm run build
docker compose build api && docker compose up -d api
docker compose exec api python -m app.dev_seed --reset && docker compose restart api
```
