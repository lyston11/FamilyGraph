# 根因确证：PII 电话号码正则超线性回溯

## 机制

```python
# app/services/policy_guard.py（修复前）
_PII_PATTERNS = (
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)(?=(?:\D*\d){10,})(?:\+?\d[\d -]{8,}\d)(?!\d)"),
)
```

`contains_pii` → `classify` → `before_provider_request` 在**事件循环**上运行，
每次 provider 请求都执行。第二个 pattern 的 `(?=(?:\D*\d){10,})` 前瞻在每个
起始位置都要扫描到输入末尾，成本随 payload 长度**平方增长**。

## 实测规模曲线

| payload | 耗时 |
|---|---|
| 3.1 KB | 49.6 ms |
| 6.1 KB | 190 ms |
| 10.1 KB | 522 ms |
| 18.1 KB | 1,673 ms |
| 34.1 KB | 6,017 ms |
| 66.1 KB | 23,381 ms |

**4x 输入 → 15.7x 时间**（超线性）。

真实 steward payload ≈ 17.8 KB → `classify` 340ms、`before_provider_request` **1.03s**。

## 为什么表现为 transport_timeout

1. 事件循环被该正则钉住数秒；
2. 同一请求随后要建 `httpx.AsyncClient`（含 `ssl.create_default_context`，~27ms）；
3. `AGENT_PROVIDER_PROXY_CONNECT_TIMEOUT_SECONDS=10` 的预算已被耗尽；
4. 连接在 10s 处超时 → `transport_timeout`、`sent=False`。

失败间隔恒为 ~10s（= connect timeout），与观测完全一致。

## py-spy 现场（进程处于故障状态时采样 200 次）

```
Thread MainThread (active+gil):
    <genexpr> (policy_guard.py:114)
    contains_pii (policy_guard.py:114)
    classify (policy_guard.py:129)
    before_provider_request (policy_guard.py:262)
    stream_provider_response (provider_proxy.py:395)
    proxy_provider_chat_completions (internal_agent.py:622)
```

**从未到达网络层**。403 处 AnyIO worker 栈停在 `queue.get`（空闲），
`connect` 相关栈 **0 处**——连接根本没开始建立。

## 排除的假设

| 假设 | 排除依据 |
|---|---|
| 网络/上游故障 | 同机 curl 5 次稳定 401、connect 0.25s；httpx 0.59s |
| DNS 污染 | `getaddrinfo` 单地址 100.71.18.78；systemd-run 同 user TCP_OK |
| 进程级状态损坏 | py-spy 全线程 idle，无卡住连接；故障源于**计算**而非状态 |
| SSL context 建造成本 | `create_default_context` 仅 26.6ms（曾被误认为根因） |
| 连接池/DNS 缓存 | `db_pool_wait` 为 0；无 SYN-SENT |

## 为何 09:02 是转折点

该 pattern 的成本随 **payload 大小**增长，而 payload 由 steward 的候选/排序
内容决定。当某个空间的证据量越过阈值后，检查耗时越过 10s connect 预算，
于是**每一次**请求都失败——表现为「某个时刻起 100% 失败」而非随机失败。

## 修复

用**一次线性扫描**替代正则：收集连续的电话字符段，统计其中数字个数，
保持「≥10 位数字才算电话号码」的原判据。

```
66 KB: 23,381ms -> 1.9ms
256 KB: 8.3ms（线性）
before_provider_request（真实 payload）: 1.03s -> 2.7ms
```

**注意**：首次简化版写成 `\+?\d(?:[\d -]{8,}\d)`，丢掉了「≥10 位数字」判据，
导致 ISO 日期 `1970-01-01`（8 位数字）被误判为电话并脱敏，破坏了 lineage 层的
生日投影——被既有测试捕获。因此线性扫描必须保留原判据。

## 附带修复：traceback 丢失

`logger.exception()` 把堆栈放在 `exc_info`，而 `JsonFormatter` 把它当
LogRecord 内部属性跳过——**全仓所有异常堆栈从未被输出**。这正是 09:02 的异常
「只知道哪个路由抛了、不知道抛了什么」的原因，也是本故障排查的最大障碍。
