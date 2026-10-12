# C4：Provider 流级资源与有界失败

## 交付

### 1. 流级三层名额

| 维度 | 作用 |
|---|---|
| `global` | 全集群并发流总数（默认 16） |
| `agent_kind` | 按 assistant/steward 分桶（默认 12），防止一类挤占另一类 |
| `tenant` | Assistant=account，Steward=space（默认 2） |

**为什么必须与建连名额分开**：建连名额在流开始前归还，所以一个租户可以同时持有多个
**已建立**的长流；而一个 100 秒的流既不占工作线程也不占连接（由
`test_stream_does_not_pin_a_pool_connection_between_chunks` 锁定）。因此既有任何名额
都无法限制「同时有多少个上游流在跑」——本层是唯一那道约束。

**为什么三层都需要**：`config._validate_agent_stream_limits` 在启动时强制
`tenant ≤ kind ≤ global`。若 tenant > kind，单租户上限永不生效；若 kind > global，
单类上限永不生效。这是配置错误而非宽松策略，因此 fail-loud。

### 2. 流级墙钟上限（有界失败）

`AGENT_STREAM_MAX_DURATION_SECONDS`（默认 900s，覆盖实测最长成功 run 约 804s）。
超限抛**专用异常** `StreamDeadlineExceeded`，审计保留 `stream_deadline_exceeded`。

两个关键设计点：

- **不能用 `break`**：break 会让客户端看到「正常结束」的截断流，sidecar 无从区分
  「上游答完了」与「被服务端掐断」；
- **必须排在通用 `except Exception` 之前**：否则会被改写成 `stream_interrupted`，
  丢失真正原因（运维上「服务端主动掐断」与「上游中断」是不同信号）。

## 关键缺陷（本次踩到，由测试抓出）

**我的插入把 `@dataclass(frozen=True)` 推到了新类上**，导致 `EgressFailure` 失去
装饰器 → `EgressFailure() takes no arguments` → 17 个既有用例失败。
`mypy` 只报「Unexpected keyword argument」，真正的错误信息来自测试。
教训：在 dataclass 前插入新类时，装饰器归属必须显式确认。

## 验证

```
backend: 2035 passed, 25 skipped
mypy: 215 files 无问题
ruff: 仅剩 main 上既有的 2 个
变异: 删掉 deadline 检查 → 用例失败（流不再中断）
新增用例: 6（流级三层维度）+ 1（deadline 中断与原因保留）
```

## 未完成（诚实声明）

1. ~~backpressure 未实现~~ → **实测已正确，并补了守护断言**。Python 异步生成器是
   **拉取式**（`async for` 只在消费者请求下一块时推进上游），httpx `aiter_raw()`
   同样按需读 socket，因此「消费端慢 → 上游不被推进 → socket 缓冲填满 → TCP 背压」
   天然成立，**实现里没有任何无界缓冲**。缺的是证明，不是机制。

   新增断言：任一时刻「上游推进量 − 已消费量 ≤ 1」。若有人改成「先收全再 yield」
   或加无界队列，该断言立刻失败（实测：改成列表推导后 3 个用例失败）。
2. ~~circuit breaker 未实现~~ → **已闭合**：`app/services/provider_circuit.py`。
   按 **`upstream × kind`** 分区（**不含 tenant**，理由见下），open 时在**发送前**
   拒绝（零出站，审计 `sent=False`），half_open 只放有界探针，需多次成功才关闭。
3. **连接生命周期**：persistent `AsyncClient`（实测节省约 49.5ms/请求）未实施，
   需先基准。
4. **故障注入**：DERP 不可达、DNS 失败、connect/header/stream 中断、
   backpressure 慢消费均未注入验证。
5. **长流多租户 p95/p99**：需要真实负载矩阵，属 `10-04-multitenant-load-acceptance`。

## 证据等级

流级名额与 deadline：**L1**（SQLite 单测 + 变异验证）。
配额在真实 PostgreSQL 多连接下的行为未单独验证（结构复用 C2/C3 已验的 counter 机制）。


## 熔断器（后续更新）

### 分区决策：`upstream × kind`，**不含 tenant**

| 方案 | 问题 |
|---|---|
| 全局 | 一个上游不可用放大成「整个系统不可用」——比不熔断更糟 |
| 含 tenant | 上游可用性是**上游**的属性；按租户分区会让每个租户各自重复发现同一个上游故障，熔断失去意义 |
| **`upstream × kind`（采用）** | 一个 profile 挂掉不牵连另一个；Assistant 与 Steward 走不同设置也不互相影响 |

租户隔离由 capacity/stream 配额负责（C2/C4 已交付），不是熔断的职责。

### 状态机

```
closed --(连续失败 ≥ 阈值)--> open
open --(冷却结束)--> half_open（只放有界探针）
half_open --(探针失败)--> open（重新计时）
half_open --(成功 ≥ N 次)--> closed
```

**为什么需要多次成功**：一次成功就关闭会让上游抖动时熔断反复开合。

### 为什么不用 Redis 共享熔断状态

熔断状态是「**本进程**观察到的上游健康」。跨实例共享会引入 Redis 依赖（而 Redis
本身可能故障），且各实例网络路径可能不同（Tailscale DERP 即如此——同一上游在
不同实例上确实可能一好一坏）。跨实例的**持久**信号由 egress 审计承担。

### 记账语义

- **连接建立且拿到响应头 = 成功**（即使状态码是 4xx/5xx：那是应用层拒绝，说明
  网络路径正常，不该触发熔断）；
- 传输类失败（超时/连接异常）记失败；
- 被熔断拒绝时**不消耗上游尝试**，审计 `sent=False`（连接未建立，可观测事实）。

### 变异验证（3 组）

| 变异 | 结果 |
|---|---|
| 冷却期内也放行 | 3 个用例失败 |
| 分区键加入 tenant | 分区用例失败 |
| half_open 不限制探针 | 探针限制用例失败 |


## 背压：机制已正确，缺的是证明（后续更新）

### 实测

| 形态 | 上游推进 vs 消费 |
|---|---|
| 当前实现（逐块 yield） | 超前 **≤ 1** 块 |
| 变异为「先收全再 yield」 | 上游一次跑完 → 3 个用例失败 |

### 为什么「拉取式」就是正确的背压

```
async for chunk in upstream.aiter_raw():
    ...
    yield chunk          # 消费者不请求下一块，这里就不执行
```

- 消费者慢 → `yield` 挂住 → `aiter_raw()` 不被推进 → socket 缓冲填满 → TCP 背压；
- **没有中间缓冲**，因此内存占用是常数（O(1) 块），不随流长度增长。

一个「先把上游读完再转发」的实现会把内存从常数变成 O(流长度)，并**失去** TCP 背压
——上游可以把任意大的响应推给一个慢消费端。因此这条断言守护的是真实的安全性质。

### 与流级墙钟上限的关系

两者互补：背压限制**速率**（慢消费者不会被淹没），墙钟上限限制**时长**
（永不结束的流会被有界掐断）。
