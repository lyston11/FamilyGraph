# R0 定位结果：连接池饥饿耗尽工作线程（AC-0 已完成）

## 结论（决定性受控复现）

**根因是数据库连接池与 AnyIO 工作线程池的容量错配：**

```
连接池上限 15（QueuePool size=5 + max_overflow=10），pool_timeout=30s
AnyIO 工作线程上限 40

当 15 个连接被占满：
  → 后续请求阻塞在连接池 checkout，最长 30s
  → 该阻塞发生在 AnyIO worker 线程内，线程被占住不放
  → 40 个这样的请求即耗尽全部工作线程
  → 心跳 / lease / health 拿不到线程 → 全端点静默
```

静默时长 32–96s ≈ `pool_timeout`(30s) 的 1–3 倍，与观测吻合。

## 决定性复现

### 1. 池饥饿使请求阻塞整整 30 秒

占满 15 个池连接后，并发 40 个请求：

```
PROBE pool max=15 timeout=30.0s
PROBE held checkedout=15
PROBE 40 reqs: min=30.0s p50=30.0s max=30.0s
PROBE outcomes: ['TimeoutError']
PROBE follow-up: ['TimeoutError 30.0s']     ← 后续单个请求仍要再等 30s
```

全部请求耗时**精确等于 `pool_timeout`**，不是"慢"，而是被池等待钉住。

### 2. 池等待期间工作线程被占满

同一场景下采样 `anyio.to_thread` 的 borrowed/total tokens：

```
PROBE held=15/15 timeout=30.0s
   t= 2.0s  40/40
   t= 4.0s  40/40
   ...
   t=28.1s  40/40
PROBE peak borrowed=40
```

**从 2s 到 28s 持续 40/40** —— 工作线程池全程被占满。此时任何新请求
（心跳、lease、health）都无法被服务。这就是「静默」。

### 3. 与真实数据吻合

- 静默窗口内 lease 轮询从 **1414 次/2 分钟骤降到 6 次**，窗口后恢复到 **1696 次**
  —— 请求确实未被服务，而非客户端没发。
- 窗口结束时请求成批涌入（04:29:54 一秒内 16 条）—— 池释放后积压一次性被服务。
- 单次 health 报告 65,830ms，量级与 `pool_timeout` 的倍数一致。

## 修正先前错误

| 先前说法 | 实测结论 |
|---|---|
| "40 个 worker 被耗尽"（推断） | **已证实**：40/40 持续被占 |
| "pool_timeout=30s 解释全部静默"（曾撤回） | **成立**：阻塞时长精确等于 pool_timeout |
| "每轮 26 个工具并发" | **错误**：真实并发峰值 8–23（run 147=23、run 189=16），单次约 1ms；一轮内是多批次 |
| "provider 流长期持有连接" | **否证**：流期间 `checkedout=0`（每 chunk `db.rollback()` 归还连接） |
| 事件循环阻塞 | **否证**：loop lag 最大 22ms，差三个数量级 |

## 为什么必须修

连接池（15）**小于**工作线程池（40）是结构性缺陷：任何连接池耗尽都会
连带耗尽线程池，使**所有**端点（含心跳、健康检查）失效。工具执行路径
每次请求都要写数据库（`acquire_run_writer` + 准入 CAS + 幂等占位 + 审计），
因此 26 个并发工具调用即可打满 15 个连接（已实测 `checkedout=15`）。

## 复现脚本

`backend/scripts/steward_contention_probe.py`（真实 uvicorn + 真实并发 +
loop lag / AnyIO tokens / 连接池采样；需显式 `DATA_DIR`）。

## 证据边界

- 已证实「池饥饿 → 30s 阻塞 → 线程耗尽 → 全端点静默」这条链，以及触发它所需的
  并发规模（≥40 个请求同时争抢已耗尽的池）。
- **未逐笔测量**生产中把 15 个连接长期占住的具体持有者。但修复不依赖这一点：
  线程池被连接池等待耗尽本身就是缺陷，无论持有者是谁。
- 未测量 `busy_timeout=5000`（SQLite 写锁）在其中的放大作用。
