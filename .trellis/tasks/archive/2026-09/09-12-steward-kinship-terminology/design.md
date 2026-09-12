# Design — Steward 个人亲属称谓计算与家族树投影闭环

## 责任边界

- `relationship_resolver`：只负责 confirmed facts、路径排序和 concept code。
- `terms`：只负责四级词典优先级与结构 fallback。
- `PersonalFamilyView`：按 viewer/account/space 物化授权后的 term、来源级别、版本和证据。
- `Steward`：负责事件影响解析、排队、租约和重算；不让模型覆盖确定性 term。
- frontend：只渲染后端 PFV 的 `edge.term`，详情展示结构路径作为证据而非替代主称谓。

## 关键数据流

```text
confirmed SourceFact
 → event impact resolver
 → space-scoped Steward job / PFV queue
 → load_graph + relationship_resolver
 → concept_code
 → terms.resolve_term_or_structural(account, space)
 → PersonalFamilyViewEdge.term/source_level
 → API schema/decoder
 → FamilyTreeView + RelationshipDetailPanel
```

## 诊断策略

先在隔离 fixture 中分别断言 resolver 输出、词典命中、rebuild 后持久化 edge、API 返回和自动 tick 结果。诊断只输出安全枚举/计数/ID，不输出姓名、原始文本、token 或模型 payload。若截图数据是旧投影，必须通过 input hash/term registry fingerprint 标 stale 并由 Steward 重建，而不是在前端临时修补。

## 兼容与回滚

保持既有 API envelope 和 PFV 授权过滤；只增强 term 计算和诊断字段（如已有 schema 可复用则不新增字段）。模型辅助关闭或失败时，确定性 PFV 仍必须成功。回滚只能关闭新称谓重算路径，不得恢复旧 graph fallback、宽松授权或前端推断。

## 依赖与发布

先复用 projection-consistency 已完成的失效/版本合同，再在 release-observability 的自动 tick E2E 中加入黄金称谓断言。真实 Provider 作为独立证据；若未运行，发布门禁保持 partial。
