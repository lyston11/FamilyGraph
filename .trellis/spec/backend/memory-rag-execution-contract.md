# Assistant 记忆与 RAG 执行合同记录

2026-09-14，记录 B 修复后的跨层实现与回归入口。本文是代码合同记录，不替代根目录 AGENTS.md 或任务授权，不将历史 spec 索引重新设为工作流门禁。索引物化、算法换版及维护租约由 D 的独立合同负责。

## 1. 适用范围

适用于 Assistant 的 run token、ContextBuilder、事件追加、公开引用投影和 sidecar 材料包。目标是让实际执行身份、原片段和当前读取权限始终对应，避免只在 HTTP 入口鉴权、只按来源 ID 认证或只在 mock 中对齐字段。

Steward 未接入私有聊天 RAG；词法查询不构成 embedding 或通用语义理解。RAG 子预算不等于整个模型请求窗口。

## 2. 签名与持久字段

- `ExecutionIdentity.from_claims(claims)` 捕获签名的 `run_id/job_id/attempt/account_id/space_id/agent_kind/tool_allowlist`。`fence_execution(db, identity, ...)` 在短 SQLite writer 内读取 Run、Job、Session、Account 和会员资格，比较两侧 attempt、scope、状态、取消和租约有效时间。
- `ContextBuilder.build(..., run_id, attempt, execution, recent_messages, provider_decision)` 为同一 `(run_id, attempt)` 复用一个构建。`context_builds` 的 `policy_json/invalidated_at/invalidation_reason` 由迁移 `0046_context_execution_contract` 添加。
- `rollback_preserving_invalidation(db, execution)` 仅从签名执行的唯一 build 捕获服务端已观察到的失效标量，回滚事件批次后按相同 scope/id 条件保留；不读取 sidecar 自报理由，不重建已删除的 build。
- `ExactChunkRef` 包含 `document_id/chunk_id/source_type/source_id/source_revision/index_version/chunk_index/content_hash`。它由服务端命中生成并保存于 item/已认证消息，客户端不能自报 hash 以获得认证。
- `POST /internal/agent/runs/{run_id}/events/append` 的事件可以携带独立于 `public_payload` 的 `context_reference`。
- `GET /api/agent/runs/{run_id}/events/{seq}/citations` 通过所属 session、assistant role 和服务端消息 key 定位完整引用；不是任意句柄查询端点。

## 3. 请求、响应及边界

```json
{
  "seq": 2,
  "type": "message.assistant_added",
  "public_payload": {"role": "assistant", "text": "合成回答 [rag:42:r1:c3]"},
  "context_reference": {"build_id": 7, "attempt": 1, "used_handles": ["rag:42:r1:c3"]}
}
```

`build_id/attempt` 为严格正整数；`used_handles` 最多 20 个，每个 1～255 字符，不可重复。只有已完成的 assistant 消息能携带 reference。sidecar 从完成文本与本次 included 句柄的交集生成列表；不从流式半句或材料列表推断已使用。

服务端先按原始请求生成 v2 指纹：`run_id/attempt/seq/type/public_payload/context_reference` 全部参与，再认证和裁剪。已提交的同请求只确认 duplicate，不因后续撤权改写原消息或指纹。没有 reference 的旧 sidecar 保留正文/web，不猜测认证引用。

`agent_citations` 统一历史、SSE/重连和固定补取的引用投影。公开字段 `citations/unavailable_citation_count/citations_complete` 由服务端生成；失权后不输出受限来源定位和摘录。内部历史只下发允许的 user/assistant 正文。既有正文的留存不等于结构化引用仍有权限。

整个 `public_payload` 按 `json.dumps(..., ensure_ascii=False).encode('utf-8')` 测量，上限 16384 字节。引用装不下时保持正文/web，固定补取完整引用；前端显示可重试的加载状态。不要截断已合法的正文，也不要抬高事件限额。

`rag_budget.render_context_appendix` 与 `agent/src/context.ts` 使用同一材料包；估算器 `utf8-half-envelope-v1` 为 UTF-8 字节数除二向上取整，包含标记、句柄、换行和引用指令。默认子预算 2000，整块纳入/排除。共享冻结包络 fixture 验证两种语言逐字一致。

