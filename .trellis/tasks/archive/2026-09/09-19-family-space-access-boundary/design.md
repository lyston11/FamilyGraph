# 技术设计：家庭空间与家族空间分离、家族树访问独立审批

## 1. 产品边界

本次严格分离两种空间和两种申请：

- `household` membership 只授权 household 范围；`FamilySpace.lineage_space_id` 只是配对关系，不是成员资格继承关系。
- household 加入申请由该 household 的 active `space_admin`（即被加入空间的那个人）审批；批准后申请人仍然没有对应 lineage membership。
- 读取家族树是第二条独立的 pending `SpaceMember` 申请：由家庭空间成员发起（`request_lineage_access`），由该 lineage 的 active `space_admin`（即被加入空间的那个人）审批；批准后才成为 lineage active member，读取 PFV/graph。
- 所有 lineage 读取端点只按 active lineage membership 授权。关系路径、visibility、household membership、配对字段均不能替代该授权。

## 2. 根因（已在运行实例上核实）

`space_members` 中 `朱元璋` 在 `李氏家族`（lineage）有一条由种子写入的 **active** 成员行（`added_by=李贞`）。该行来自演示清单，不是用户决定；它直接让一个只加入 `李家` 家庭空间的人读到了李氏家族树。同时 `朱氏皇族` 名册缺 `朱佛女`，且种子没有 `direct_sibling` 事实，所以姐弟结构边无法生成。

## 3. 审批主体

不新增 schema：审批人就是该空间的 active `space_admin`（owner 即「被加入空间的那个人」）。
`space_fsm.transition` 对 `added_by == user_id` 的本人申请只允许该空间管理员 accept/reject，
申请人只能 withdraw，不能自批；他人邀请仍由受邀人本人接受。通知收件人沿用同一判据
（本人申请通知空间管理员）。这避免为一个已有等价判据引入新列与新迁移。

## 4. 申请入口

- `request_join_by_user` 只解析 target 作为 owner 的 `household`，保留 target 可见性和安全 404；准入要求双方同属至少一个 active lineage，避免任意可见用户进入他人家庭空间。
- `request_lineage_access`（`POST /spaces/lineage-access-requests`）从家庭空间出发解析配对 lineage，要求 actor 是该 household 的 active 成员、尚未是 lineage 成员，且该 lineage 存在可审批的 active 管理员；只创建 pending，不激活。
- ActionCard `lineage_request` 沿用既有 `request_lineage_membership` 命令，同样只产生 pending。

## 5. 读取授权

`family_projection.authorized_space_or_404` / `steward_views.authorize` 的 active `SpaceMember` 校验作为共享边界，明确不增加 household-to-lineage 回退；`graph/me` 在指定空间时同样只按 active membership 授权。household 端点继续使用 household 专用 helper；治理端点继续使用 active `space_admin`。

## 6. 共同家庭幂等

保留推荐层 `share_active_household` 和 ActionCard execute 的写锁冲突复核，并在 `create_shared_household` 命令入口增加同一 household active membership 检查：返回现有空间、不新增 FamilySpace/SpaceMember；不同 household 不合并，pending/removed 不视为共同家庭。

## 7. 演示种子与关系

- `朱元璋` 从 `李氏家族` lineage 名册移除，只保留在 `李家` household；`朱佛女` 增加到 `朱氏皇族`。
- 增加独立 confirmed `direct_sibling` SourceFact seed（v1 Relation 无法表达 sibling）。
- 生产迁移 0052 一次性删除旧清单授予的那条越权 lineage 成员行（insert-only 收敛永远不会删行，所以必须由迁移修正）。删除条件极窄：指定 lineage 空间名 + 成员名 + active + 由该空间 owner 添加；不碰用户自己的申请、不碰其他空间。
- 该迁移是**纯数据修正**（无 schema 变更）：`batch_alter_table` 重建会静默丢失既有 FK 的 `ON DELETE CASCADE`（实测首版把 CASCADE 改成 NO ACTION），故不重建表。
- downgrade 只回退版本，不恢复已删除的越权行（399 合同），并先履行父级 0051 的源计时拒绝合同。

## 8. 不做的事

不扩大跨空间 discovery/bridge/visibility 规则，不把普通 household 成员静默加入 lineage，不删除用户已有空间或事实，不在 GET 中写库，不用生产手工数据修复代替迁移和代码边界。
