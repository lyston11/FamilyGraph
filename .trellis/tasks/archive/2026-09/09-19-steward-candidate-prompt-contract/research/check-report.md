# 独立核查报告：候选 prompt 契约与规模降级

核查者：主会话（用户指示不使用子智能体）
日期：2026-09-19
worktree：`/Users/lyston/PycharmProjects/fg-09-19-steward-candidate-prompt-contract`
分支：`feat/09-19-steward-candidate-prompt-contract`（已 merge `origin/main` @ `2110177`）

## 0. 现场与依赖

- 并行依赖任务 `09-19-steward-recommendation-correctness` **已合并进 main 并归档**（`2d1e9bf` → `e6b0122` → `8cc1a28` → `2110177`），串行前置条件满足。
- 依赖任务与本任务改动面**零交集**（`git diff --name-only 4d6fb28 origin/main` 未触及 `steward_assist.py`/`admin_steward.py`/`schemas/admin_steward.py`/`test_steward_eval.py`/`test_steward_log_hygiene.py`/`fixtures/steward_eval`/`spec/backend/steward-action-card.md`）。已 `git merge origin/main`，无冲突。
- 依赖任务新增了独立 Spec 叶 `steward-recommendation-suppression.md`（输出侧语义拦截）；本任务在 `steward-action-card.md` 增补输入侧契约，两者不冲突，边界清晰。

## 1. AC 逐项判定

| AC | 判定 | 证据 |
| --- | --- | --- |
| AC1 | **通过** | `_PROMPTS["candidate"]` 由 `_CANDIDATE_DIRECTION_CLAUSE`(163B)/`_CANDIDATE_CONFLICT_CLAUSE`(124B)/`_CANDIDATE_EXAMPLE_CLAUSE`(71B) 三段拼接；prompt 582→1214 字节。`test_candidate_prompt_declares_direction_contract` 逐条断言 4 个 parent kind + 3 个对称 kind + 「父/母/监护人」「对称」；`..._conflict_and_duplicate_ban` 断言「矛盾/重复/不得」+ 显式 kind 互斥例；`..._parseable_minimal_example` 用 `json.loads` 解析示例并逐字段校验 kind 与代号格式。三段常量本身被断言 `in prompt`，措辞改写会红。 |
| AC2 | **通过** | fixture `adversarial.json` v1→v2 新增 `adv-candidate-conflicts-with-input-fact`（已有 `biological_parent` 事实，模型输出 `direct_sibling`），`expected.security_case=false` 使其**不计入**硬安全门禁。核查实测：`security_cases_total` 仍为 27（新增用例未计入），`case_passed` 为 true。`:note` 字段明确写出「不要求校验器拦截语义矛盾」。 |
| AC3 | **通过** | `prompt_version()` 是 `_PROMPTS` 的纯 `sha256(json.dumps(sort_keys=True))`，`test_steward_eval._prompt_version()` 改为直接调用它（单一真源）。核查实测报告字段 main `edb657bd…` → 本分支 `dbf4c72e…`，且回归断言「变更→变、未变更→稳定」。**非自证循环**：核查用独立复算的 `sha256` 与 `prompt_version()` 比对一致，并验证改 `_PROMPTS` 必然改变值。 |
| AC4 | **通过** | 终态映射 `elif REASON_PROMPT_TOO_LARGE in all_error_codes: terminal_code = REASON_PROMPT_TOO_LARGE` 置于 `unknown`/`failed` 之后（优先级正确）。断言具体终态 `failed` + `error_code="prompt_too_large"`，并加回归：预算类 `insufficient_budget` 仍 `applied`/`error_code=None`；同批 `unknown` 压过 `prompt_too_large`（实测 `network_unknown`）。 |
| AC5 | **通过** | `metrics.assist_skipped` 与 `metrics.assist_skipped_prompt_too_large` 为纯计数。核查实测响应文本不含空间名、人物姓名、prompt 原文；`test_steward_log_hygiene` 的**精确集合断言**已同步（实测 keys 15 个，含两个新字段）。 |
| AC6 | **通过** | `candidate_user_content` 二分取最大前缀（`fact_id` 升序）；核查逐字节扫描 2180 个 cap 值验证：每个 cap 要么取到**最大**可行子集且实际 prompt 在预算内，要么回落全量原样。可复现性有断言（`expire_all` 后重算相等），`input_hash`/`prompt_digest` 与发送前 fence 一致有断言且实测 `status=succeeded`（未误判 `evidence_changed`）。 |
| AC7 | **通过** | 评测门槛：`hard_gate_passed=true`、`security_cases_failed=0`、`candidate_recall=1.0`、`negative_cases_empty_ok=true`、`security_cases_total=27`。未超限投影逐字节一致有断言。节点代号/未成年/白名单/封闭 schema/字节上界/预算租约栅栏相关回归 212 passed。 |
| AC8 | **通过** | `ruff check` / `ruff format --check` / `mypy app` 全绿；完整后端 pytest **1728 passed, 3 skipped**。**未跑真实模型质量评测**（fake transport 只证明程序合同），报告中如实标注，未声称质量已改善。 |

