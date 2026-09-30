# 验收证据（2026-09-30）

## AC-0 定位（已完成）

根因：`passthrough_with_audit` 是 async generator，**每个 chunk** 在事件循环上调用
同步的 `_refresh_run_gate`（`db.rollback()` + `db.get()`）。连接池被占满时该调用在
QueuePool 里等待 `pool_timeout`=30s，**阻塞整个事件循环**。

实测：

```
池耗尽 15/15 + 事件循环上调用 _refresh_run_gate → 30.0s
心跳协程最差延迟 30,055ms（预期 50ms）
3 个 chunk → 90.0s 全进程停顿
```

与观测的 96 / 101 / 102 / 111 秒（3–4 个 chunk）精确吻合。同一代码还跨 chunk
持续持有 1 条池连接（实测整个流期间 `checkedout == 1`），加剧池耗尽。

完整证据见 `research/evidence/r0-root-cause.md`。

## 修复

每个 chunk 的 gate 复核移入工作线程，并使用**独立的短生命周期 Session**：
事件循环不再被阻塞，chunk 之间不再持有连接。检查函数、判定条件、
`ProviderProxyError` 语义逐字未变。

## AC 逐项

| 验收 | 结果 | 证据 |
|---|---|---|
| AC-0 | ✅ | 池耗尽 → 30.0s/chunk → 3 chunk = 90s；与 96–111s 吻合 |
| AC-1 | ✅ | 心跳最差延迟 **30,055ms → 58ms**；流期间持有连接 **1 → 0** |
| AC-2 | ✅ | 开发环境 run 244 心跳间隔 **20s 稳定**（修复前 run 232 有 111s/102s 空档） |
| AC-3 | ✅ | backend **1941 passed / 3 skipped**；ruff/mypy 干净 |
| AC-4 | ⚠️ 部分 | 已有栈采样手段可定位；产品内可观测性未加（见下） |

## 部署后实测

```
health 延迟：p50=9ms  p95=10ms  p99=16ms  max=60,927ms
三次慢响应（8.4s / 13.7s / 60.9s）时 MainThread 全部停在 select（空闲）
```

**修复前**：事件循环被钉住，全进程静默。
**修复后**：事件循环空闲；慢响应来自工作线程在池里等连接。

## 负向验证（证明测试不是恒真）

| 变异 | 结果 |
|---|---|
| gate 复核放回事件循环 | `test_per_chunk_gate_check_never_runs_on_the_event_loop` **失败**（`['MainThread', 'MainThread']`） |
| 复用请求级 Session | `test_stream_does_not_pin_a_pool_connection_between_chunks` **失败** |

## 未完成 / 边界

- **池耗尽本身未修**：修复后仍有 5 次 >1s 慢响应，最高 60.9s，原因是
  steward 的 4 个 core 线程 + 每空间 2 个 assist + 心跳/lease 轮询共同争抢
  15 条连接。实测 60.9s 那次：**4 个线程在等连接、仅 2 个在执行**，
  5 个心跳请求也在排队。这是与「事件循环阻塞」**独立的第二个缺陷**：
  前者致命（全进程静默），后者只让该请求变慢。
- run 244 在修复后仍 `expired`，但**心跳间隔稳定为 20s**（修复前为 111s/102s 空档），
  说明本任务缺陷已解决；其 expired 另有原因，未定位。
- **产品内可观测性未加**（AC-4 部分）：目前仍依赖外部探针 + `py-spy` 采样定位。
- 线上未操作。
