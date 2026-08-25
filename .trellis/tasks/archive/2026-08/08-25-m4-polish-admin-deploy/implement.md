# M4 实施计划（父级编排）

## 顺序

1. [ ] m4a ∥ m4b（互不依赖）
2. [ ] m4c 收口（依赖前两者 + 全部历史出口）

## 审查门禁

- m4c 开工前置：M0-M3 所有验收 checkbox 已勾选
- 发布门禁：HANDOFF §五七条逐条人工复核并留痕

## 回滚点

- m4a/m4b 独立分支；m4c 只含脚本与文档，风险最低

## 验证命令

```bash
docker compose up --build -d
docker compose exec api python -m app.backup
cd backend && pytest   # 全量
```
