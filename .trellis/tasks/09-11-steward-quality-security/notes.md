# Notes — Steward 模型辅助安全修复与质量评测

## 2026-09-11 规划记录

- F18 已核实模型支路没有调用 policy_guard，provider resolver 只保证设置/Provider 合法，不等于 payload 安全。
- F08 当前非空解释直接截断写入 reason_text_llm；不会 XSS（Vue 插值转义），但存在事实/隐私承诺误导风险，不把它误报为 XSS 漏洞。
- F20 目前没有本轮真实模型质量实验；先建立可复现评测，16 个 fake 测试不代表线上模型已验证。

## 决策与证据边界

- 已确认路线：确定性核心 + 可选候选/排序/解释；先可靠性再用户审核；站内通知。
- 对应条目：[F07](../09-11-steward-complete-hardening/research/findings.md#f07), [F08](../09-11-steward-complete-hardening/research/findings.md#f08), [F18](../09-11-steward-complete-hardening/research/findings.md#f18), [F19](../09-11-steward-complete-hardening/research/findings.md#f19), [F20](../09-11-steward-complete-hardening/research/findings.md#f20)。
- 当前状态：规划完成待实施评审；本轮不启动。实现清单全部未勾选，不代表工作已完成。
- 09-09 会话报告核心/维护 46 passed、辅助 16 passed；这次没有重复运行测试，不能作为未来改动通过依据。

## 实施与验证约定

本轮仅生成规划，未修改上述产品代码，也未重跑历史测试。文件行号以 2026-09-11 工作区为准；实施前必须读将修改的完整函数与现行 spec。
迁移必须接实施时唯一 head，不硬编码已被并行工作使用的编号；测试只用隔离 DATA_DIR。不得把 conftest 的 downgrade base 对准业务库。

## 实施结果待记录

实施后逐 AC 追加实际命令、退出码、必要的脱敏证据及剩余问题。本节是交接记录入口，不替代 PRD 验收。

## 2026-09-11 实施记录（quality-security 安全链 + 评测）

### 改动文件

- 新增 `backend/app/services/steward_guard.py`：受众限定输入投影（稳定节点代号 n001.. + fact type/id/revision + minor 布尔，绝不发送 User.name/raw 字段）、`outbound_check`（复用 policy_guard.before_provider_request，不经 ProviderProxy、不伪造 AgentRun）、封闭 schema 校验器（候选=原子 SOURCE_FACT_TYPES+节点代号、排序=严格排列、解释={reason_code, supporting_fact_ids, template_slots}+确定性模板渲染）、`candidate_digest`（仅结构，不含 rationale）、评测用例评估/聚合纯函数。
- `backend/app/services/steward_assist.py`：prompt 全部换为投影输入；排序按 recipient_account_id 分组（fence.ranking_groups，subject_key=`ranking:<recipient>:<ids>`）；发送前 outbound_check（block→attempt skipped/policy_blocked，零计费；redact→发 redacted）；_fence_check 增加云同意撤销/local_required 检查（REASON_POLICY_BLOCKED，绝不自动切云）；解释产物为结构化 schema v2（output_json 含 rendered），应用层只写渲染文本；`trusted_explanations()` 读取面信任入口；untrusted 旧 reason_text_llm 同时成为重新生成目标。
- `backend/app/api/action_cards.py` + `backend/app/schemas/action_card.py`：CardOut 新增 `reason_text_llm`，只暴露已验证（schema v2 且 rendered 与卡内一致）的解释；旧纯文本行序列化 null → 前端回退模板 reason_text（ActionCardItem.vue 已有 `?? ` 回退，前端无需改动）。
- 新增 `backend/tests/fixtures/steward_eval/{relation_matrix,adversarial}.json`（38 例：ST-5 矩阵 7 行×正负 + 24 条对抗/边界）与 `backend/tests/test_steward_eval.py`（runner：写 JSON 报告至 `backend/.steward-eval-report.json`，记录 fixture/prompt sha256/model 版本，model_version=fake-transport(program-contract-only)）；新增 `backend/tests/test_steward_guard.py` 单元回归；`backend/tests/test_steward_assist.py` 更新+新增 12 个回归。

### 命令与结果（backend 目录）

- `pytest -q`（全量）：932 passed, 3 skipped，exit 0。
- 定向：test_steward_assist.py(47)+test_steward_guard.py(8)+test_steward_eval.py(1)+action_cards API/core 全过。
- `ruff check .`：剩余 7 处全部位于本轮未触碰文件（space_model_settings/spaces/0028 迁移/test_space_lineage_link/test_space_model_settings，属并行依赖任务遗留）；本轮改动文件 0 错误。
- `ruff format --check .`：10 个待格式化文件全部为并行任务遗留文件；本轮文件全部 clean。
- `mypy app`：5 errors，全部位于未触碰的 app/api/space_model_settings.py 与 app/api/admin_agent.py（依赖任务遗留）；本轮 0 新增。
- 无迁移（未新增字段：信任标记存于既有 StewardModelCall.output_json.schema_version）。

### 评测聚合（fake transport，仅程序合同）

hard_gate_passed=true（27 条安全用例 0 失败）、candidate_recall=1.0（阈值 0.9）、negative_cases_empty_ok=true、cases=38。prompt_version=d5650a0f6fc8…（_PROMPTS 规范化 sha256）。

### 验证过的风险 / 边界

- 出站捕获测试在姓名植入 token/手机号/注入句/masked/私人 RAG，全部出站 payload 零外泄；明文 prompt 只存 sha256 摘要。
- 云同意撤销→批次 superseded（resolver 层 provider_unavailable；发送窗口内为 policy_blocked），零发送；local_required+云 provider 同样降级，绝不自动切云。
- 编造亲生关系（证据外 fact id）、隐藏人物（roster 外代号）、自动入族承诺（非法槽位值）、任意 JSON kind、超长文本均整体拒绝并回退模板（非截断）。
- openai-completions 与 openai-responses 两协议 adapter 均有端到端回归；崩溃点②③④恢复路径在新校验器下仍通过。

### 证据边界 / 未获得

- 未运行任何真实 provider 请求；评测报告的 model_version 明确标注 fake-transport，不构成线上模型质量证明（F20 的真实模型评测仍待 release 任务）。
- 候选输出形状合同（CANDIDATE_PAYLOAD_KEYS={"kind","subject_user_id","object_user_id"}，payload 不含 rationale/digest 仅结构）已在 steward_guard docstring 与测试固化，供 09-11-steward-candidate-review 直接消费；审核 API/审核池消费流程不在本轮范围。
- 未改动 frontend（API 层 gating 已满足"只展示已验证解释"）；未运行 npm 检查。
- 过程事故：一次误执行 `git stash push` 立即 `stash pop` 完整恢复，工作树（含并行任务未提交改动）经 diff 状态核验无损，全量测试随后通过。

## 2026-09-11 Check Agent 记录（quality-security）

### 验证命令与结果（backend 目录，独立复核）

- `pytest -q`（全量）：932 passed, 3 skipped，exit 0（与本文件实施记录一致，独立重跑）。
- `ruff check .`：7 处剩余错误全部位于本轮未触碰文件（space_model_settings/spaces/0028 迁移/test_space_lineage_link/test_space_model_settings，属并行任务遗留）；本轮改动文件 0 错误。
- `ruff format --check .`：10 个待格式化文件均为并行任务遗留；本轮文件 clean。
- `mypy app`：5 errors 全在 app/api/space_model_settings.py 与 app/api/admin_agent.py（遗留基线）；本轮 0 新增。
- 评测模块单独重跑：test_steward_eval.py 1 passed；JSON 报告复核 hard_gate_passed=true（27 安全用例 0 失败）、candidate_recall=1.0、negative_cases_empty_ok=true、38 例、prompt_version=d5650a0f…、model_version=fake-transport(program-contract-only)（诚实标注）。报告路径已确认被 gitignore。

### 逐 AC 复核（读码结论）

- AC-1：project_candidate/ranking/explanation_input 只含节点代号 n001..、fact id/type/revision、minor 布尔；越出授权端点的 fact 不投影（steward_guard.py:111）。outbound_check 直连 policy_guard.before_provider_request，无 ProviderProxy、无伪造 AgentRun。test_outbound_payload_never_contains_raw_planted_fields 植入 token/手机号/注入句/masked/私人 RAG 并捕获出站 payload 断言零外泄；test_cloud_consent_revoked_degrades_without_send 与 test_local_required_with_cloud_provider_degrades 均降级不发送。
- AC-2：validate_* 三校验器对编造关系/隐藏人物/入族承诺槽值/任意 kind/超长槽文本一律整体拒绝（None→degraded→模板），无截断通过路径；render_explanation 确定性；读取面 reason_text_llm 只暴露 trusted_explanations 验证过的渲染文本，旧纯文本行序列化 null 前端回退模板。
- AC-3：validate_ranking_output 拒绝 bool/浮点/字符串 id、重复、遗漏、混入集合外 id；_ranking_groups 按 recipient_account_id 分组且 subject_key 携带收件人标识；_apply_batch 候选只写 StewardLlmCandidate（结构化 payload，digest 仅 kind+端点），全文件无 SourceFact/PFV/SpaceMember 写入；candidate kind 限于 CANDIDATE_ATOMIC_KINDS=SOURCE_FACT_TYPES。
- AC-4：38 条 fixture 均有 case_id/expected/metric 标记与共享植入证据；报告记录 fixture 版本、prompt sha256、model_version；无伪造真实 provider 分数。
- AC-5：aggregate 分列 hard_gate（100%）与 candidate_recall（阈值 0.9）；openai-completions（test_openai_completions_protocol_path）与 openai-responses（_parse_response/responses 路径 + 既有一致性回归）两协议均有回归；崩溃点②③④恢复路径在新校验器下仍通过。

### 其他复核项

- 无秘密入日志/DB：steward_assist 唯一 logger 语句仅含 batch_id；prompt 只存 sha256 digest；异常原文以异常类名/安全码落库。
- 候选 payload 合同已文档化：CANDIDATE_PAYLOAD_KEYS 在 steward_guard docstring 与 __all__ 固化，供 candidate-review 任务消费。
- git 事故核验：`git stash list` 为空，`git status` 工作树完整（37 modified + 全部 untracked 新文件在位），无 stash 残留。

### 结论

PASS。无 BLOCKER/MAJOR。MINOR（不阻塞）：`steward_assist.py:1207` 写回时对 rendered 做 `[:500]` 截断——因模板输出确定性且远短于上限，且若不一致 trusted_explanations 会判 untrusted（fail-safe 回退模板），不构成"截断通过校验"路径。
