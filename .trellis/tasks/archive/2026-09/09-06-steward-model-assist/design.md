# Design: Steward 模型辅助层（候选/排序/解释）

> 依据：父任务 PRD D8/D9 + 本任务 prd.md + `09-01-agent-runtime-assistant-only/notes.md`（"未来模型只能辅助候选、排序和解释"、"child run/context 审计另立任务"）。行号锚点基于 2026-09-06 工作区。

## 0. 架构裁定

| # | 裁定 | 说明 |
|---|---|---|
| B1 | Steward 模型调用 **in-process 直连 Provider**（不经 sidecar、不伪造 AgentRun） | 管家由 API maintenance worker 进程内执行（spec §2）；`services/provider_proxy.py` 全链路 run 门控（`_require_executable_run`），不可复用。新 egress 走 `resolve_runtime(db, space_id, agent_kind="steward")` 唯一解密出口，与既有合同一致（api 容器仍是唯一 egress 点） |
| B2 | child run/context 审计 = 新表 `steward_model_calls`（每次模型调用一行） | 09-01 明确不为 Steward 伪造 generic AgentRun；该表即"child run 审计"：space_id/job_id/policy_version/assist_kind/provider/model/prompt_digest(sha256)/chars/token_usage/status/error_code/latency。**永不存 prompt 明文**（只存摘要+长度） |
| B3 | 三类辅助点 hook 在 `_execute_locked` 主流水线**之后**（dirty 重算/检测/建卡全部完成后），整体 try/except 包裹 | 模型失败绝不影响确定性流水线（AC-5）；checkpoint 语义不变 |
| B4 | 幂等：per (job_id, assist_kind, seq) 已有审计行即跳过；候选 seq=1/排序 seq=1（同 job 仅一轮）/解释 seq=card.id（逐卡） | 同事务重入与部分重试幂等；整体 crash 回滚后审计行随事务消失、重试会重花 token——已知权衡，由候选 digest 去重与解释逐卡跳过提供最终幂等 |
| B5 | 候选产物**只落内部池 + 不自动浮现**；排序只改呈现顺序；解释只写 `reason_text_llm` | R3 红线：候选不经过确定性矩阵绝不进卡片（本增量候选根本不进卡片）；卡片仍只从确定性 SourceFact 生成 |
| B6 | Prompt 只含白名单结构化字段（display name、fact_type、创建选择） | 绝不含 masked 值、健康/住址等高敏感类别、私人 Session/Memory；prompt 明文不落库（B2） |

## 1. 数据模型（迁移 0034）

1. `agent_space_provider_settings` 加三列（简单 add_column，无需重建）：`assist_candidate` / `assist_ranking` / `assist_explanation`，Boolean `server_default=sa.false()`（空间级开关；仅 steward 维度消费）。
2. 新表 `steward_model_calls`：id、space_id FK family_spaces CASCADE、job_id FK steward_jobs CASCADE、policy_version String、assist_kind CHECK IN (candidate/ranking/explanation)、provider_id FK agent_providers SET NULL、model String(120)、prompt_digest String(64)、prompt_chars Int、completion_chars Int、prompt_tokens/completion_tokens/total_tokens Int nullable、status CHECK IN (succeeded/failed/degraded/skipped)、error_code String(64) nullable、latency_ms Int nullable、created_at；唯一键 `(job_id, assist_kind, seq)`（seq=该维度第几次调用，普通为 1）。
3. 新表 `steward_llm_candidates`：id、space_id FK CASCADE、job_id FK CASCADE、candidate_kind String(48)、payload_json JSON、candidate_digest String(64)（payload sha256，幂等去重）、status CHECK IN (proposed/dismissed) default proposed、created_at；唯一键 `(space_id, candidate_digest)`。
4. `action_cards` 加两列：`reason_text_llm` Text nullable（解释产物；NULL=用模板 `reason_text`）、`presentation_rank` Int nullable（排序产物；NULL=按 created_at 既有序）。
5. downgrade：逐项 drop（新表 drop、两表列 drop）——无可逆数据风险，不 fail-closed。

