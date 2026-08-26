# V2.1 Agent Runtime 实施计划

- [ ] 建立 `agent/` TypeScript 工程、锁版本、质量脚本、最小容器和 health endpoint。
- [ ] 添加 Agent Session/Message/Run/Event/Job/Provider 配置模型与 Alembic 迁移。
- [ ] 实现 durable queue lease/heartbeat/attempt/concurrency 和后台清理。
- [ ] 实现 internal service/run token 与工具协议 schema/version registry。
- [ ] 集成 Pi Session，明确只注册测试工具；订阅事件并批量追加 FastAPI。
- [ ] 实现 browser Session/Message/Run/SSE API、Idempotency-Key 与 Last-Event-ID。
- [ ] 实现 ProviderGateway、云/本地配置、策略结果、无 fallback 错误。
- [ ] 更新 Compose/nginx 内部网络；确认 agent 无 DB/uploads mount。
- [ ] 补并发、重放、崩溃、token 篡改、Provider 失败、日志脱敏测试。

## 验证

```bash
cd agent && npm run type-check
cd agent && npm run lint
cd agent && npm test
cd agent && npm run build
cd backend && pytest
cd backend && mypy app
docker compose config
docker compose up --build
```

手工/自动 E2E：创建 Run → 中途断开 SSE → 继续执行 → Last-Event-ID 重连 → 只补发事件；重复提交同 Idempotency-Key 不产生第二 Run。

## 回滚

- Agent feature flag 默认关闭；sidecar/表/API 分提交。
- Runtime 失败时关闭 Agent 路由并停止 sidecar，Foundation 与 v1 功能继续运行；不可回退到 sidecar 直连 DB。
