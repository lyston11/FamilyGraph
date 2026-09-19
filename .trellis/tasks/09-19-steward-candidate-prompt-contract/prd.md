# PRD：管家候选辅助的提示词契约与事实规模降级

## 目标

修复管家候选辅助（`STEWARD_ASSIST_CANDIDATE`）的两个独立缺陷：

1. **提示词契约不完整**：模型无法从输入得知亲属关系的方向语义，也没有任何"不得与已给事实矛盾/重复"的约束，导致在已知 `A 是 B 的父亲` 的情况下仍输出 `direct_sibling(A, B)`。
2. **事实规模超限静默降级**：事实数超过 prompt 字节上限时，候选辅助既不产生结果、也不报错、也不告警，批次仍记为 `applied`。

本任务只处理**候选辅助的输入契约与可观测性**。候选输出与已确认事实的语义冲突拦截由并行任务 `09-19-steward-recommendation-correctness` 负责（确定性判据），本任务不重复实现，也不得削弱它。

## 授权与状态

- 2026-09-19 用户确认："需要"（创建本任务），并指示创建后回到原任务继续执行。
- 当前仅获准创建任务与规划；保持 `planning`，未批准实施。
- 依赖关系：本任务与 `09-19-steward-recommendation-correctness` **共享** `steward_assist.py` / `steward_guard.py` 的 prompt 与投影代码。两者**必须串行**，不得并行修改同一文件。

## 问题与证据边界

- 已核对（本机主检出、`feat/09-19-steward-recommendation-correctness` worktree）：
  - candidate prompt 全文 296 字符，不含 `父`/`母`/`方向`/`矛盾`/`冲突`/`已存在`/`重复`，也无 few-shot 示例。
  - 模型输入为 `{"nodes":[{"node":"n001","minor":false}],"facts":[{"fact_id":7,"fact_type":"biological_parent","subject":"n001","object":"n002"}]}` —— 只有代号与事实类型，**没有方向说明**（`source_facts.py` 的"subject 是 object 的父/母"合同从未下发给模型）。
  - `_candidate_facts` 返回空间内**全部**已确认事实，无条数上限；实测单事实投影约 137 字节，64KiB 上限约容纳 **478** 条事实。
  - 超限路径：`_reserve_attempt` 写 `StewardModelCall(status="skipped", error_code="prompt_too_large")`；`skipped` 不在 `_BUDGETED_STATUSES`（不计费）；批次收尾只对 `unknown`/`transport_failed` 置 `failed`，因此该批次终态为 **`applied`**。
  - `skipped` 无任何聚合统计（`admin_steward.py` 的 `assist_counts` 只统计 `failed`/`degraded`/`unknown`），也无 `logger` 记录。
- 未核对：生产库真实事实分布与是否已越过 478 条阈值。规划不声称线上已发生该降级。
- 结构去重（`find_confirmed_relation` 按同 `fact_type` 比较）**无法**捕获跨类型矛盾，这是并行任务的语义判据要解决的问题，本任务不依赖它，也不把它当作 prompt 正确性的替代。

## 需求

- R1：候选 prompt 必须显式声明方向语义 —— `biological_parent`/`adoptive_parent`/`step_parent`/`guardian` 的 `subject` 是 `object` 的父/母/监护人；`spouse`/`partner`/`direct_sibling` 为对称关系。
- R2：候选 prompt 必须显式禁止输出与输入事实清单矛盾或重复的候选（同一对端点的不同 kind 也属于矛盾，如已有 `biological_parent(A,B)` 时不得再输出 `direct_sibling(A,B)`）。
- R3：prompt 应包含至少一个符合输出 schema 的最小示例，降低纯 schema 描述带来的歧义。
- R4：prompt 变更必须可追溯：`_prompt_version()` 已把 `_PROMPTS` 规范化哈希写入评测报告，变更后报告中的 `prompt_version` 必须随之变化；不得绕过该版本记录。
- R5：事实规模超限时，批次终态与错误码必须如实反映"未产生候选"，不得记为 `applied`。
- R6：超限必须可观测：至少一处系统管理员可见的计数或告警（沿用 `admin_steward.py` 既有 metrics/alerts 形状），使运营者能发现某空间候选辅助已停止工作。
- R7：超限必须是**可收敛**的：给出确定性的处理策略（有界事实子集，或明确标注截断的输入），使空间规模增长后候选辅助仍能工作；不得只报错而永久停摆，也不得伪造候选或降低输出校验强度。
- R8：不得削弱既有安全边界：节点代号投影、未成年过滤、`SOURCE_FACT_TYPES` 白名单、`validate_candidate_output` 的封闭 schema 校验、prompt/响应字节上界、预算与租约栅栏全部保持。
- R9：不得扩大模型可见信息：不向 prompt 下发真实姓名、masked 值、空间成员名单、空间 kind 或其他空间内容。
- R10：prompt 约束只是**次要防线**。本任务不得声称 prompt 修复可以替代确定性语义校验；若并行任务未落地，本任务不得单独关闭该缺口。

## 非目标

不实现候选语义冲突的确定性拦截（并行任务负责）；不更换 Provider/模型；不改 `ranking`/`explanation`/`terminology` 三类 prompt（除非同一机制确需共享，需在设计阶段单独论证）；不改动推荐矩阵、ActionCard、共同家庭判定；不做真实模型质量评测；不调整预算默认值。

## 验收标准

| ID | 可验证结果 |
| --- | --- |
| AC1 | prompt 文本包含方向语义与矛盾/重复禁止条款，且有可解析的最小示例；断言按关键条款逐条检查，不做模糊子串匹配 |
| AC2 | 对抗 fixture 新增"与输入事实矛盾的同辈候选"用例，校验器按既有封闭 schema 语义处理（本任务不要求它拦截语义矛盾，但要求用例存在且结果被如实记录） |
| AC3 | 评测报告 `prompt_version` 随 prompt 变更而改变；未变更时不改变（防漏记与误记） |
| AC4 | 事实数超限时 `StewardModelCall.error_code == "prompt_too_large"`，且批次终态不再是 `applied`（断言具体终态与错误码，不只看非 applied） |
| AC5 | 超限计数可从系统管理员端点读到，或产生一条安全告警；响应不含 prompt、事实、姓名或空间内部内容 |
| AC6 | 规模超过阈值的合成空间仍能产生候选（有界子集或标注截断），且输出仍通过全部既有校验；断言子集选择确定、可复现（同输入同结果） |
| AC7 | 既有负例全为空、硬安全门禁 100%、候选召回 ≥0.9 的评测门槛继续通过；节点代号投影、未成年过滤、字节上界、预算/租约栅栏回归无变化 |
| AC8 | 定向回归、完整后端 Ruff/format/mypy/pytest 通过；未跑真实模型质量评测时如实标注 |

## 规划审阅门

设计需明确 R7 的策略选择（有界子集 vs 标注截断）及其对候选召回的影响，并说明与并行任务 `09-19-steward-recommendation-correctness` 的串行顺序。用户批准最新规划后才运行 `task.py start`。
