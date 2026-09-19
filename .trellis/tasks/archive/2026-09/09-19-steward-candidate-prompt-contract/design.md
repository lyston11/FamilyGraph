# Design：候选 prompt 契约与规模降级

## 1. 基线与边界

基于主检出与 `feat/09-19-steward-recommendation-correctness` worktree 的实测代码：

- `steward_assist._PROMPTS["candidate"]`：296 字符，无方向语义、无矛盾禁止、无示例。
- `steward_guard.project_candidate_input`：只投影 `{node, minor}` 与 `{fact_id, fact_type, revision, subject, object}`。
- `steward_guard.validate_candidate_output`：封闭结构校验（kind 白名单/代号格式/端点互异/授权范围内/非未成年）。
- `_candidate_facts`：返回全部已授权 confirmed 事实，无上限。
- `_reserve_attempt`：超 `STEWARD_ASSIST_MAX_PROMPT_BYTES` 时落 `skipped` + `prompt_too_large`。
- 批次收尾：`terminal_code` 只由 `unknown`/`transport_failed` 产生，故超限批次为 `applied`。
- `admin_steward.py`：`assist_counts` 只含 `failed`/`degraded`/`unknown`；无 `skipped` 统计。

与并行任务 `09-19-steward-recommendation-correctness` 的边界：该任务新增 `steward_candidate_policy` 做**输出侧**语义拦截；本任务只改**输入侧**契约与规模处理。两者在 `steward_assist.py`/`steward_guard.py` 有交集，必须串行。

## 2. 提示词契约（R1–R4）

在 `_PROMPTS["candidate"]` 中补齐，保持"只输出 JSON、无自由文本"的既有形状：

1. **方向语义**：明确 `biological_parent`/`adoptive_parent`/`step_parent`/`guardian` 的 `subject` 是 `object` 的父/母/监护人；`spouse`/`partner`/`direct_sibling` 对称。
2. **矛盾与重复禁止**：不得输出与 `facts` 清单矛盾或重复的候选；同两端点已有任一种亲属事实时，不得再输出另一种与之冲突的 kind（显式举 `biological_parent(A,B)` 与 `direct_sibling(A,B)` 互斥为例）。措辞只描述输入内可判定的关系，不引入现实世界的多重亲属身份断言。
3. **最小示例**：给一个符合 schema 的 JSON 数组示例（使用 `n001`/`n002` 这类占位代号，不含真实姓名）。示例不得包含具体真实家族数据。
4. **版本可追溯**：不改 `_prompt_version()` 机制；它在 `test_steward_eval.py` 中已对 `_PROMPTS` 做规范化哈希。新增回归断言"prompt 变更 → 版本变化"，并确认评测报告的 `prompt_version` 字段随之更新。

不改 `project_candidate_input` 的字段集合（R9）。若认为"性别/长幼"有助于模型判断，属独立增强项，须另行论证其隐私影响，本任务不做。

## 3. 规模降级策略（R5–R7）

### 3.1 如实结算

`prompt_too_large` 目前与 `budget_exhausted`/`insufficient_budget` 共用 `skipped` 路径。需要区分：

- 预算类 `skipped` 是"本次不发送，稍后可重试"的良性状态；
- `prompt_too_large` 是**确定性输入问题**：同一输入永远超限，重试无意义，但它**不是**上游失败。

设计取向：`prompt_too_large` 必须进入批次终态与错误码，使运营者与后续逻辑都能看到"本批候选未产生"。**不**把它归入 `unknown`（不伪造上游不确定性），也**不**让它消耗网络预算或触发重放。具体终态取值在实现阶段对齐既有状态机后确定（倾向批次 `error_code="prompt_too_large"` 且终态非 `applied`），并在 Spec 中写死该映射。

### 3.2 可收敛的有界输入

单纯报错会让大空间永久失去候选辅助，因此必须给出确定性策略。两个候选方案：

