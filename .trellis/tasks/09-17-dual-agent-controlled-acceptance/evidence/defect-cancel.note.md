# D-F1 取消收敛为 failed：证据与判别实验

## 用户可见后果

浏览器收到 `run.failed` 且 `error_code=SIDECAR_ERROR`，前端文案映射为
「助手服务暂时不可用，请稍后重试」（`frontend/src/api/agent.ts` `AGENT_ERROR_COPY`），
而不是取消应有的语义。用户无法区分「我取消了」与「助手坏了」。

## 复现（3/3）

```
python3 scripts/smoke/run_controlled_acceptance.py --scenario A6-cancel --no-reused-suites
```

观测前提已由脚本保证：取消只发在 run 进入 `running` 之后（`status_before_cancel=running`）。

```
run1 A6-1 fail | cancel_http=200 run=failed error=SIDECAR_ERROR
run2 A6-1 fail | cancel_http=200 run=failed error=SIDECAR_ERROR
run3 A6-1 fail | cancel_http=200 run=failed error=SIDECAR_ERROR
```

公共事件流只有：`message.user_added, run.started, turn.started, run.failed`。

## 内部请求台账（决定性证据）

sidecar 侧记录到每一次 internal 请求的状态码：

```
  28ms POST /internal/agent/jobs/lease            200
  44ms GET  /internal/agent/runs/1/context        200
 517ms POST /internal/agent/runs/1/events/append  200
3348ms POST /internal/agent/runs/1/events/append  409   ← 取消已生效
3845ms POST /internal/agent/runs/1/events/append  409
 ...  （共 16 次连续 409，间隔约 500ms = 事件 flush 周期）
9146ms POST /internal/agent/runs/1/settle        200   ← 自造 failed 结算成功
```

首个 409 的响应体：

```json
{"error":{"code":"AGENT_RUN_NOT_RUNNING","message":"Run 已请求取消"}}
```

## 机理

1. 浏览器取消 → 服务端置 `cancel_requested`，`_require_active_run` 开始对
   `append` 返回 409。
2. sidecar 的取消检测**唯一**来源是心跳
   （`agent/src/worker.ts:startHeartbeat`，间隔 `max(floor(defaultLeaseMs/3), 1000)`；
   出厂 `AGENT_DEFAULT_LEASE_MS=60000` → **20000ms**）。
3. 在飞的 `events/append` 先返回 409；`InternalClient.request` 把它映射为
   `ConflictError` 抛出（409 属 typed terminal，不重试）。
4. `executeJob` 的 catch：

   ```ts
   if (active.cancelRequested || active.leaseLost) return;   // 两者均为 false
   ... settle('failed', { error_code: 'SIDECAR_ERROR' })
   ```

   于是 sidecar 用自己的 catch-all 分支覆盖了「服务端已裁决取消」这一事实。
5. 服务端按已取消终态化（`cancel_requested` 权威），用户看到
   「助手服务暂时不可用，请稍后重试」而不是「已取消」。

观测到的 sidecar 检查值：`abort_seen_ms=null`、`stream_ticks_after_abort=0`、
`heartbeat_outcomes=[]` —— 证明**没有任何心跳在取消后到达**，也就没有任何代码
把 `cancelRequested` 置为 true。

## 判别实验（证明因果）

`AGENT_DEFAULT_LEASE_MS=3000` → 心跳 1000ms，会在取消后到达：

```
baseline (hb=20000ms): fail | run=failed error=SIDECAR_ERROR
                       settle_requests=[{at_ms:12223, body:{status:"failed",error_code:"SIDECAR_ERROR"}}]

fast hb  (hb=1000ms) : run=running  error=None
                       settle_requests=[]
                       heartbeats=[{at_ms:1317,200},{at_ms:2312,200}]
```

心跳一到就调 `markCancelRequested` → `abort()` → `executeJob` 提前
return，不再自造失败。**结论：是否失败完全取决于心跳是否赶在在途 append 之前到达**，
与上游、模型、网络都无关。

## 这是缺陷而非设计：两者需分清

`agent_queue.request_cancel` 的语义写得很清楚，且 **failed 确实会被原样保留**：

> leased/running：置 cancel_requested 标记（run/job 镜像），settle 时把本应
> succeeded 的终态改判为 cancelled（结果丢弃、审计注明）；**failed 原样保留**

所以「failed 保留」是设计。问题在于：**这里的 failed 是 sidecar 在取消后自造的**，
而 sidecar 自己的注释写明了它不该这么做：

> Cancellation/lease loss is adjudicated by FastAPI. The abort signal intentionally
> rejects the in-flight Pi/internal request; **do not turn that expected rejection into
> a sidecar `failed` settle** that could race the server's cancelled terminal state.
> — `agent/src/worker.ts` executeJob catch

守卫条件是 `active.cancelRequested || active.leaseLost`，但**取消从未到达 sidecar**
（`heartbeat_outcomes=[]`、`abort_seen_ms=null`），两个标志都还是 false，于是
catch-all 分支接管。结论：实现违反了自身声明的意图，且**服务端已明确裁决取消**
（`AGENT_RUN_NOT_RUNNING / "Run 已请求取消"`）这件事被丢弃了。

## 覆盖漏洞

- backend `tests/test_agent_queue.py` 覆盖了 `queued` 取消立即终态
  （`test_cancel_from_queued_writes_event`）与「heartbeat 不延长被取消租约」
  （`test_heartbeat_does_not_extend_cancel_requested_lease`），
  但**没有**「running 中取消 + sidecar 在飞 append」这条路径。
- agent `worker.integration.test.ts` 只在**心跳返回 `cancel_requested:true`** 的
  前提下验证取消（`state.cancelOnNextHeartbeat`），因此恰好绕过了本缺陷：
  真实场景里心跳根本来不及发生。

## 建议

不要把「在飞 append 的 409」当作普通冲突：服务端已返回
`AGENT_RUN_NOT_RUNNING / "Run 已请求取消"`，这是**权威终态信号**，应等价于心跳的
`cancelRequested`（`run_cancelled` 已是网关侧对同类情况的安全分类名）。
心跳间隔（20s）不应当成为取消语义的可用性上限。
