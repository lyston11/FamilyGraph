# Controlled Web 规范（V2.6）

> 权威来源：`backend/app/services/controlled_web.py`、`app/api/controlled_web.py`、`app/schemas/controlled_web.py`、`app/models/controlled_web.py`、迁移 0015。任务工件：`.trellis/tasks/08-26-v2-6-controlled-web/`。

## 能力边界

- 受控联网只提供版本化 `search_web` 与 `fetch_approved_page`；**不提供**任意 HTTP client、浏览器自动化、文件下载执行或登录态访问。
- Agent sidecar 永不直接打开 socket：所有 egress 发生在 FastAPI 域网关（`services/controlled_web.py`），sidecar 通过 internal tool dispatch 调用。
- **双侧工具声明**：sidecar `agent/src/tools.ts` 必须注册 `search_web`/`fetch_approved_page`（与后端 `agent_tools.py` 同名同 schema）；否则后端披露后 `buildRunSession` 的 fail-closed 检查会拒绝整个 run。
- 外部网页内容永远是 `trust=external` 的不可信资料，**不能**成为 SourceFact、Memory 或工具指令；用户若要沉淀须另行走 MemoryCandidate 确认流程。
- Steward 不拥有 Web 工具：web 工具 `min_kind="assistant"`，steward 的 `default_allowlist("steward")` 与 `check_scope` 双重门禁排除它们（AC-W6）。

## 双层开关与工具披露

- 平台总开关 `CONTROLLED_WEB_ENABLED`（config.py，默认 **false**）+ 空间 `WebSpaceConfig.enabled` + 平台 `WebPlatformConfig.enabled`，三者皆开才可用。
- `agent_tools_enabled(db, account_id, space_id)` fail-closed：任一缺失/关闭返回 False。
- **披露合同**：`default_allowlist` 的静态遍历**排除** `search_web`/`fetch_approved_page`，仅在 `agent_tools_enabled` 返回 True 时显式加入。这保证关闭时模型永远看不到工具（AC-W1），不靠提示词隐藏。
- `default_allowlist` 的 db 范围参数向后兼容：无 db 时返回静态工具集（协议 fixture / 测试用），不披露 web 工具。
- 空间配置只能收窄平台策略（max_results/bytes/rate 取 min；denied_domains 不可被空间覆盖）。

## Egress / SSRF

- `_validate_public_url` 在每次 DNS resolve 后校验 IP：拒绝 loopback / RFC1918 / link-local（169.254.x，含云 metadata）/ reserved / multicast / unspecified；拒绝非 http/https、凭据 URL、非标准端口。**返回已验证 IP 集合**；`_fetch_bytes`/`_provider_search` 经 `_PinnedTCPBackend` 只连接该集合（连接层 DNS 钉扎，TOCTOU 关闭；TLS SNI/证书校验仍用原域名）。
- `_ensure_allowed_domain` 强制平台 allowlist（host 或后缀匹配）；denied 优先。
- fetch 不跟随 redirect（`follow_redirects=False`）；content-length 与流式累计字节双重上限（`WEB_FETCH_TOO_LARGE`）。
- provider endpoint 也走 `_validate_public_url` + allowlist（平台自管，但仍校验）。

## approved token

- `search_web` 成功结果签发短期 token（`CONTROLLED_WEB_TOKEN_TTL_SECONDS`，默认 300s），绑定 account/space/url/domain，`token_hash` 唯一；行上持久化签发用途 `use_case`（migration 0019），`fetch_approved_page` 按该用途取 policy（非法值回退 research），citation 返回体携带 `use_case`。
- `fetch_approved_page` 用 CAS `UPDATE ... WHERE used_at IS NULL` 一次性 claim，在 egress 之前完成——并发调用无法重复使用同一 token。
- token 不可被其他 account 使用；过期 token 返回 `WEB_APPROVAL_EXPIRED`。

## query PII / secret 最小化（AC-W3）

- `_sanitize_query` 在 `_check_quota` **之前**执行：被拒查询不计入用量、不消耗配额、不发给 provider。
- fail-closed 而非脱敏（部分泄露也是泄露）：检测到居民身份证、电话、邮箱、secret token（keyword+长 opaque）、32+ hex blob、masked 占位符、聚集 CJK 住址 token → `WEB_QUERY_BLOCKED`。
- 审计 `query_hash` 用原 query 哈希（可关联，不存明文）；provider 收到的是 sanitized 后的 query。

## 配额、审计与引用

- `_check_quota`：每分钟成功请求数 + 月度预算（`cost_cents`，成功计费）；超限返回 `WEB_RATE_LIMITED` / `WEB_BUDGET_EXCEEDED`。
- `WebRequestUsage` 只存 hash 与标量用量，**不存** raw query/payload；`detail_json` 不得泄露明文。
- `WebCitation` 记录 url/title/excerpt/content_hash/fetched_at/trust=external，绑 run；浏览器端点 `/api/web/spaces/{id}/citations/{id}` 可查。
- 所有 search/fetch/config 变更写 `audit_log`（actor/space/tool/决定/域/用量），不记敏感 query。
- **引用传递**：sidecar `RunEventBuffer` 从 `fetch_approved_page` 工具结果提取 citation（仅 `trust=external`），附加到下一个 `message.assistant_added` 事件的 `web_citations` 字段；前端 store 解析后经 `WebCitationList` 展示（外部来源与本地家谱引用明显区分，AC-W4）。

## 错误码

新增 `errors.py`：`CONTROLLED_WEB_DISABLED`、`WEB_SPACE_DISABLED`、`WEB_PROVIDER_UNAVAILABLE`、`WEB_PROVIDER_INVALID_RESPONSE`、`WEB_DOMAIN_NOT_ALLOWED`、`WEB_URL_INVALID`、`WEB_SSRF_BLOCKED`、`WEB_APPROVAL_INVALID/EXPIRED/USED`、`WEB_RATE_LIMITED`、`WEB_BUDGET_EXCEEDED`、`WEB_FETCH_TOO_LARGE`、`WEB_QUERY_BLOCKED`、`WEB_CITATION_NOT_FOUND`、`WEB_TOOL_DISABLED`。

## 部署与恢复

- `httpx` 是生产依赖（`pyproject.toml` 主 dependencies），不是 dev-only——`controlled_web.py` 生产代码直接 import。
- Compose：`CONTROLLED_WEB_ENABLED` 默认 0；api/agent 均设 `stop_grace_period`（优雅停机）；agent 无 DB/uploads mount，仅 internal network。
- `app/backup.py` 的 `verify_restore` 覆盖 V2 真源表（agent_sessions/runs/events/messages、memories、rag_documents/chunks、action_cards、source_facts、domain_events）并校验 `rag_chunks_fts` 与 active 投影自洽。

## 验证约定

- 测试（`tests/test_controlled_web.py`）：默认关闭、双层开关、SSRF（loopback/RFC1918/metadata/端口/凭据）、allowlist 过滤、token 一次性/过期/跨账户、配额/预算、usage 不存明文、PII/secret 拒绝、工具披露镜像策略。
- 网络相关用 monkeypatch 隔离 DNS/provider（`_validate_public_url`/`_provider_search`/`_fetch_bytes`）；SSRF 专用测试调用真实 `_validate_public_url`。
- `SessionLocal` 配置 `autoflush=False`：service 层测试在 `search_web` 后查 ORM 前必须 `db_session.commit()`/`flush()` 才能看到 pending token 行。
