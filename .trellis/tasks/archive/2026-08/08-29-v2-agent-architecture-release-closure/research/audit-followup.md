# V2 Agent 架构收口复审证据

## 1. 复审目标

核对当前工作树与两轮 Trellis V2 任务、`.trellis/spec/` 以及 Obsidian familygraph 00–08 设计文档，回答三个问题：

1. 已修复的整改是否真正闭环；
2. Assistant/Steward 到底使用哪一层 Pi 能力；
3. 哪些差异仍构成发布阻断，后续任务应如何落地。

## 2. Pi 运行时结论

| 层 | 责任 | 当前证据 |
|---|---|---|
| `pi-ai` | Provider、模型 literal、OpenAI-compatible stream adapter | `agent/package.json:18-20`、`agent/src/session.ts:26,99-119,211-214` |
| `pi-agent-core` | Agent loop、tool dispatch、事件 | `pi-coding-agent` SDK 内部依赖；由 `createAgentSession` 间接调用 |
| `pi-coding-agent` | Session、Extension、ResourceLoader、Settings/Session manager | `agent/src/session.ts:19-25,216-265` |
| FamilyGraph | FastAPI 身份、scope、VisibilityPolicy、关系/FSM、RAG、审计、数据库 | `.trellis/spec/architecture.md`、Obsidian 00/02 |

Assistant 的真实链路是：

```text
SidecarWorker -> buildRunSession -> createAgentSession
  -> AgentSession.prompt -> pi-agent-core loop
  -> versioned FamilyGraph tool -> FastAPI domain service
  -> pi-ai openai-completions -> approved Provider
```

## 3. Steward 现状与目标差异

| 项 | 设计要求 | 当前实现 | 判断 |
|---|---|---|---|
| 身份 | `space_id + job_id + policy_version` 系统 Agent | `StewardJob` 有空间作业和 policy/checkpoint | 基础模型存在 |
| 关系/推荐真值 | 确定性 engine，LLM 不能改结构结论 | Python `run_steward_job()` 执行派生、冲突和推荐 | 方向正确但未接 Pi 运行模型 |
| Pi 运行 | sidecar 运行受限 Steward loop | 只有 `steward_ping`；无专用 prompt/context/生产入口 | 未完成 |
| 队列 | 每空间一个 Steward Job | `StewardJob` 与 generic `AgentJob(kind=steward)` 并存 | P1 架构风险 |
| Web/私密数据 | 无 Web、不读 private | Steward service 读取当前空间投影；Pi 侧无正式入口 | 需补负向 E2E |

## 4. 关键发布阻断证据

### P1 安全/协议

1. Graph node filter 将不可见节点标为 `LEVEL_NONE`，但边过滤只判断 ID 是否在 `levels`；随后完整序列化 `from_user/to_user/label`（`backend/app/api/graph.py:71-85,142-160`）。
2. Internal router 挂在公开 FastAPI app，Compose 发布 `8000:8000`（`backend/app/main.py:148-150`、`docker-compose.yml:15-24`）。
3. Provider 配置可经 FastAPI 解密进入 projection，但 sidecar 仍承担实际 Provider egress；readiness、usage、rotation 和统一错误边界不足（`agent/src/session.ts:136-151`）。
4. 后端关系工具 registry 最高版本为 2，sidecar `TOOL_VERSIONS` 固定为 1；台账写入 `spec.version` 后，合法重放可能被视为版本冲突（`backend/app/services/agent_tools.py:105-126,409-457`、`agent/src/tools.ts:34-49`）。
5. 工具副作用采用“查台账→执行→插台账”，并发请求可在唯一约束之前共同执行。
6. 导出 envelope 是自制 XOR+HMAC，不是成熟 AEAD，也没有 key id/rotation；`open_export_file()` 在解密前记录下载消费（`backend/app/utils/secretbox.py:98-134`、`backend/app/commands/data_rights.py:320-350`）。
7. Pi coding-agent 的 extension runner 会吞掉 `before_provider_request` 异常并继续旧 payload（`pi-coding-agent/dist/core/extensions/runner.js:776-806`）；当前真正的阻断来自 sidecar 直接调用 `policyGuard.beforeProviderRequest`（`agent/src/session.ts:197-214`），两者不能混称。

### P1/P2 数据、Web、缓存

1. RAG candidate 只校验消息属于账号，未校验来源 session 的 space（`backend/app/services/memory_rag.py:182-213`）。
2. Web 先解析 DNS 再由 httpx 独立连接，存在 TOCTOU；fetch 固定使用 `research` policy（`backend/app/services/controlled_web.py:209-243,521-558`）。
3. Provider stream error 的文本被截断但未统一 redaction，可能进入日志/settle（`agent/src/worker.ts:253-285`）。
4. 前端 members/spaces/actionCards store 缺 generation/abort，旧请求可能跨登出或切换回写。
5. 确定性 Steward 执行器和测试存在，但缺少与 canonical `StewardJob` 绑定的生产 scheduler/worker 入口。
6. `ruff format --check backend` 当前仍失败；guga 正文成功、375px 人工、空库恢复、FTS/SSE/优雅停机 E3 仍未提供。

## 5. 证据等级与关闭规则

- E0：设计声明，不关闭 AC。
- E1：代码或单测存在，只能说明实现线索。
- E2：当前 commit 可重跑命令、退出码、数量和关键断言，可关闭局部 AC。
- E3：空库 Compose、跨进程 E2E、恢复演练和安全对抗产物，才可关闭发布级 AC。

P0/P1、跨空间、网络、导出、备份恢复和 Steward 运行边界至少 E2；发布门禁和恢复要求 E3。

## 6. 研究来源

- `.trellis/tasks/08-28-v2-audit-remediation/research/audit-baseline.md`
- `.trellis/tasks/08-28-v2-audit-remediation/research/verification-protocol.md`
- `.trellis/spec/architecture.md`
- `.trellis/spec/backend/agent-runtime.md`
- `.trellis/spec/backend/steward-action-card.md`
- `/Users/lyston/Obsidian/lyston/Codex/项目与服务/familygraph/00 FamilyGraph v2 Agent 系统总体架构与设计决策.md`
- `/Users/lyston/Obsidian/lyston/Codex/项目与服务/familygraph/02 FamilyGraph v2 Pi Runtime 与领域工具安全设计.md`
- `/Users/lyston/Obsidian/lyston/Codex/项目与服务/familygraph/04 FamilyGraph v2 Steward、领域事件与 ActionCard 设计.md`
- `/Users/lyston/Obsidian/lyston/Codex/项目与服务/familygraph/05 FamilyGraph v2 Memory、RAG 与 Policy Guard 设计.md`
- `/Users/lyston/Obsidian/lyston/Codex/项目与服务/familygraph/06 FamilyGraph v2 Web、SSE、部署与运行治理设计.md`