## 2. 配置（平台级开关，R1）

`config.py` 新增（全部默认关闭 → AC-1 行为等价）：

- `STEWARD_ASSIST_CANDIDATE` / `STEWARD_ASSIST_RANKING` / `STEWARD_ASSIST_EXPLANATION`：平台级 per-kind 开关（env bool，默认 0）。
- `STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB`（默认 6）、`STEWARD_ASSIST_MAX_TOKENS_PER_JOB`（默认 20000）、`STEWARD_ASSIST_TIMEOUT_SECONDS`（默认 30）、`STEWARD_ASSIST_MAX_CARDS_PER_JOB`（默认 5，解释/排序单 job 处理卡上限）。

**有效开关 = 平台级 AND 空间级**（空间级在 settings 行三列，owner 经子任务 A 的 PUT 端点设置）。

## 3. 服务层 `services/steward_assist.py`（新文件）

### 3.1 模型调用底座

`_call_model(db, *, space, job, policy_version, assist_kind, system, user, max_out_tokens, seq, transport=None) -> str | None`：

1. 预算检查：该 job 已成功调用数与 token 累计（查 steward_model_calls）超上限 → 记 `status="skipped", error_code="budget_exhausted"` 行，返回 None。
2. `runtime = agent_provider.resolve_runtime(db, space.id, agent_kind="steward")`；None 或 base_url 空 → 记 `status="degraded", error_code="provider_unavailable"`，返回 None（AC-5/AC-6）。
3. 按 `runtime.api` 组 payload（非流式）：`openai-responses` → `{"model", "input":[{"role","content"}...], "max_output_tokens"}`，路径 `/responses`；`openai-completions` → `{"model", "messages", "max_tokens"}`，路径 `/chat/completions`（路径表对齐 provider_proxy `_API_PATHS`）。
4. `transport = transport or _post_json`（模块级函数，httpx.Client 同步 POST，`timeout=config.STEWARD_ASSIST_TIMEOUT_SECONDS`；测试 monkeypatch `_post_json`）。
5. 解析响应文本 + usage（两种协议防御式解析）；失败/超时/非 2xx → 记 `status="failed"`（error_code=异常类名缩写）返回 None。
6. 成功 → 记 `status="succeeded"` 行（prompt_digest=sha256(system+user)、chars、usage、latency_ms），返回文本。

### 3.2 三个辅助点

- **候选** `maybe_generate_candidates(db, space, job, policy_version, transport=None)`：平台∧空间开关；digest 去重（该 job 已有 succeeded candidate 行即跳过，B4）。Prompt 输入 = 空间已确认事实白名单摘要（fact_type、双方 display name、创建选择；来自 `_applicable_confirmed_facts` 同口径数据），要求输出 JSON 数组 `[{kind, subject_user_id, object_user_id, rationale}]`。校验：JSON 可解析、subject/object id ∈ 空间可见集合、kind 非空字符串；非法项丢弃并计入 completion 校验失败数。合法项写 `steward_llm_candidates`（candidate_digest 去重；跨 job 重复候选因唯一键静默跳过）。
- **排序** `maybe_rank_cards(db, space, job, policy_version, cards, transport=None)`：输入 = 该空间 active pending 卡 id 列表（≤MAX_CARDS 上限截断）；要求 LLM 返回 id 排列。**校验必须是原集合的严格排列**（等长、无重复、无额外 id）——非法一律丢弃记 degraded（`error_code="invalid_permutation"`），绝不应用（AC-2）。合法 → 按 LLM 序写 `presentation_rank=1..n`。
- **解释** `maybe_explain_cards(db, space, job, policy_version, cards, transport=None)`：对无 `reason_text_llm` 的 pending 卡逐张（≤MAX_CARDS）；Prompt = 卡结构化事实（kind、模板 reason_text、证据 fact_type 列表、proposed action、红线指令"只可复述已确认事实"）；产物长度截断 ≤500 字、空串拒绝；写 `reason_text_llm`。失败 → 保持 NULL（模板兜底）。

