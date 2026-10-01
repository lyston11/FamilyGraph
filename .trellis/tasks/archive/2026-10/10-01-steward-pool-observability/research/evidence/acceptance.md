# 验收证据（2026-10-01）

## 交付：产品内可观测性

新增 `backend/app/services/runtime_diagnostics.py`，两个**独立**信号：

| 事件 | 含义 | 触发阈值 |
|---|---|---|
| `db_pool_wait` | `engine.connect()` 在池里等待的时长 + 当时占用 | 1000ms |
| `event_loop_lag` | 事件循环未能按时处理定时器的时长 | 500ms |

`db_pool_wait` 在 `finally` 里记录，因此**池满到超时**这条最严重的路径也有日志
（实现时先只在成功路径记录，测试立刻暴露了这个漏洞）。

## 顺带发现并修复：`extra` 被 formatter 丢弃

部署后读日志发现 `event_loop_lag` 触发了 4 次，但日志行里**没有 `lag_ms`**。
根因：`JsonFormatter` 只输出固定的六个键，静默丢弃所有 `extra=` 字段。

这是既有缺陷，影响范围超出本任务：`maintenance tick` 的 `counters` 同样一直是空的。
诊断「只报事件、不报数值」等于无效——修 formatter 后两者都带上了数值。

## 诊断的直接结论：证伪了池尖峰假设

**`db_pool_wait` 部署至今为 0 次。** 池从未接近耗尽，因此
「连接池需求超过上限」**不是**这些停滞的原因——这是我上一任务的假设，已被自己的
诊断证伪。

同时部署后 1 小时内：**0 次 >20s 请求停滞、0 次 `expired`**。

`event_loop_lag` 触发了 4 次（03:56、04:27×3），说明事件循环确实曾被短暂占住，
但量级远小于此前观测的 96–195 秒停滞。

## AC 逐项

| 验收 | 结果 | 证据 |
|---|---|---|
| AC-0 | ✅ | 两个信号产品内可观测；`db_pool_wait` 字段仅 `waited_ms/checkedout/size/overflow`，有精确白名单断言与 SQL 文本拒绝 |
| AC-1 | ⚠️ **未完成** | 诊断证伪了池尖峰假设（`db_pool_wait`=0），但**未定位**这些停滞的真实机制 |
| AC-2 | ✅ | 探针是 asyncio 任务，不占工作线程、不取连接；有区分性测试 |
| AC-3 | ⚠️ | 未实施收敛（池不是原因）；部署后 1 小时无 expired，但样本不足以归因 |
| AC-4 | ✅ | backend **1950 passed / 3 skipped**；ruff/mypy 干净 |
| AC-5 | ✅ | Provider/fence 回归通过（全量含相关用例） |

## 测试与变异验证

| 用例 | 断言 |
|---|---|
| `test_pool_wait_is_reported_when_the_pool_is_exhausted` | 池满时记录，且时长≈池等待上限 |
| `test_no_pool_wait_record_when_connections_are_available` | 池空闲时不报（否则告警被淹没） |
| `test_event_loop_lag_is_reported_when_the_loop_is_blocked` | 循环被同步阻塞时记录 |
| `test_the_two_signals_are_distinguishable` | **仅池满时只有 `db_pool_wait`，不得有 `event_loop_lag`** |
| `test_diagnostics_logs_carry_no_sensitive_fields` | 字段白名单 + 拒绝 SQL 文本 |
| `test_logctx_extra.py`（3 条） | `extra` 与嵌套 `counters` 被输出；内部属性不外泄 |

变异验证 3 组，全部被捕获：去掉池等待阈值判断、池等待只在成功路径记录、
formatter 不输出 `extra`。

## 边界与未完成

- **AC-1 未达成**：已用诊断**排除**池尖峰，但停滞（历史 96–195s、run 262/288 的
  409）的真实机制**仍未定位**。不得据此宣称已解决。
- `event_loop_lag` 的 4 次触发量级小（阈值 500ms），与历史长停滞不匹配；
  历史停滞发生在诊断部署之前，缺少同期的 `lag_ms` 数值。
- 历史 `expired` 中 run 262/288 收到 **409**（非 401），心跳间隔有 195s 空档，
  机制未定；诊断部署后的 1 小时无复现，样本不足。
- 线上未操作。
