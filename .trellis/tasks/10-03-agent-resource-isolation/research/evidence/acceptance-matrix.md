# 运行时资源隔离验收矩阵（2026-10-03）

本文件记录**实际验证过什么**、用什么证明、以及**没有覆盖什么**。不把「设计如此」
当作验收证据。

## AC-1 单租户突发不得占满另一租户/另一 kind/控制面

| 断言 | 证据 | 状态 |
|---|---|---|
| 无竞争时单租户可用满全局名额（不误伤正常扇出） | `test_a_lone_tenant_may_use_the_whole_global_capacity` | ✅ |
| 有等待者时突发租户被压到保留量，等待者优先取得释放的名额 | `test_a_waiting_tenant_reserves_capacity_from_a_bursting_one` | ✅ |
| 端点上真的接线（名额耗尽 → 503 `AGENT_EXECUTION_BUSY`） | `test_the_endpoint_actually_consults_the_execution_limiter`（变异验证：删除取名额调用即失败） | ✅ |
| 两个空间互不饿死 | `test_two_tenants_do_not_starve_each_other_through_the_real_endpoint` | ✅ |
| 同组内先到者优先（aging，后来者不插队） | `test_earliest_waiter_wins_within_a_tenant_group`（变异：改 LIFO 即失败） | ✅ |

**未覆盖**：三租户以上同时突发的公平性；上游**并发流数**不受本层限制（已知边界，
见 `execution-admission-design.md`）。

## AC-2 控制面在过载下仍有界

| 断言 | 证据 | 状态 |
|---|---|---|
| 工具突发（40 并发，单租户）期间心跳在 1s 预算内被服务 | `test_agent_tool_admission.py::test_pool_exhaustion_still_serves_heartbeat_and_converges`（09-30 建立，本次改动后仍通过） | ✅ |
| 等名额不占工作线程（等待在事件循环上） | 同上（`borrowed <= 名额`） | ✅ |
| 工具批次最终全部完成（不是靠拒绝全部工具通过） | 同上 | ✅ |
| 全局名额小于连接池上限（为控制面留连接） | `test_shipped_defaults_leave_room_for_the_control_plane` + `config._validate_agent_execution_admission` | ✅ |
| **多租户**同时突发时心跳仍在 1s 预算内被服务 | `test_control_plane_keeps_its_budget_under_multi_tenant_burst`（3 空间 × 6 并发工具） | ✅ |
| 名额达到连接池上限时进程**拒绝启动** | 变异验证：`AGENT_EXECUTION_GLOBAL_CAPACITY=15` → `RuntimeError`（比测试失败更强的保证） | ✅ |

**未覆盖**：lease/settle/cancel 端点未单独做预算内断言（它们与心跳同属控制面、同一
保护机制）；跨实例控制面余量（进程内 limiter）。

## AC-3 Assistant/Steward 执行资源隔离

| 断言 | 证据 | 状态 |
|---|---|---|
| 租户键按 kind 分主体（Assistant=account，Steward=space） | `execution_tenant_key` + 上述端点用例（steward 空间） | ✅ |
| 两个平面（tool / provider）额度独立 | `_EXECUTION_PLANES` + 端点分别取名额 | ✅ |
| provider 名额不跨流持有 | `test_stream_does_not_pin_a_pool_connection_between_chunks` 继续通过 | ✅ |

**未覆盖**：sidecar 分进程；backend 控制面独立 worker 池（当前靠「名额 < 池上限」
提供余量，而非独立池）。

## AC-4 取消/撤权/租约恢复/egress/fence/settle 回归

| 断言 | 证据 | 状态 |
|---|---|---|
| 既有合同未回归 | backend 全量 **1970 passed / 3 skipped** | ✅ |
| 取消不泄漏名额 | `test_cancelling_a_waiter_releases_nothing_and_leaves_no_leak` | ✅ |

## AC-5 retry 预算（Phase C 第一项）

| 断言 | 证据 | 状态 |
|---|---|---|
| 两层重试不再相乘 | `agent/test/retry-budget.test.ts`（真实 SDK，变异验证 4 个用例失败） | ✅ |
| 生产基线 | 失败 run p50=p90=p99=**24** 次出站；成功 run p50=3 | ✅ |
| **未做**：upstream/profile circuit key | — | ❌ |

## 未完成的 Phase

- **Phase A 基线**：未系统采集按 account/space/kind 的 queue wait / run duration 基线
  （已有 retry 与出站次数的生产统计，见 `retry-multiplication-baseline.md`）。
- **Phase B 剩余**：公平队列已含保留量 + aging，但没有权重（优先级分级）。
- **Phase D**：sidecar 分进程、backend 控制面独立 worker 池未做。
- **Phase E**：未做「同用户跨空间 / RAG background 并发 / 撤权 / 重启」的完整矩阵。

因此本子任务**未达到父任务 AC-2/AC-3 的完整形态**：隔离已建立并可证明，但控制面
独立池与跨实例配额仍缺。
