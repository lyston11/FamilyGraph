# m0a 实施计划

## Checklist

1. [ ] backend/: pyproject(ruff/mypy/pytest 锁版本) + app/{main,config,db}.py + health 路由 + Alembic init
2. [ ] frontend/: vite+vue3+ts 模板裁剪 + eslint/vitest 配置 + Login 空壳页 + axios 封装占位
3. [ ] docker-compose.yml + 两份 Dockerfile（多阶段）+ nginx.conf（含"禁直挂 uploads"注释）
4. [ ] README：双模式启动说明
5. [ ] 门禁脚本全绿基线（空测试）

## 验证命令

```bash
docker compose up --build -d && curl -f localhost:8000/api/health && curl -f localhost:8080
cd backend && ruff check . && mypy app/ && pytest
cd frontend && npm run lint && npm run type-check && npm run test && npm run build
sqlite3 /tmp/t.db "PRAGMA journal_mode=WAL;"  # 冒烟
alembic upgrade head && alembic downgrade base && alembic upgrade head
```

## 审查门禁

trellis-check 对照 spec/backend/directory-structure.md 与 spec/frontend/directory-structure.md 逐项核对目录。

## 回滚点

单 PR 合入；revert 即回滚。