## 2. 核查中发现并已修复的缺陷

**D1（中等，已修）：`low == 0` 分支注释与真实语义不符。**

- 实测扫描：`total(n) = [1545, 1634, …, 2180]`。当 `cap == 1545`（= `system + 空投影`）时，`low == 0`，函数返回全量（2181 字节，超界）→ 由 `_reserve_attempt` 结算为 `prompt_too_large`。
- 原注释写「连空投影都装不下」，但在 `cap == total(0)` 这一端点值上**空投影装得下**（恰好相等，`low` 却停在 0）。按契约「取最大可行子集」字面理解，此处应返回空事实集。
- **行为本身正确**（返回空输入会发送注定零候选的请求，把确定性输入问题伪装成正常 `applied` 成功——正是本任务要修的缺陷），但注释错误会误导后续维护者。
- 修复：更正注释说明「能装下的子集是 0 条」；新增 `test_bounded_subset_contract_holds_across_every_cap`（逐 cap 扫描）、`test_empty_subset_is_never_sent_as_a_fake_success`、`test_budget_skipped_stays_benign_not_prompt_too_large`、`test_upstream_unknown_outranks_prompt_too_large` 四个回归。

## 3. 已验证的技术前提（无需修改）

1. **投影字节单调不减**：`project_candidate_input` 按 `facts` 顺序逐个 append `{fact_id, fact_type, revision, subject, object}`，端点越界时 `continue`（跳过但不缩短），长度随输入前缀**单调不减**。核查用含不可投影事实（端点越出可见集）的夹具实测 `sizes == sorted(sizes)` 成立。二分查找前提成立。
2. **预算边界与真实发送精确一致**：实际发送 `f"{system}\n{user_content}"`（`_build_payload` 把二者放进 messages/input，无额外包裹；`_reserve_attempt` 用同一 `f"{system}\n{user_content}"` 度量）。`overhead = len(system)+1` 与之精确一致——逐 cap 扫描无「域内却超界」或「非最大子集」的反例。
3. **`evidence_hash` 覆盖全量事实，不受子集影响**：`_facts_evidence` 仍读全量 `_applicable_confirmed_facts`，`test` 实测前后 hash 稳定。语义正确：全量事实变化仍 supersede（保守），子集化不引入「事实变了却放行」的新缺口。
4. **子集化不改变 `unknown` 优先级的付费语义**：`skipped` 仍不在 `_BUDGETED_STATUSES`，`billed_tokens` 为 `None`，无网络调用，无重放。

## 4. 无法核实的结论（如实标注）

- **未做真实模型质量评测**：prompt 变更后的实际候选质量改善（幻觉 sibling 是否下降）只有 fake transport 的程序合同证据，**不声称质量已改善**。真实 provider 评测需单独授权。
- **未读生产库**：不声称线上空间事实分布已越过约 478 条阈值，也不声称线上已发生该降级（与规划边界一致）。
- **未做生产部署**（S7 未开始，需单独授权）。
- 前端 `system-admin-frontend` 未改：`StewardStatus` 接口本就不声明 `metrics`，新计数不影响前端解码；未跑前端门禁（未改前端代码）。

## 5. 结论

无阻塞缺陷。8 项 AC 全部通过；D1 已修复并有回归守护。就绪进入 S6（Spec 已更新、提交与串行集成）。
