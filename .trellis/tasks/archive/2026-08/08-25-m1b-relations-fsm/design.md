# m1b 技术设计

> 遵守 [architecture.md](../../spec/architecture.md) §4（Relation FSM/合并请求/新建直连例外）、§5（DB 契约/环检测）。前置：m1a 已归档（users 档案列/custody 可用）。

## 数据契约（Alembic 0004）

```
relations
  id PK
  from_user  INT NOT NULL FK users CASCADE   -- 方向语义: to_user 是 from_user 的 dir_class
  to_user    INT NOT NULL FK users CASCADE
  dir_class  VARCHAR CHECK IN ('elder','younger','peer','spouse') NOT NULL
  label      VARCHAR NULL                    -- 创建者视角自由称谓, 展示检索用 [D3]
  created_by INT NOT NULL FK users           -- 视角归属人(=from_user, 冗余留审计)
  status     VARCHAR CHECK IN ('pending','active','rejected','cancelled','revoked') NOT NULL DEFAULT 'pending'
  created_at / updated_at

索引: ix_relations_from(from_user), ix_relations_to(to_user)
部分唯一索引: uq_relations_pair_active ON relations(lower(min(from_user,to_user)), ...)
  → SQLite 不支持表达式 min(); 落地为两条 partial unique index:
    (from_user,to_user) WHERE status IN ('pending','active')
    (to_user,from_user) WHERE status IN ('pending','active')
CHECK: from_user != to_user
```

## FSM（services/relation_fsm.py）

```
pending --accept--> active        pending --reject--> rejected
pending --cancel--> cancelled     active  --revoke--> revoked
非法转换/终态再转换 → 409 RELATION_INVALID_TRANSITION（带当前状态）
accept/cancel 仅限关系双方; revoke 任一方; reject 仅被请求方
```

- **环检测**（仅 elder 边写入时）：从 to_user 出发沿"作为晚辈方向的活动边"上溯，遇 from_user 即成环 → 422 RELATION_CYCLE_FORBIDDEN。spouse/peer 不参与。
- 终态不可复活；重连 = 新边（partial index 只约束非终态）。

## 合并请求 connection_request

`POST /api/connection-requests {target_id, dir_class, label?, space_membership?: {space_id}}`

- **新建 managed 档案例外**：本任务不覆盖（建档即建关系属 m1a 向导与 m1c 的组合场景，M1 内由前端串联）；本 API 处理**已有账号**目标 → relation 置 pending + 可选 space_members 行置 pending（同事务，AD-4 合并语义）。
- 接受端点 `POST /api/connection-requests/{id}/accept`：relation→active 且 space_member→active 同事务；reject → 双双终态。幂等：重复 accept 返回既有结果（409 或 200 幂等返回，取 409 ALREADY_RESOLVED）。

## 反向显示推导（services/kinship.py）

`display_relation(edge, viewer_id)`：
- viewer == from_user → `{dir_class 原样, label 原文}`
- viewer == to_user → 结构类反译 elder↔younger、peer/spouse 对称；label 仍显示创建者视角原文并标注视角来源（D3）
- 第三者（图渲染用）→ 按 edge 原样 + creator 视角标注

## 图查询（graph.py 骨架）

`GET /api/graph/me?scope=family|clan&depth=n`
- family：我为起点的 active 边 ± depth（默认 1）
- clan：BFS 连通分量（active 边无向遍历），本任务先全量返回节点+边；**可见性过滤参数位预留，m2a 接入**
- 响应 `{nodes:[{id,name,gender,birth,death,label...}], edges:[{id,from,to,dir_class,label,status,view:{dir_class,label}}]}`——edges 附 viewer 视角 view 字段

## 前端

- stores/graph.ts + api/graph.ts
- 「添加关系」入口（HomeView 档案列表页内）：搜索已有用户（名字精确/前缀匹配最小实现）或提示走建档向导 → 选结构四分类（自然问法："TA 是我的：长辈/晚辈/平辈/配偶"）+ 称谓标签 → 提交 connection_request
- 「收到的连接」列表占位（审批 UI 归 m2c，本任务提供 store 与空态）

## 兼容与回滚

- 0004 纯新增表；回滚 drop 即可
- graph/me 为新增端点，不影响既有路由
