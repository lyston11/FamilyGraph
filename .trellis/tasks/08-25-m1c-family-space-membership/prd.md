# m1c 家庭空间与成员状态机

> 父任务：[08-25-m1-relations-family-space](../08-25-m1-relations-family-space/prd.md)｜依赖：m0b（可与 m1a/m1b 并行开发，联调需 m1b）

## Goal

家庭空间实体与成员资格的完整生命周期，含多空间归属（D6）与首登空间引导（AD-3）。

## Requirements

- family_spaces(id,name,owner_id,created_at) + space_members(space_id,user_id,added_by,role,status,created_at)，UNIQUE(space_id,user_id)。
- SpaceMember FSM：pending→active|rejected|withdrawn|expiry(30d)；active→removed（owner 或本人）；幂等重复申请返回既有 pending。
- 空间 CRUD：建多个空间、改名、切换；移除成员不动档案（D8 断连轨）。
- 首登无任何成员资格 → 引导创建「我的家庭」默认空间（owner=本人，初始仅自己）+ 基于 active 关系的邀请建议列表（走 D4 正常确认流，绝不静默拉人）。
- 默认进入空间优先级：最近活跃 > own 第一个 > 被拉入第一个 active（AD-3）。
- 首页聚合视图接口：我所属全部空间的成员去重并集 + 关系图数据（第一人称渲染的数据源）。

## Acceptance Criteria

- [ ] 一人可同时属于 ≥2 空间并可创建第 3 个；各空间成员列表独立正确。
- [ ] pending 成员不出现在空间活跃名单；接受/移除即时生效。
- [ ] 重复加入申请幂等返回同一 pending；30 天 expiry 有定时清理或惰性判定。
- [ ] 无任何空间的新用户登录被引导创建默认空间；建议列表只含 active 关系对象且邀请需对方确认。

## Non-goals

- 家族连通视图（m2b）；join_request 审批 UI（m2c）。