| 方案 | 做法 | 优点 | 风险 |
| --- | --- | --- | --- |
| A：有界事实子集 | 按稳定排序（fact_id 升序）取前 N 条，N 由字节预算反推 | 输入自洽，无截断歧义 | 尾部事实永不参与候选；可能系统性遗漏 |
| B：标注截断 | 全量排序后取能装下的前缀，并在输入中显式声明 `truncated: true` 与总数 | 模型可知输入不完整，行为可解释 | 仍遗漏尾部；需模型正确理解该标记 |

**推荐 A**，理由：与既有"确定性、可复现、不依赖模型理解元信息"的模块风格一致；`truncated` 标记属于让模型自行处理不完整性，与"prompt 不是安全屏障"的既有立场冲突。最终选择需在设计评审时确认。

无论选哪个，都必须满足：

- 子集选择**确定且可复现**（同输入 → 同子集），使 `input_hash` 稳定、`prompt_digest` 可复算，不破坏发送前 fence 校验。
- 子集大小由 `STEWARD_ASSIST_MAX_PROMPT_BYTES` 反推，不新增硬编码常量，也不放宽上限。
- 子集化后**仍可能超限**（如单条事实极长）时，回落为 `prompt_too_large` 并如实结算（3.1）。
- 不因截断而放宽任何输出校验（R8）。

### 3.3 可观测性

在 `admin_steward.py` 的既有 metrics 形状内新增 `skipped` 计数（至少按 `error_code` 区分 `prompt_too_large`）。响应继续只含计数与安全错误码，不含 prompt、事实、姓名或空间内部内容（R6、R9）。若既有 `alerts` 机制适合，则同时产出 `queue`/`assist` 类告警；否则仅计数，不新造告警框架。

## 4. 评测与回归（AC1–AC8）

- prompt 断言：对关键条款逐条检查（方向、矛盾禁止、示例可解析），不做宽泛子串匹配，避免"改了措辞就通过"。
- 对抗 fixture：新增"与输入事实矛盾的同辈候选"用例。本任务只要求用例存在并被如实记录结果，**不要求**校验器拦截语义矛盾（那是并行任务的输出侧职责）。
- `prompt_version` 回归：prompt 变更后版本必须变化；未变更时必须稳定。
- 超限回归：构造超限事实集，断言 `error_code`、批次终态、admin 计数；断言无网络调用、无预算消耗、无重放。
- 子集回归：断言确定性、可复现、输出仍过全部校验；断言未截断场景行为与现在逐字节一致。
- 既有门槛：硬安全门禁 100%、负例全空、候选召回 ≥0.9、节点代号投影/未成年过滤/字节上界/预算与租约栅栏回归不变。

## 5. 变更面与排除项

| 文件 | 预期职责 |
| --- | --- |
| `app/services/steward_assist.py` | prompt 文本、`_candidate_facts` 有界子集、超限终态结算 |
| `app/services/steward_guard.py` | 仅当示例/契约断言需要共享常量时改动；校验强度不变 |
| `app/api/admin_steward.py` | `skipped`/`prompt_too_large` 计数投影 |
| `tests/test_steward_assist*.py`、`tests/test_steward_eval.py`、fixtures | prompt 断言、超限与子集回归、对抗用例 |
| Spec 叶（候选证据/action-card 或新建叶） | prompt 契约、规模策略、终态映射 |

默认不改 schema、不新增开关、不新增依赖、不改前端。若超限终态需要新增状态枚举或迁移，先更新本设计并重新审阅。

## 6. 串行与发布

顺序：先完成并集成 `09-19-steward-recommendation-correctness`（输出侧语义拦截），再做本任务（输入侧契约），避免两者同时改 `steward_assist.py`/`steward_guard.py` 产生冲突。

prompt 变更会改变 `prompt_digest`：已 `reserved` 未发送的 attempt 在发送前比对 digest 时会判为 `evidence_changed` 并 supersede。这是既有 fence 的正确行为，但需在部署说明中记录"prompt 变更会使在途未发送的候选 attempt 失效"。不影响已结算结果，不需迁移。

回退：prompt 与子集策略均为纯代码；回退不恢复任何被截断的历史数据，也不改变已产生的结果。
