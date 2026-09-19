# Steward 推荐抑制合同（09-19 steward-recommendation-correctness）

## 1. 适用范围

修改 Steward 关系候选的公开性判定、推荐卡资格判定、或建议/推测边的读取与命令重验时读本合同。
两件事：

- **关系候选负向判据**：模型线索与已确认事实冲突时不得公开消费；
- **共同 household 布尔抑制**：双方已在任一 household 空间共同生活时不再推荐共建。

本合同的判定都是**确定性代码**，不依赖模型行为；`prompt` 约束不是安全屏障。

## 2. Signatures

```python
# app/services/steward_candidate_policy.py
CandidateConflictIndex.build_conflict_index(session, *, space_id) -> CandidateConflictIndex
CandidateConflictIndex.build_conflict_index_from_facts(facts) -> CandidateConflictIndex
CandidateConflictIndex.empty() -> CandidateConflictIndex
index.sibling_conflict_reason(subject_user_id, object_user_id) -> str | None
index.conflicts(*, relation_kind, subject_user_id, object_user_id) -> str | None
sibling_conflict_reason(session, *, space_id, subject_user_id, object_user_id) -> str | None

# app/services/steward.py
share_active_household(db, user_a_id, user_b_id) -> bool
card_household_conflict(db, card) -> bool

# app/services/steward_inferred.py
active_edges(db, space_id, *, limit=None, exclude_conflicted=False) -> list[StewardInferredEdge]
```

原因码：`confirmed_parent_child`、`confirmed_biological_ancestry`、
`confirmed_biological_ancestry_budget_exceeded`。

## 3. Contracts

### 3.1 关系候选负向判据

针对 `direct_sibling`（对称，两方向等价）：

| 已知 confirmed 事实 | 结果 |
| --- | --- |
| 任一方向 `biological_parent` / `adoptive_parent` / `step_parent` | 抑制，`confirmed_parent_child` |
| 仅沿 `biological_parent` 可达祖先/后代（≥2 跳） | 抑制，`confirmed_biological_ancestry` |
| 仅 `guardian` / `spouse` / `partner` 或其他不同类型关系 | **不**抑制（不存在「任意不同关系互斥」假设） |
| 收养/继亲/监护混合长路径 | 不归约为血亲，不落入祖先规则 |
| 无冲突且无完整共同父母支撑 | 保持既有 `unsupported` 公开流程，本判据不隐藏 |
| 有完整共同生物父母支撑 | 保持既有 `versioned` 内部证书路径 |

- 事实范围沿用 `_space_visible_user_ids` + `_applicable_confirmed_facts`（当前空间 ∪ 全局、
  confirmed、双端点在内部获权集合）。不得扩大范围，不得调用无空间限定的全局祖先遍历。
- 祖先遍历迭代并去环；节点数超 `MAX_ANCESTRY_NODES` 时返回
  `confirmed_biological_ancestry_budget_exceeded`（fail-closed：不放行也不伪造关系）。
- 只对 `direct_sibling`（`kind == "direct_sibling"`）判定；其他 kind 返回 `None`。

### 3.2 共同 household 布尔抑制

- `share_active_household` 以两个 `SpaceMember` 别名 + `FamilySpace.kind == "household"` +
  双端 `status == "active"` 的 EXISTS 实现，**只返回 bool**。
- 不返回匹配空间的 ID/名称、成员名单、他空间事实或关系；不把该空间加入本空间可见集合；
  不授予任何读取权；不写入日志/响应。它只是推荐矩阵的负向抑制信号，不是跨空间发现能力。
- active `SpaceProfileRef`、`owner` 身份本身、pending/rejected/退出成员均不足以判定已共享。
- `lineage_request_possible` 仍按当前空间 kind 与成员分布计算，与本判定解耦。
- `card_household_conflict` **只对 `household_link`** 生效；`create_household` 被抑制时
  同一对的 `lineage_request` 仍合法。

### 3.3 读取、命令与后台收敛一致

同一判据必须同时用于：候选投影、推测边投影、`source_state`/`effective_state`、
list/detail、notifications、`submit_suggestion`、`confirm_edge`/`reinstate_edge`、
`_revalidate_active_cards`、`steward_delivery._card_review`、`steward_overlay.payload_for`。

- **读侧不落库**：GET/列表/通知按当前状态隐藏不合格项，但**不写库**；持久退役由后台
  review/supersede FSM 完成。终态历史仍可 `open_details` 回看。
- **命令侧重验**：`accept` 与 `execute` 必须重验；`execute` 在 `BEGIN IMMEDIATE` 写锁内
  完成「读资格 → 写空间」，否则两个并发执行者会各建一个家庭空间。
