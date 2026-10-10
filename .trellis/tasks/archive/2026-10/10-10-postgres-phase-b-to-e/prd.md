# PostgreSQL Phase B 到 E：lease/CAS、数据导入、开发灰度与收尾

## Goal

完成 `10-03-postgres-migration` 的 Phase B/C/D/E，使 PostgreSQL 从「schema 可建」
变成「可切 writer」。前置条件已满足（Phase A 尾项：69 个触发器 PG 等价物已实现）。

## Background

- Phase A 已完成：schema 87/87 可建、行锁语义已验证、锁序死锁已记录、
  触发器 PG 等价物已实现。
- `10-05-migration-proof-gates` 已归档，Gate 0-5 全部完成（L2/原型 L3）。
- 剩余工作不是「研究」，是「执行」：把已验证的语义应用到真实业务 schema 上。

## Scope

- Phase B：lease/CAS 语义验证（counter 归还、settle/cancel、fence、token scope、
  egress、两阶段写回）。
- Phase C：真实 schema 导入与对账（sequence 修复、RAG revision/citation、
  egress 一次一审计）。
- Phase D：开发灰度（control-plane → agent execution → domain read/write，
  每阶段对账）。
- Phase E：收尾（迁移往返、downgrade/refusal、backend/agent 回归、多租户压力矩阵）。

## Non-goals

- 不改授权模型、不加新的可见性来源。
- 不做线上切换（线上只生成手动发布和回滚步骤）。
- 不做 PG-7（多租户压测）——那是 `10-04-multitenant-load-acceptance` 的职责。

## Acceptance Criteria

| ID | 可观察结果 |
|---|---|
| AC-1 | 四条 counter 归还路径（settle/cancel/租约过期/栅栏退休）各自归还且不重复归还。 |
| AC-2 | Assistant/Steward fence、token scope、egress one-audit、两阶段写回在真实 schema 上验证。 |
| AC-3 | 真实 SQLite 快照导入后，行数、摘要、状态、授权 scope、attempt/run、RAG revision/citation、egress 审计全部对账通过。 |
| AC-4 | 开发灰度每阶段有计数、hash、scope、审计对账；SQLite 过渡模式上限明确。 |
| AC-5 | 迁移往返、downgrade/refusal、backend/agent 回归、多租户压力矩阵全部通过。 |
