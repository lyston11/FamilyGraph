# 错误处理规范（初始规范 v0）

- 统一错误响应结构：`{"error": {"code": "MACHINE_CODE", "message": "用户可读文案", "detail": {...可选}}}`。
- 全局 exception handler 分类：HTTPException(业务) / ValidationError(422 保持 FastAPI 默认外壳但映射 code) / 未捕获异常(500 + 日志，不向客户端泄露堆栈)。
- 业务错误码常量表集中在 `app/errors.py`，如 AUTH_INVALID_CREDENTIALS、PIN_CHANGE_REQUIRED、RELATION_CYCLE_FORBIDDEN、VISIBILITY_MASKED。
- 认证失败永远返回同一文案"名字或 PIN 码错误"，不区分账号不存在/PIN 错误（防枚举）。
- 可见性遮罩不是错误：被遮罩字段返回 MASKED 标记结构而非抛错（前端渲染锁样式）。
- FSM 非法转换返回 409 + 当前状态信息；环检测拒绝返回 422 RELATION_CYCLE_FORBIDDEN。
- 服务端日志记录完整上下文，客户端响应永不包含内部细节。

## V2.5 Memory/RAG/Policy Guard（2026-08-26）

- `MemoryCandidate` 只能引用同一 `author_account_id` 的 `AgentMessage`；候选 `pending` 不创建 `RAGDocument`。确认是显式命令，scope 只接受 `private` 或带目标 `space_id` 的 `household`/`lineage`，高敏感内容不得共享。
- `RAGDocument` 只允许 `memory`（由确认命令创建）或白名单授权来源；检索先用 SQL scope/status/confirmation/sensitivity 过滤，再运行 `VisibilityPolicy`，最终响应只包含可引用的 `citation_handle`，不返回原始私人会话。
- `private` 记忆只有 Assistant 且作者本人可检索；Steward、其他账号、其他空间和未 active 成员均为空结果。撤销、删除、过期、Profile 删除和数据权利删除必须在同一事务中将文档与 chunk tombstone，失效数据不依赖 FTS 物理清理才停止命中。
- `ContextBuild`/`ContextBuildItem` 持久化 provider、policy version、source type/id、citation、trust 与排除原因；`ContextBuilder` 先做 scope/sensitivity/provider 决策再写追踪记录。`context` hook 只消费已预取的安全上下文，不查数据库。
- `BehaviorProjection` 只从 append-only `DomainEvent` 重建，允许保存空间限定的词条、冷却、纠正偏好和推荐质量；禁止键盘、鼠标、停留时长等泛行为采集。重建是显式操作，Steward 普通运行不清空刚写入的投影。
- Policy Guard 在工具结果、上下文、模型输出、provider 请求和持久化前后均 fail-closed：masked/跨 scope 内容阻断，`proposed`/`disputed`/`pending` 事实必须降级为 `[UNCONFIRMED FACT]`，敏感内容只允许 local provider；未知 hook 输入拒绝而不是放行。

### 可执行检查

```bash
cd backend && .venv/bin/python -m pytest -q && .venv/bin/python -m mypy app
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd agent && npm run type-check && npm run lint && npm test && npm run build
cd frontend && npm run type-check && npm run lint && npm test && npm run build
```

测试必须覆盖：相同文本的不同 scope 只返回有权文档；撤销/过期/删除后立即零命中；候选跨账号原消息引用被拒；private 不进入 Steward；masked/不确定事实阻断或降级；provider 请求含 masked 时拒绝；DomainEvent 重建投影与原事件一致。
