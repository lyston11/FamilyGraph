# 设计：记忆模型与检索质量

设计原则：**安全骨架零改动，质量能力可增量、可回退、可证伪**。
每个决策都给出「为什么不是另一个方案」，并标注它依赖的前置条件。

## 决策 1：记忆用「时间有效区间 + 取代指针」，不引入知识图谱

```text
memories
  valid_from        -- 事实开始有效的时间（可空 = 未知，不猜）
  valid_to          -- 事实失效时间（空 = 仍有效）
  superseded_by_id  -- 取代它的 Memory id（空 = 未被取代）
  supersede_reason  -- 'user_replaced' | 'source_revision' | 'expired'
```

**语义**：检索只返回 `status=active AND superseded_by_id IS NULL AND (valid_to IS NULL OR valid_to > now)`。
被取代的行**不删除**：历史可审计、可回溯、可撤销取代（用户改主意时把 `superseded_by_id` 清空）。

**为什么不是「直接改 content」**：家族记忆需要时间维度（「他 2020 年前住上海」），
覆盖写会把时序信息永久丢掉，而时序推理正是 LongMemEval 明确的一类能力。

**为什么不是知识图谱**：家族图谱已是结构化事实源（person / relation / event 表）；
记忆层只需解决「记忆行之间的取代关系」，引入第二套图会制造两个真源。

**谁判定取代**（分阶段）：
- P1 只做**用户显式取代**（确认新候选时可选「取代哪条」）+ 来源 revision 变化自动取代；
- P2 引入**模型提议取代**：抽取时输出 `supersedes_candidate_id`，但仍是**提议**，
  用户确认后才生效；冲突无法判定时**不静默择一**，两条都保留并标记冲突。

**拒绝的做法**：用文本相似度自动合并。同一个人「住上海」和「住上海浦东」相似度高但语义不同，
自动合并会静默丢事实。

## 决策 2：提取分两段——规则召回 + LLM 精炼，入库边界不变

```text
规则召回（快、免费、可解释）  →  候选池
LLM 精炼（只对召回结果做分类 / 摘要 / 去噪 / 取代提议）
                              →  仍是 pending candidate，等用户确认
```

**为什么不是「LLM 直接扫全部消息」**：成本随会话量线性增长，且会产大量低质候选。
规则先召回把 LLM 调用量压到「疑似可记忆」的小集合上。

**为什么不是「纯规则扩展」**：规则能枚举的类别有上限，而「这句话里哪部分值得记住」是语义判断。

**硬边界**（沿用 `memory-contract` §8）：LLM 产物**只能**是 pending candidate；
`source_quote` 仍必须是完整 user 原文；失败时 savepoint 回滚、settle 不受影响；
`MEMORY_ENABLED` 关闭时前置短路。

**成本控制**：LLM 精炼走 provider gateway 的**低优先级**预算，与在线问答共享 retry budget，
单次 run 的调用数有上界，不新建通道。

## 决策 3：新增受控记忆工具（只能提议，不能确认）

```text
memory_propose   required_kind="assistant"   -- 写 pending candidate，返回 candidate_id
memory_search    required_kind="assistant"   -- 只读，复用 search_rag 的授权过滤
```

**为什么需要**：用户显式说「记住这个」时，agent 必须有确定性的写入通道，不能指望 settle 规则。
`memory_search` 让模型能在回答前自查「我是否已经知道这件事」，减少重复提问。

**为什么不是「agent 直接写 Memory」**：那会绕过用户确认，把模型输出变成一等事实。
记忆入库的确认步骤是**产品语义**，不是技术细节。

**边界**：两个工具都进 `REGISTRY`，走既有 `resolve_tool → validate_input → check_scope → audit`
四道门禁；`memory_search` 复用同一 eligibility 过滤与 `_rows_to_hits` 引用投影，不新增检索路径。

## 决策 4：检索先做确定性重排，cross-encoder 由数据决定

```text
授权过滤 → PGroonga 词法候选 + pgvector 向量候选
        → union（按 chunk_id 去重，保留两路分数）
        → 确定性重排（RRF + 特征）
        → revision / citation 复核（_rows_to_hits）
```

