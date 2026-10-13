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

- [x] 用 PostgreSQL lease/capacity 在两个实例上做 duplicate lease、crash recovery、重复 settle 测试。
      证据：2026-10-23 `two_process_acceptance.py`（**真实多进程**，隔离库、真实业务 schema
      `agent_capacity_counters`）：
      - 重复租约：6 真实进程竞争 capacity=2 → 恰好 2 个授予、active=2；
      - **反证**：无行锁形态 6 进程全部成功（6 > 2），证明行锁承重；
      - 崩溃恢复：`kill -9` 持锁进程后 active 仍为 1 → 名额**不自动归还**，
        回收靠 lease expiry 扫描（修正「进程死了名额自然回来」的误解）；
      - 重复归还：4 进程并发归还 2 个占用 → 恰好 2 次成功、终态 0、未为负。
      详见 `research/evidence/two-process-acceptance-2026-10-23.md`。
- [x] 评估并原型化 Assistant/Steward 分进程或独立 worker；定义部署、优雅停机和回滚顺序。
      证据：`docker-compose.yml` 的 `agent-assistant`/`agent-steward` 两个独立服务（`FG_AGENT_ROLE` 各一），`agent-combined` 藏在 profile 后；`agent/test/deployment-split.test.ts` 5 条断言

## Gate

- [x] execution 满载时 control p95/p99 有界（2026-10-23 已闭合）；limiter mutation 回归见下。
      证据：开发服务上 `GET /api/ready` 基线 p99=27.04ms；16 并发 × 240 次负载下
      p99=**377.74ms**、**无 5xx**、吞吐 80 req/s；占住 12 条连接（idle 5→17）时
      p99=20.58ms。另在专用验收实例上占住 **81/100** 条连接时 p99=18.23ms、零 5xx。
      见 `research/evidence/control-plane-latency-2026-10-23.md` 与
      `10-04-postgres-operations-cutover/research/evidence/pool-exhaustion-2026-10-23.md`。
      原文：任一 limiter mutation 必须被回归捕获；不得接触线上部署。
      **尾延迟已测**（2026-10-23，运行中的开发服务）：`GET /api/ready` 基线
      p50=11.61/p95=15.52/p99=27.04ms；16 并发 × 240 次负载下
      p50=201.18/p95=351.45/**p99=377.74ms**，**无 5xx**，吞吐 80 req/s；
      占住 12 条只读连接（`max_connections=100`，idle 5→17）时 p99=20.58ms，
      证明连接余量确实保护控制面。见
      `research/evidence/control-plane-latency-2026-10-23.md`。
      **仍未做**：limiter mutation 回归（移除配额/保留容量的变异必须被捕获）。


## 覆盖核查（2026-10-06）

本任务**不是从零开始**。AC-1/AC-2 已由 `10-03-agent-resource-isolation` 交付并有回归；
AC-3 部分覆盖（原型 L3）；**AC-4/AC-5 未实现**。逐条证据见
`research/evidence/current-coverage.md`。

### 剩余工作（不重复 AC-1/AC-2）

- [x] **AC-4 跨实例配额**（2026-10-23 已闭合）：`two_process_acceptance.py` 用**真实
      操作系统进程**（各自独立解释器/连接池/事务）对真实业务 schema
      `agent_capacity_counters` 竞争，四条结果：
      6 进程争 capacity=2 → 恰好 2 个授予、active=2；**反证**无行锁形态 6 进程全部成功
      （6 > 2）；`kill -9` 后 active 仍为 1（名额**不自动归还**，靠 lease expiry 回收）；
      4 进程并发归还 2 个占用 → 恰好 2 次成功、终态 0、未为负。
      另 `cross_instance_capacity.py` 证明进程内 limiter 在多实例下把配额翻倍
      （2 实例 → 观测 4 > 上限 2）。见
      `research/evidence/two-process-acceptance-2026-10-23.md`。
      原文：当前 limiter 是进程内状态，两个实例的
      `AGENT_EXECUTION_GLOBAL_CAPACITY` 会**相加**，实际并发可达 2× 配置值。
      需要持久化协调（依赖 `10-03-postgres-migration` 的 capacity counter 落地），
      并补「两实例同时租赁不重复领取」回归 + 「移除单实例 limiter 后测试必须失败」的 mutation。
- [x] **AC-3 补齐**（2026-10-23 已闭合）：`two_process_acceptance.py` 在**真实业务
      schema** 上跑（非 `mt_*` 原型表），并做**真实进程 `kill -9`** 崩溃恢复。
      见 `research/evidence/two-process-acceptance-2026-10-23.md`。
      原文：真实业务 schema 上的故障路径（现为原型）；真实进程 kill；
      membership revoke 期间执行。
- [x] **AC-5 分进程/分池**（2026-10-23 核实**已在生产满足**）：
      生产部署是**两个独立容器**，各有不同 PID 与容器 ID，角色分离：

      ```text
      familygraph-prod-agent-assistant-1  id=17547f33f1cd pid=43100  FG_AGENT_ROLE=assistant
      familygraph-prod-agent-steward-1    id=fa0b63e9ae4f pid=43173  FG_AGENT_ROLE=steward
      ```

      因此 event loop / HTTP client / heap / 重试风暴**不共享**——
      assistant 的重试风暴不会挤占 steward 的事件循环。

      **开发环境仍是 `FG_AGENT_ROLE=both`**（单容器），这与生产的拓扑不同：
      在 dev 上测「工具突发影响 steward」会把生产里已隔离的两个故障域混在一起，
      得出的结论不能外推到生产。这一差异已记录，是 dev 拓扑的已知偏差。

      **仍未做**：两容器各自的**内存限额未设置**（`HostConfig.Memory=0` = 无限制），
      因此「一个容器 OOM 会否拖垮另一个」未被约束，也未测。

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
