# 管家记忆读取：可配置可见级别、只读工具与 RAG 空跑修正

## Goal

让 steward（管家）能够在受控、可配置的前提下读取用户记忆，用于生成针对该用户的个性化投影；同时修正 steward 上下文路径上「RAG 检索白跑 + `ContextBuildItem` 审计与实际发送内容不符」的缺陷。

## Background

### 记忆链路现状（已实现的部分）

- **产生**：`memory_extractor.rule_detector`（确定性，9 类按优先级，单消息上限 3）在
  `agent_queue._settle` 中随终态同事务产出 **pending 候选**；`propose_memory` 是模型驱动的补充路径。
  用户经 `POST /api/memory-candidates/{id}/confirm` 确认后才成为 `Memory`。
- **沉淀**：`Memory.scope ∈ MEMORY_SCOPES = ("private","household","lineage")` →
  `memory_rag.index_memory` → `RAGDocument`（`scope`/`sensitivity`/`confirmation_status`）→ `RAGChunk`。
  取代与有效区间（`superseded_by_id`/`valid_to`）在 SQL、文档状态、投影复核三层独立挡住。
- **检索**：assistant 的 `familygraph.search_memory` 工具 + `ContextBuilder` 的 RAG 纳入
  （分层预算，memory 份额 0.6）。

### steward 侧的两处切断（本任务要处理的）

**第一处（明文、有意）** `memory_rag._ELIGIBILITY_SQL`：

```sql
(d.scope = 'private' AND d.author_account_id = :account_id AND :is_assistant = 1)
OR (d.scope IN ('household','lineage') AND d.space_id = :space_id AND EXISTS(space_members active))
OR (d.scope = 'public' AND :is_assistant = 1)
```

`is_assistant = int(agent_kind == "assistant")`。private/public 对 steward 恒假；
household/lineage 分支不检查 `is_assistant`。

**第二处（决定性）** `api/internal_agent._steward_run_context`：

```python
built = context_builder.ContextBuilder(db).build(..., agent_kind="steward", ...)  # 跑了完整 RAG 检索
context_build_id = built.build_id
context_blocks = policy_guard.enforce(
    policy_guard.context_hook(_steward_projection_blocks(db, attempt))            # 结果被整体丢弃
) or []
```

`_steward_projection_blocks` 只返回 `steward_assist._user_content_for(db, attempt)`。
因此 **steward 的 prompt 里从来没有记忆**，household/lineage 也没有。

### 为什么不能把记忆内联进 prompt

`steward_assist` 的 digest 契约：

```python
prompt = f"{system}\n{user_content}"
"prompt_digest": sha256(prompt),  "input_hash": _canonical_hash(user_content)
```

`test_steward_child_run_acceptance.py:848::test_the_sent_prompt_is_the_one_the_digest_describes`
从投影重建 digest 验证「发送的就是 digest 描述的那段」。而 `candidate_user_content` 的注释说明
投影按字节预算取**确定前缀子集**，使 `input_hash`/`prompt_digest` 稳定，**发送前 fence 不会误判**。
记忆是可变、随时间变化的（取代、有效区间、新确认），内联进 `user_content` 会让 `input_hash` 抖动，
抬高 fence 拒绝率。用户已明确否决该方向（「不能每次都是把提示词等植入管家 agent」）。

### steward 的身份约束（决定 private 能否开放）

`StewardModelCall.viewer_account_id` **只有 `terminology` kind 会被设置**（`steward_assist.py:1311`）；
`candidate`/`ranking`/`explanation` 是空间级 kind，无 viewer，`_steward_run_context` 回落到
**space admin** 作为 policy 身份。

因此：**空间级 kind 无法证明「读的是谁的私有记忆」**。若把 private 对它们放开，
读到的将是 space admin 的私有记忆 —— 那是数据泄露，不是个性化。

## Requirements

### R1 可配置的可见级别（平台 ∩ 空间两层，默认全关）