查询计划 `lex-v1`：NFKC、500 字符、8 词项、受控别名。只在明确指代且最近 4 条同会话允许文字内有唯一锚点时扩展；任一历史消息超过 500 字符则保守降级。短词 LIKE 使用参数绑定和显式 `ESCAPE '!'`，转义 `!/%/_` 以保留查询字面含义。FTS 和两字后备共享 200 个 SQL 返回候选预算、每页最多 32；这是候选预算，不是 SQLite 内部实际扫描行数上界。

## 4. 校验与错误矩阵

| 条件 | 行为 |
|---|---|
| 签名 attempt 缺失、bool 或非正整数 | token 拒绝，不补成当前 attempt |
| 入口鉴权后发生真实 reaper/lease 换代 | writer/admission 拒绝 `AGENT_TOKEN_SCOPE_MISMATCH`，无旧执行副作用 |
| Run/Job 不可执行、取消或租约过期 | 409，保持现有终态/取消优先级 |
| 同 attempt 构建来源、策略或预算已失效 | 409 `AGENT_CONTEXT_INVALIDATED`，失效状态不可恢复；新 attempt 可重建 |
| reference 错 build/attempt、句柄未 included 或正文未使用 | 409 `AGENT_EVENT_INVALID`，无认证引用 |
| 原片段缺失/改文/改版本/改 revision 或当前失权 | 不认证该片段，不改指向新搜索结果 |
| 同 seq 异原请求 | 409 `AGENT_EVENT_SEQ_CONFLICT`，整批事件/消息/指纹回滚 |
| public payload 16385 字节 | 422 `AGENT_EVENT_INVALID` |
| 0046 存在新 policy/失效证据时尝试降级 | 第一项 DROP 前拒绝，保留证据 |

已准入的 Provider/工具在途操作允许按现有合同完成；准入在网络 I/O 前提交，不持有网络长事务。工具 `ToolRunScope` 固定原 attempt，持久结果和审计不得随 ORM 自动刷新漂移到新 attempt。网页日期输出先统一成 JSON，首次与重放同样经过结果策略。

## 5. 正常、兼容与反例

- 正常：同 attempt 并发 GET 返回同一构建；实际回答使用的合法句柄在 SSE、历史及补取一致。
- 兼容：旧消息没有 citations 正常渲染；RAG 关闭时建立的空构建在同 attempt 再开启后仍为空，不偷偷换材料。
- 反例：合法 context 已因关闭 RAG 或来源失效被服务端判无效，不能因随后事件批次冲突回滚而丢掉这一安全状态；事件事务和失效状态分别保持各自语义。

## 6. 回归入口及断言

- `test_rag_acceptance_contract.py`、`test_rag_acceptance_bindings.py`：真实独立 Session 换租；context 并发；精确定位与持续失效；伪造字段、各读取出口；16384/+1；原请求重放；已准入工具归属和真实 gateway 网络点可写。
- `test_rag_query_context.py`：相同问题配不同唯一历史得到不同锚点，歧义/无前文/越窗口不猜；授权补足跨页且总候选受限。
- `test_context_execution_migration.py`、`test_memory_source_migration.py`：证据保留与真实迁移 head；分支降级不能用 scalar 任取 Alembic version 当唯一 head。
- `agent/test/context.test.ts`、events/worker tests：真实包络、完成文本引用列表、attempt/build 接线和 C 的压缩恢复。
- `scripts/smoke/run_agent_memory_smoke.py`：隔离三 listener、真实 Pi/HTTP、撤权/丢响应/重连/补取与原始内部历史。模型流为合成，不代表真实模型忠实度或线上性能。

## 7. 错误做法与对应实现

错误：从可变 ORM `run.attempt` 重建期望身份，在入口 SELECT 后直接写入；引用只核对 `source_id`；认证后 payload 用来判断原请求幂等；把 failed 事件批次回滚当成可以恢复旧 context 的理由。

正确：签名身份贯穿短 writer/admission；服务端保存精确片段描述符并按当前权限重新读取；指纹在认证/裁剪前计算；同 attempt 失效标记单调保留，事件本身仍保持整批原子性。


## 8. 检索索引的授权分层（2026-10-06 实测补充）

PGroonga 与 pgvector 的实测共同确认一条安全关键结论：

> **检索索引不承载授权。** 撤权只改主表状态，索引条目仍存在（PGroonga 实测：
> 无过滤查询仍能查到已撤权 chunk；pgvector 实测：行与向量都还在）。
> 可见性**完全**依赖查询层的 `status`/`scope`/`revision` 过滤。

