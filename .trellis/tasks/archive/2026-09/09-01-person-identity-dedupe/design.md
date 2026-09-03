# 人物重复建档防护与 Steward 回溯审计：技术设计

## 1. 边界与目标

本任务只做两件事：把 `09-01-personal-family-view` 随 8c1c892 附带交付的去重基线固化为受回归保护的现实（不重写），以及补齐"残留重复 → 合并处置 → 投影失效"的最后一段链路。不新增表、不改既有状态机、不扩 `CARD_KINDS`。

判定口径继续以 `services/person_identity.py` 为唯一真源；合并命令是它的第三个调用方（前两个：写入门禁、回溯审计）。

## 2. 合并命令设计

### 2.1 服务合同

`backend/app/commands/members.py` 新增（与建档/删除同文件，共享 custody 与快照纪律）：

```text
merge_duplicate_profile(session, ctx, *,
    survivor_id: int,
    retired_id: int,
    confirm_same_person: bool,   # 显式两步确认；缺省 False
) -> MergedProfile
```

单事务（`command_transaction(immediate=True)`，与建档门禁同一并发合同）内按序执行：

1. 加载两档案（不存在 → 404 `USER_NOT_FOUND`，防枚举统一文案）。
2. 授权：`custody.assert_can_edit(actor, survivor)` 与 `assert_can_edit(actor, retired)` 双向通过；空间管理员角色不参与判定。
3. 状态门：两档案 `account.status == "managed"`（未认领）。任一 claimed → 409 `IDENTITY_INVALID_TRANSITION`，detail 引导 `claim_dispute`。
4. 重复复核：对该对现算 `person_identity.match_strength`；`none` → 409 `VALIDATION_ERROR`（同名不同人保护）。strong/weak 均要求 `confirm_same_person=True`，否则 409 `PERSON_DUPLICATE_AMBIGUOUS`（weak 语义与建档门禁一致）。
5. owner 义务预检：`assert_no_owner_obligations(ctx, retired_id)`（managed 档案不应持有空间，防御性兜底，同 `delete_profile_core`）。
6. 迁移改指向（见 §3 表清单）。
7. 吊销 retired 活跃会话（managed 但可能存在未完成首登的 refresh session，复用 `refresh_session.revoke_all_active`）。
8. `session.delete(retired)`；flush 竞态兜底同 `delete_profile_core`（RESTRICT 竞态 → rollback + 409 `OWNER_TRANSFER_REQUIRED`）。
9. `emit(profile.merged)` + `audit.write_audit`（retired 全量快照 + survivor 引用 + 迁移计数）。

### 2.2 迁移改指向清单（FK survey 结论）

| 表 | 现有 FK 行为 | 合并动作 |
| --- | --- | --- |
| `space_profile_refs.user_id` | CASCADE | **改指向 survivor**（provisional 空间引用是身份承载行，CASCADE 会静默丢失） |
| `source_facts.subject_user_id` / `object_user_id` | CASCADE | **改指向 survivor**（confirmed 结构事实是图真源，丢失即断树） |
| `attachments.user_id`（档案照片 FK） | CASCADE | **改指向 survivor**（照片随档案保留） |
| `attachments` 第二个 users FK（上传者，无 ondelete） | 默认 NO ACTION | 改指向 survivor（否则阻塞删除） |
| `profile_fact_reviews.profile_id` | CASCADE | 留给 CASCADE 清除（proposed 提议项；survivor 自己的清单不受影响） |
| `derived_facts` 两端 | CASCADE | CASCADE 清除，Steward 下轮全量重算（`_rebuild_space_derived`） |
| `relations`（旧模型）、`node_positions` | CASCADE | CASCADE 清除 |
| `action_cards.subject/object_user_id` | CASCADE | CASCADE 清除（关于 retired 的候选证据随之失效，符合语义） |
| `personal_family_view*`（root/node/edge 的 users FK） | CASCADE | CASCADE 清除 retired 行；同空间视图由 R4 失效 + 重建收敛 |
| `notifications` / `audit_log` / `rag` 的 user FK | SET NULL | 不处理（审计与 tombstone 天然保留） |
| `accounts.user_id`（retired 登录凭据） | CASCADE | 随 retired 删除（managed 凭据作废） |

`family_spaces.owner_id` 为 RESTRICT：managed 档案理论上有 owner 行，由第 5 步预检 + 第 8 步 flush 兜底。

### 2.3 source_facts 指向迁移的事件合同

指向变化不是事实内容变化：直接 `UPDATE subject_user_id/object_user_id` + `revision += 1`，并走 `services/source_facts.py` 既有 `_emit_fact_event` 发 `source_fact.revised`（payload 标注 `identity_merge=true` 与 merged 事件 id）。若既有 `revise_source_fact` 签名无法承载指向修订，则在同服务内新增 `repoint_fact_for_identity_merge` 私有函数复用 `_emit_fact_event`，不绕过事件合同。DerivedFact 缓存由既有 `source_fact.` 失效路径清两端，无需新代码。

