# m1b 关系模型与状态机

> 父任务：[08-25-m1-relations-family-space](../08-25-m1-relations-family-space/prd.md)｜依赖：m1a｜FSM 契约：architecture.md §4 `[AD-4]`

## Goal

四分类关系的完整后端：数据契约、状态机、环检测、反向显示推导。

## Requirements

- relations 表 + 约束：dir_class CHECK 枚举(elder|younger|peer|spouse)、自环 CHECK、partial unique index（每对用户仅一条非终态边）、双向索引。
- FSM：pending→active(reject/cancel)；active→revoked（任一方，D8 断连轨）；终态不可复活，重连=新边。非法转换 409。
- connection_request 合并请求：`POST /connection-requests` 携带 relation{dir_class,label}+可选 space_membership(space_id)（AD-4）；对新建账号直接 active（D4），对已有账号 pending。
- elder 边写入环检测（DFS/并查集），拒绝时 422 RELATION_CYCLE_FORBIDDEN；spouse 边不参与层级。
- 反向显示：动态推导不存储——结构类反译 elder↔younger、peer/spouse 对称；称谓标签始终创建者视角原文（D3）。
- 图查询端点 `GET /graph/me?scope=family|clan&depth=n`：返回节点+边（可见性过滤在 m2a 接入前先按"本人关系图"实现）。

## Acceptance Criteria

- [ ] 四分类 CRUD 全通过；非法 dir_class/status 转换被 CHECK 与服务层双重拒绝。
- [ ] A—elder→B 再 B—elder→A 或成环链被拒且事务回滚无残留。
- [ ] 同一对用户第二条非终态边触发唯一约束冲突提示；revoked 后可重新发起新边。
- [ ] 合并请求一次接受后 relation 与 space_members 同时 active；拒绝同时取消。
- [ ] 反向视角 API 返回正确反译结构类与原文标签。

## Non-goals

- 可见性遮罩（m2a）；申请审批 UI（m2c）；树形渲染（m1d）。
