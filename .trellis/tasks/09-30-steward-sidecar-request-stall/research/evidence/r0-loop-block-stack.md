# R0 定位证据：事件循环上的同步 SQL 调用

## 抓到的现场（2026-09-30 13:57:13）

用 `curl` 直连 8000 的主动探针抓到单次 **3275ms**（code=200），同步 `py-spy dump`：

```
Thread 311037 (idle): "MainThread"            ← 事件循环线程
    do_execute (sqlalchemy/engine/default.py:941)
    _exec_single_context (sqlalchemy/engine/base.py:1967)
    ...
    get (sqlalchemy/orm/session.py:3693)
    _refresh_run_gate (app/services/provider_proxy.py:232)
    passthrough_with_audit (app/services/provider_proxy.py:560)
    stream_response (starlette/responses.py:244)
    wrap (starlette/responses.py:255)
    _run_coro (anyio/_core/_tasks.py:327)
    run_forever (asyncio/base_events.py:641)
    main (app/serve.py:210)

Thread 311093 (active+gil): "asyncio_0"       ← to_thread 默认 executor
    run_maintenance_batch (app/services/rag_maintenance.py:286)
    run_maintenance_tick (app/services/maintenance.py:106)
```

**事件循环线程正卡在同步 SQLite 查询里**（`_refresh_run_gate` → `db.get()`），
同时 `asyncio_0` 线程在跑 `rag_maintenance`。

## 这推翻了我先前的否证

我在上一任务中用隔离探针测得 `_refresh_run_gate` 约 **3µs**、400 chunk 累计
lag 8.3ms，据此判断它「差四个数量级，不可能是主因」。

**那次测量是在无竞争的隔离空库上做的**，因此不适用于真实竞争场景。真实负载下
同一调用会在事件循环上阻塞**秒级**（本次抓到 3.3s）。这是我先前推理的错误。

## sidecar 侧阻塞已被否证（纠正）

我一度认为 sidecar 事件循环被阻塞，依据是其 `/healthz` 慢到 2011–3355ms。

**误读。** `agent/src/health.ts` 的 `/healthz` 每次都调 `client.probeFastAPI()`，
而它（`client.ts:675`）用 `AbortSignal.timeout(2000)` 打 FastAPI 的 `/api/health`：

- 慢样本众数 **2011 / 2013ms** = 该 **2s 超时**，即 **API 的 `/api/health`
  当时超过 2 秒未响应**；
- `healthz` 慢是**结果**（它在等 API），不是 sidecar 自身阻塞的证据。

两条独立探针（我的 curl 与 sidecar 的 probeFastAPI）在同期都变慢 → 指向
**同一个服务端原因**。

## 已排除

| 候选 | 依据 |
|---|---|
| 上游 Provider 故障 | egress `upstream_status=200`、`status=succeeded`；模型调用成功 |
| sidecar 进程崩溃/重启 | `NRestarts=0` |
| sidecar CPU/fd 耗尽 | CPU 0%、fd 24（上限 1048576）、无 TIME_WAIT 堆积 |
| Node fetch 长流阻塞同 origin | **实测复现**：流期间对同 origin 请求仅 6ms |
| 机器 CPU 压力 | load average 0.32（4 核） |
| 维护 tick 本身很慢 | 实测 38–202ms（真实库副本） |
| SQLite **写锁**阻塞读 | 实测持写锁时 `_refresh_run_gate` 仅 **0.06s**——WAL 模式下读不阻塞于写 |

## 与观测的吻合

| 观测 | 解释 |
|---|---|
| 停滞窗口与 provider 流时长重合（26/27） | `passthrough_with_audit` **每 chunk** 调 `_refresh_run_gate`，流期间循环执行 |
| 停滞窗口内 API 0 请求 | 事件循环被钉住，所有端点一起停 |
| sidecar `probeFastAPI` 2s 超时（2011ms） | `/api/health` 也在同一事件循环上，同样被钉住 |
| sidecar 心跳 15s×4 重试全超时 → 失租 | 同上 |
| 窗口结束时请求成批到达 | 事件循环恢复后积压一次性被服务 |
| 模型调用本身成功（200） | 流已建立，失败的是**流期间的续租**，不是模型调用 |

## 尚未确证（R0 缺口）

- **未复现 100s 量级**：抓到的是 3.3s，机制方向一致但量级差约 30 倍。
- **未确证每次 `_refresh_run_gate` 的阻塞时长分布**。WAL 下读不阻塞于写锁，
  所以「写锁持有」不足以解释；需进一步定位是**连接池等待**、
  **GIL 竞争**（栈显示 `asyncio_0` 为 `active+gil`）还是**磁盘 I/O**。
- 未解释 run 232 停滞（102s/111s）与 run 238 正常（最长 26s）的差异条件。

**因此 AC-0 仍未完成**：有直接栈证据指向「事件循环上的同步 SQL」，但量级与
触发条件未收敛。不得据此宣称已解释全部历史停滞。

## 复现方法

```bash
# 主动探针 + 栈采样（慢响应未结束时抓，不能在响应结束后补抓）
# 见任务报告中的 /tmp/mon.sh 模式：curl 轮询 health，>2.5s 时 py-spy dump
```
