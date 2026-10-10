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

## Phase D：开发灰度 —— 阶段机已验证，真实流量灰度未做

- [x] 阶段机在隔离 PG 上验证：`sqlite → shadow → pg_control → pg_all`
      （epoch 0→3，持久化确认，`pg_cutover_stage.py`）。
- [x] writer epoch 守卫、stage 枚举、逐级推进（不接受跳级）。
- [ ] 真实前端/agent 流量切换：未做（需要真实流量，不是探针能覆盖的）。

## Phase E：收尾 —— 已通过（真实 PG）

- [x] 迁移往返：`test_rag_lifecycle_migrations.py` 13 项全部通过。
- [x] backend/agent 回归：2329 passed。
- [x] **真实 PostgreSQL 上 18 个探针 PASS / 0 FAIL**，含 69 个触发器的等价物
      双向验证（13/13 负向+正向+反证）、配额越限反证、六类故障注入、
      四锁锁序承重、PGroonga/pgvector 授权过滤反证。
- [x] **生产库直接对账通过**（无差异）：取代语义、revision 镜像、外键完整性、
      egress 发送确定性。
- [ ] 真实 p95/p99 延迟、真实 DERP/provider 故障、真实 sidecar 多实例：未测。

## 在真实数据库上发现并修正的 4 个真实缺陷

见 `research/evidence/phase-b-to-e-real-pg.md`。四个都只在真实 DB 上执行才暴露：
pgvector 探针缺必填参数（证明已失效）、对账脚本把合法 NULL FK 报成孤儿、
egress 断言与合同不符、scope 分布三元组构造错误。
