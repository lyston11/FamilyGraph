# 证据：09:02 转折点与进程内损坏

## 转折点

```
08:56:23  run 350 succeeded（最后一个成功 run）
08:56:35  egress 350 succeeded upstream_status=200 header_ms=2455
08:57:11  egress 351 succeeded upstream_status=200 header_ms=822
09:02:17  runs/352 与 runs/351 的 provider/responses 抛 unhandled exception
09:02:17  egress 352 stream_interrupted  sent=True  upstream_status=200
09:02:17  egress 351 stream_interrupted  sent=True  upstream_status=200
09:02:29  egress 352 transport_timeout sent=False   ← 之后全部如此
```

注意 `09:02:17` 那次 `stream_interrupted` 仍带 `upstream_status=200`——
说明**上游已响应**，中断发生在读取阶段。此后才变成连接都建不起来。

## 异常日志内容（缺失）

```
{"logger": "app.main",       "msg": "unhandled exception on POST /internal/agent/runs/352/provider/responses"}
{"logger": "uvicorn.error",  "msg": "Exception in ASGI application\n"}
```

**没有异常类型、没有 traceback。** journal 中该时刻前后也无 traceback 行。
因此无法判断是 `CancelledError`、`GeneratorExit` 还是别的异常。

## 同机连通性（排除网络）

```
curl  http://lystonmacbook-air.tail79d583.ts.net:8443/v1/models
      try1..5: http=401 connect≈0.25s total≈0.49s
httpx 同 URL，connect=10s：OK 401 0.59s
getaddrinfo: 100.71.18.78（仅 IPv4 单地址）
systemd-run 同 user：TCP_OK
```

## 进程状态

```
uptime 22h（05:54:07 启动，早于 09:02 转折点 → 转折发生在进程生命周期内）
Threads=11, fd=33, ulimit 1024（未耗尽）
py-spy: 全部 idle，无卡住栈
ss: 无 SYN-SENT/SYN-RECV，无到上游的连接
environ: 无 proxy/SSL 变量
```

## 为什么指向进程内状态

同一进程、同一时刻：外部 curl/httpx（新进程）成功，而**网关路径**每次都在
connect 阶段 10s 超时。网络、DNS、上游、配置、凭据都被排除后，剩下的解释是
**该进程内的某个状态被 09:02 的异常破坏**，且无法自愈（重启前持续 18.8h）。

## 证据边界

- 未取得异常 traceback（R2 要修这一点）；
- 未确认是「每次新建 `httpx.AsyncClient`」还是「共享状态」受损；
  代码里每次请求都新建 client，因此**共享状态**更可疑，但未验证；
- 未做受控复现：需要能触发同类未处理异常的路径。
