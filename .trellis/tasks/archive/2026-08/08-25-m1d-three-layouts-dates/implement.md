# m1d 实施计划

## Checklist

1. [ ] Alembic 0006：node_positions 表
2. [ ] api/spaces positions 端点 + 权限测试
3. [ ] services/lunar.py（lunar-python 封装）+ 单测（闰月往返、超范围 None）
4. [ ] POST/PATCH users 挂接 lunar 自动互补
5. [ ] composables/useLayout.ts + 单测（tree 层级正确/多根/spouse 同行/列表排序兜底）
6. [ ] FamilySpaceView 重构：Vue Flow 画布 + 三布局切换器 + MemberCard + 位置持久化 + 失败回退 toast
7. [ ] HomeView 迁移至 FamilySpaceView（路由默认页），保留档案列表入口
8. [ ] 门禁全绿 + 手工旅程

## 验证命令

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app/ && .venv/bin/python -m pytest -q
cd frontend && npm run lint && npm run type-check && npm run test && npm run build
```

## 回滚点

- 0006 独立；FamilySpaceView 为新文件可 revert；lunar 服务独立 commit
