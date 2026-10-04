# 技术设计：控制面与执行面故障域隔离

## 1. 资源平面

```text
control: heartbeat / lease / context / renewal / cancel / settle / health / recovery
assistant execution: model/tool account-scoped
steward execution: model/tool space-scoped
background: maintenance / RAG / indexing
```

每个平面至少有独立 limiter 和保留 worker/DB capacity。control 请求不得排队等待普通执行名额；health/metrics 另有最小诊断容量。

## 2. Backend

当前单 FastAPI 进程保留协议和授权边界，但为 control、execution、background 使用明确的 AnyIO/DB pool budget。PostgreSQL 后每个 pool 总和必须受 `max_connections` 预算约束。同步 DB 工作不得在事件循环上执行，长流不得持有 DB 事务。

## 3. Sidecar

短期同部署内按 `agent_kind` 分离 slots、poll、retry 和 HTTP client；长期评估 Assistant/Steward 分进程，使 event loop、heap、provider retry storm 和 graceful shutdown 故障域独立。分进程不改变 internal API、token claims、fence、settle 和 recovery。

## 4. 多实例

进程内 limiter 只作局部优化。PostgreSQL 的 lease owner、capacity grant、writer epoch 和 recovery 是跨实例真源；任何实例重启都能由 recovery 收回未完成 grant。Redis 不作为 lease 真源。

## 5. 失败语义

control pool 耗尽、worker shutdown、sidecar crash、重复 settle、取消和撤权都必须是幂等且可收敛的。未知执行结果遵守现有 unknown/保守计费合同，不自动重放可能已发送的请求。
