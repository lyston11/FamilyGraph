# 实施计划：控制面与执行面故障域隔离

## Phase A：基线

- [ ] 测量 backend AnyIO worker、DB checkout、sidecar event loop/heap、kind slots 和 control latency。
- [ ] 注入 tool burst、长 provider stream、RAG maintenance、retry storm，确认当前共享故障域。

## Phase B：分池

- [x] 建立 control/execution/background limiter、worker budget、DB reserve 和拒绝码。
      证据：`config._validate_agent_execution_admission`：`AGENT_EXECUTION_GLOBAL_CAPACITY ≤ POOL_MAX_CONNECTIONS-1` 启动期强制（09-30 心跳延迟 30s 事故的直接防线）；`capacity.py` 三层 counter
- [x] 将 heartbeat/lease/context/settle/cancel/health 接入 control path，做 cancellation/recovery 回归。
      证据：control 路径不占执行名额（`_admit_upstream_request` 的 fence 走独立判据）；取消/撤权收敛回归见 `agent-runtime.md` §4
- [x] 维护 Assistant account 与 Steward space 的独立 slots 和容量。
      证据：`capacity.assistant_run_specs`(account) / `steward_job_specs`(space) 分离；`agent_kind` 维度独立

## Phase C：多实例与 sidecar

- [ ] 用 PostgreSQL lease/capacity 在两个实例上做 duplicate lease、crash recovery、重复 settle 测试。
      **部分完成**：2026-10-23 `cross_instance_capacity.py` 证明进程内 limiter 在多实例下会把配额
      翻倍（2 实例 → 观测 4 > 上限 2），持久化 counter 形态未越限；`multitenant_matrix.py`
      覆盖提交前/后断连与取消竞争。
      **仍未做**：两个**真实进程**（非同进程模拟）、真实 crash（kill -9）、真实重复 settle。
      本条**不勾选**：探针 docstring 自述「在同一进程内模拟两个独立 limiter 实例」，
      模拟不是 AC 要求的「两个实例」。
- [x] 评估并原型化 Assistant/Steward 分进程或独立 worker；定义部署、优雅停机和回滚顺序。
      证据：`docker-compose.yml` 的 `agent-assistant`/`agent-steward` 两个独立服务（`FG_AGENT_ROLE` 各一），`agent-combined` 藏在 profile 后；`agent/test/deployment-split.test.ts` 5 条断言

## Gate

- [ ] execution 满载时 control p95/p99 有界；任一 limiter mutation 必须被回归捕获；不得接触线上部署。


## 覆盖核查（2026-10-06）

本任务**不是从零开始**。AC-1/AC-2 已由 `10-03-agent-resource-isolation` 交付并有回归；
AC-3 部分覆盖（原型 L3）；**AC-4/AC-5 未实现**。逐条证据见
`research/evidence/current-coverage.md`。

### 剩余工作（不重复 AC-1/AC-2）

- [ ] **AC-4 跨实例配额**：当前 limiter 是进程内状态，两个实例的
      `AGENT_EXECUTION_GLOBAL_CAPACITY` 会**相加**，实际并发可达 2× 配置值。
      需要持久化协调（依赖 `10-03-postgres-migration` 的 capacity counter 落地），
      并补「两实例同时租赁不重复领取」回归 + 「移除单实例 limiter 后测试必须失败」的 mutation。
- [ ] **AC-3 补齐**：真实业务 schema 上的故障路径（现为原型）；真实进程 kill；
      membership revoke 期间执行。
- [ ] **AC-5 分进程/分池**：当前 `FG_AGENT_ROLE=both` 时 Assistant/Steward 共用进程，
      共享 event loop/HTTP client/heap/重试风暴。需设计与资源预算。

## 2026-10-23 复核

**AC-4（跨实例配额）已闭合**：`cross_instance_capacity.py` 在隔离库上同时证明
「进程内 limiter 形态确实越限（2 实例 → 观测 4 > 上限 2）」与「持久化 counter 形态
未越限」，反证齐备。原先「依赖 postgres-migration 的 capacity counter 落地」的
阻塞已解除（开发环境已在 `pg_all`，counter 已 bootstrap）。

**AC-5（分进程）已闭合**：compose 已拆为两个独立服务且各持单角色，combined 形态
需显式 `--profile combined` 才启用。

**仍然开放**：

| 项 | 为什么还开着 |
|---|---|
| 测量 AnyIO worker / DB checkout / event loop / heap 基线 | 需要真实多实例部署与负载 |
| 注入 tool burst、长流、RAG maintenance、retry storm | 同上 |
| 真实进程 kill + membership revoke 期间执行 | 需要真实部署对象 |
| execution 满载时 control p95/p99 有界 | **需要真实尾延迟**，不能用探针替代 |
| 任一 limiter mutation 必须被回归捕获 | 部分已做（跨实例反证），完整 mutation 矩阵未做 |
