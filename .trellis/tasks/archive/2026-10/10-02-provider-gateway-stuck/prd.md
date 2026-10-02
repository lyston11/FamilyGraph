# Provider 网关进程级损坏：未处理异常后所有出站失败

## 现状（严重，正在持续）

**从 2026-10-01 09:02 起，所有 steward provider 出站请求全部失败，已持续 18.8 小时。**

```
08:00-09:02   egress ok=19  fail=0
09:02-10:00   egress ok=0   fail=116
09:02 起      74 个 run 全部 failed（PROVIDER_STREAM_ERROR）
```

## 时间线

```
08:56:23  run 350 succeeded          ← 最后一个成功
08:57:11  run 351 egress 200 OK      ← 仍正常
09:02:17  runs/351、runs/352 的 provider/responses 抛 unhandled exception
09:02:29+ 此后**每个** egress 都是 transport_timeout, sent=False
          持续到 2026-10-02 03:50（观察时点），零成功
```

## 关键证据

### 失败形态

```
03:00:01.252  transport_timeout  sent=False  retryable=True
03:00:12.195  transport_timeout  sent=False   gap=10.9s
03:00:24.160  transport_timeout  sent=False   gap=10.4s
03:00:37.984  transport_timeout  sent=False   gap=10.1s
```

- 间隔恒为 **~10s = `AGENT_PROVIDER_PROXY_CONNECT_TIMEOUT_SECONDS`**；
- `sent=False` 表示**连接未建立**；
- sidecar 收到 `502 AGENT_PROVIDER_PROXY_UNAVAILABLE`。

### 同机直连完全正常（排除网络/上游/配置）

```
curl  http://<upstream>:8443/v1/models   → 401  connect=0.25s total=0.49s  (连续 5 次稳定)
httpx 同 URL 同超时配置                    → 401  0.59s
socket.getaddrinfo                        → 100.71.18.78（单地址，无 IPv6 干扰）
systemd-run 同 user 上下文 TCP 8443        → TCP_OK
tailscale status                          → 上游节点 active
```

配置也一致：`provider 2 = buddy2api`，`allowed_models=["deepseek-v4.1-flash"]`，
space 1/2 的 steward 与 assistant 均指向它。

### 进程内状态

```
api 进程: 22h uptime（05:54 启动），Threads=11，fd=33，ulimit 1024
py-spy:  所有线程 idle，无卡住的出站连接（ss 无 SYN-SENT）
environ: 无 proxy / SSL / RESOLV 变量
```

**同一进程内 curl/httpx 正常，但网关路径 100% 失败** —— 指向进程内状态损坏，
而非网络或配置。

## 需求

### R0 定位「未处理异常」与随后的必然失败之间的因果

必须解释：09:02:17 的那个未处理异常如何导致**此后所有**连接尝试失败。

候选（**均未验证**，需区分）：

1. **事件循环被污染**：异常发生在 async generator 中，可能留下未完成的
   `httpx.AsyncClient` 或未清理的 await，使后续 `asyncio` 网络操作无法调度；
2. **`_admit_upstream_request` 状态**：准入记录被写坏，使后续请求在构造前即失败；
3. **某个全局/连接池对象被破坏**（如 `httpx` 的全局 resolver 或 transport）；
4. **进程内 DNS 缓存**（`getaddrinfo` 的 aiodns/线程池）被污染。

**未取得该异常 traceback**：`app.main` 只记 `unhandled exception on <route>`，
`uvicorn.error` 只记 `Exception in ASGI application\n`，**异常正文与 traceback 丢失**。
这本身是缺陷（R2）。

### R1 修复并使失败可自愈

- 修复导致进程级损坏的根因；
- 同类异常不得让进程进入「此后全部失败」的状态——要么隔离（每次请求独立），
  要么可恢复（失败后重置相关状态）；
- **不得**用「自动重启进程」掩盖：先定位根因。

### R2 未处理异常必须可诊断

当前只记录到「哪个路由抛了未处理异常」，**没有异常类型、没有 traceback**，
无法定位。必须记录异常类型与 traceback（脱敏：不含请求体/凭据/SQL 参数）。

### R3 合同不变

- 不改变 egress 审计语义（每次真实尝试恰好一条、`sent` 判定不变）；
- 不改变上游 4xx 状态码与不可重试合同；
- 不放宽任何超时或租约；不改 sidecar。

## 验收

| ID | 可观察结果 |
|---|---|
| AC-1 | 指出 09:02:17 异常如何导致后续全部失败（非推断，有直接证据） |
| AC-2 | 修复后 provider 出站恢复成功；同负载下连续多轮 run 成功 |
| AC-3 | 未处理异常日志含异常类型与 traceback，且不含敏感数据 |
| AC-4 | 同类异常后进程不再进入「此后全部失败」状态（有回归用例） |
| AC-5 | backend 全量 pytest / ruff / mypy 通过 |
| AC-6 | 既有 egress/Provider 合同回归通过 |

## 不在范围

- 放宽 `AGENT_PROVIDER_PROXY_*` 超时；改 sidecar；改上游 provider 配置；
- 用自动重启掩盖；线上操作。
