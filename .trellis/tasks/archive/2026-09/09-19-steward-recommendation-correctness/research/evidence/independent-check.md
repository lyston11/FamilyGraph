# 自审记录（不派子智能体，主会话自查）

任务：`09-19-steward-recommendation-correctness`
日期：2026-09-19
方式：主会话自查 + 定向探针脚本 + 破环验证（移除新代码看用例是否失败）

## 一、发现并修复的缺陷

### D1（中）：`source_state` 覆盖真实已完成的线索

**现象**：一条 `direct_sibling` 建议被真实确认后成为 `resolved`；此后同一对出现另一条冲突的
`biological_parent` 事实，读模型把它改判为 `superseded`。由于 `notifications.py` 把
`superseded` 映射为域状态 `revoked`（前端显示「已撤销」），等于向用户谎称已完成的处理被撤销。

**违反**：R8（不自动撤销用户确认）、AC8（受保护历史不被改写）。

**探针证据**：

```text
初始: proposed
确认同辈后: DB status='resolved' (处理 0 行), 读模型=resolved
冲突亲子出现后: DB status='resolved', 读模型=superseded   ← 缺陷
```

**根因**：我把冲突判据放在 `find_confirmed_relation` 之前，冲突优先于「已产生正式结果」。

**修复**：调整 `source_state` 优先级 —— 同类型关系已确认（`resolved`）与关联提案已
`confirmed` 先返回，冲突仅在尚无正式结果时生效。

**修复后**：

```text
冲突亲子出现后: DB status='resolved', 读模型=resolved
```

**永久回归**：`tests/test_steward_suggestion_quality.py::test_confirmed_sibling_stays_resolved_despite_unrelated_conflicting_parent`

### D2（低，已撤销）：PFV confirmed 读取路径的过滤是死代码

我一度在 `personal_family_view._view_payload_for_view` 增加冲突过滤，并写了两个用例。破环
验证显示：**移除该过滤后两个用例仍然通过**。

**原因**：PFV confirmed 读取路径有新鲜度门 —— `view_is_current` 比较
`steward_snapshot.core_versions(input_versions)`（含 `source_facts` 触发器推进的 structural
revision）。冲突事实一写入，视图即判 stale 并在进入推测边循环前短路（探针实测返回
`status='stale'`、无 `inferred_edges` 键）。因此该分支对「同进程内新增冲突事实」不可达。

**处置**：删除该过滤与两个无守护力的用例，不保留无法证明必要性的代码。

### D3（验证通过）：overlay 读取过滤确实守护存量缓存

对 `steward_overlay.payload_for` 的同类怀疑做了破环验证，结论相反 —— 它**是**必要的：

- 正常路径下证据变化会让 overlay 整行版本失配而失效，所以过滤对「同进程新增冲突事实」不可达；
- 但**修复前已缓存的 overlay 行**（input_versions 仍匹配、未过期、witness 路径合法）中含
  冲突边时，只有该过滤能拦住它。

```text
去掉冲突过滤后:  saved edges: ['direct_sibling']
                overlay 返回 edges: ['direct_sibling']   ← 泄漏
恢复后:          overlay 返回 edges: []
```

**永久回归**：`tests/test_steward_overlay_conflict_regression.py::test_legacy_cached_overlay_row_with_conflicting_edge_is_not_served`

## 二、逐项核验结果

| 项 | 结论 | 依据 |
| --- | --- | --- |
| design §2.2 全部消费者接线 | 已接线 | `grep` 逐点确认：建议投影、推测边投影、`source_state`/`effective_state`、list/detail、submit、confirm/reinstate、`_revalidate_active_cards`、`card_review`、overlay、rebuild |
| 未改 prompt / guard 校验强度 | 确认未改 | `git diff --stat` 对 `steward_assist.py`/`steward_guard.py`/`steward_candidate_evidence.py` 为空 |
| `share_active_household` 只返回 bool | 确认 | `select(FamilySpace.id)...limit(1)` 包在 `bool()` 内；唯二调用点分别赋给矩阵输入与卡判定，均不外传 |
| 不泄漏他空间信息 | 确认 | 函数无返回值旁路；不在日志/响应中出现；未加入 visible 集合 |
| `lineage_request` 不被误抑制 | 确认 | `card_household_conflict` 只对 `household_link` 生效；`test_lineage_request_is_kept_when_household_already_shared`、`test_lineage_card_kept_when_pair_shares_household` 通过 |
| guardian/spouse 不误抑制 sibling | 确认 | 探针 `guardian → None`；政策单测覆盖三类 |
| 写锁必要性 | 确认 | 移除 `immediate=True` 后新并发用例连续 5 次稳定失败（`[200, 200]`），恢复后通过 |
| 评测硬门禁 | 通过 | `test_steward_eval.py` 通过（硬门禁 100%、负例全空、召回 ≥0.9） |
| 既有历史保全 | 确认 | 存量建议/边/卡/通知行未被删除或改写；dry-run 把已关联事实的建议标为人工核查 |

## 三、未能验证 / 限制

- **未读取生产库**：不声称线上对象数量或状态。D3 的存量场景是合成的「修复前缓存行」，不等于
  线上确实存在这类行；线上是否命中需 S7 授权后用 dry-run 逐条核对。
- **未做真实模型质量评测**：本任务屏障是确定性代码。
- 未跑移动视口截图与真实浏览器上的 overlay 存量场景（已用隔离 API/DB 断言替代）。
- D2 的结论依赖「PFV 视图新鲜度包含 source_facts structural revision」这一实测行为；若未来
  新鲜度判定被弱化，该路径会重新变为可达。
