# 实施记录 —— 2026-10-10

## Phase B：lease/CAS 语义验证 —— SQLite 侧已完成，PG 侧待真实连接

- [x] counter 归还路径（settle / cancel / 租约过期恢复 / 栅栏退休）：
  `test_capacity_release_gate.py` 5 项全部通过，含「恰好一次」的 mutation 验证。
- [x] Assistant/Steward fence、token scope、egress one-audit、两阶段写回：
  各测试套件全部通过（`test_agent_tool_admission`、`test_policy_guard`、
  `test_admin_agent_latency`、`test_admin_audit`、`test_steward_pi_carrier_terminology`）。
- [x] `capacity.py` 的 release() 补了对称性说明：acquire 是「全部或不占」，
  release 因此也必须是「全部或不还」——否则出现半释放状态。

**PG 侧尚未验证**（需要真实 PostgreSQL 连接，当前本地 Docker 不可用）：
- counter 行锁替代 `BEGIN IMMEDIATE` 后的端到端语义；
- 四条归还路径在 PG 上的并发故障注入。

## Phase C：数据导入与对账 —— SQLite 侧基线已建立

- [x] 真实 schema 对账脚本 `scripts/migration-proof/reconcile_real_schema.py`：
  七个维度（行数、摘要、状态分布、授权 scope、revision 镜像、egress 一次一审计、
  attempt/run 孤儿），refusal 语义与 Gate 5 一致。
- [x] sequence 修复必要性已由 Gate 5 的负向用例证明（不修复必主键冲突）。

**PG 导入尚未验证**（需要真实 PG 连接）。

## Phase D：开发灰度 —— 未开始（依赖 PG 连接）

- [ ] control-plane → agent execution → domain read/write，每阶段对账。

## Phase E：收尾 —— 部分完成

- [x] 迁移往返：`test_rag_lifecycle_migrations.py` 13 项全部通过。
- [x] backend/agent 回归：2329 passed。
- [x] 多租户压力矩阵：未做（需要真实 PG 连接与并发环境）。
