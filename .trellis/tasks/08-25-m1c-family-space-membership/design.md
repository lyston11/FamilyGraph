# m1c 技术设计

> 遵守 [architecture.md](../../spec/architecture.md) §3（空间生成规则 AD-3）、§4（SpaceMember FSM）、§5（DB 契约）。前置：m1a/m1b 已归档。

## 数据契约（Alembic 0005）

```
family_spaces
  id PK, name VARCHAR(64) NOT NULL, owner_id INT NOT NULL FK users CASCADE, created_at

space_members
  id PK
  space_id INT NOT NULL FK family_spaces CASCADE
  user_id  INT NOT NULL FK users CASCADE
  added_by INT NULL FK users SET NULL      -- 发起人；本人自加亦记自己
  role     VARCHAR CHECK IN ('owner','member') NOT NULL DEFAULT 'member'
  status   VARCHAR CHECK IN ('pending','active','rejected','withdrawn','removed') NOT NULL DEFAULT 'pending'
  created_at / updated_at
UNIQUE(space_id, user_id)；索引 ix_space_members_space(space_id)、ix_space_members_user(user_id)
```

## SpaceMember FSM（services/space_fsm.py）

```
pending --accept--> active     pending --reject--> rejected（被请求方）
pending --withdraw--> withdrawn（发起方撤回）   pending 过期(30d，惰性判定)--> withdrawn
active  --remove--> removed    （space owner 或成员本人）
幂等：UNIQUE 兜底 + 服务层重复申请返回既有 pending 行
```

## API

```
POST   /api/spaces {name}                          创建空间，owner+active member 自动静默生效（自建即同意）
GET    /api/spaces                                 我 active 成员的空间列表 + 各自 pending 计数
PATCH  /api/spaces/{id}                            改名（仅 owner）
GET    /api/spaces/{id}/members                    成员列表（active 成员可见）
POST   /api/spaces/{id}/members {user_id?|name?}   邀请已有账号 → pending（幂等）；managed 新建走建档向导（m1a 插槽此时接入）
POST   /api/space-memberships/{mid}/accept|reject  被请求方处理
DELETE /api/space-memberships/{mid}                owner 移除 或 本人退出（D8 断连轨，不动档案）
GET    /api/graph/me 扩展 ?space_id=                指定空间的 active 成员子图（家庭空间页数据源）
PUT    /api/connection-requests 放开               m1b 的 SPACE_MEMBERSHIP_DEFERRED_M1C → 真正同事务写 pending space_member；
                                                   accept 时 relation+member 同时 active（跨表原子性测试补齐）
```

- **首登无任何空间资格**：`GET /api/spaces` 返回空时前端引导创建默认空间（AD-3：初始成员仅自己）。
- 建档向导第四步（m1a 预留插槽）：「加入我的 XX 空间」勾选 → POST /users 后调 members invite？不——新建 managed 档案由代管人创建时**直接 active** 不走确认（AD-4 例外）。故向导提交改为组合调用：后端新增 `include_in_space_id` 可选字段于 POST /api/users？——否，保持单一职责：**m1c 提供 POST /api/users 的可选 body 字段 `space_membership: {space_id}` 由后端在建档事务内直接写 active member 行**（同事务原子，避免前端两步）。design 变更登记：m1a 的 POST /users 增加 optional 字段（向后兼容）。

## 前端

- stores/spaces.ts + api/spaces.ts；HomeView 重构为「我的家庭空间」页：顶部空间切换器、成员卡列表（临时列表布局，m1d 换画布）、添加成员/邀请入口
- 建档向导接入第四步（选择我的某个空间，默认当前）
- 收到的空间邀请与连接请求合并展示在「待处理」区（accept/reject 按钮——connection 部分审批 UI 本就归 m2c，此处只做 space 邀请的 accept/reject；connection 审批仍占位）

## 回滚点

- 0005 downgrade drop 两表；connection_request 放开逻辑单 commit 便于 revert