因此：

1. **所有检索路径必须带授权过滤**，不能依赖索引删除生效；
2. 过滤条件**承重**——去掉它就能查到已撤权内容（已用反证确认）；
3. 索引是**可重建派生物**：PGroonga 恢复时自动重建、pgvector `REINDEX` 后结果一致；
4. **ANN 必须 filter-then-ANN**：实测 post-filter 在低选择性下静默返回不足 k
   （允许 1/10 空间时只剩 1 条），而 RAG 无法区分「无相关内容」与「被授权过滤掉」；
5. 词法主路径为 **PGroonga**（四方对照 10/10 精确），`pg_trgm` 已实测排除
   （CJK 相似度全部低于阈值），Unicode n-gram 为后备（短查询可用但过度召回）。

详见 `10-04-lexical-search-migration` 与 `10-03-pgvector-rag` 的 `research/evidence/`。

## 10. 分层上下文预算与确定性重排（2026-10-10 补充）

本任务（10-10）的两个交付，都是**可审计的质量参数**，因此进入本合同。

### 分层预算（P4）

- 每个 `source_type` 有独立的份额上限 `tier_budget = ceil(token_budget * fraction)`，
  且**不做容量转移**：空类别不把自己的份额借给其它类别。结构性保证是任何单一类别
  都不可能占满整个预算（所有份额 < 1）。
- 份额集合：`memory 0.6 / family_story 0.4 / authorized_document 0.4 / profile 0.2 /
  public_kinship 0.2`，总和 > 1 是刻意的——它表示各类别**各自**的上限，不是配额切分。
- `tier_budget` 与 `token_budget` 是**两种**排除理由，必须可区分；trust 门禁
  （`invalid_trust`）必须先于两者。
- `policy_json` 记录 `tier_budget_version` 与份额；`_replay` 用 `_tier_allocation`
  复算，预算算法变化时以 `tier_budget_changed` 失效，与 `budget_changed`（总量）区分。
- 新增来源类别必须显式登记份额，否则静默落到默认 0.2——有专门断言防这个。

### 确定性重排（P3-a）

- 候选收集**不再在 limit 处短路**：所有分支先收集候选（受 `_CANDIDATE_BUDGET`
  约束），再统一重排。修掉的缺陷是「主分支填满后短词后备分支被饿死」。
- 重排特征（`lex-v2`）：查询词与正文的重叠度（按长度加权，替代原来只是行号的
  `rank`）、分支共识（被多分支命中的 chunk 更相关）、来源类别（用户确认的记忆
  > 家族故事 > 授权文档 > 公共亲缘）、chunk 位置与 chunk_id（稳定全序）。
- `rank_version` 进 `policy_json`；`lex-v1` 是显式回退开关（候选到达顺序）。
- 重排必须是**确定性**的：同一数据同一版本给出逐字节相同的顺序，否则
  `ContextBuild` 的「每次执行不可变」与 `_replay` 一致性无从验证。因此**不做**
  模型重排——cross-encoder 的质量增益需要先有基线余量证明，且要经 provider gateway。
- 词法规划（`_segment_terms`）的二元组按「先偶数位后奇数位」排列，固定词额下最大化
  被覆盖的字符位置；「句尾实义词被滑动窗口挤出」是这个顺序修掉的真实缺陷。

### Wrong vs Correct

错误：让空类别把份额「借给」其它类别；用截断句子边界来省预算（会破坏
`_replay` 的 `content_hash` 校验与不可变合同）；用模型 rerank 却要求逐字节
可复现；把「短词分支被饿死」当成词法能力的固有限制而不修候选收集逻辑。

正确：分层预算各自设上限、不做容量转移、理由可区分、版本进 policy_json；
重排用确定性特征、版本化、可显式回退。

## 9. 检索质量评估基线（2026-10-09 补充）

本任务（P0）建立的评分设施。任何检索/提取改动的**证明义务**是先有基线分，再有对比分；
「感觉更好了」不是证据。这是 `10-05-migration-proof-gates` 的证明门在检索领域的对应物。

### 组件与版本

