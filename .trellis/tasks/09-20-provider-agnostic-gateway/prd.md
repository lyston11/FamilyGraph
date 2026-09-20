# 去除受控 profile 门禁：支持任意 OpenAI 兼容模型供应商

## Goal

移除把云 Provider 硬钉死在 `liu-dada/gpt-5.6-sol` 的代码门禁，使**官方或任意第三方、
只要支持 `/responses` 或 `/chat/completions` 的 OpenAI 兼容端点**都能注册使用；
同时清掉后端、前端、文档、规范与测试里对单一供应商的假设与提示语。

## 背景（实测现状）

`backend/app/services/agent_provider.py` 的 `provider_profile_error()` 对
`kind="openai_compatible"` 强制逐字段等于一组常量：

```python
STANDARD_PROVIDER_NAME = "liu-dada"
STANDARD_MODEL = "gpt-5.6-sol"
STANDARD_API = "openai-responses"
STANDARD_BASE_URL = "https://api.liu-dada.com/v1"
STANDARD_CONTEXT_WINDOW = 272_000
STANDARD_MAX_TOKENS = 60_000
STANDARD_REASONING = True
STANDARD_INPUT_MODALITIES = ("text", "image")
STANDARD_THINKING_LEVELS = ("low", "medium", "high", "xhigh", "max")
```

任何一项不符即 422 拒绝注册（`admin_agent.py` 两处）或在解析期 `POLICY_DENIED`
（`agent_provider.py` 两处）。`config.AGENT_PROVIDER_STANDARD_PROFILE_ONLY` 硬编码
`True`、无环境变量逃生开关，注释明确「不可由运行环境关闭」。

该门禁带来三个实际问题：

1. **供应商单一依赖**：liu-dada 停服/变更协议即无法切换到任何其他供应商。
2. **无法接入自有/私网端点**：本机已部署 Buddy2api（`127.0.0.1:8787`，OpenAI 兼容，
   同时提供 `/v1/responses` 与 `/v1/chat/completions`），经 Tailscale 从服务器
   实测可达（线上 api 容器 → `100.71.18.78:8080` 可达，MagicDNS 容器内可解析），
   但门禁不允许注册。
3. **只能靠冒充 `kind=local` 绕过**：而 `local` 在 `resolve_for_space()` 里
   提前 `return _allowed(...)`，**完全跳过 `cloud_allowed` 检查**，等于绕开
   空间所有者的「云执行同意」——语义不实（Buddy2api 实际会把数据转发到消费级
   AI 厂商，并非本机模型）。

## Requirements

### R1 删除单一供应商门禁

- `provider_profile_error()` 不再要求 name/model/base_url/协议等于任一固定供应商。
- 删除 `STANDARD_PROVIDER_NAME` / `STANDARD_MODEL` / `STANDARD_BASE_URL` /
  `STANDARD_API` / `STANDARD_CONTEXT_WINDOW` / `STANDARD_MAX_TOKENS` /
  `STANDARD_REASONING` / `STANDARD_INPUT_MODALITIES` / `STANDARD_THINKING_LEVELS`
  这组常量，以及 `config.AGENT_PROVIDER_STANDARD_PROFILE_ONLY` 开关。
- 管理 API 的两处 422 文案不再出现 `liu-dada` / `gpt-5.6-sol` / 「受控 profile」。

### R2 用结构性校验替代供应商白名单

门禁的价值在于「不让未经审查的端点静默承接用户数据」。删除白名单后必须补上
**结构性**校验，且这些校验是安全控制，不是可选项：

- `kind="openai_compatible"` 必须提供 `base_url`。
- `base_url` 必须是带 `http`/`https` scheme 的绝对 URL，且 host 非空。
- `api` 必须是 `openai-completions` 或 `openai-responses`（既有 `Literal` 已保证）。
- `allowed_models` 非空（既有 schema 已保证 `min_length=1`）。
- `context_window` / `max_tokens` 使用 schema 既有范围（既有 `ge/le` 已保证）。
- **`kind="local"` 不再需要 `base_url`**（沿用既有行为）。

### R3 保留并强化空间级云同意

- `cloud_allowed` 继续是唯一决定「用户数据可否离开本机」的空间级开关。
- 不再以 `kind` 旁路该开关：`local` 的语义收窄为「本机可达的端点」，
  且**仍不检查 `cloud_allowed`**（这是既有设计，本任务不改），但需要在
  规范中明确写清「`local` ≠ 数据不出网」，避免再次被误用为绕过手段。

### R4 移除面向用户与运维的单一供应商文案

