# PersonalFamilyView 后续任务研究：当前合同缺口

## 研究范围

检查已完成 `09-01-personal-family-view` 的设计/实现记录，以及当前 household、stats、notifications 的前端合同、后端 `/stats` 路由和页面安全降级行为。

## 已确认事实

1. `frontend/src/api/household.ts` 约定 `GET /household-card?space_id=<id>`，响应仅包含 space 元数据、view version、viewer、members 和 allowed actions；页面不得回退全局用户列表。
2. `frontend/src/api/spaceStats.ts` 约定 `GET /stats?space_id=<id>`，统计必须由服务端按授权口径聚合，前端不能从 PersonalFamilyView 节点数组推导。
3. `frontend/src/api/notifications.ts` 约定通知按账号和空间过滤，支持 ETag/304、单条已读和全部已读；read_at、domain_status、ActionCard revision 严格独立。
4. `backend/app/api/misc.py:55-105` 仍是旧无空间统计，只按当前可见用户集合返回 total、gender、generation 和 birthdays，不能满足新合同。
5. HouseholdCardView、StatsView、NotificationsView 当前对后端 404 采用安全未就绪状态，不访问旧 fallback。
6. 已完成的 PersonalFamilyView 任务已经固定 `none` 省略、`lineage_summary` 不得继续遍历、撤权后查询层立即复核和推荐只能消费 current view。

## 结论

本任务应新增明确的服务端投影合同，而不是把旧 `/stats` 或旧 graph 扩展成隐式 fallback。优先顺序为 household card、空间统计、notifications，再做前端真实端点联调和跨层回归。
