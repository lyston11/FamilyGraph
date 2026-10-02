# 验收证据（2026-10-02）

## 缺陷

`b656d89` 让心跳续签 run token 并写回 `job.run_token`，但有**三处调用点在启动时按值捕获**旧 token：

```ts
startEventFlusher(job.run_id, job.run_token, ...)   // -> events/append
buildRunSession(..., job.run_token, ...)            // -> provider api key
client.executeTool(..., runToken, ...)              // -> 工具调用
```

token 有硬 TTL（600s）且只在心跳续签，因此存活超过 600s 的 run 在这些路径上 401，
而心跳本身仍 200。

## 证据

| run | 存活 | 失败码 | 401 路径 |
|---|---|---|---|
| 298 | 620s | SIDECAR_ERROR | events/append |
| 303 | 693s | SIDECAR_ERROR | events/append |
| 466 | **601s** | SIDECAR_ERROR | events/append + provider/responses |
| 467 | **602s** | SIDECAR_ERROR | events/append + provider/responses |

**关键判据**：401 只出现在 `events/append` 与 `provider/responses`，
**心跳全部 200**——说明 token 续签生效了，但其他路径没用上。
存活时长 601/602 秒与 TTL 600s 吻合。

## 修复

三处改为**按需读取**（传 getter 而非值），使所有路径跟随续签：

```ts
startEventFlusher(job.run_id, () => job.run_token, ...)
this.sessionFactory(..., () => job.run_token, ...)
client.executeTool(projection.run_id, runToken(), ...)
```

## 验证

回归断言的是**续签后读到新值**，而不是「传了个函数」——这个区别正是缺陷本身：

- `resolveProvider(..., () => token, ...)` 在 token 变更后返回新值；
- flusher 在 token 变更后发出的 Bearer 是新值。

变异验证：把 flusher 改回按值捕获 → 用例失败（`expected [ [Function] ] to include 'renewed-token'`）。

## 部署后实测（12:52）

```
egress: total=15 ok=15 fail=0
run 473/474/475: succeeded
401 / 403 / 409 / 502: 全部 0
running run: 0     in_flight attempt: 0
```

## 边界

- 部署后窗口内没有自然出现的 >600s 长 run，因此「长 run 不再 401」由**单元回归**
  证明，而非端到端复现；端到端证据是 401 归零。
- 线上未操作。