| 组件 | 路径 | 版本标识 |
|---|---|---|
| golden set | `backend/tests/fixtures/memory_eval/golden_v1.json` | `fixture.version` |
| 评测器 | `backend/app/services/memory_eval.py` | `memory-eval-v1` |
| 回归门 | `backend/tests/test_memory_eval_baseline.py` | 常量 `MIN_*` / `MAX_FORBIDDEN_HITS` |
| 报告 | `artifacts/memory-eval/baseline.json`（gitignore） | 含 evaluator/fixture/model 版本 |

指标定义变化必须提升 `EVALUATOR_VERSION`，否则历史报告不可直接比较。

### 三种用例 `mode` 必须互不混同

```text
answerable     必须召回全部期望来源（recall = 命中/期望）
abstention     必须返回空（库里没有就说没有）
forbidden_only 只约束「不得返回某来源」，不要求整条为空
```

用单个 `expect_empty` 表达「不得把已结束事实当当前事实」是**错的**：问题里的其它词
（如「舅舅」）本来就合法匹配别的记忆。三种 mode 分别有自己的指标与阈值。

### 两层回归门（取值理由）

```text
contract 层  pass_rate / recall / abstention_accuracy 必须 100%
quality 层   只防退化（当前基线见报告）
两层共享     forbidden_hits 必须为 0
```

contract 层表达的是**合同**（取代后不返回旧事实、弃答返回空、单跳提取与时序），
允许部分通过等于允许静默退化，因此不设余量。quality 层（多会话聚合：一个问题同时
指向两条记忆）当前词法路径做不到——设成硬门会让门立刻失败从而被绕过，设成 0 又等于
放弃观测，因此记录基线，由 P3 确定性重排提升后再升级为 contract 层。

**放宽 `MIN_*` 阈值或修改 fixture 的期望值，必须在同一次提交里写明为什么是 fixture
的期望错了**；否则等于把回归伪装成通过。

### 提取器评测

`evaluate_extraction` 同样分 contract（规则应当覆盖的类别与守卫）与 gap（实测能力缺口）
两层。gap 用例的期望值是**产品要求**，不是实现现状：它们刻意保持失败，作为 P2 提取
精炼的输入清单。

### 指标边界（不得越界宣称）

本模块**不评测回答准确率**——那需要真实 provider egress与独立的判分协议。它只评测
检索层与确定性提取器，理由是检索是回答的必要条件，且可完全离线、可复现。

### Wrong vs Correct

错误：为了让报告变绿而放宽 fixture 的期望值；把「不得返回某来源」写成 `expect_empty`；
把 quality 层失败当作环境问题跳过；用单个总 pass_rate 掩盖某一层的退化。

正确：三层 mode 各自度量、两层阈值各自设置、报告落盘并与 evaluator/fixture 版本绑定、
fixture 期望值只允许在写明理由时修改。

## 10. 向量检索的接线与相似度地板（P3-b，2026-10-10）

### 1. Scope / Trigger

改动 `memory_rag._vector_candidates`、`search_rag` 的向量分支，或更换 embedding
模型 / chunking 算法。

### 2. Signatures

```python
def _vector_candidates(
    db: Session, *, actor: User, account: Account, space_id: int, agent_kind: str,
    query: str, eligibility: str, seen_chunk_ids: set[int], limit: int,
    now: Any, is_assistant: int, user_id: int,   # ← eligibility 的绑定参数，必须传
) -> tuple[list[RAGHit], int]
```

### 3. Contracts

- **eligibility 的每个命名参数都必须传给向量查询**：向量 SQL 与词法 SQL 共用同一段
  `_ELIGIBILITY_SQL`，漏传任何一个都会让整条查询抛 `StatementError`、被 `except`
  吞掉、**静默回退词法**。实测：P1 引入 `:now` 后向量路径**完全死亡**而无人察觉。
- **失败必须记录 `error_class`**：只写「回退词法结果」从现象看不出原因。
- **相似度地板 `_VECTOR_MIN_SIMILARITY = 0.50`**：低于它的向量候选丢弃。取值来自
  真实 `bge-small-zh-v1.5` 上的实测分布（见下）。地板**只作用于向量新增候选**，
  不作用于词法命中，因此误伤代价为零。
- **只增不减**：向量候选不得挤掉词法命中。
- **换模型或换 chunking 算法必须重测地板**：相似度尺度会变，不能沿用。

### 4. Validation & Error Matrix

