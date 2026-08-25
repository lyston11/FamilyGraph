# m2c 加入申请与断连流程

> 父任务：[08-25-m2-clan-view-privacy](../08-25-m2-clan-view-privacy/prd.md)｜依赖：m2a/m1c

## Goal

两类请求的完整闭环 + 断连后的即时权限降级验证。

## Requirements

- connection_request 审批 UI：我的设置→连接请求列表（收到的 pending：接受/拒绝；我发起的：可撤销 cancel）；接受=relation+space_membership 同时 active（AD-4 合并语义）。
- join_request 流：家族摘要卡发起 → 目标空间 owner 待处理列表审批 → 接受后双方同空间、可见性升级为完整数据；30 天过期惰性清理。
- 通知载体 v1 从简：登录后红点 + 待处理计数，不做推送。
- 断连轨（D8）：任一方 revoke 关系 / owner 或本人 remove 空间成员 → 即时降级；并发请求幂等（唯一约束兜底 + 服务层幂等返回既有记录）。
- 边界场景测试：重复申请、自己申请进自己空间、pending 期间对方删除档案等。

## Acceptance Criteria

- [ ] A 发起合并请求→B 登录红点提示→一次接受→关系与空间同时生效，A 刷新即见 B 完整档案。
- [ ] join_request 从发起、owner 审批到可见性升级端到端通过；拒绝后 A 侧有明确状态展示。
- [ ] B revoke 与 A 的关系后，A 对 B 的下一次读取立即回落为 summary/invisible（无缓存残留）。
- [ ] 幂等与边界用例全部按预期行为通过（写成集成测试）。

## Non-goals

- 推送/邮件通知；撤销历史关系重建向导（重新发起即可）。
