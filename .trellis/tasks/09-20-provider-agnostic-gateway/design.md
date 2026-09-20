# 技术设计：去除受控 profile 门禁

## 1. 边界

**改**：`provider_profile_error()` 的判定依据（供应商白名单 → 结构性校验）；
清理单一供应商常量/文案/提示；规范同步；测试同步。

**不改**：`kind` 枚举、空间云同意语义、快照不可复活语义、网关出站收口、
密钥存储方式、重试预算、任何功能开关默认值。

**无 Alembic 迁移**：已核实 `agent_providers.kind` 的 CHECK 约束是
`kind IN ('openai_compatible','local')`，枚举未变。

## 2. 核心改动：门禁语义替换

### 现状

```python
def provider_profile_error(provider, model=None) -> str | None:
    if provider.kind == "local" or not config.AGENT_PROVIDER_STANDARD_PROFILE_ONLY:
        return None
    if provider.name != STANDARD_PROVIDER_NAME: return "provider_name_not_allowed"
    if provider.api != STANDARD_API: ...
    if (provider.base_url or "").rstrip("/") != STANDARD_BASE_URL: ...
    if model is not None and model != STANDARD_MODEL: ...
    if list(provider.allowed_models_json or []) != [STANDARD_MODEL]: ...
    if provider.context_window != STANDARD_CONTEXT_WINDOW: ...
    # …共 10 条白名单比对
```

调用点四处：

| 位置 | 作用 | 改法 |
|---|---|---|
| `admin_agent.py:143`（注册 POST） | 拒绝非受控 profile | 改为结构性校验 |
| `admin_agent.py:225`（编辑 PATCH） | 同上 | 同上 |
| `agent_provider.py:263`（`resolve_for_space`） | 改配置后运行期 fail-closed | 同上 |
| `agent_provider.py:704`（`_provider_runtime`） | 出站前最后一道 | 同上 |

### 目标

保留函数名与签名（四处调用点不变），把判定改为**结构性**：

```python
def provider_profile_error(provider, model=None) -> str | None:
    """结构性校验：Provider 行是否具备可安全出站的最小完整信息。

    这里刻意不再比对任何具体供应商/模型/端点。原先的供应商白名单使平台
    绑死在单一上游（上游停服即无法切换），且无法接入自有/私网兼容端点；
    数据是否可离开本机由空间级 cloud_allowed 决定（见 resolve_for_space），
    不由「是不是某个供应商」决定。
    """
    if provider.kind == "local":
        # 本机端点：base_url 可选（由运行环境决定本机协议地址）
        return None
    if provider.kind != "openai_compatible":
        return "provider_kind_not_allowed"
    if provider.api not in ("openai-completions", "openai-responses"):
        return "provider_api_not_allowed"
    base_url = (provider.base_url or "").strip()
    if not base_url:
        return "provider_base_url_required"
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "provider_base_url_invalid"
    if not list(provider.allowed_models_json or []):
        return "provider_model_allowlist_empty"
    if model is not None and model not in list(provider.allowed_models_json or []):
        # 空间选的 model 必须在 allowlist 内 —— 这条原本由白名单比对顺带覆盖，
        # 删掉白名单后必须显式保留，否则越权模型名会一路走到出站
        return "model_not_allowed"
    return None
```

关键点：**`model in allowed_models` 这条必须显式保留**。原来的实现里
`resolve_for_space` 在 `provider_profile_error` 之后还有一次独立的
`if setting.model not in allowed_models` 检查，所以更该保留的是「结构性
合法性」，而不是重复 allowlist 检查；两者都留不冲突（纵深防御）。

### 为什么不是「加一个环境变量开关」

原设计刻意不给 env 逃生开关（避免把部署变量当安全开关）。本次是**语义替换**
而非放宽：白名单换成结构校验 + 依赖既有的空间云同意。因此仍不引入 env 开关，
`config.AGENT_PROVIDER_STANDARD_PROFILE_ONLY` 直接删除而非改成可配。

## 3. 直接影响面清单

| 文件 | 改动 |
|---|---|
| `backend/app/services/agent_provider.py` | 删 9 个 `STANDARD_*` 常量；重写 `provider_profile_error`；`_provider_runtime` 里 `api=resolution.api or STANDARD_API` 改为显式兜底常量或直接要求非空 |
| `backend/app/config.py` | 删 `AGENT_PROVIDER_STANDARD_PROFILE_ONLY` 及其注释 |
| `backend/app/api/admin_agent.py` | 两处 422 文案改为中性（描述结构性问题，不点供应商名） |
| `system-admin-frontend/src/views/AgentProviderAdminView.vue` | `QUICK_PROVIDER_PRESETS` 去掉 `liu-dada` 项；占位符去 `gpt-5.6-sol` |
| `frontend/src/...`（若有硬编码） | 同步清理（已初查未见，实现时确认） |
| `docker-compose.yml` | 删/改「首版云模型固定为…门禁由代码固定启用」注释 |
| `README.md:146` | 改写云 Provider 门禁描述 |
| `.trellis/spec/backend/agent-runtime.md:219` | 改写为结构性校验 + 云同意合同 |
| `backend/tests/test_agent_admin_providers.py` | 改写 strict-mode 测试 |
| `backend/tests/test_agent_provider.py` | 改写 strict-mode 测试 |
| `backend/tests/conftest.py` | 删除 `AGENT_PROVIDER_STANDARD_PROFILE_ONLY = False` 的三处赋值与 `_reset_synthetic_provider_gate` fixture（开关已不存在） |
| `backend/scripts/steward_e2e.py` | 删对该开关的赋值与 `provider_profile_error` 断言（保留真实模式仍用 liu-dada 作为**可选**默认，但不再是唯一允许值） |
| `system-admin-frontend/tests/*.spec.ts` | 预设相关断言更新 |