| 情形 | 后果 |
|---|---|
| 漏传 eligibility 绑定参数 | `StatementError` → 静默回退词法（检索看似正常） |
| 无相似度地板 | 弃答用例被无关向量填满，abstention 1.00 → 0.00 |
| 地板过高 | 向量补充失效（退化为词法-only，可接受但无收益） |

### 5. Good/Base/Bad Cases

- **Good**：`1.0 - hit.rank >= _VECTOR_MIN_SIMILARITY`（`rank` 是余弦距离）。
- **Base**：地板保留词法命中不动。
- **Bad**：用余弦相似度做**重排依据**——实测相关/不相关分布重叠
  （不相关最高 0.7712 > 相关最低 0.5091）。

### 6. Tests Required

- `test_search_rag_vector.py`：绑定参数齐全、地板丢弃、地板保留（反证）、
  失败回退并 rollback、只增不减。**接线层此前完全没有测试**，这正是缺陷得以
  隐藏的原因。
- mutation：去掉地板 → 必须失败；去掉 `now` → 必须失败。

### 7. Wrong vs Correct

#### Wrong

```python
db.execute(sql, {"query_vector": literal, "limit": k, "offset": 0})  # 缺 now/is_assistant/...
except Exception:
    logger.warning("向量检索查询失败，回退词法结果")   # 无法诊断
return [h for h in hits if h.chunk_id not in seen]     # 无地板
```

#### Correct

```python
db.execute(sql, {
    "query_vector": literal, "model": ..., "limit": k, "offset": 0,
    "now": now, "is_assistant": is_assistant, "user_id": user_id,
    "account_id": account.id, "space_id": space_id,
})
except Exception as exc:
    logger.warning("向量检索查询失败，回退词法结果 error_class=%s detail=%s",
                   type(exc).__name__, str(exc)[:200])
return [h for h in hits
        if h.chunk_id not in seen and (1.0 - float(h.rank)) >= _VECTOR_MIN_SIMILARITY]
```

## 11. contextual chunking：不实现（有实测负面证据，2026-10-10）

**结论：不实现。** 实测在长文档多分段场景下，来源级前缀**降低**质量：

```text
plain (现状)        7/8   ← top-1 分段所属文档正确率
contextual prefix   6/8
```

原因：来源级前缀对同一来源的所有分段相同，等于给每个向量加常量分量，**稀释**了
分段自身的内容差异。实测表现：`谁的胃不好？` 从命中「舅舅传」变成「外婆传」。

探针：`scripts/migration-proof/contextual_chunking_probe.py`（含第一轮**无效测量**
的记录：golden set 的记忆平均 20 字符、每 chunk 只有 1 段，前缀没有可测量空间）。

**何时重新考虑**（4 个条件全部成立）：golden set 里有真实长文档用例；前缀由**每个
分段单独生成**（LLM）而非来源级常量；该 LLM 调用经 provider gateway 且成本已计入
预算；在同一份 golden set 上证明增益。


## 11. 记忆可见级别：显式允许集与 `private` 的读者（2026-10-10 补充）

本任务（10-10）给管家加了受控的记忆读取。它同时改动了 §8 所说的**授权过滤**，因此进入本合同。

### 允许集是集合，不是布尔

`search_rag` 新增两个关键字参数，都是**额外**的门，不替代 fenced 身份与 space 成员判据：

- `scope_allowlist: Sequence[str] | None`（`None` = 全部，即既有行为）；
- `private_reader_account_id: int | None | _UnsetReader`（未传 = 调用方自己的 `account.id`，
  即 assistant 语义；显式 `None` = 无私有读者，恒不可读）。

`_ELIGIBILITY_SQL` 里每个 scope 分支都有自己的开关（`:allow_private` / `:allow_household` /
`:allow_lineage`）。**不能用布尔**：管家按配置可能只允许 `household` 而不允许 `lineage`，
布尔会把「读哪些级别」退化成「全开或全关」。

哨兵用专属类型 `_UnsetReader` 而不是 `object()`：`object()` 会让类型检查器把参数类型退化成
`object`，`is` 判断无法收窄。

### `private` 的判据是读者，不是 kind

private 分支由 `:is_assistant = 1` 改为 `d.author_account_id = :private_reader_account_id`。
旧写法的缺陷是真实的：管家的空间级 kind（`candidate`/`ranking`/`explanation`）没有 viewer，
身份回落到 **space admin**，因此一旦为管家打开 private 就会读到管理员本人的私事。
改成显式读者后，无法提供读者（NULL）即恒不可读。

