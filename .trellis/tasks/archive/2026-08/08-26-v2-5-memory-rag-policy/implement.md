# V2.5 Memory、RAG 与 Policy Guard 实施计划

- [x] 定义 MemoryCandidate/Memory/RAGDocument/Chunk/ContextBuild/BehaviorProjection schema 与迁移。
- [x] 实现 MemoryCandidateExtractor、确认/选择 scope/撤销/删除 UI 与 API。
- [x] 建立 RAG ingestion 白名单、FTS5 trigram、可选 embedding adapter 和 metadata filter。
- [x] 实现 RAGGateway 的 pre/post filter、citation、invalidation/tombstone/rebuild。
- [x] 实现 BehaviorProjection projector，消费 V2.4 DomainEvent，不采集泛行为。
- [x] 实现 ContextBuilder 的 scope/sensitivity/trust/rank/budget/provider 决策与可追溯记录。
- [x] 编写 `familygraph-policy-guard` 六个 hook；把 DB 预取放在 Run context endpoint，不放 context hook。
- [x] 扩展 Assistant 与 Steward tools/context；增加 Memory/RAG 管理和引用 UI。
- [x] 完成 prompt injection、PII/secret、跨 scope、删除传播、本地强制和性能测试。

## 验证

```bash
cd backend && pytest
cd backend && mypy app
cd agent && npm run type-check && npm run lint && npm test && npm run build
cd frontend && npm run type-check && npm run lint && npm test && npm run build
```

专项：建立两个相同文本但不同 scope 的文档，验证 FTS/embedding 都只命中授权项；撤销后立即查询为零；注入文档中的伪系统指令不能改变 tool allowlist。

## 回滚

Memory、FTS、embedding、BehaviorProjection、Guard 各有 feature flag；关闭 RAG 时 Assistant 回退结构化工具，不能自动把 Session 全文塞入 Context 补偿。