- **`source_state` 优先级**：同类型关系已确认（`resolved`）或关联提案已 `confirmed`
  必须先于冲突退役返回。否则一条真实完成的线索会被改判 `superseded`，而通知域把
  `superseded` 映射为 `revoked`，等于谎称已完成处理被撤销。
- **存量退役不依赖证据 hash 变化**：`supersede_evidence_changed` 同时按语义冲突退役活跃边。
- `active_edges` 的 `exclude_conflicted=True` 只用于**展示消费**；维护枚举
  （`inferred_review` 的 `prepare_intents`）必须用默认 `False`，否则应退役的边永远得不到复核。

## 4. 校验与错误矩阵

| 条件 | 结果 |
| --- | --- |
| 冲突候选投影 | 不创建 suggestion/recipient/notification/edge；候选与 model call 审计保留 |
| 冲突建议的 submit | 409 `SUGGESTION_STATE_CONFLICT`，不创建 SourceFact、无确认副作用 |
| 冲突推断边 confirm/reinstate | 409 `INFERRED_EDGE_STATE_CONFLICT`，不写关系事实 |
| 冲突建议读模型 | `superseded`，`allowed_actions` 仅 `open_details`；私人 `dismissed` 优先级更高 |
| 已 `resolved` 建议遇冲突事实 | 保持 `resolved`（正式结果优先） |
| `household_link` 双方已共享 household | 列表/通知不再作为待处理；`accept` 409 `CARD_EXECUTE_REJECTED`（`household_already_shared`）；`execute` 同 |
| `lineage_request` 同一对 | 不受影响，仍可读取与执行 |
| 祖先遍历超预算 | `confirmed_biological_ancestry_budget_exceeded`，阻止公开消费并记录安全失败 |
| 跨空间查询 | 只返回 bool；无权主体仍按原 403/404 拒绝，响应与日志不含他空间信息 |

## 5. 基线、正例与禁止行为

- 基线：`legacy`/`unsupported` 且无冲突的候选沿原公开流程；`versioned` 双向内部隔离与
  不可变历史不变；候选 payload/status/首次来源不被改写。
- 正例：冲突候选被抑制，同批合法候选照常投影（不得整批丢弃）。
- 正例：已确认同辈关系保持 `resolved`，不因另一条冲突事实显示为已撤销。
- 禁止：把判据扩成「任意不同关系互斥」；用共同父母证书当清理工具；把 `unsupported`
  一律改 `versioned`；为通过测试删除历史行或伪造 `resolved`；在 GET 路径写库。
- 禁止：让 `share_active_household` 返回空间标识、把他空间加入可见集合，或据此实现跨空间亲属发现。

## 6. 必需验证

`tests/test_steward_candidate_policy.py`（判据矩阵）、`test_steward_suggestion_quality.py`
（投影/读模型/提交，含 `resolved` 优先回归）、`test_steward_inferred.py`（投影/退役/转正）、
`test_steward.py`（共同 household 与真实作业链退役）、`test_action_cards_api.py`
（读取/accept/execute/并发写锁）、`test_steward_staged_pipeline.py` 与
`test_steward_overlay_conflict_regression.py`（缓存 overlay 的存量冲突行）。

```bash
cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_steward_candidate_policy.py tests/test_steward_suggestion_quality.py \
  tests/test_steward_inferred.py tests/test_steward.py tests/test_action_cards_api.py \
  tests/test_steward_staged_pipeline.py tests/test_steward_overlay_conflict_regression.py
```

新增抑制规则必须同时给出：正向通过用例、负向拒绝用例、**破环验证**（移除新代码后用例必须
失败，否则它没在守护任何东西）。完整后端 Ruff/format/mypy/pytest 为收尾门槛。

## 7. Wrong vs Correct

```python
# 错误：只有「同类型事实」去重；跨类型的父子冲突逃逸。
if find_confirmed_relation(..., fact_type=raw_kind) is not None:
    continue  # sibling 与 biological_parent 是不同 kind，永远不相等

# 正确：跨类型语义冲突先于同类型去重。
if conflict_index.conflicts(
    relation_kind=raw_kind, subject_user_id=raw_subject, object_user_id=raw_object
) is not None:
    continue
```

```python
# 错误：冲突优先于正式结果，把真实已完成的线索改判为已撤销。
if conflict_index.conflicts(...) is not None:
    return "superseded"
if find_confirmed_relation(...) is not None:
    return "resolved"

# 正确：正式结果优先，冲突只压掉尚未产生结果的活动线索。
if find_confirmed_relation(...) is not None:
    return "resolved"
if linked_proposal is not None and linked_proposal.state == "confirmed":
    return "resolved"
if conflict_index.conflicts(...) is not None:
    return "superseded"
```

关联合同：[Steward 发布与交付](steward-action-card.md)、[候选相关证据版本](steward-candidate-evidence.md)、
[事实与称谓](relationship-intelligence.md)、[数据库事务](database-guidelines.md)。
