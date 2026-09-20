# 技术设计：家族空间内双向加入

## 1. 信任单元

「当前家族空间」（lineage space）是本次的信任单元与授权范围：

- 双方必须都是该 lineage 的 active 成员（**同族**）才能互相加入家庭空间；
- 邀请只能落在**我**在该 lineage 下的家庭空间；申请只能落在**对方**在该 lineage 下的家庭空间；
- 空间归属判定：`family_spaces.kind='household' AND family_spaces.lineage_space_id = <该 lineage id>`。

不同族的人只能走邀请码途径（既有 `POST /me/invite-codes/redeem`，设置页已实现）。

## 2. 只读投影：一个端点给出两个方向

替换上一版的 `GET /spaces/household-invite-options`：

```
GET /api/spaces/family-space-options?lineage_space_id=<id>&target_user_id=<id>
→ {
    "lineage_space_id": <id>,
    "lineage_space_name": "...",
    "shares_lineage": true,
    "invite": [ { space_id, space_name, target_status: active|pending|none } ],
    "join":   [ { space_id, space_name, my_status:     active|pending|none } ]
  }
```

- `shares_lineage=false` 时两个列表都为空，前端显示「不同族 → 走邀请码」提示；不因此报错（这是正常状态，不是探测）。
- 目标不可见（`visibility.evaluate` 失败）→ 与既有同形状 404，不做存在性探针。
- 调用者必须是该 lineage 的 active 成员；否则 404（与「空间不存在」同形状）。
- 只读：不写库、不产生通知、不创建 pending 行。
- 为什么合并成一个端点：两个方向共享同一套「同族 + 空间归属」判定与同一份 lineage 名称，分成两个请求会重复判定并让前端做两次加载态。

## 3. 写端点收紧

### 3.1 邀请

沿用 `POST /spaces/{space_id}/members`（`invite_member`）。新增校验：调用者必须与被邀请目标同族，且 `space_id` 是该 lineage 下调用者的家庭空间。

- 判定放在命令层（`commands.spaces.invite_member` 增加可选 `lineage_space_id`），使「从公示页发起」的邀请受家族限定，而既有空间治理面板的邀请路径保持原样（管理员在空间内邀请本来就不需要跨族证明）。
- 实现方式：新增一个薄的命令 `invite_into_family_household(session, ctx, *, lineage_space_id, space_id, user_id)`，复用 `invite_member` 的内部逻辑（`space_fsm.invite` + 事件 + 审计），只多两层校验。既有 `invite_member` 不动，避免影响空间治理面板与 invite code 路径。

### 3.2 申请

`POST /spaces/join-by-user` 增加必填 `lineage_space_id` 与可选 `space_id`：

- 重新校验：`space_id` 是**对方**在该 lineage 下的家庭空间；双方都是该 lineage 的 active 成员；我尚未是该空间 active 成员。
- 不再使用「全局 owner 优先解析」——那是上一版遗留的跨族回退，本次移除。目标在该 lineage 下没有家庭空间时 → 409 `SPACE_JOIN_NO_TARGET_SPACE`。
- 仍只产生 pending，由该空间管理员批准；`added_by == user_id` 时申请人不得自批（既有 `space_fsm` 判定）。

## 4. 前端

- `PersonProfileView`：解析当前上下文对应的 lineage（lineage 上下文直接用；household 上下文用 `lineageForSpace` 配对）。解析不出 lineage 时不显示入口。
- 弹窗改为两组：`邀请 TA 加入我的家庭空间` / `申请加入 TA 的家庭空间`，各自列出候选并显示状态；`shares_lineage=false` 时两组都不显示，改为提示可走邀请码途径（附设置页入口）。
- 按钮文案随所选方向与空间变化：`发送邀请` / `发送加入申请`。
- 不在前端做授权推断：入口与禁用只是 UX，服务端仍是唯一边界。

## 5. 兼容与回滚

- 移除 `household-invite-options` 端点与其前端调用（同一次改动内替换，不保留双轨）。
- `join-by-user` 增加必填参数是行为收紧；调用方只有公示页（`stores/graph.ts` 的 `requestJoin` 目前无页面使用），同步更新。
- 无数据库迁移；回滚只需回退端点与前端入口。
