# V2.0 Foundation 实施计划

## 顺序

- [ ] 更新 `.trellis/spec/architecture.md`，将 v2 可见性/owner/确档合同标为新权威，明确取代的 v1 条款。
- [ ] 设计并添加 Account/Profile/Role/Space kind/Ref/Disclosure/OwnerInvite/Transfer/Dispute/DataRight/DomainEvent 迁移与约束。
- [ ] 抽出 Unit of Work / application commands，先迁移建档、档案修改、空间/关系组合流程，再让现有 API 调用。
- [ ] 实现确档清单、owner invitation/transfer、VisibilityPolicy 与字段投影。
- [ ] 实现未成年人 overlay 和 break-glass 审计。
- [ ] 实现自助导出/更正/删除/争议的基础状态机和 invalidation events。
- [ ] 更新前端 onboarding、创建表单、确档清单、空间角色/邀请/移交、披露设置与数据权利入口。
- [ ] 逐行补齐授权矩阵、FSM、事务/并发、IDOR、缓存清理和 E2E 测试。

## 预计影响面

- 后端 models/migrations/services/api/schemas/tests，重点为 user/account/space/visibility/custody/audit。
- 前端 auth/members/spaces stores、onboarding/settings/admin/profile 相关组件。
- `.trellis/spec/architecture.md`、backend/frontend 规范中的授权与敏感缓存条款。

## 验证

```bash
cd backend && pytest
cd backend && mypy app
cd frontend && npm run type-check
cd frontend && npm run lint
cd frontend && npm test
cd frontend && npm run build
docker compose up --build
docker compose exec api python -m app.backup
```

另需独立测试：并发兑换同一 owner token、并发接受 owner transfer、删除 owner、provisional 推荐资格为 false、minor 跨 lineage、operator 普通读取为 404/403。

## 回滚

- schema/领域命令抽取/前端迁移分逻辑提交；授权矩阵未全绿时不得继续 V2.1。
- 迁移回滚只针对开发空库；不得靠兼容双写掩盖新旧合同冲突。