- `system-admin-frontend` 的 Provider 治理页：删除 `liu-dada` 快捷预设或改为
  中性（不点名特定供应商），占位符不写死 `gpt-5.6-sol`。
- `docker-compose.yml`、`README.md` 中「云 Provider 门禁在代码中固定启用，
  只允许 `liu-dada/gpt-5.6-sol`」一类描述改为描述新的结构性校验。
- 前端模型设置面板（家庭端）若有 `liu-dada` 硬编码一并清理。

### R5 规范同步

- `.trellis/spec/backend/agent-runtime.md` 第 219 行那条「Provider profile 首版
  固定为 liu-dada/gpt-5.6-sol；代码门禁拒绝其他云 profile」必须改写为新的
  结构性校验 + 空间云同意合同。

### R6 不破坏既有安全与数据合同

- 不得放宽：Provider 密钥仍只存密文、响应永不含明文/密文（仅 `has_secret`）。
- 不得放宽：`resolve_for_run` 的快照不可复活语义、被拒决策对已入队 Run 不可变。
- 不得放宽：网关出站唯一收口、上游错误脱敏、每次出站恰好一条审计。
- 不得放宽：`sidecar` 不持有 api_key（仍由 api 容器解密后转发）。
- 不需 Alembic 迁移（`kind` 的 CHECK 约束未变，已核实为
  `CHECK (kind IN ('openai_compatible','local'))`）。

### R7 测试与验收

- 删除/改写断言「只接受 liu-dada」的两个测试：
  `test_strict_mode_accepts_only_canonical_liu_dada_profile`、
  `test_standard_liu_dada_profile_is_enforced_in_strict_mode`。
- 新增断言：任意合规第三方 Provider 可注册且可解析为 `POLICY_ALLOWED`。
- 新增断言：结构性校验拒绝（缺 base_url、非绝对 URL、非法 scheme）。
- 新增断言：`cloud_allowed=False` 时第三方云 Provider 仍被 `denied_cloud_forbidden`
  （证明没有因删门禁而丢云同意）。
- 前端受影响测试同步更新。

## Acceptance Criteria

- AC1：用一个**与 liu-dada 完全无关**的第三方 Provider（如
  `name=buddy2api`、`kind=openai_compatible`、`api=openai-completions`、
  `base_url=http://100.71.18.78:8787/v1`、任意模型名）经管理 API 注册成功（201）。
- AC2：该 Provider 在 `cloud_allowed=True` 的空间解析为 `POLICY_ALLOWED`。
- AC3：同一 Provider 在 `cloud_allowed=False` 的空间解析为
  `denied_cloud_forbidden`（云同意未被旁路）。
- AC4：`kind=openai_compatible` 缺 `base_url`、或 `base_url` 非绝对 URL/非法
  scheme 时注册被拒（422），错误码与文案不含任何供应商名。
- AC5：全局 `grep -rn "liu-dada\|gpt-5.6-sol\|AGENT_PROVIDER_STANDARD_PROFILE_ONLY"
  backend/app system-admin-frontend/src frontend/src` **零命中**（注释、文案、
  常量、校验逻辑全部清理）。
- AC6：管理后台 Provider 治理页在浏览器中可注册并显示第三方 Provider，无
  `liu-dada` 字样（人工或 Playwright 核对）。
- AC7：既有安全合同回归：密钥只写不读、`resolve_for_run` 快照不可复活、
  网关出站审计、sidecar 无 api_key 的既有测试全部通过。
- AC8：受影响的后端与前端定向检查全绿；未运行的高成本检查如实记录。

## Out of Scope

- 不改 `kind` 枚举本身（仍 `openai_compatible|local`），不新增「私网」第三类。
- 不改 `local` 旁路 `cloud_allowed` 的既有语义（只在规范中写明风险）。若要
  收紧，另立任务。
- 不实现 Anthropic/Gemini 等非 OpenAI 兼容协议（`api` 仍限两种 OpenAI 协议）。
- 不把 Buddy2api 正式接为线上默认 Provider（那是部署/运维动作，本任务只保证
  能力可用；是否启用由用户决定）。
- 不改任何功能开关默认值、不改重试预算、不改延迟相关配置。

## 回滚

- 单点回滚：`provider_profile_error()` 恢复原实现 + 恢复常量 + 恢复
  `AGENT_PROVIDER_STANDARD_PROFILE_ONLY=True` + 恢复两处 422 文案与前端预设。
- 无迁移，故无数据回滚需求；已注册的第三方 Provider 行在回滚后会被门禁判
  `POLICY_DENIED`（fail-closed，不会静默继续使用）。