`public` 分支**继续保留** `:is_assistant = 1`。

### 两层必须各自独立挡住

`private` 的读者约束在两处实现，两处都不得回退：

1. `memory_rag._ELIGIBILITY_SQL`（SQL 层，filter-then-ANN 的 `authorized` CTE 也走它）；
2. `memory_sources._can_read_document`（投影复核层；`_rows_to_hits`、精确片段重读、来源访问
   检查都走它）。

只改一层的后果都已实测：只改 SQL 会让 `document_readable` 把已授权命中全部拒掉（表现为
「检索不到」——`_vector_candidates` 复用 `_rows_to_hits` 正是为了避免这种不一致）；只改复核层
则会让未授权行进入投影。

`_rows_to_hits` / `_vector_candidates` 都必须把解析后的读者一并传给向量 SQL 与复核层：
漏传任何一个绑定参数都会让整条查询抛 `StatementError` 并**静默回退词法**（§8 已记录过这个
失败形态）。

### 配置语义

有效可读集 = 部署 env ∩ 平台列 ∩ 空间列，**交集**且任一为空即整体为空（默认全空 = 管家读不到
记忆，与引入该能力之前逐字等价）。未知 scope 在读取时忽略并告警、在写入时 422：静默丢弃会让
「关掉某级别」看起来生效了。

**Wrong vs Correct**

错误：用「哪些 kind 能读 private」当判据；用布尔代替 scope 允许集；只改 SQL 层或只改复核层；
把「配置不允许」返回成空列表（它会把授权拒绝伪装成「没有相关内容」）；让 `private` 读者回落到
回落身份（space admin）。

正确：显式允许集 + 显式读者；两层各自独立挡住；空可读集直接 403；写入时严格校验。

## 12. 档案清空声明：字段级掩码落到语料文本（2026-10-11 补充）

任务 `10-11-rag-authorization-model-unification` 把 `visibility.py` 的四级阶梯接到 RAG
语料上。此前 `_author_visible` 只把 `evaluate` 当「作者可见/不可见」一道门，读取路径用
`RAGDocument.scope` + 空间成员，**字段级掩码与未成年人 overlay 到不了 chunk 文本**。

### 声明的位置与形状

复用既有但此前恒为 `{}` 的 `RAGDocument.visibility_snapshot`（JSON）+ `visibility_snapshot_key`：

```json
{"subject_user_id": 42, "required_categories": ["dates"], "key": "profile-v1"}
```

含义是「**读我至少需要哪些类别是清的**」，不是「写入时的可见性快照」。`key` 与
`visibility_snapshot_key` 必须同时等于 `visibility.PROFILE_CLEARANCE_KEY`；不符即不可读
（fail closed），旧声明不会被新语义重新解释。

`_check_document_metadata` 对 metadata 的**每个键**做等值比较，因此同
`(source_type, source_id, revision)` 但声明不同 → `_index_conflict()`，不会静默覆盖。

### `subject_user_id` 是 opt-in 标记

| 值 | 语义 | 行为 |
|---|---|---|
| `NULL` | 内容不是「关于某个具体人的档案派生」 | **scope 裁决**（`memory` / `public_kinship` 保持逐字不变） |
| 非 `NULL` | 内容派生自该主体档案 | **主体阶梯裁决**（字段掩码 + 未成年人 overlay + HIGH_RISK 自限） |

**为什么 `memory` 不叠加掩码**：scope 是主体在确认候选时**显式选过的**可见性，再叠加字段掩码
等于推翻主体的选择。掩码管的是主体**没有选择过**的可见性，即他人档案派生的内容。

### 判据（`visibility.required_clearance_satisfied`，单一真源）

1. `HIGH_RISK_CATEGORIES`（health/address/school/contact/private_notes）**只有主体本人**
   （`self_private`）可读。它们的 `CATEGORY_FIELD_MAP` 条目当前是空占位，无法用「字段是否
   清空」表达；这条与 `HIGH_RISK_CATEGORIES` 的既有注释一致。
2. 其余类别要求其映射字段全部 `FIELD_CLEAR`。
3. 未知类别按 HIGH_RISK 处理（fail closed）：分类不了的内容只有主体可读。