### 3.3 Hook（steward.py `_execute_locked` 尾部）

主流水线（重算/检测/建卡/复核）完成并提交后：

```python
try:
    steward_assist.run_assists(db, space=space, job=job, policy_version=POLICY_VERSION)
except Exception:  # noqa: BLE001 — 辅助层绝不拖垮确定性流水线（AC-5）
    logger.exception("steward assist failed; pipeline results retained")
```

`run_assists` 内部：查 flags → 逐点调用（每个内部自带 budget/幂等/降级）；cards 参数取 `action_cards.active_cards_in_space`。任一辅助点异常只记审计行与日志，不向上抛。

## 4. 排序消费与解释呈现

- `api/action_cards.py list_cards`（:205 `order_by(created_at.desc())`）改为 `ORDER BY (presentation_rank IS NULL) ASC, presentation_rank ASC, created_at DESC`——有排名的卡先呈现，未排名保持时间序（服务端单点改动，前端无需感知）。
- `frontend/src/components/actioncard/ActionCardItem.vue`：文案行 `reason_text_llm ?? reason_text`（types/actionCard.ts 加可选字段）。

## 5. 空间设置 schema/UI（R1 空间级开关）

- `AgentSpaceModelSettingsRequest` 加 `assist_candidate/assist_ranking/assist_explanation: bool | None = None`；`agent_kind="assistant"` 时任一非 None → 422（flags 仅 steward 语义）；PUT 存行（enabled 与 flags 独立存储，解析只在 enabled 行读 flags）。
- `SpaceAgentSettingOut` 透出三字段；`SpaceModelSettingsPanel.vue` steward 区块加三个开关（默认关）。

## 6. 测试计划

`backend/tests/test_steward_assist.py`（fake transport = monkeypatch `steward_assist._post_json`）：

1. **AC-1**：默认全关 → `run_steward_job` 后 steward_model_calls/steward_llm_candidates 零行、卡行为与基线一致（既有 steward 测试不改一行全过）。
2. 开关矩阵：平台开∧空间关、平台关∧空间开 → 均零调用；双开 → 调用。
3. **AC-5**：resolve 不可用 → degraded 行 + 流水线照常建卡；transport 抛错 → failed 行 + 卡保留。
4. **AC-2**：排序返回合法排列 → presentation_rank 应用且集合不变；返回重复/缺失/多余 id → 不应用（全 NULL）+ degraded 行；未确档者永不出现在任何卡（候选不进卡）。
5. **AC-3**：审计行字段完整（space/job/policy_version/kind/digest/usage）；同一 job 内 assistant 三表（agent_sessions/messages/runs）零新增行。
6. 预算：超过 MAX_CALLS → skipped 行且不再发起 transport 调用。
7. 幂等（B4）：同 job 重跑 → 不产生第二行/不重打模型（fake transport 计数不变）。
8. 候选校验：越界 user id、非法 JSON、混合合法/非法 → 只入合法项；跨 job 相同候选因 digest 唯一键不重复。
9. 解释：成功写入 reason_text_llm、超长截断、失败保持 NULL。
10. flags schema：assistant 维度带 flags → 422；steward 维度存储与透出。

前端：SpaceModelSettingsPanel steward 开关 spec、ActionCardItem llm 文案优先 spec。

## 7. 明确不做

- 候选的用户可见面板/通知 UI（池表 + API 数据已备，产品化另立）。
- 候选自动进卡（永久红线，除非 owner 确认流改造——父 PRD D9 预留）。
- 排序/解释的自主工具调用、Steward 会话化（09-01 边界）。
- 流式响应（辅助点全部非流式，一次往返）。
