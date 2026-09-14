# 证据版本与行为投影研究索引

## 已确认

- MR-26 只重放三类键，却曾清空整个作用域；`cff6f8e` 已修复并通过定向测试、独立检查。
- 渐进重算已集成，证据版本须通过现有发布后交付消费。
- 新迁移必须在任何 DDL 前执行祖先证据拒绝预检；当前相对降级图有 merge 歧义。
- assist 以入锁前的旧时间检查租约会放行真实过期写回，已用独立 SQLite writer 复现。

## 当前决定

- 共同父母证书仅内部版本化；保留驳回，不新增关系待办。
- 重用真实 core、delivery 与 fake transport；捕获版本 ID 与输入 fence 分别验证。
- 写回取得 writer 后重采样时间，调用方的未来时间只能收紧租约检查。

## 已排除与未完成

- 不照搬旧 harness 的新增 Suggestion 预期；不把来源失效误称为 SourceFact TTL。
- 不为当前不可执行的相对多步迁移增加计划缓存。
- MR-23 代码与迁移已通过 188 项定向回归；完整后端 1594 passed、导入整理后 43 passed、静态检查及独立最终复核全部通过。

## 入口

- [集成摘要](mr23-integration-summary.md)：实施默认上下文。
- [集成调查证据](evidence/mr23-integration-investigation.md)：夹具、源位置与迁移图核验。
- [租约写回反例](evidence/lease-writeback-investigation.md)：真实写锁等待导致的过期应用与修复选择。
- [验证记录](validation.md)：adoption marker 前建立的原位审计记录，后续结果继续追加。
- [最终验收摘要](final-validation-summary.md)：SP-AC1～6、最终门禁和保留的限制。