**RAG 的字段清空实际来自披露，不是来自成员身份。** `_PURPOSE_LEVEL_CAP[PURPOSE_RAG]` 把层级
封在 `lineage_summary`，而该层只清 `disclosed_categories` 映射到的字段。这是刻意的：空间成员
身份本身不足以读别人档案派生的内容，必须主体已披露该类别。

未成年人 overlay **不需要在 RAG 层复制**：`evaluate` 已在最后移除 birth/death/bio，
`required_clearance_satisfied` 只是要求它们为 clear。

### 施加点：投影复核层，不是 SQL 层

判据落在 `memory_sources._profile_clearance_ok`（复核层），因为 `evaluate` 需要读关系、成员与
披露记录。SQL 层继续做**粗筛**（scope 允许集、来源类型、取代/有效区间），复核层做**细判**。
两层各自独立挡住的设计不变——把 `evaluate` 塞进 SQL 会把细判降级成粗筛，反而更弱。

### 写入侧

`ingest_authorized_document(..., subject_user_id=..., required_categories=(...))`：
- 写入侧对未知类别 **422**；读取侧对未知类别保守（只有主体可读）。两侧口径不同是刻意的。
- 声明了档案类别时 `scope` 不得为 `public`：`public` 的定义是「不含个人数据」。

**Wrong vs Correct**

错误：把 `memory` 也加上清空声明（推翻主体选过的 scope）；在 RAG 层复制四级阶梯 /
`HIGH_RISK_CATEGORIES` / overlay 字段；把判据放进 `_ELIGIBILITY_SQL`；用 household 成员身份
当作字段清空依据；声明 key 不符时按新语义重解释。

正确：`subject_user_id` 作为 opt-in；判据只在 `visibility.py` 一处；复核层施加；RAG 清空来自
披露；key 不符即不可读。

## 13. 统一授权检索入口（2026-10-11 补充）

任务 `10-11-unified-authorized-retrieval-entry` 把「谁在读、被允许读什么」从每个调用点的
参数列表收拢到一处。

### 问题

`memory_rag.search_rag` 的三个授权参数在多数调用点上**靠默认值**：

| 参数 | 默认值语义 |
|---|---|
| `scope_allowlist=None` | **三个 scope 全开**（assistant 语义） |
| `private_reader_account_id` 未传 | 回落到调用方自己的 `account.id` |
| `source_types=None` | **全部来源类别** |

因此**新增一个消费者时，最省事的写法恰好是最宽的授权**。改造前 5 个调用点里有 3 个
（`context_builder`、`build_run_context`、`rag.search`）依赖默认全开；其中
`build_run_context` 对 steward run 也会拿到 assistant 的允许集。

### 形状

`services/knowledge_retrieval.py`：

```python
identity = knowledge_retrieval.RetrievalIdentity(
    agent_kind=..., actor=..., account=..., space_id=...,
    viewer_account_id=..., source_types=...,
)
outcome = knowledge_retrieval.retrieve(db, identity, query, ...)
```

- `resolve_authorization(db, identity)` 派生允许集：assistant → `MEMORY_SCOPES`；
  steward → `steward_memory.readable_scopes(...)`（env ∩ 平台 ∩ 空间，无 viewer 时去掉
  private）。**未知 kind → 422（fail closed）**。
- `private_reader_account_id` 由身份派生：assistant → 自己的 account；steward → **该 run 的
  viewer**，空间级 kind 为 `None`（恒不可读）。
- **可读集为空是授权结论，不是「读全部」**：直接返回空 `RetrievalOutcome`，不调 `search_rag`。
- `RetrievalOutcome.filtered_out` 把「没有相关内容」与「被授权过滤掉」分开——RAG 之前无法
  区分这两者，而它们在产品上是完全不同的事。

本模块**不复制**任何授权判据：阶梯、`HIGH_RISK_CATEGORIES`、档案清空声明、未成年人 overlay
仍全在 `visibility.py` 与 `memory_sources.py`。它只把身份翻译成参数。

### 结构性守卫

`tests/test_knowledge_retrieval_entry.py::test_search_rag_is_only_called_from_the_unified_entry`
用 AST 扫描 `app/**/*.py`：`search_rag(` 只允许出现在 `memory_rag.py`（定义处）与
`knowledge_retrieval.py`（唯一调用方）。新增消费者绕过统一入口会**直接失败**。

### 循环导入

