# 新认领用户的个人家族初始化与亲属推荐：技术设计

## 1. 边界与目标

推荐层是 PersonalFamilyView 之上的**只读候选投影**：读时从当前视图派生，不物化为事实、不落新表。Steward 的职责收敛为两件事——认领触发时保证各空间视图进入可计算管道（初始化），以及既有的视图重算与审计。推荐本身无状态，冷却/拒绝记忆落在既有 `BehaviorProjection`。

不复用 `recommendation_matrix.py`（ST-5 建档矩阵）：那是"两个已确认档案之间建空间"的出卡矩阵；本任务是"已认领用户的可见血亲与待确认线索汇聚"。只复用其纯函数风格与机器可读原因码纪律。

## 2. 触发与初始化

### 2.1 补齐 `account.claimed` 发射

`commands/members.py` `change_own_pin` 的强制认领分支（`was_forced and status == "managed"`）补发：

```text
event_type: account.claimed
aggregate_type: account
aggregate_id: actor.account.id
payload: {"user_id": actor.id, "via": "pin_change"}
```

`claim_and_confirm_own_identity` 既有发射加 `via: "claim_and_confirm"` 以便审计区分，其余不变。

### 2.2 认领事件 → 每空间作业 fan-out（收窄既有行为）

`account.claimed` 无 `space_id`，而 `steward.schedule_steward_job_for_event`（`steward.py:322`）对无空间事件的现状是 **fan-out 到实例内全部空间**——对认领事件过宽（会碰触无关空间的活跃 job cursor）。本任务为其增加特判：按 payload `user_id` 查询该用户全部 active `SpaceMember` ∪ active `SpaceProfileRef` 的空间，**只对这些空间**合并/派生作业，`_cause_for_event` 为 `account.claimed` 映射 `cause="claim"`（词表已有该值）。空间单活 partial unique index 兜底幂等；`_schedule_steward_job` 的 memory/rag 跳过分支不影响本事件（前缀不匹配），无需改动。

作业执行完全复用 `_run_space_job` 现有步骤：`rebuild_space_views` 重建 `never_computed`（初始化语义）→ 检测/出卡照旧。推荐不进入作业产物——它是读时派生。

## 3. 推荐派生服务

`backend/app/services/family_recommendations.py`：

```text
recommendations_payload(session, *, account, space_id) -> dict
dismiss_recommendation(session, ctx, *, space_id, target_user_id, category) -> None
```

### 3.1 数据来源与过滤

1. `view = personal_family_view.get_view(...)`；`view.status != "current"` 时返回 `{status, items: []}`（不冒充 current，AC-8）。
2. **血亲条目**：遍历该视图 edges——
   - 排除：`path_json` 任一步骤 `edge_type ∈ {spouse, partner}`（AC-6 配偶边界）；
   - 排除：已在 `BehaviorProjection` 冷却内的目标（`kinship_recommendation_dismissed:<target_user_id>`，`updated_at + STEWARD_COOLDOWN_DAYS` 未到）；
   - 输出字段：`target_user_id`、`display`（直接复用视图节点 `display_json`，桥接节点保持 `lineage_summary` 最小字段）、`term`/`concept_code`/`path_class`（视图边既有字段）、`path_summary`（主路径步骤的 edge_type/subtype 标签序列，不含 fact id 与隐藏节点身份）、`reason_code`（如 `confirmed_blood_path`）。
3. **待确认线索条目**：查询 viewer 为端点、另一端在空间可见集合内的 proposed `SourceFact`（`space_id` 为本空间或 NULL），输出 `target` 最小元数据（`visibility.evaluate` graph purpose，invisible 即丢弃）、`proposed_fact_type`、`reason_code="pending_relation_fact"`；不渲染为确认关系，不带执行动作。
4. 排序确定性：血亲按 `to_user_id` 升序、线索按 fact id 升序；推荐层不做模型排序。

### 3.2 冷却写入