**重排特征**（全部可从现有数据算出，无需新服务）：
词法分、向量分、来源类型权重（用户确认的记忆 > 家族故事 > 授权文档）、
revision 新近度、scope 距离（private 命中优先于 lineage）、chunk 在文档中的位置。

**为什么先做确定性重排**：当前向量路径**刻意不重排**（`memory_rag.py:1330-1370` 注释说明
「词法顺序是既有行为，改变它会让所有历史回归失效，而向量质量尚未经中文基准验证」）。
这条注释指出的正是本任务的入口条件：**先有中文 golden set，再谈重排**。

**迁移路径**：重排作为 `rank_version` 显式版本化；golden set 上证明提升后才默认开启，
且保留「词法-only 与今日逐字一致」的回归门。

**cross-encoder 的门槛**：只有确定性重排在 golden set 上的余量被证明不足时才引入，
且必须经 provider gateway（不得新增出网路径）。

## 决策 5：上下文按分层预算组装，不做贪心填满

```text
L1 系统指令 + 关系骨架（确定性投影，固定小预算）
L2 记忆（按 scope 优先级：private > household > lineage，活跃优先）
L3 检索片段（重排后按序填，单条超限则截断到句边界而非整块丢弃）
L4 最近对话（现有 recent_messages）
```

**为什么分层**：四类材料的作用不同，用一个总预算让它们互相挤占，会出现
「一段长家族故事把用户自己的记忆全挤掉」。分层后每层独立上限，溢出原因可解释。

**为什么允许 L3 截断**：现在「整块纳入/排除」的粒度是 chunk（800 字符），
一条长 chunk 会顶掉后续所有命中；按句边界截断到剩余预算比整块丢弃更接近用户预期。

**不变式保持**：每 `(run_id, attempt)` 一次构建、失效单调、引用由服务端生成、
`trust=untrusted_data` 标记不变；`policy_json` 记录分层预算与版本，使历史构建可解释。

## 决策 6：评估先行（P0 是硬前置）

```text
golden set（中文，人工标注）:
  - 信息提取     : 用户陈述 → 应产出哪些记忆候选
  - 时序推理     : 「他以前住哪」→ 需要 valid_from / valid_to
  - 知识更新     : 事实变化 → 只返回新事实
  - 多会话推理   : 跨会话聚合
  - 弃答         : 库里没有时必须说没有，不能编

指标: retrieval recall@k（含 citation 正确率）、answer 准确率、弃答正确率、
      延迟 p50/p95、单次问答的 token 成本
```

**为什么这是硬前置**：本任务全部改动都是质量改动，没有基线就无法判断提升还是回归，
也无法回答「是否值得引入 cross-encoder / 向量路径」。这是 `10-05-migration-proof-gates`
建立的证明门在检索领域的对应物。

## 实施顺序与依赖

```text
P0 评估基线（golden set + 指标 + 回归门）        ← 无外部依赖，可立即做
P1 记忆取代语义（valid_to / superseded_by + 迁移） ← 依赖 P0 才能证明「旧事实不再返回」
P2 LLM 提取精炼 + memory_propose 工具            ← 依赖 P1 的取代提议接口
P3 hybrid 确定性重排（rank_version）             ← 依赖 PostgreSQL 迁移 + P0
P4 分层上下文预算                                ← 独立，可并行
P5 contextual chunking + index_version 回填      ← 依赖 P3 / P4 的评估能力
```

**阻塞说明**：P3 的向量路径与 P5 的批量回填在 PostgreSQL 迁移落地前无法真实运行
（现有 RAG 跑在 SQLite FTS5 上），与 `10-03-pgvector-rag` 记录的阻塞一致。

## 回滚

- P1：新列可空，关闭取代判定即回到「全部 active」的今日行为。
- P3：`rank_version` 回退到 `lex-v1` 顺序，向量路径可单独关闭。
- P4：分层预算可由 `policy_json` 版本回退到单预算贪心。
- 任一阶段失败都不得删除历史记忆或索引数据。