`knowledge_retrieval` 在模块级导入 `memory_rag`（它是上层入口），因此 `memory_rag` 只能
在函数内惰性导入 `knowledge_retrieval`。反过来会在 `memory_rag` 初始化完成前触发
`AttributeError: partially initialized module`。

**Wrong vs Correct**

错误：新增消费者时直接调 `search_rag` 并只传部分参数（其余吃默认全开）；把「可读集为空」
当作「无内容」或退回全开；在 `knowledge_retrieval` 里再定义一份来源类别常量（与
`MEMORY_TOOL_SOURCE_TYPES` / `STEWARD_READABLE_SOURCE_TYPES` 同义并会漂移）。

正确：以 `RetrievalIdentity` 声明身份；用既有常量作目的限定；空可读集直接返回空结果。

## 14. 预算的消费方维度与新鲜度衰减（U3，2026-10-11 补充）

### 份额是消费方相关的

`rag_budget.tier_fraction(source_type, consumer_kind)` 增加第二个维度，覆盖表为
`CONSUMER_TIER_OVERRIDES = {consumer_kind: {source_type: fraction}}`。

**为什么需要它**：同一批语料在不同消费方下的恰当配比不同，而原因不是调参——
assistant 是**账号级**消费者（记忆是主料），steward 是**空间级**消费者（输入是空间投影，
记忆是受控附加项，默认关闭）。用 assistant 的 0.6 记忆份额会让空间级来源在管家侧被挤掉。

**作用域被刻意限制**：只登记 `steward`，assistant 的取值与引入该维度之前**逐字相同**
（有测试钉住）。未登记的 `(consumer_kind, source_type)` 回落到 `SOURCE_TIER_FRACTIONS`，
因此新增消费方不需要先登记就能工作，不会静默变成 0。

`tier_budget_policy(token_budget, consumer_kind)` 必须同时记录 `consumer_kind` 与
`consumer_overrides`——份额是消费方相关的，只记 `tier_fractions` 会让事后审计看不出
实际用了哪一套。

### `budgeted` 必须进入被比较的 policy

`build()` 把 `budgeted` 写进 `policy`（因此进 `policy_json` 并被 `_replay` 比对）：
从「按预算」切到「不按预算」是一个真实的政策变化，重放时应当失效而不是静默沿用旧分配。

**并且 `_replay` 在不按预算时不得重算分层分配**：`budgeted=False` 的构建（steward 的
确定性投影）没有经过分层预算，投影整块大于默认份额，重算必然不同，会让每次 steward
重放都误报 `tier_budget_changed`。`_tier_allocation` 也相应接受 `consumer_kind`。

### 新鲜度是独立的排序特征（`lex-v3`）

`RANK_VERSION_RECENCY = "lex-v3"` 在 v2 的特征序列里插入**新鲜度**，位置在**来源类别之后**：

```
词法重叠 → 分支共识 → 来源类别 → 新鲜度 → chunk 位置 → chunk_id
```

- **为什么在类别之后**：类别优先是既有语义（确认过的记忆 > 家族故事 > 授权文档 > 公共亲缘）。
  把新鲜度放到最前面会直接推翻类别优先级——那不是「加上衰减」，是「换掉排序」。
- **为什么分桶而不是连续**：连续衰减让排序对任意时间差敏感，同一批数据隔一天就可能换序，
  测试与审计都不可复现。桶边界见 `RECENCY_BUCKETS`（7/30/180 天）。
- **缺失时间戳按最旧处理**，时钟偏差不越过最高桶分值。
- **`lex-v3` 不是默认**：排序是历史回归与评测基线的基准，换默认值要先有评测证据。

数据来源是 `rag_documents.updated_at`（词法 SQL 与向量 SQL 都必须选这一列；`RAGHit` 用
`.get` 容错，使漏选表现为「新鲜度缺失」而不是整条检索 500）。

**Wrong vs Correct**

错误：把衰减直接混进 `_SOURCE_TYPE_RANK_WEIGHT`；把新鲜度放到排序首位；用连续衰减函数；
新增消费方时不登记覆盖就假设它会生效；`budgeted=False` 的构建仍走分层比对；只给词法 SQL
加 `updated_at` 而漏掉向量 SQL（那会让向量候选静默失去新鲜度）。

正确：份额按 `(consumer_kind, source_type)` 查；新鲜度是独立特征且排在类别之后；
分桶；未登记即回落；`_replay` 尊重 `budgeted`。