## 3. 领域事件与失效接入

### 3.1 `profile.merged`

```text
event_type: profile.merged
aggregate_type: profile
aggregate_id: survivor_id
payload: {
  "survivor_id": int,
  "retired_id": int,
  "space_ids": [int, ...],          # 迁移前从两侧 active refs/member 收集
  "moved": {"space_profile_refs": n, "source_facts": n, "attachments": n}
}
space_id: None（跨空间聚合事件；空间范围在 payload.space_ids）
```

### 3.2 失效监听扩展

`domain_events.py` 的 `_invalidate_personal_family_view` 前缀元组追加 `"profile."`：

- `profile.merged` / `profile.deleted` → 按 `payload.space_ids` + `event.space_id` 逐空间 `invalidate_space_views`。
- `profile.created` / `profile.updated` 顺带接入（同一前缀）：新建 provisional 人物与改名/补生日都会改变可见集合与重复判定，标 stale 属正确语义且成本为异步重建。
- `profile.deleted` 现有 `emit` 不携带空间信息：在 `delete_profile_core` 删除前查询 retired 的 active `space_profile_refs`/`space_members` 并写入 `payload.space_ids`（本任务一并补齐，改动限于同一文件）。

已知相邻缺陷不在本任务修：`space.membership.changed` 与监听前缀 `space_member.` 不匹配（前缀从未被发射）。该缺陷归 `09-02-personal-family-view-followup` 的 PFV-F4 回归收口；本任务若先行合入，只追加 `profile.` 前缀，不动既有前缀元组的其余项。

## 4. 只读查询合同

`GET /api/spaces/{space_id}/duplicate-people`：

- 授权：空间 active 成员且对空间内人物具备 custody 编辑视角的操作者；无权上下文统一 404（沿用 PersonalFamilyView 防枚举文案纪律）。
- 实现：取空间可见集合（`_space_visible_user_ids` 同口径）→ `person_identity.find_duplicate_pairs` → 每对返回 `{user_ids, strength, names, birth_known_flags}`；只含 id/姓名/生日有无，不含 bio/关系/附件。
- 该端点是处置入口的发现面；Steward 事件仍负责异步告警语义，不在此端点重复。

`POST /api/spaces/{space_id}/duplicate-people/merge`，body `{survivor_user_id, retired_user_id, confirm_same_person}` → 调用命令层；错误走统一 envelope。路由挂既有 spaces 治理路由（`api/spaces.py`），schema 落 `schemas/space.py` 附近，命令落 `commands/members.py`。

## 5. 幂等、并发与错误矩阵

| 场景 | 行为 |
| --- | --- |
| retired 已不存在（重放） | 幂等成功，返回 merged 结果标记 `already_merged=true`；不重复写事件 |
| 并发合并同一对 | `BEGIN IMMEDIATE` 串行化；后到者走幂等分支 |
| revision/CAS | 命令不涉及卡片 revision；source_fact revision+1 自带并发水位 |
| survivor == retired | 422 `VALIDATION_ERROR` |
| 任一 claimed | 409 引导 claim_dispute |
| strength == none | 409 同名不同人保护 |
| confirm 缺失 | 409 `PERSON_DUPLICATE_AMBIGUOUS` |
| owner 义务/RESTRICT 竞态 | 409 `OWNER_TRANSFER_REQUIRED` |

## 6. 兼容与回滚

- 无 schema 变更、无新迁移；回滚 = 代码回退，无需数据迁移。
- `profile.` 前缀接入只增加失效范围（异步重建更勤），不改变任何读取授权路径；回滚后视图停留在旧收敛行为，无数据损坏。
- 旧 `/api/graph/me`、Steward 作业结构、建档门禁行为全部不变。

## 7. 测试策略

后端（`tests/test_person_dedupe.py` 扩展 + 新 `test_profile_merge.py`）：

- 合并主路径：refs/facts/attachments 迁移计数、retired 删除、survivor 字段未被覆写、事件 payload 与 audit 快照断言。
- 状态门：claimed×managed、claimed×claimed 拒绝并提示 dispute；managed×managed 通过。
- 复核门：none 拒绝；weak 无 confirm 拒绝；strong 无 confirm 拒绝。
- 授权门：单向 custody 拒绝；space_admin 不放行；防枚举 404。
- 失效联动：合并/删除后同空间视图 stale（`invalidate_space_views` 被以正确 space_ids 调用）；Steward 下轮作业重建后 retired 不在视图（Scenario E 回归：两棵个人树经 survivor 连接）。
- 审计收敛：合并前 `duplicate_person_strong` 签名存在，合并后下一次 `_detect_findings` 无该签名；历史事件行未被删除。
- 幂等：重放合并 already_merged；并发合并恰好一次生效。
- `profile.deleted` 携带 space_ids 的回归（删除后失效生效）。

验证命令沿用 backend 全量门禁：`pytest -q`（deselect 已知 ownership-transfer deadlock 项）、`mypy app`、`ruff check . && ruff format --check .`；无迁移故不需要 Alembic 往返。
