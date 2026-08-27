# FamilyGraph V2.6 Controlled Web：受控联网与发布治理

> 依赖：V2.0–V2.5 全部完成。本阶段默认关闭，可独立延后而不影响本地 Agent。

## Goal

在不把家庭 PII、秘密、内部 prompt 或任意 URL 访问权交给模型的前提下，为 Assistant 增加可关闭、可审计、带引用的受控搜索/网页获取能力，并完成 Agent sidecar、SSE、Provider、备份恢复和安全运行的发布门禁。

## Requirements

### WEB-1 能力与开关

- 平台总开关默认 false；空间 owner/admin 只能在平台允许后为本空间启用，普通 member 不能扩大能力。
- 首版只提供版本化 `search_web` 与 `fetch_approved_page`，不提供任意 HTTP client、浏览器自动化、下载执行或登录态访问。
- 工具是否披露给模型取决于本次 Run 的平台策略、空间开关、用户权限、Provider readiness 和 query sensitivity。

### WEB-2 Egress 与 SSRF

- 所有 URL 经过 scheme、host、DNS/IP、redirect、port、content-type、大小、时间和下载次数限制；拒绝 loopback、私网、metadata、file/data/javascript 等协议。
- 搜索 Provider 与 fetch adapter 使用独立服务凭据，密钥不进模型/浏览器；响应先按文本安全策略清洗。
- 默认不抓取用户提交的任意地址；fetch 只接受搜索结果签发的短期 approved URL token 或平台 allowlist。

### WEB-3 隐私与查询最小化

- before_provider_request 检查搜索词/URL/context，移除非必要姓名、出生、住址、联系方式、secret 和 masked 数据；高风险请求拒绝，不以“用户同意”绕过平台红线。
- Web 内容永远是 untrusted external data，不能成为 SourceFact、Memory 或工具指令；用户可另行创建 MemoryCandidate 并确认 scope。
- 联网结果不得进入 Steward 的自动推荐/关系真值；Steward 首版不拥有 Web 工具。

### WEB-4 引用与回答

- Assistant 对使用 Web 的事实显示来源 URL、标题、抓取时间和引用片段；本地家谱事实与外部事实在 UI 中明显区分。
- Provider/工具失败、内容被阻止或无可靠来源时明确说明，不伪造引用。
- 外部页面中的 prompt injection 按 V2.5 trust/Guard 合同处理。

### WEB-5 配额、审计与滥用

- 每 user/space/provider 配置速率、并发、结果数、抓取字节和月度预算；达到限制返回稳定错误。
- 审计记录 actor、space、tool、策略决定、目标域、用量和结果状态，不记录完整敏感 query/payload。
- 失败/拒绝/SSRF/redirect 异常有安全事件与可观测指标。

### WEB-6 部署与恢复

- Compose 固定 api/web/agent 版本和内部网络；agent 无 DB/uploads mount，nginx 仅公开 API/SSE。
- 健康检查、优雅停机、Run lease 恢复、日志脱敏、事件保留/压缩、Provider secret 轮换均有操作说明。
- SQLite online backup 覆盖 Agent/Memory/RAG/ActionCard 表；恢复后重建可重建投影并校验消息/事件/SourceFact 行数与 FTS 完整性。
- Agent 或联网整体关闭时 v1/基础 v2 家谱功能保持可用。

## Acceptance Criteria

- [ ] AC-W1：全新安装联网默认关闭，模型看不到 Web 工具；平台+空间双开后才披露。
- [ ] AC-W2：loopback、RFC1918、metadata、恶意 DNS/redirect、超大/非文本响应全部被拒绝并审计。
- [ ] AC-W3：含姓名/生日/住址/secret/masked 值的查询按策略脱敏或拒绝，未经许可不会发给搜索/云 Provider。
- [ ] AC-W4：答案引用真实存在且能追溯；无结果/阻断/超时不伪造来源。
- [ ] AC-W5：外部 prompt injection 无法增加工具、修改 scope、写 SourceFact 或扩大 Memory。
- [ ] AC-W6：Steward 没有 Web tool；联网内容不参与确定性关系或自动推荐资格。
- [ ] AC-W7：配额、并发、预算、Provider outage、secret rotation 与 feature kill switch 可验证。
- [ ] AC-W8：空库 Compose E2E、SSE 断线、sidecar crash、backup/restore、FTS rebuild、全套质量门禁通过。

## Out Of Scope

- 不做任意浏览器、登录网页、文件下载执行、shell、MCP 或 unrestricted HTTP。
- 不让联网替代本地家谱数据，不给 Steward 联网能力。
- 不做真实生产数据迁移；仍以空库部署为准。

## Blocking Open Questions

无；搜索/fetch Provider 品牌在实现时选定，不改变安全合同。
