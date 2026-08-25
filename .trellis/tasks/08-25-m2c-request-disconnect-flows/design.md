# m2c 技术设计

## join 端点（非成员向空间发起加入）

POST /api/spaces/{space_id}/join —— 任何已登录用户对**可见空间**（owner 与自己有活动边或同族可达）发起：
写 space_members pending 行（added_by=自己）。幂等：已有行则返回既有状态。
owner 的待处理列表 = GET /api/spaces/{id}/members 中 pending 行（owner 可见）——需放宽 list 成员权限给 owner。

## 断连即时降级验证

revoke 关系 / remove 成员后立即 GET 对方档案 → 按 classify 回落 summary/invisible（无缓存）。

## 并发幂等

重复 join/accept 返回既有 pending 或 ALREADY_RESOLVED；UNIQUE 兜底。