## 4. `_provider_runtime` 的兜底

```python
api=resolution.api or STANDARD_API,
```

`STANDARD_API` 被删后，这里需要等价兜底。该行的注释说明「缺 adapter 不是静默
降级 Responses profile 的许可；非法行在 profile 解析前已 fail-closed」。
因此改为：`resolution.api or "openai-responses"`（保留既有默认语义，但不再是
「标准 profile」措辞），或改为在 `resolution.api is None` 时返回 `None`
（更严格）。**选择返回 `None`**：既然门禁已不保证 api 一定存在，
`_provider_runtime` 不应猜协议；缺 api 的行由结构校验在解析期就拒（
`provider_api_not_allowed`），走到这里说明数据异常，fail-closed 更安全。

## 5. 安全影响分析

删除白名单会削弱一层「数据出境的对象控制」，必须用其他机制补位：

| 原保护 | 删除后由谁承担 | 是否充分 |
|---|---|---|
| 只能把数据发往已审查的单一供应商 | 空间级 `cloud_allowed`（owner 显式同意）+ 管理员只读监控 + 每次出站审计 | **是**：出站主体从「平台固定」变为「空间 owner 同意」，与新语义一致 |
| 协议被限定为 openai-responses（审查过的适配器） | `api` 的 `Literal` 校验（仍限两种 OpenAI 协议）+ sidecar 只支持这两种 | **是**：协议面未扩大 |
| 元数据（context_window/max_tokens/reasoning/modalities/thinking_levels）被钉死 | schema 的范围校验（`ge/le`）+ 运行期 `_TOKEN_CAP_FIELDS` 截断 | **部分**：管理员可填误导性数值，但只影响自身配置，且出站有字节与 token 上限 |
| compat_json 必须为空 | **无替代** | **风险点**：`compat` 会被透传给 pi-ai 适配器。需确认它是否能被用来重定向端点或注入头 |

**`compat_json` 是本次最需要盯的点**：原白名单要求 `compat_json == {}`。删除后
管理员可通过 `compat` 传入适配器级选项。

**已核实（2026-09-20，`agent/node_modules/@earendil-works/pi-ai/dist/types.d.ts`）**：
`OpenAICompletionsCompat` / `OpenAIResponsesCompat` 的全部字段都是**行为开关**
（`supportsStore`、`supportsDeveloperRole`、`maxTokensField`、`thinkingFormat`、
`sendSessionAffinityHeaders`、`supportsStrictMode` 等），**不存在** `headers`、
`baseUrl`、`endpoint`、`proxy` 一类字段 —— 既不能重定向出口主机，也不能注入任意
请求头。`openRouterRouting` / `vercelGatewayRouting` 仅在 baseUrl 本就指向
OpenRouter/Vercel 网关时生效，不改变 `target = base_url + api_path` 的构造。
`chatTemplateKwargs` / `chatTemplateArgs` 是受限的嵌套 payload，但字段名固定且仅
在特定 `thinkingFormat` 下发送。

结论：**删除 `compat_json == {}` 要求可接受**，不引入新的出口重定向或请求头注入能力。
不需要在结构校验里限制 compat 键集合。

## 5.1 权限面变化的如实说明

删掉白名单后，**平台管理员（system_admin）获得了把全部推理流量指向任意 HTTP(S)
端点的能力**。这是「支持任意供应商」的固有含义，不是实现缺陷。补偿控制：

- 该动作仅限 `/admin-api`（独立 JWT 域、8002 listener、无公网路由）。
- 每次注册/编辑都写 `admin_access_audits`（已有）。
- 空间所有者仍必须显式同意云执行（`cloud_allowed`），本任务不动该闸门。
- 每次出站恰好一条 `agent_provider_egress` 审计（已有），含 `provider_id`。
- 结构校验保证 `base_url` 是合法绝对 URL，排除空值/相对路径/非法 scheme。

## 6. 兼容性

- 既有的 `liu-dada` Provider 行（开发库与线上库各 1 行）在改动后仍满足结构校验，
  继续可用；`provider_name` 等字段不再被比对，行为不变。
- 既有 Run 的 `runtime_snapshot_json` 不受影响（快照字段结构未变）。
- 回滚：恢复常量与白名单实现即可；回滚后已注册的第三方 Provider 会被判
  `POLICY_DENIED`（fail-closed），不会静默继续使用。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| 误删 `model in allowed_models` 检查导致越权模型名出站 | 结构校验显式保留该分支；补单测断言 |
| `compat_json` 成为端点重定向/注入通道 | 实现时先核查 pi-ai 支持的 compat 键；必要时白名单键集合 |
| `conftest.py` 删除 fixture 后 strict 测试失去隔离 | 改写而非删除测试；新测试断言「第三方可注册」与「结构非法被拒」 |
| `steward_e2e.py` 引用被删常量而 import 失败 | 同步修改；该脚本不在默认测试路径，但要保证可运行 |
| 前端删预设后测试断言 `quick-provider-liu-dada` 失效 | 同步改测试，改为断言「自定义」与中性预设 |
| 规范里那条已锁定的合同被改动 | 这是用户明确要求的语义变更，规范随之更新（不是推翻既有决定） |
