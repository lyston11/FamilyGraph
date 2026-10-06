# Gate 2 缺口 5 闭合：deadlock_timeout 对延迟预算的影响

## 实测（隔离 PostgreSQL 16）

```
服务端 deadlock_timeout = 1s
默认 timeout + 真实死锁 : 1.31s  (deadlock)
200ms timeout + 真实死锁: 0.51s  (deadlock)
默认 timeout + 无死锁    : 0.61s  (ok)
PASS
```

## 数值解释（与机制一致，不是巧合）

`deadlock_timeout` 度量的是「从**开始等待某把锁**到启动死锁检测」的时长。
探针在取到第一把锁后 `sleep(0.3)`，因此：

| 场景 | 计算 | 实测 |
|---|---|---|
| 默认（1000ms） | 0.3s 持锁 + 1.0s 检测 | 1.31s |
| 200ms | 0.3s 持锁 + 0.2s 检测 | 0.51s |
| 无死锁（对照） | 0.3s 持锁 + ~0.3s 等前者提交 | 0.61s |

即**死锁检测本身的开销约等于 `deadlock_timeout`**（默认 1.0s）。

## 结论（对 lease/settle 的影响）

1. **一次真实死锁会让被中止方多等约 1 秒**（默认配置）。这不是「立即发现」。
2. 该延迟可**会话级**调整：`SET LOCAL deadlock_timeout = 200` 把检测降到 0.2s。
   生产是否调小需与「误报检测开销」权衡——调太小会让正常的长等待被误判为死锁
   （PostgreSQL 会为此做额外的等待图检查）。
3. 因此：
   - lease/settle 的**延迟预算必须计入 ~1s 量级**，不能按毫秒设计；
   - 死锁**重试必须 bounded**（否则 1s × N 次会放大成秒级乃至十秒级停顿）；
   - 结合已实测的反向锁序（`lock-order-deadlock.md`），**根治方式是统一锁序**，
     而不是依赖检测+重试。

## 探针

`scripts/migration-proof/pg_deadlock_timeout_probe.py`

三个用例：默认 timeout + 真实死锁、200ms + 真实死锁、默认 + 无死锁（对照）。
第三个是对照组：若没有它，无法区分「死锁开销」与「固定开销」。

若死锁未触发（`label != deadlock`），探针报 FAIL 而不是通过——避免把
「未构造出交叉窗口」当作安全。
