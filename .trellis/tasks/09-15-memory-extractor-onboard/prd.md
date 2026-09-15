# 接入记忆候选提取器

> 父任务：09-15-agent-audit-remediation。优先级 P1。

## 背景

`backend/app/services/memory_rag.py` 的 `MemoryCandidateExtractor._default_detector` 明确返回空列表——"Product-specific extraction is an explicit opt-in detector"。结果：普通聊天永不产生记忆候选，远端 `memory_candidates`/`memories`/`rag_documents` 全部 0 行，整个 RAG 检索链路（规划→FTS→scope→投影→引用回链）空转。前端只有手动建记忆的 UI。

## Requirements

- R1：实现一个确定性规则式提取器（detector），从会话文本中识别值得长期记住的家庭事实（生日、忌口/过敏、偏好、职业/学校、住址/城市、纪念日等），产出 `MemoryCandidateInput`（source_quote=原句、summary=概括、suggested_scope、purpose、sensitivity）。不调用模型，不扩大授权——`propose_candidate` 的既有校验链不变。
- R2：接入点：assistant run settle 成功后（或消息落库时）异步/同事务提取，候选只落 review card（`status=pending`），等待用户确认，绝不直接索引。
- R3：频控与上限：单条消息候选数上限（如 3）；同 (session, message) 幂等，不重复产卡。
- R4：sensitivity 保守默认 normal；涉健康等敏感词归 sensitive；不产 high。
- R5：测试：规则命中/不命中、上限、幂等、scope 校验路径单测。
- R6：feature flag 沿用 `MEMORY_ENABLED`（平台级）与既有 env，不新增开关，除非设计评审认为必要。

## Acceptance Criteria

1. 单测覆盖提取器各规则类别与边界。
2. 远端部署后，一次包含"我妈妈生日是 3 月 5 号"的助手会话在 `memory_candidates` 产生 pending 候选。
3. 用户确认后 `memories` 出现记录、`rag_documents`/`rag_chunks` 建立索引，助手后续提问能命中并带引用。

## 回滚

提取器入口退回默认空 detector（保留实现，靠 flag 关闭或直接 revert 接入点）。
