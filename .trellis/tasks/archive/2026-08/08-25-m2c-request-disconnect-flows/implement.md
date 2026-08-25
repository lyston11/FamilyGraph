# m2c 实施计划

1. [ ] POST /api/spaces/{id}/join（可见性校验 + 幂等）+ 测试
2. [ ] spaces members 列表权限放宽（owner 可见 pending）+ 测试
3. [ ] 断连即时降级集成测试（revoke 后立刻读档案回落）
4. [ ] 前端：摘要卡申请入口接线、我的待处理审批区（接受/拒绝）、断连操作入口
5. [ ] 门禁全绿

回滚点：join 端点独立 commit。
