# PRD：中文 RAG 召回与回答引用闭环

## Goal

让用户用正常中文问题找到有权限的已确认资料，并能从 Assistant 回答追溯到本轮实际引用的来源。

父任务：[治理总任务](../09-13-agent-memory-rag-remediation/prd.md)。B 负责 MR-03、MR-04、MR-09、MR-14、MR-10 的 RAG 子预算；MR-24 通过真实检索与跨端合同回归解决。2026-09-14 已获继续修复、审查验收与通过后集成归档授权；本轮关闭 [B-I01～10](../09-14-memory-rag-acceptance-audit/research/b-integration-check.md)，以原 B-AC1～8 和复查 F-01～06/F-12 重新验收。

## Background

当前检索是 SQLite FTS5 trigram + 整句引号短语 + BM25，没有 embedding。对“今年春节在上海聚餐，外婆喜欢清淡饮食。”，两字查询“春节”“上海”和自然问句均实测 0 命中，连续三字“在上海”可命中。查询只用本次最新 user 原文；固定 1200 字符分块无重叠。实际 ContextBuilder 预算为 2000，按 len(text)//4 估算。

本轮 RAG 只预取一次，sidecar 把 citation/content 拼入问题，未将 RAG citations 回传到事件。前端已有 citations 解析和 CitationList，因此缺口在可信来源核验及事件/历史接线。证据：[MR-03～14](../09-13-agent-memory-rag-remediation/research/audit.md)，并非线上模型质量结论。

## Requirements

- B-R1：可安全执行的确定性 query plan 支持中文两字词、自然问句、精确短语和已声明的实体别名/领域词；不要求用户复述原文。不引入必需的额外模型调用。
- B-R2：仅在当前问题明确指向有限同会话前文且实体唯一时扩展查询；无法判定时记录降级，不从别的会话/空间补猜。历史用于构造查询，不自动成为可检索长期记忆。
- B-R3：所有召回支路先限制可读来源，再做排名和有界补足；复用 A 的来源依赖。legacy/unverified、pending、失效和无权来源不得进入结果或 AgentContext。
- B-R4：句/段边界优先、长度和重叠有上限；保留 source revision 与 index_version 的区别。同源同版本重试定位稳定；明确 Memory.content 默认是确认摘要，不能悄悄把全部 raw_quote 入索引。
- B-R5：RAG 子预算计入文本与包装开销，记录包含/排除原因，提供中文保守估算或已验证 tokenizer；不声称已经解决全请求窗口、长历史和输出 reserve。
- B-R6：回答可标记本次有效 attempt/build 的 included 来源；服务端重验绑定、句柄与当前权限后持久化到 citations。同一来源句柄可合法跨轮复用，但不能绑定他轮 build；事件幂等不因服务端认证/裁剪而失效。检索结果与已引用来源分别表达；引用核验不等于保证正文语义正确。
- B-R7：SSE、历史刷新、重连重放使用相同 citation 合同，兼容无 citations 的旧消息；网页引用继续保留。来源撤销后不得通过引用详情取回原文。
- B-R8：事件总 payload 遵守 16 KiB 上限；引用默认只含有限元数据，无私有摘录。不能为了增加引用静默截断既有可持久化正文或抬高后端限制。

## Acceptance Criteria

| ID | 可观察结果 |
|---|---|
| B-AC1 | 验证计划冻结的中文核心正例 Recall@5 = 100%；精确英文原有用例保持通过；独立扩展集单列指标，不能只改词典迎合单句 |
| B-AC2 | 唯一指代可使用同 session 锚点；歧义/无前文不添加猜测实体，跨 session/space 的资料不进入 query plan |
| B-AC3 | 两字后备、FTS 特殊字符和长查询均安全有界；权限过滤后的补足不漏掉预算内合法目标、不返回无权原文 |
| B-AC4 | 跨块事实、段落、长中英文与极短文本可重现定位；同版本索引重试 chunk ID 稳定，超预算项有明确原因 |
| B-AC5 | 真实后端 context → sidecar → 事件 → SSE/历史链对有效句柄输出一致 citations；未使用来源不被标成已引用 |
| B-AC6 | 编造/错 Run或attempt绑定/未 included/错 revision/已撤销或失权的引用不能获认证；同来源跨轮重新 included 合法；历史/SSE/详情不反显受限元数据或正文 |
| B-AC7 | Unicode 长文本、网页引用与最大引用集合下 UTF-8 序列化 payload 不超 16 KiB；元数据裁剪有稳定顺序和可解释提示，原正文合同不退化 |
| B-AC8 | 旧消息无 citations 正常渲染；认证/裁剪后丢响应重试、撤权后重放、同 attempt 重取 context、旧 token/attempt 竞争均有明确行为；前后端/sidecar schema 和相关检查通过 |

## Dependencies and scope

A 的来源/权限合同先稳定；C 的 Pi 恢复修复先完成，再改 worker/session 相关接线。D 消费 B 的 index_version、chunk 稳定性与状态约定，禁止各写一套重建规则。

不接主动检索工具、自动记忆提取、文档上传、embedding 或通用语义改写；不将 Steward 接到 RAG；不持久化 Pi 摘要。全请求预算和这些能力由 E 评估。本任务设计中的查询词表是版本化的通用领域规则，不接受为单个 fixture 硬编码答案。

## Validation entry

[统一验收与数据集](../09-13-agent-memory-rag-remediation/research/validation-plan.md)。所有期望值是未来验收门槛，既有 211 pass 只是修复前基线；真实 Provider 的生成质量和生产延迟尚未验证。
