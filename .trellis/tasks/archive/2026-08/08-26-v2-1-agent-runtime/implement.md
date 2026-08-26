# V2.1 Agent Runtime 实施计划

- [x] 建立 `agent/` TypeScript 工程、锁版本、质量脚本、最小容器和 health endpoint。（B2）
- [x] 添加 Agent Session/Message/Run/Event/Job/Provider 配置模型与 Alembic 迁移。（B1，迁移 0009）
- [x] 实现 durable queue lease/heartbeat/attempt/concurrency 和后台清理。（B1+B3 reaper/cancel_requested）
- [x] 实现 internal service/run token 与工具协议 schema/version registry。（B1；token typ 对齐修复见 notes）
- [x] 集成 Pi Session，明确只注册测试工具；订阅事件并批量追加 FastAPI。（B2+B4 合同对齐）
- [x] 实现 browser Session/Message/Run/SSE API、Idempotency-Key 与 Last-Event-ID。（B3）
- [x] 实现 ProviderGateway、云/本地配置、策略结果、无 fallback 错误。（B1 解析 + B3 治理端点/策略矩阵）
- [x] 更新 Compose/nginx 内部网络；确认 agent 无 DB/uploads mount。（B3；compose 真实 E2E 验证）
- [x] 补并发、重放、崩溃、token 篡改、Provider 失败、日志脱敏测试。（271 backend + 32 agent 用例）

> B4 合同对齐记录：真实 compose E2E 发现三处漂移并已修复——①lease 请求/响应形状（sidecar 对齐后端平铺 LeaseOut）；②service token 的 typ 声明（"agent_service"）；③settle/events/tools 字段名。教训：**双侧各自 mock 自测不能证明合同，compose 实联是验收必要环节**。

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
