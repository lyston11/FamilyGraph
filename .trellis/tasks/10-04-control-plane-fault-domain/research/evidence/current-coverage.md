# Control-plane 任务：当前覆盖核查（2026-10-06）

## 结论

本任务的 **AC-1 与 AC-2 已有实现与回归**（在 `10-03-agent-resource-isolation` 交付），
**AC-3 部分覆盖**，**AC-4 未实现**（跨实例）。因此本任务不是从零开始，而是补齐
AC-3/AC-4 并完成分进程决策。

## 逐条核查

### AC-1：execution pool 占满时 control-plane 仍有预算 —— **已覆盖**

回归：`backend/tests/test_agent_execution_admission.py`

- `test_shipped_defaults_leave_room_for_the_control_plane`：出厂默认值必须为控制面留余量；
- `test_control_plane_keeps_its_budget_under_multi_tenant_burst`：3 个空间同时突发时，
  心跳仍须在预算内被服务，且**参与突发的工具请求最终全部完成**（拒绝一切不算隔离成功），
  同时断言执行面占用的工作线程不超过全局名额。

机制：`config._validate_agent_execution_admission` 强制
`AGENT_EXECUTION_GLOBAL_CAPACITY ≤ POOL_MAX_CONNECTIONS - 1`。

### AC-2：Assistant 突发不消耗 Steward/control 保留容量 —— **已覆盖**

- 平面分离：`agent_tool` 与 `agent_provider` 各持独立 limiter（时间尺度不同）；
- 资源主体分离：Assistant 用 `account_id`、Steward 用 `space_id`；
- 保留策略：无竞争时可用满全局，**有等待者时**本租户新增被压到
  `per_tenant_capacity`，为等待者保留 `global - per_tenant_capacity`；
- 已执行调用不被抢占。

回归覆盖：`test_a_lone_tenant_may_use_the_whole_global_capacity`、
`test_a_waiting_tenant_reserves_capacity_from_a_bursting_one`、
`test_two_tenants_do_not_starve_each_other_through_the_real_endpoint`。

**sidecar 侧 kind 槽位隔离**：`agent/test/worker-slots.test.ts`（槽位按 `run_id` 定位、
按 kind 独立预算、取消信号不互相覆盖）。

### AC-3：重启/断连/租约过期/重复 settle 后状态唯一 —— **部分覆盖**

已有：
- `10-05` 的 `pg_fault_injection.py`：提交前/后断连、重复 settle、cancel vs settle、
  崩溃租约回收、`SERIALIZABLE` 冲突（**原型 L3**）；
- `agent_queue._settle` 的锁内终态 CAS：`tests/test_agent_queue_settle_race.py`
  （陈旧对象 + 结构断言，变异验证有效）。

**未覆盖**：真实业务 schema 上的这些路径；真实进程 kill；membership revoke 期间执行。

### AC-4：两实例同时租赁不重复 + 单实例 limiter 的 mutation —— **未实现**

**这是本任务的核心缺口**：

- `agent_admission.py` 的 limiter 是**进程内**状态，只保护当前实例，
  不是跨实例全局配额（代码注释已声明）；
- 因此两个 backend 实例各自的 `AGENT_EXECUTION_GLOBAL_CAPACITY` 会**相加**，
  实际并发可达到 2× 配置值；
- 跨实例配额需要持久化协调（PostgreSQL counter / Redis），归父任务后续阶段。

**回归缺失**：没有「两实例同时租赁不重复领取」的测试，也没有「移除单实例 limiter
后跨实例测试必须失败」的 mutation。

### AC-5：分进程/分池 —— **已实现**（2026-10-06）

`docker-compose.yml` 现在默认启动**两个独立服务**：

| 服务 | `FG_AGENT_ROLE` | 资源 |
|---|---|---|
| `agent-assistant` | `assistant` | 自己的 event loop / HTTP client / heap |
| `agent-steward` | `steward` | 同上，独立 |
| `agent-combined` | `both` | **profile 门禁**，默认不启动 |

分进程消除的共享：Node event loop、HTTP client 与连接池、heap 与 GC 停顿、
重试风暴。

契约由 `agent/test/deployment-split.test.ts` 守护（5 个用例），因为回归是**静默**的
——改回 `both` 运行时不会报错，只是长流互相拖慢。三组变异均被捕获：默认改回
`both`、去掉 combined 的 profile、锚点漏掉共享变量。

**未覆盖**：真实双进程部署的端到端验证（需实际 `docker compose up` 并观察
两类互不干扰），属部署验收。

## 因此本任务的实际工作

1. **AC-4**：跨实例配额（需要 PostgreSQL counter，依赖 `10-03-postgres-migration` 的
   capacity counter 落地）+ 两实例租赁回归 + mutation；
2. **AC-3 补齐**：真实 schema 上的故障路径；
3. **AC-5**：分进程/分池设计与资源预算。

**不重复实现** AC-1/AC-2——它们已交付且有回归；本任务只在其上补跨实例维度。
