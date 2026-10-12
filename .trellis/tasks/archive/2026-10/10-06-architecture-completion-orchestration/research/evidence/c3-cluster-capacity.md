# C3：control-plane 故障域 —— 集群级执行名额（AC-4）

## 实测：进程内 limiter 在多实例下把配额翻倍

`scripts/migration-proof/cross_instance_capacity.py`，隔离 PostgreSQL，
2 个实例、每实例配置 2、6 个 worker/实例：

```
1) 进程内 limiter（当前实现形态）
   每实例配置=2 实例=2 观测并发=4 期望集群上限=2
   OK  确实越限（4 > 2），证明进程内状态无法约束集群配额
2) 持久化 counter
   集群配置=2 实例=2 观测并发=2 lease 行=2
   OK  未越限（2 <= 2）
PASS
```

**反证是关键**：只测「counter 形态并发=2」不能证明 limiter 有问题——可能只是用例
没构造出并发。必须同时测两种形态并断言 limiter **确实越限**（4 > 2），
才能说明集群层是必要的。

## 修复：两层分工

| 层 | 职责 | 为什么不能互相替代 |
|---|---|---|
| 进程内 `ResourceLimiter` | 排队与公平（事件循环上等待、按租户 aging、有界拒绝） | 持久化计数行做不到排队——它只能在事务里快速尝试 |
| 集群级 counter | 跨实例总量 | 进程内状态无法约束其他实例 |

顺序：**先进程内（本地排队/公平）→ 再集群级（总量）**。等待发生在事件循环上，
不占工作线程，也不在数据库事务里阻塞。

### 渐进引入

`try_acquire_cluster` 三态：`None` = 未登记 → **不限制**（未 bootstrap 集群层的
部署行为与改动前逐字一致）；`True` = 已占用；`False` = 全集群已满 → 释放本实例
名额并给出与进程内拒绝**同一个**有界错误码（503 `AGENT_EXECUTION_BUSY`）。

### 归还路径

| 平面 | 取得 | 归还 |
|---|---|---|
| `agent_provider` | 建连阶段前 | `finally`（建连结束即归还，不跨整个流） |
| `agent_tool` | 派发前 | done-callback + 未派发分支（两处） |

归还从**事件循环**调度到工作线程（`_release_cluster_slot_soon`）：done-callback
在事件循环上执行，同步 DB 工作会重演 09-30 的连接池饥饿。

## 关键边界（诚实声明）

1. **provider 的集群名额只覆盖建连阶段**，不覆盖整个流。上游**并发流数**本身仍不受
   本层限制——要限制它需要流级配额，属 `10-04-provider-reliability-boundaries`。
2. **本探针用「两个独立 limiter 实例」模拟两个进程**，不是真的起两个进程。
   进程隔离带来的额外因素（独立内存、独立事件循环、崩溃独立）未验证。
3. **控制面端点不取任何名额**（heartbeat/lease/settle/cancel/context/health），
   这是保留余量的机制；但「执行面满载时控制面 p95 有界」需要真实负载矩阵，
   属 `10-04-multitenant-load-acceptance`。
4. **AC-5（Assistant/Steward 分进程）未做**：`FG_AGENT_ROLE=both` 时两者仍共享
   进程、event loop、HTTP client 与 heap。

## 证据等级

**L3（原型）**：真实 PostgreSQL 多连接、真实跨实例配额竞争、含反证。
**不是**真实双进程部署或真实业务 schema 上的验证。