- 新增平台级配置，表达 steward 可读的记忆 scope 集合（值域为 `MEMORY_SCOPES` 的子集）。
- 新增空间级配置（`AgentSpaceProviderSetting`），同一值域。
- **有效可见集 = 平台集 ∩ 空间集**，默认空集（保持现状：steward 读不到任何记忆）。
- 环境变量作为部署级上界（沿用既有 `_steward_assist_effective` 的 kill-switch 语义）。
- 管理面：平台级经既有 platform-features 端点读写；空间级经既有
  `GET/PUT /api/spaces/{space_id}/config` 读写（space admin）。

### R2 `private` 的额外约束（不可配置放宽）

即使 `private` 出现在有效可见集中：

- 只有**有 viewer 的 kind**（当前为 `terminology`）可以读 private；
- 且只能读 **该 viewer 本人**的私有记忆（`d.author_account_id = viewer_account_id`）；
- 空间级 kind（无 viewer）的 private 分支**恒假**，不因配置而放开。

### R3 管家只读记忆工具

- 新工具 `familygraph.steward.search_memory`，`required_kind="steward"`，version 1。
- 输入仅 `query`（≤500）与可选 `limit`（1..10）；`additionalProperties: false`，
  显式拒绝 `space_id`/`account_id`/`viewer_account_id`/`scope`/`run_id`/`attempt_id`。
- 作用域全部来自已 fence 的 run 身份（space 来自 claims，viewer 来自 attempt）。
- 有效可见集为空时**不广告该工具**（不注册进 allowlist），与既有
  「必然失败的工具不广告」口径一致。
- 结果过 `policy_guard.tool_result_hook`，审计不含原始输入。
- sidecar `agent/src/tools.ts` 同步声明 + `TOOL_VERSIONS` 条目，两侧 canonical name 一致。

### R4 修正 RAG 空跑与审计不符

- steward 上下文路径不再执行 RAG 检索（其结果从未被使用）。
- 写入的 `ContextBuild`/`ContextBuildItem` 必须**如实描述实际发送的上下文**：
  唯一纳入项即 steward 投影块（`source_type="steward_projection"`，`included=True`）。
- `context_blocks` 与 `built.included` 必须来自**同一处**，消除两处各自构造导致的漂移。
- 不改变 `prompt_digest`/`input_hash` 的取值与稳定性；不改变 assistant 路径的任何行为。

### R5 边界（不可越）

- 不改变 assistant 的 `search_memory` 行为与 `_ELIGIBILITY_SQL` 对 assistant 的语义。
- 不把记忆写进 steward 的静态 prompt（digest/`input_hash` 保持稳定）。
- 不引入跨空间读取；`space_id` 只来自 fenced run。
- 不放宽 #307（跨空间桥接）、#321（家庭卡成员资格）、#584（授权主体独立）。
- steward 工具保持只读；本任务不新增任何写工具。

## Acceptance Criteria

- [ ] 有效可见集 = 平台 ∩ 空间；两者任一为空 → 工具不广告、不注册进 run allowlist。
- [ ] `private`：`terminology` run 只能读到该 viewer 本人的私有记忆；
      用 space admin 的私有记忆做反向断言必须读不到。
- [ ] `private`：空间级 kind（`candidate`/`ranking`/`explanation`）即使配置放开也读不到。
- [ ] `household`/`lineage`：非该空间 active 成员不可读；跨空间不可读。
- [ ] 默认配置（空集）下，steward 行为与改动前**逐字等价**（无新增可读记忆）。
- [ ] `ContextBuildItem.included` 与实际发送的 `context_blocks` 一致；
      steward 路径不再发生 RAG 检索（以调用计数断言）。
- [ ] assistant 的 `search_memory` 与 6 个 steward 只读工具回归全绿；
      `prompt_digest`/`input_hash` 稳定性测试全绿。
- [ ] `cd backend && ruff check . && ruff format --check . && mypy app && pytest` 通过。
- [ ] `cd agent && npm run lint && npm run type-check && npm test` 通过。

## Constraints

- 迁移：新增列必须可空或带默认值，保证 `upgrade head` 不改变既有行为；降级不得丢配置语义。
- 不新增第二份 `_ELIGIBILITY_SQL` 副本（取代/有效区间谓词只有一处真源）。
- 不改动 `MEMORY_SCOPES` 值域本身。
- 不触碰 `.trellis/tasks/archive/**` 与 `artifacts/`。
