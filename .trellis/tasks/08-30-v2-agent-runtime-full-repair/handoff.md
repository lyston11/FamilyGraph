# Handoff — V2 Agent Runtime Full Repair

## 当前状态

**代码修复完成，本地质量门禁全绿；任务保持 `in_progress`。** 唯一未关闭门禁是一次真实 `liu-dada/gpt-5.6-sol` 成功正文回显的外部证据。不得因 stub 或 JSONL 校验通过而提前归档。

## 运行时裁定

```mermaid
flowchart LR
  B[Browser] --> F[FastAPI public API]
  F --> Q[AgentRun/AgentJob]
  Q --> S[Node sidecar]
  S --> PCA[pi-coding-agent session]
  PCA --> PIA[pi-ai openai-responses adapter]
  PIA --> I[FastAPI internal provider proxy]
  I --> G[ProviderGateway: decrypt + policy + audit]
  G --> L[liu-dada / gpt-5.6-sol]
```

- 没有独立 `pi-sdk`；Assistant loop/session 归 `pi-coding-agent`，模型 wire 协议归 `pi-ai`。
- Steward 仍是后端确定性 worker，不能被 Assistant sidecar 租用。
- 云 profile 固定：`liu-dada`、`gpt-5.6-sol`、`openai-responses`、`https://api.liu-dada.com/v1`、reasoning、text+image、272000 context、60000 max tokens、五级 thinking。
- Provider key 只在后端 ProviderGateway 解密，绝不进入 projection、sidecar env、事件、日志或文档。

## 已完成

- profile/snapshot/provider_name 对齐与多层 fail-closed；Responses wire stub。
- credential-key policy guard、最终 provider guard、空/非法 body 拒绝。
- AbortController/Pi abort/stream signal 贯通；工具和 Provider proxy 取消竞态门禁；上游 client 异常关闭。
- Assistant/Steward 队列隔离、session 文本历史恢复、事件序列幂等。
- Graph/TermRegistry/工具 schema/consent/Controlled Web/Compose/readiness/secret hygiene。

## 复核证据

- Backend：560 pytest；ruff check/format；mypy 120 文件。
- Agent：12 个测试文件、78 tests；type-check/lint/build。
- Frontend（保留 dirty redesign）：233 tests；type-check/lint/build。
- Compose：`docker compose config --quiet` 通过；Trellis `task.py validate` 通过（implement 8/8、check 5/5）。

## 下一步（仅剩外部证据）

1. 确认 `AGENT_RUNTIME_ENABLED=1`，通过管理 API 注册上述非敏感 profile，并从受限 secret 注入 API key（不写入命令历史/日志）。
2. 运行一次 bootstrap→lease→context→Responses→settle 全链路；记录 provider、model、HTTP 状态、字节数、正文长度与失败码，全部脱敏。
3. 成功与失败证据均回写 `notes.md`、`implement.md`、本 handoff 和 `journal-1.md`，再将 `task.json.status` 改为 `completed` 并归档。

此前其他中转出现的 `503 service_busy` 属上游容量状态；不能把它当作本地 Pi adapter 协议实现错误，也不能以 guga/luna 作为本任务运行配置。
