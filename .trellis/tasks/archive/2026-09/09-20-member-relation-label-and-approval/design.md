# 技术设计：房主审批链 + 成员间自由关系词标注

## 1. 审批链（新增两个字段表达状态）

现状 `space_members` 只有 `status`，无法区分「等房主批准」与「等受邀人接受」。新增：

- `origin`：`'invite' | 'join_request' | 'code'`，NULL = 迁移前旧行（保持旧语义）。
- `owner_approved_at`：房主批准时刻；NULL = 未批准。

三条链：

| origin | 房主批准后 | 最终生效 |
|---|---|---|
| `invite` | 转为「等受邀人接受」 | 受邀人 accept → active |
| `join_request` | 同事务置 active（申请人提交即其同意） | — |
| `code` | 同事务置 active（兑换人提交即其同意） | — |
| NULL（旧行） | 不适用 | 沿用旧行为：受邀人本人 accept |

`space_fsm.transition` 的 `accept` 分支按 `origin` 判定：

- `owner_approved_at IS NULL` 且 origin ∈ {invite, join_request, code} → 403（等待房主批准），只有该空间 active `space_admin` 可先批准；
- `invite` 且已批准 → 仅受邀人本人可 accept；
- `join_request` / `code` 已批准时，accept 由房主批准动作内部完成，外部再 accept 返回 409。

新增 `approve_membership` 命令（房主批准）：校验 actor 是该空间 active `space_admin`，且 `added_by != actor_id`（不得自批），写 `owner_approved_at`，按 origin 决定是否同事务置 active。

## 2. 关系词标注（新表）

```
member_relation_labels
  id, space_id (FK CASCADE), user_a_id, user_b_id, label(String(64)),
  created_by, created_at, updated_at
  UNIQUE(space_id, user_a_id, user_b_id)   # 端点规范化：user_a_id < user_b_id
  CHECK(user_a_id != user_b_id), CHECK(length(label) BETWEEN 1 AND 64)
```

- 无序对唯一：`(min(id), max(id))` 规范化后落库，保证一对人一条标注。
- `label` 是**自由文本**，不做词表约束（兄弟/朋友/闺蜜均可），长度上限 64（与既有 `relations.label` 同规格）。
- **绝不进入** `source_facts` / `relations` / `social_relations`；它只是标注，不参与任何亲属推导。

写入路径：

- 三条加入链在提交时**必填** `relation_label`，服务端写 `(initiator, counterpart)` 对：
  invite → (发起人, 受邀人)；join_request → (申请人, 目标)；code → (兑换人, 码创建者)。
- 修改：`PUT /spaces/{space_id}/member-relation-label`，仅该对两端本人可改（`is_self` 判定），改完即时生效，无需对方确认、无需房主审批。
- 清空 label = 删除该行（移除标注边）。

## 3. 读取投影（不参与快照版本）

标注边在**读取时**按授权节点集合过滤，不进 steward 快照（否则要动版本与失效链，收益为零）：

- 新增只读 helper `member_relation_labels_for(session, *, space_id, visible_ids)`，返回两端都在 `visible_ids` 内的 `{id, from_user_id, to_user_id, label}`。
- 接入两处读取路径：`steward_views.payload_for`（生产实际路径）与 `personal_family_view._view_payload_for_view`（fallback）。
- PFV 载荷新增 `label_edges` 字段；`PersonalFamilyViewOut` 同步。
- 任一端退出/被踢/不再可见 → 该边立即不再返回（`visible_ids` 是当次授权结果）。

## 4. 前端

- 家族树：`label_edges` 作为**独立标注层**渲染（与 confirmed 结构边、推测虚线边并列，第三种样式），不参与布局与世代计算。
- 个人页：显示 viewer↔target 的标注词，并给两端本人提供修改入口。
- 三条加入表单（公示页双向弹窗、空间治理面板邀请、设置页兑换邀请码）各加一个「与对方的关系」自由文本输入，必填、上限 64。

## 5. 兼容与回退

- 迁移 0053：加两列（可空）+ 建表；旧 pending 行 `origin IS NULL` 保持旧语义，不追溯要求房主批准。
- 回退：先回退前端入口与读取字段，再回退命令与迁移。