`dismiss_recommendation`：校验账号对该空间 active、目标属于当前可见集合（防枚举 404 统一文案）；写 `BehaviorProjection(space_id, account_id, key="kinship_recommendation_dismissed:<target_user_id>", value_json={"at": iso, "category": ...})`。`steward.py` 的 `PROJECTION_KEY_PREFIXES` 白名单追加该前缀。读取过滤在 §3.1 第 2 步完成；跨空间天然隔离（UNIQUE 键含 space_id）。

## 4. API 合同

`backend/app/api/family_recommendations.py` + `schemas/family_recommendations.py`：

- `GET /api/family-recommendations?space_id=`：认证账号固定 viewer；空间不存在/无 active 成员资格 → 404 `PERSONAL_FAMILY_VIEW_NOT_FOUND`（复用安全文案，防枚举）。响应 `{space_id, view_status, view_version, generated_from_view_version, items: [{category, target_user_id, display, term?, concept_code?, path_class?, path_summary?, proposed_fact_type?, reason_code}], truncated: false}`。
- `POST /api/family-recommendations/dismiss`：body `{space_id, target_user_id, category}` → 204；错误统一 envelope。
- 无独立 ETag（视图 ETag 已覆盖内容新鲜度；推荐条目随视图版本变化，`view_version` 暴露给前端做条件刷新即可）。

## 5. 前端接入

- `frontend/src/types/api.ts`：`FamilyRecommendations` 联合类型 + runtime guard（与后端 Pydantic 一一对应，沿用 personalFamilyView decoder 纪律）。
- `frontend/src/api/familyRecommendations.ts` + `frontend/src/stores/familyRecommendations.ts`：按 `space_id` 缓存、epoch 迟到响应丢弃、切换空间/401/logout 清空、非乐观更新；`load(spaceId)` 依赖 personalFamilyView store 的 `view_version` 做条件重取。
- 最小展示组件（非画布）：候选列表（类别徽章、称谓、理由文案、"不再推荐"入口、待确认线索跳转既有确认流页面）；挂载位置首版放 FamilyTreeView 侧栏或首页摘要区，实现时按现有视图结构择一，画布组件不直接请求 API。
- 候选一律不渲染为已确认关系；`view_status != "current"` 时展示安全状态文案。

## 6. 兼容与回滚

- 无 schema 变更、无新迁移（`BehaviorProjection` 已存在，新键前缀是数据层约定）。
- `change_own_pin` 补发事件对既有调用方无影响（事件此前不存在）；steward fan-out 只新增 `cause="claim"` 作业。
- 回滚 = 代码回退；BehaviorProjection 中已写的 dismissed 键可保留（前缀白名单回退后仅不再被读取，无语义破坏）。

## 7. 测试策略

后端（`tests/test_family_recommendations.py` + 触发用例并入 `test_steward.py`）：

- 触发：两条认领路径都发 `account.claimed`；事件为每个 active 空间派生至多一个 `cause="claim"` 作业；重复事件幂等。
- 初始化：认领后作业把 `never_computed` 视图算到 `current`。
- 血亲：共享祖辈场景出条目与称谓；spouse/partner 路径排除（含"血亲路径必须穿过配偶边"的反例）；bridge 路径条目保持 `lineage_summary` 字段。
- 排除：同空间无路径人物不出条目（Scenario C）；dismissed + 冷却期内不出现、期满重现、跨空间隔离；proposed 确认/争议后线索消失。
- 隐私：无权空间 404 防枚举；`none`/masked 目标零泄漏（含 path_summary 不含隐藏节点）；stale/failed 视图返回空集 + 状态。
- 只读消费：推荐链路全流程不写 SourceFact/SpaceMember/视图行（视图版本号不变断言）——AC-15 合同测试落地。

前端：decoder/guard 单测、store 空间缓存与清理、`view_version` 条件重取、stale 状态文案、候选非确认渲染断言。

验证命令：backend `pytest -q tests/test_family_recommendations.py tests/test_steward.py tests/test_personal_family_view.py`、全量 pytest/mypy/ruff；frontend `type-check/lint/test/build`。
