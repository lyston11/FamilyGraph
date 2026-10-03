# 技术设计：Redis 协调与限流加速层

## 1. 职责边界

Redis 是可丢失、可重建的加速层，不是业务事实或 lease 真源。允许职责：tenant admission/token bucket、短缓存、scheduler wakeup/pub-sub、短期 circuit hint。禁止只在 Redis 保存 run 终态、attempt 结算、lease、授权或审计。

## 2. Key 与租户隔离

key 必须包含版本、环境、kind 和安全 tenant 标识：

```text
admission:{env}:{kind}:{account|space}:{class}
rate:{env}:{upstream}:{kind}:{tenant}
cache:{env}:{scope}:{revision}:{key}
wakeup:{env}:{queue}:{kind}
```

原始个人数据、prompt、凭据不能进入 key 或日志。每类 key 定义 TTL、最大值、失效、版本和 eviction 行为；token bucket 的消费必须有幂等/请求标识，不能因重试重复扣除而永久饿死租户。

## 3. 真源与降级

PostgreSQL 在 admission grant、lease、run/attempt 状态和 settlement 上最终裁决。Redis 只提供快速拒绝或提示；Redis 不可用时：control-plane 继续走 PostgreSQL 保留容量，普通 execution 回退 PostgreSQL 有界 admission 或 fail closed，不自动放宽配额。Redis 与 PostgreSQL 结果冲突时以后者为准并记录安全诊断。

不使用 Redis lock 替代 PostgreSQL CAS；pub/sub 丢消息可由周期扫描补偿。Redis 重启、TTL 提前/延后、重复/乱序消息和网络分区不能修改持久业务状态。

## 4. 故障与运维

定义 Redis unavailable/degraded/healthy 状态，不能把 Redis down 伪装成租户不存在或 provider 失败。健康检查只报告依赖状态，不泄露 key。连接池、超时、重连和内存 eviction 必须有上限，避免 Redis 客户端反过来占满 control worker。

## 5. 与 Agent/RAG 的边界

Agent token/fence/settle 仍由 backend/PostgreSQL 处理；RAG cache 必须带 revision/scope/visibility，命中后仍重新走授权投影；embedding/index work 不得依赖 Redis 中唯一的任务记录。
