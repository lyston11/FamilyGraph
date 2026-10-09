# 设计

## P4 分层预算：按「来源类别的责任」分配，不做容量转移

```text
tier_budget(category) = ceil(token_budget * fraction[category])
硬门一：单条来源估算 + 已用 tier 用量 ≤ tier_budget
硬门二：全局已用 ≤ token_budget
```

fraction 取值：`memory 0.6 / family_story 0.4 / authorized_document 0.4 /
profile 0.2 / public_kinship 0.2`。**刻意让总和 > 1**：单个类别最多只能占 60%，
因此「一部长家族故事吃满预算」在结构上不可能，同时空类别不会浪费预算（全局上限
仍由 `token_budget` 承担，不需要把未用配额搬给别的类别——那是另一种贪心）。

**为什么不做句子边界截断**：`ContextBuild` 只持久化描述符，`_replay` 通过
`read_exact_chunk` 重读原始 chunk 并按 `content_hash` 校验。截断后的文本与重读的完整
chunk 不一致，会让同一 `build_id` 在重放时返回不同内容，破坏「每次执行不可变」的
合同；把截断文本写进 `metadata_json` 又违反「只持久化描述符」。因此粒度保持整块。

**为什么放 policy_json**：分层预算是**影响结果的安全相关参数**，必须进 `policy_json`
并带版本号，否则历史构建无法解释，且 `_replay` 的 policy 比对会漏掉这个变化。

## P2-b 受控工具：只提议、只读

```text
familygraph.propose_memory   required_kind="assistant"   写 pending 候选
familygraph.search_memory    required_kind="assistant"   只读，复用 search_rag
```

- `propose_memory` 的 `source_quote` 取**该 run 的原始 user 消息全文**（不是模型的转述），
  `summary` 取模型提议的正文。理由：`memory_sources` 对 `agent_message` 来源做全等校验，
  用模型的转述会 422；且「哪句原话」是审计真源，模型不该改写它。
- 两个工具都进 `REGISTRY`，走既有 `resolve_tool → validate_input → check_scope → 执行 → 审计`
  五道门禁。`search_memory` 复用 `search_rag`（同一 eligibility、同一引用投影），
  不新增检索路径。
- **拒绝的做法**：让工具直接确认记忆。确认是产品语义（用户对「这条事实进入我的记忆」
  的明示同意），不是技术细节；工具的输出只能是待确认候选。

## P3-a 确定性重排：先收集候选，再排序

现状缺陷：`collect` 在命中数达到 `limit` 时**立即停止**，因此主 FTS 分支一旦填满，
两字词 LIKE 后备分支根本不会运行——`桂花`（2 字）找不到 `story-桂花` 就是这个原因。

```text
旧：分支1 → 填满 limit → 停止             （分支2 被饿死）
新：分支1 + 分支2 → 候选池（受 _CANDIDATE_BUDGET）
    → 确定性重排（rank_version）
    → revision/citation 复核 _rows_to_hits
    → 取 top-k
```

重排特征（全部可从现有数据算出，零外部依赖）：
两路命中（phrase/term 与 LIKE 后备）的**来源权重**、`source_type` 权重
（用户确认的记忆 > 家族故事 > 授权文档）、词法相关度、`chunk_index`。

`rank_version` 进 `policy_json`；`lex-v1` 为旧顺序（显式回退开关），`lex-v2` 为新顺序。

## 实施顺序

```text
P4   分层预算          —— 自包含，无前置
P2-b 工具              —— 自包含，复用既有门禁
P3-a 重排              —— 依赖 P4 的 policy_json 版本化机制（同一处改动）
P3-b 向量重排          —— 阻塞：依赖 10-03-postgres-migration
```
