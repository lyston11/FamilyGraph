# 接入记忆候选提取器

> 父任务：09-15-agent-audit-remediation。优先级 P1。

## 背景

`backend/app/services/memory_rag.py` 的 `MemoryCandidateExtractor._default_detector` 明确返回空列表——"Product-specific extraction is an explicit opt-in detector"。结果：普通聊天永不产生记忆候选，远端 `memory_candidates`/`memories`/`rag_documents` 全部 0 行，整个 RAG 检索链路空转。前端只有手动建记忆的 UI。

## 已核实的现有机制（实现必须复用，不得绕开）

- `propose_candidate`（memory_rag.py）已是完整候选落库入口：来源归一化（`memory_sources.normalize_source`）、`agent_message` 来源校验（本人会话、原始 user 全文、active 成员）、fingerprint、幂等键、领域事件。提取器只需产出 `MemoryCandidateInput` 列表。
- `MemoryCandidateExtractor.extract(db, author_account_id, conversation_text, source_message_id)` 已支持注入 detector；`MemoryCandidateInput(source_quote, summary, suggested_scope, purpose, sensitivity, source_message_id, source_document_ref)`。
- `memory_sources.resolve_source` 对 `agent_message` 来源要求 raw_quote 与消息原文**全等**——所以 `source_quote` 必须是完整 user 消息原文（不是片段），summary 承载结构化概括。
- 幂等：`idempotency_key`（8..128 字符）+ request fingerprint。`propose_candidate` 的 fingerprint 不含 idempotency_key，但含 source/summary/scope/purpose/sensitivity/extractor_version——同输入重放安全。
- feature flag：`platform_features.is_memory_enabled(db)` 在 propose 路径强制（关闭时 503，异常会破坏 settle——见设计中的容错要求）。

## Requirements

- R1：实现确定性规则式 detector（新模块 `backend/app/services/memory_extractor.py`，版本 `extractor-v1`）：
  - 识别类别（首发集合，保守）：生日/纪念日（X月X日、农历生日）、饮食忌口与过敏、职业/学校、居住城市、显著偏好（喜欢/讨厌 X）。
  - 每条命中产出 `MemoryCandidateInput`：`source_quote`=完整 user 消息原文（满足 agent_message 全等校验）、`summary`=「<类别>：<一句概括>」、`suggested_scope="private"`、`purpose`=固定类别用途文案（≤120 字符）、`sensitivity`：健康/过敏类 `sensitive`，其余 `normal`；不产 `high`。
  - 纯函数、无模型调用、无 DB 读取；相同输入逐字节相同输出。
  - 单消息候选上限 3，超出丢弃并在返回值中计数（便于审计/日志，不落事件）。
- R2：接入点：`agent_queue._settle` 成功路径（status 有效后、事务内）调用提取——只对 `run.message_id` 对应的**本次 user 消息**提取，不回扫历史。异常（含 MEMORY_DISABLED 503）必须吞掉并 log warning，**不得让 settle 失败**。
- R3：幂等：每次提取的候选使用 `idempotency_key=f"extract:{message_id}:{category}:{index}"`——同消息重放（重试/reaper 重驱）不重复产卡。
- R4：`MemoryCandidateExtractor` 默认 detector 从空实现切换为新规则式提取器；保留注入 seam。`extractor_version="memory-extractor-v1"`。
- R5：测试（backend/tests/test_memory_extractor.py + settle 集成测试）：
  - 各类别命中/不命中/边界（无日期格式的"生日"一词不命中）；
  - 上限 3 与丢弃计数；
  - settle 成功后候选落库、settle 失败后无候选、MEMORY_ENABLED 关闭时 settle 仍成功；
  - 同消息重复 settle（幂等键）不重复；
  - source_quote 与消息原文全等（agent_message 校验通过）。
- R6：无 schema 变更、无迁移。MEMORY_ENABLED flag 语义不变（平台级 kill-switch 依旧阻断）。

## Acceptance Criteria

1. `cd backend && ruff check . && pytest tests/test_memory_extractor.py` 全绿；agent 相关既有测试不回归。
2. 隔离库中 settle 一个含「我妈妈生日是 3 月 5 号」的 run → `memory_candidates` 出现 pending 候选，summary 含生日信息，sensitivity=normal，scope 建议 private。
3. 含过敏类内容 → sensitivity=sensitive。
4. 远端部署后（后续子任务/部署步骤执行），真实会话产生候选。

## 设计取舍说明

- 接入点选 settle 而非消息创建：创建时用户还没说完话，且 settle 是终态唯一收口（含 reaper 重驱路径幂等）。
- source_quote 必须是整条原文：`resolve_source` 对 agent_message 的全等校验决定了不能只存片段；候选 UI 已有展示长原文的合同（raw_quote ≤12,000 字符）。
- 不做模型辅助提取：与 intake_extractor 同一红线（纯词法、确定性），模型辅助留给后续任务。

## 回滚

`MemoryCandidateExtractor` 构造处回退为默认空 detector（或 revert 接入点 commit）；候选数据无需清理（pending 候选可 dismiss）。
