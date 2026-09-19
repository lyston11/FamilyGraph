# 候选 prompt 契约与规模降级的诊断证据

## 核对范围

2026-09-19，在 `feat/09-19-steward-recommendation-correctness` worktree 的本地主检出代码上实测。**未读取生产库**，不声称线上事实分布或线上已发生降级。

## 证据 1：prompt 缺少方向语义与矛盾约束

`app/services/steward_assist.py::_PROMPTS["candidate"]` 全文 296 字符。逐项关键词检查：

| 应有内容 | 实测 |
| --- | --- |
| `subject` 是 `*_parent` 的父/母（方向合同） | 不含 `父`/`母`/`方向` |
| 不得与已给事实矛盾 | 不含 `矛盾`/`冲突`/`不得与` |
| 不得重复已确认关系 | 不含 `已存在`/`重复` |
| few-shot 示例 | 无任何 JSON 样例 |
| 同辈/兄弟语义 | 不含 `同辈`/`兄弟`/`姐妹` |

方向合同只写在代码里（`app/services/source_facts.py` 模块 docstring：「`*_parent` 类 subject 是 object 的父/母/监护人」），**从未下发模型**。

## 证据 2：模型实际输入不含方向说明

实测 `steward_guard.project_candidate_input` 输出（合成数据）：

```json
{"nodes": [{"node": "n001", "minor": false}, {"node": "n002", "minor": false}, {"node": "n003", "minor": false}],
 "facts": [{"fact_id": 7, "fact_type": "biological_parent", "revision": 1, "subject": "n001", "object": "n002"},
           {"fact_id": 13, "fact_type": "biological_parent", "revision": 1, "subject": "n001", "object": "n003"}]}
```

模型能看到 `n001` 与 `n002`/`n003` 存在 `biological_parent` 事实，但**没有任何字段说明谁是父、谁是子**；也没有性别或长幼信息。

因此「模型把 `(n001, n003)` 输出为 `direct_sibling`」是**在已有该事实的情况下**发生的，不是"没读到家族树"。这证明 prompt 契约不完整，但**不等于**模型缺陷可以被接受 —— 正确性责任在后端校验层（见证据 4）。

## 证据 3：事实规模上限与静默降级

- `steward_assist._candidate_facts` 返回空间内**全部**已授权 confirmed 事实，**无条数上限**（两处调用点均直接传全量）。
- 实测单事实投影约 137 字节；`STEWARD_ASSIST_MAX_PROMPT_BYTES` 默认 64KiB → 约 **478** 条事实即触顶。

```text
1 node 0 facts:        58 bytes
100 nodes 99 facts: 13610 bytes
64KiB 上限约可容纳事实数: 478
```

- 超限路径（`_reserve_attempt`）：写 `StewardModelCall(status="skipped", error_code="prompt_too_large")`，**不发送 HTTP**。
- `skipped` 不在 `_BUDGETED_STATUSES = ("reserved","in_flight","succeeded","failed","degraded","unknown")` → 不计费、不产生 unknown。
- 批次收尾（`execute_batch`）：`terminal_code` 仅由 `"unknown" in all_statuses` 或 `"failed" in all_statuses` 置位 → **超限批次终态为 `applied`**。

后果：空间事实数越过阈值后，候选辅助**永久停止产生候选**，而运营者在既有观测面上看到的是正常 `applied`。

## 证据 4：为什么结构去重捕获不到这类矛盾

`steward_suggestions.project_for_job` 与 `steward_inferred._edge_from_candidate` 的重复判定基于同 `fact_type` 的三元组比较（`find_confirmed_relation` / `_triple_keys`）：

- 已有事实：`(A, B, biological_parent)`
- 模型输出：`(A, B, direct_sibling)`

`kind` 不同 → 结构比较必然漏过。这是并行任务 `09-19-steward-recommendation-correctness` 用 `steward_candidate_policy` 做输出侧语义拦截的原因；本任务只补输入侧契约，**不替代**该拦截。

## 证据 5：可观测面缺口

- `app/api/admin_steward.py` 的 `assist_counts` 只统计 `failed`/`degraded`/`unknown`，**无 `skipped`**。
- 全仓搜索确认无任何地方对 `skipped` 或 `prompt_too_large` 做聚合。
- `steward_assist` 唯一的 `logger.warning` 在批次崩溃路径，不覆盖超限。

## 结论与边界

两项缺陷均可在本地代码与合成数据上复现，不依赖生产数据：

1. prompt 缺方向语义与矛盾禁止，且无示例（R1–R3）。
2. 超限静默降级为 `applied`、无计数、无告警、永久停摆（R5–R7）。

未验证：生产库事实分布是否已越过约 478 条阈值。规划不声称线上已发生该降级，实施阶段如需可读线上量级，必须走只读核对与授权。
