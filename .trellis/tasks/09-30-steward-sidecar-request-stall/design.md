# 技术设计：sidecar 侧请求停滞

## 状态

planning。R0 已取得**部分直接证据**，但**机制尚未完全确证**：已抓到事件循环阻塞在同步 `db.get()` 的栈，并已否证 sidecar 侧阻塞，但尚未复现 100s 量级。

**同时纠正上一任务（`09-30-steward-event-loop-block`）验收记录中的归因错误**：那里写「原因是上游 `stream_interrupted`」。该说法错误——`stream_interrupted` 在 `provider_proxy.passthrough_with_audit` 里是**兜底分支**（同时覆盖 `httpx.HTTPError`、`GeneratorExit`、`CancelledError`、`Exception`），sidecar 断开也会落到它。实际 egress 为 `upstream_status=200`、`status=succeeded`，模型调用成功。

## 1. 已确证的事实

### 1.1 服务端并非「全程健康」——曾出现秒级慢响应（新证据）

用 `curl` 直连 8000 的主动探针在 **13:57:13 抓到单次 `3275ms`**（code=200），并同步用 `py-spy` 抓到**事件循环线程**的栈：

```
Thread 311037 (idle): "MainThread"          ← 事件循环线程，正卡在同步 SQL 执行
    do_execute (sqlalchemy/engine/default.py:941)
    ...
    get (sqlalchemy/orm/session.py:3693)
    _refresh_run_gate (app/services/provider_proxy.py:232)
    passthrough_with_audit (app/services/provider_proxy.py:560)
    stream_response (starlette/responses.py:244)
    _run_coro (anyio/_core/_tasks.py:327)
    run_forever (asyncio/base_events.py:641)
    main (app/serve.py:210)

Thread 311093 (active+gil): "asyncio_0"     ← to_thread 默认 executor 在跑维护
    run_maintenance_batch (app/services/rag_maintenance.py:286)
    run_maintenance_tick (app/services/maintenance.py:106)
```

**这推翻了我先前用「3µs」否证的结论**：那次探针是在**无竞争**的隔离库上测的。真实竞争下 `_refresh_run_gate` 会在事件循环上阻塞**秒级**，因为它是在事件循环里做同步 SQLite 查询。

### 1.2 sidecar 侧阻塞已被否证（重要纠正）

我一度以为 sidecar 的事件循环被阻塞（其 `/healthz` 慢到 2011–3355ms）。**这是误读**：`src/health.ts` 的 `/healthz` 每次都调 `client.probeFastAPI()`，而它用 `AbortSignal.timeout(2000)` 打 `/api/health`。

所以：

- 慢样本的众数 **2011 / 2013ms** = `probeFastAPI` 的 **2s 超时**，即 **API 的 `/api/health` 当时超过 2 秒未响应**；
- `healthz` 慢是**结果**（它在等 API），不是 sidecar 自身阻塞的证据。

结合 1.1 的栈，指向**同一个服务端原因**。

### 1.3 停滞窗口与 provider 流时长重合（26/27）

全天 27 个 >30s 的 API 接收空档中，**26 个在结束 ≤20s 内紧接着一条 `provider/responses` 完成**。uvicorn access 在响应完成时写日志，故两次 provider 日志间隔 = 流时长。窗口结束时请求**成批**到达（积压释放形态）。

### 1.4 已排除

| 候选 | 依据 |
|---|---|
| 上游 Provider 故障 | egress `upstream_status=200`、`status=succeeded` |
| sidecar 进程崩溃/重启 | `NRestarts=0` |
| sidecar CPU 密集 / fd 耗尽 | CPU 0%、fd 24（上限 1048576）、无 TIME_WAIT 堆积 |
| Node fetch 长流阻塞同 origin | 实测复现：流期间对同 origin 请求仅 6ms |
| 机器 CPU 压力 | load average 0.32（4 核） |
| 维护 tick 本身很慢 | 实测 38–202ms（真实库副本） |
| SQLite 写锁单独致阻塞 | 实测持写锁时 `_refresh_run_gate` 仅 0.06s |

### 1.5 未验证（R0 缺口）

- **未复现 100s 量级**。抓到的是 3.3s，机制方向一致但量级差约 30 倍。
- 未确证 `rag_maintenance` 的长写事务与事件循环读之间的实际等待时长分布。
- 未解释 run 232 停滞（102s/111s）与 run 238 正常（最长 26s）的差异条件。

## 2. 候选机制（需 R0 继续区分）

1. **事件循环上的同步 SQLite 读被写事务阻塞**（已有栈证据，量级未证）：
   `_refresh_run_gate` 在事件循环线程做 `db.rollback()` + `db.get()`；`rag_maintenance`
   经 `_acquire_index_writer` 取写锁后**整批循环**（每行一个 SAVEPOINT），持有时间长。
   事件循环被钉住 → 所有端点（含 `/api/health`、心跳）一起停 → sidecar 的
   `probeFastAPI` 2s 超时、心跳 15s×4 重试耗尽 → 判失租 abort。
2. **SDK 层 provider 重试**（`maxRetries=5` × `maxRetryDelayMs=20000` = 最坏 100s）：
   量级吻合，但 run 232 在网关侧只有 2 次 provider 请求，需解释重试未打到网关。

## 3. 修复方向（取决于 R0 收敛）

- 若确认 1：让 `_refresh_run_gate` 的同步 DB 访问不落在事件循环上（改为 async 路径或
  移入线程池），并缩短/隔离 `rag_maintenance` 的写事务持有时间。**优先修持有者**，
  限流不能替代。
- 若指向 2：让 SDK 重试不阻塞心跳/轮询路径。

**不得**放宽 `AGENT_REQUEST_TIMEOUT_MS` / `defaultLeaseMs` /
`STEWARD_ASSIST_CALL_LEASE_SECONDS` / `probeFastAPI` 的 2s 超时——那只是把失租推迟。

## 4. 可观测性要求

- 记录事件循环延迟（如 `loop.call_soon` 往返或 `perf_hooks.monitorEventLoopDelay`）；
- 记录同步 DB 调用的耗时（阶段名、时长），使「事件循环被 SQL 钉住」可直接定位；
- 字段不含正文、token、凭据。

## 5. 验收与回退

见 PRD AC-0…AC-4。无 schema/迁移变更；回退 = revert 提交。

## 6. 风险

- 停滞窗口 30–340s 不等，复现需真实长 run；若受控环境无法复现 100s 量级，
  必须先建立第 4 节的可观测性，再等下一次真实停滞，**不得凭 3.3s 的栈直接宣称
  已解释 100s 现象**。
- 修复若触及 provider 转发或维护事务边界，必须验证 fence/幂等/结算合同未变。
