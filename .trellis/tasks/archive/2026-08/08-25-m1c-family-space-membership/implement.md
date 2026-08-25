# m1c 实施计划

## Checklist

1. [ ] Alembic 0005：family_spaces + space_members（约束/索引见 design）
2. [ ] services/space_fsm.py：转换表 + 幂等 + 30d 惰性过期 —— 全分支单测
3. [ ] api/spaces.py：CRUD/成员管理/accept/reject/remove 全端点 + 权限测试（owner/member/无关者）
4. [ ] POST /api/users 增加可选 space_membership.space_id：建档事务内直写 active member（AD-4 新建例外）+ 测试
5. [ ] connection_request 放开 space_membership：pending 同事务写入；accept 跨表原子；reject 双终态 —— 测试补齐
6. [ ] GET /graph/me?space_id= 空间子图过滤
7. [ ] 前端：spaces store/api、HomeView 空间化改造（切换器/成员卡/邀请）、向导第四步接入、待处理邀请 accept/reject
8. [ ] 门禁全绿 + 手工旅程：A 建「我家」→ 邀请 B（B 已 claimed→pending）→ B 接受 → 双方 graph 含对方 → B 退出空间 → 关系边仍在

## 验证命令

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app/ && .venv/bin/python -m pytest -q
cd frontend && npm run lint && npm run type-check && npm run test && npm run build
```

## 回滚点

- 0005 drop 表回滚；放开 connection_request 的 commit 独立
