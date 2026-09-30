# 验收证据（2026-09-30）

## AC-0 定位（已完成）

根因：连接池上限 15 **小于** AnyIO 工作线程上限 40。池被占满后，等待连接的请求
在**工作线程内**阻塞最长 `pool_timeout`=30s；40 个这样的请求耗尽全部工作线程，
心跳/lease/health 一起拿不到执行机会。

决定性受控复现（`research/evidence/r0-root-cause.md`）：

```
池满后并发 40 请求：min=30.0s p50=30.0s max=30.0s  outcomes=['TimeoutError']
线程占用：t=2s..28s 持续 40/40
```

## 修复

`execute_tool` 改为 `async def`：先在**事件循环**上等执行名额（等待不占工作线程、
不占连接），拿到名额后进工作线程，用**自己的 Session** 跑同步体（授权 → fence →
准入 CAS → 幂等 → 审计 → commit）。名额由 done-callback 释放，取消不得提前放行。
心跳/lease/settle/context/health 不取名额。

`app/db.py` 显式声明池参数（此前依赖 SQLAlchemy 默认值），并导出
`POOL_MAX_CONNECTIONS`；`config.ensure_ready` 强制名额 ≤ `POOL_MAX_CONNECTIONS - 1`。

## AC 逐项

| 验收 | 结果 | 证据 |
|---|---|---|
| AC-0 | ✅ | 池满 → 请求耗时精确等于 pool_timeout → 线程 40/40 持续占用 |
| AC-1 | ✅ | 修复前 3 个窗口 **0 请求**（真静默）；修复后同长度窗口 **1146 个 heartbeat/lease 被服务**；部署后 `>10s 静默 0 次`（覆盖 1841 秒） |
| AC-2 | ✅ | `test_agent_tool_admission.py`：工具突发下心跳在 1s 预算内成功、工作线程占用 ≤ 名额、批次全部完成 |
| AC-3 | ✅ | 开发环境部署 `30906a4`；heartbeat 稳定推进；`tool_admission_wait` 计数 0（未触及上限） |
| AC-4 | ✅ | fence/幂等/Provider 合同回归 103 passed；取消不泄漏名额用例 |
| AC-5 | ✅ | backend **1939 passed / 3 skipped**；ruff/mypy 在我的文件上干净 |
| AC-6 | ✅ | `tool_admission_wait` 结构化日志（阶段名/路由模板/run_id/时长/活跃与等待计数/上限） |

## 负向验证（证明测试不是恒真）

| 变异 | 结果 |
|---|---|
| 名额设为 1000（等效无准入） | 心跳断言**失败**（1.0s 内未被服务） |
| 名额 = 池上限（15） | `_validate_agent_tool_admission` **拒绝** |
| 名额 = 0 | 同上**拒绝** |
| 等待中被取消 | 不泄漏名额，释放后仍可取满 cap 个 |

## 指标修正（重要）

先前用「日志唯一秒数」判断静默，会漏计。改用**请求计数**复核：

```
修复前 04:28:20→04:29:50（声称 96s 静默）: 日志 1 行，请求 0   ← 真静默
修复前 05:26:30→05:27:00（声称 41s 静默）: 日志 1 行，请求 0   ← 真静默
修复后 13:00:27→13:03:36（同长度窗口）:    请求 1146 个        ← 无静默
```

修复后仍出现的「turn 间隔 187s」不是服务端饥饿：该窗口内 API 持续服务
heartbeat/lease，且 `tool_admission_wait`=0；间隔来自**上游模型响应慢**
（run 232 的 provider egress 间隔 186s，最后一次 `stream_interrupted`）。

## 未验证 / 边界

- run 232 在修复后仍 `expired`。**本文件初版把原因写成「上游 `stream_interrupted`（模型流中断）」，
  该归因是错的，已更正**：`stream_interrupted` 在 `provider_proxy.passthrough_with_audit`
  里是**兜底分支**（同时覆盖 `httpx.HTTPError`、`GeneratorExit`、`CancelledError`、`Exception`），
  sidecar 断开也会落到它；而该 run 的 egress 实际为 `upstream_status=200`、
  `status=succeeded`，模型调用成功。
  真实原因是一个**独立缺陷**：停滞期间事件循环被同步 SQL 调用钉住
  （已抓到栈：`_refresh_run_gate` → `db.get()` 在 MainThread 上执行），
  使 sidecar 的心跳重试耗尽后判失租 abort。该缺陷由后续任务
  `09-30-steward-sidecar-request-stall` 继续定位。
- 未逐笔测量生产上把 15 条连接占住的具体持有者；修复不依赖该定位（线程池被连接池
  等待耗尽本身就是缺陷）。
- 未测 `busy_timeout`（SQLite 写锁）在其中的放大作用。
- 线上未操作。
