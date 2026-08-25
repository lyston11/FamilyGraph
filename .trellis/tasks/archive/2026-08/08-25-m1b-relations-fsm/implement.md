# m1b 实施计划

## Checklist

1. [ ] Alembic 0004：relations 表 + CHECK 自环禁令 + 双向 partial unique index + 两个方向索引
2. [ ] services/relation_fsm.py：转换表 + 权限（accept/cancel 双方、reject 被请求方、revoke 任一方）——全分支单测
3. [ ] 环检测：elder 边 DFS 上溯，422 RELATION_CYCLE_FORBIDDEN —— 成环/不成环用例
4. [ ] api/connections.py：POST connection_request（含可选 space_members pending 同事务）、accept/reject/cancel/revoke 端点 + 幂等/非法转换测试
5. [ ] services/kinship.py：display_relation 反译 + 测试（elder/younger 对称、peer/spouse 对称、label 视角标注）
6. [ ] GET /api/graph/me：family scope ±depth、clan scope BFS 全量 —— fixture 三家庭连通测试节点/边正确性
7. [ ] 前端：stores/graph.ts、api/graph.ts、添加关系对话框（搜人+四分类自然问法+称谓标签）、收到连接空态列表
8. [ ] 门禁全绿 + 手工旅程脚本：A 建 B 关系 pending → B accept → graph 双方视角正确 → A revoke → B 再连新边成功

## 验证命令

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app/ && .venv/bin/python -m pytest -q
cd frontend && npm run lint && npm run type-check && npm run test && npm run build
```

## 审查门禁

- FSM 转换表与 architecture §4 逐格核对；partial index 用真实 DB 写入验证（非仅模型声明）
- 合并请求接受后 relation+space_members 同事务 active（跨表原子性测试）

## 回滚点

- 单分支；0004 downgrade drop relations；graph 端点独立可 revert
