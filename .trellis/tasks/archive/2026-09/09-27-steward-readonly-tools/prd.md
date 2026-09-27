# S3 Steward 只读工具集实现

## Goal

按 Steward child run 设计补齐受限只读工具集，使 Steward 模型可以在当前空间、可选 viewer 投影和当前已授权证据范围内查询信息，同时保持 Assistant 工具与 Steward 工具的注册、allowlist、授权和审计隔离。

本任务包含设计落地和代码实现，不包含 S5 删除 in-process carrier、不启用生产开关、不新增写入工具、不开放任意 Web 工具。

## Requirements

### R1 工具分层

提供三类、全部只读的 Steward 工具：

- **空间级**：读取当前 Steward attempt 所属空间的稳定、有限投影；不得接收或信任输入中的 `space_id`、actor、account、provider 或权限字段。
- **viewer 级**：仅在 run token 带有 `viewer_account_id` 且该账号仍是当前空间 active member 时可用；读取该 viewer 当前已发布的 PersonalFamilyView/term projection；缺少 viewer scope 时 fail closed。
- **证据级**：只读取当前空间、当前发布代/当前 attempt 投影允许引用的结构化证据；返回 fact/relation id、kind、revision、方向和安全 reason code，不返回原始姓名、masked 值、私人 Session/Memory/RAG 文本或跨空间数据。

### R2 注册与协议

- 后端 registry 为每个 Steward 工具设置 `required_kind="steward"`，版本为 `1`，输入 schema 为闭合对象 `additionalProperties=false`。
- sidecar `KindAdapter.toolNames()` 显式只返回 Steward 工具集合；不得使用 Assistant 工具的差集或默认继承。
- `open_child_run` / lease 返回的 `tool_allowlist` 与 registry 同源；run token、sidecar session、内部 execute endpoint 三层必须一致。
- canonical tool name 保持 `familygraph.*`；provider wire name 仅由 sidecar 做既有的点号映射，回到后端时恢复 canonical name。
- 未注册、错误 kind、未在 token allowlist、版本不符或输入越界均 fail closed，并写安全审计。

### R3 授权

- Steward tool execute 必须走 `_authorize_steward_run`、`StewardExecution` fence 和 run token claims；不能复用要求 `AgentSession` 的 Assistant 授权路径。
- scope 只取 token claims：`space_id`、可选 `viewer_account_id`、`steward_attempt_id`；客户端不得扩大 scope。
- viewer 级调用必须再次核验 active membership、space 绑定和发布视图绑定；viewer 撤权后立即拒绝。
- evidence 级调用必须核验 evidence/revision 属于当前空间和当前 attempt 可引用集合；不可见、不存在、跨空间目标统一为安全的不可用结果，不泄露枚举信息。
- `platform_operator`、space admin 或其他账号角色不能替代缺失的 viewer consent，也不能扩大 viewer scope。

### R4 数据边界

- 工具只能 SELECT/读取已有 Steward projection、published view、confirmed structure/evidence；不得写 SourceFact、Relation、Memory、RAG、TermUsage、Notification、ActionCard 或成员资格。
- 不允许 `record_term_usage`、`search_web`、`fetch_approved_page`、echo/probe 等 Assistant/诊断工具进入 Steward allowlist。
- 每次输出有界，使用现有安全 JSON 输出限制；不得返回 ORM 对象、原始 provider 内容、prompt、token、secret 或日志敏感字段。

### R5 测试验收

- 每个工具至少有正常、空/不可用、跨空间、缺 viewer、撤权、revision 不匹配和恶意额外字段用例。
- 后端验证 registry kind 门禁、token allowlist、Steward execute 授权、零写入和审计。
- sidecar 验证 adapter 工具集合、schema/版本、wire name 往返和未知工具拒绝。
- 变异测试删除任一 scope/fence/allowlist 检查时必须失败。
- 受影响 backend 测试、agent type-check/lint/test/build 全部通过；不以 mock 结果宣称生产启用或真实模型质量。

## Acceptance Criteria

- [ ] 空间级、viewer 级、证据级 Steward 只读工具均已注册并有明确输入/输出合同。
- [ ] Steward child run 能拿到非空且仅包含 Steward 工具的 allowlist；Assistant allowlist 不改变。
- [ ] Steward internal tool execute 不再因无 AgentSession 失败，且不能绕过 Steward fence。
- [ ] 任何客户端提供的 actor/space/viewer/evidence scope 字段都被拒绝或忽略（优先拒绝），scope 只来自 token 与服务端发布状态。
- [ ] 跨空间、不可见、viewer 撤权、过期/revision 漂移均 fail closed 且不泄露目标存在性。
- [ ] 工具执行无业务写入，审计不含原始输入、姓名、prompt、凭据或返回敏感正文。
- [ ] backend 和 agent 受影响质量检查通过，并记录未运行的高成本检查及原因。
- [ ] 规范文档补充 Steward 工具合同与 allowlist/授权不变量。
