# M0 实施计划（父级编排）

## 顺序

1. [ ] m0a 工程骨架 → 验证：`docker compose up --build` + 双端门禁脚本全绿
2. [ ] m0b 认证安全 → 验证：`pytest backend/tests/test_auth*.py` + 手工旅程（登录/锁定/消歧/改PIN）

## 审查门禁

- m0a 完成后：目录结构符合 spec/backend/directory-structure.md 再开始 m0b
- m0b 完成后：trellis-check 全量检查 + 安全项人工复核（日志无 PIN/token）

## 回滚点

- 每个子任务一个 feature 分支独立合并；m0b 异常可单独 revert 不影响骨架
- 数据库迁移只增不改；回滚 = alembic downgrade 对应版本

## 验证命令

```bash
docker compose up --build -d && curl -f localhost:8000/api/health
cd backend && ruff check . && mypy app/ && pytest
cd frontend && npm run lint && npm run type-check && npm run test && npm run build
```
