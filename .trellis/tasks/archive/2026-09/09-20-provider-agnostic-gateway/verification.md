# 验收记录：去除受控 profile 门禁

状态：**全部通过**。验收时间 2026-09-20。

- 产物提交：`07ece78`（分支 `feat/09-20-provider-agnostic-gateway`）
- 无 Alembic 迁移（`kind` 的 CHECK 约束未变，已核实）

## 交付物

| 文件 | 改动 |
|---|---|
| `backend/app/services/agent_provider.py` | 删 9 个 `STANDARD_*` 常量；`provider_profile_error` 重写为结构性校验；`_provider_runtime` 缺 api 时 fail-closed |
| `backend/app/config.py` | 删 `AGENT_PROVIDER_STANDARD_PROFILE_ONLY` |
| `backend/app/api/admin_agent.py` | 两处 422 文案改中性；合并重复的 base_url 前置检查到统一闸门 |
| `backend/app/schemas/agent.py`、`services/steward_assist.py` | 注释去供应商点名 |
| `backend/scripts/steward_e2e.py` | 真实模式改用环境变量描述上游（`STEWARD_E2E_PROVIDER_*`） |
| `backend/tests/conftest.py` | 删 `_reset_synthetic_provider_gate` fixture 与三处开关赋值 |
| `backend/tests/test_agent_{provider,admin_providers}.py` | 改写两个「只接受 liu-dada」测试 |
| `system-admin-frontend/.../AgentProviderAdminView.vue` | 快捷预设改通用协议形态；占位符去具体模型 |
| `system-admin-frontend/tests/agent-provider.view.spec.ts` | 预设断言改写 |
| `frontend/.../SpaceModelSettingsPanel.spec.ts` | 合成数据去供应商名 |
| `agent/src/{client,session,tools}.ts` | 注释去供应商点名 |
| `docker-compose.yml`、`README.md` | 门禁描述改写为结构性校验 + 云同意 |
| `.trellis/spec/backend/agent-runtime.md` | 合同改写（含 `local` ≠ 数据不出网的说明） |

## 逐条验收证据

### AC1 任意第三方 Provider 可注册 ✅

在隔离环境（`DATA_DIR=/tmp/fg-ac1`、独立端口 18000/18001/18002、全新密钥、全新迁移）
跑新代码，经**真实管理 API** 注册一个与 liu-dada 完全无关的 Provider：

```
POST /admin-api/v1/agent/providers
{"name":"buddy2api","kind":"openai_compatible","api":"openai-completions",
 "base_url":"http://100.71.18.78:8787/v1","allowed_models":["workbuddy/gpt-5.4"],
 "secret":"sk-local-test-not-real"}

→ HTTP 201
{"id":1,"name":"buddy2api","kind":"openai_compatible","api":"openai-completions",
 "base_url":"http://100.71.18.78:8787/v1","has_secret":true,
 "allowed_models":["workbuddy/gpt-5.4"],"enabled":true, ...}
```

对照：`name=x5` + `api=openai-responses` + `https://api.other-vendor.com/v1` +
`allowed_models=["some-model"]` → **HTTP 201**（第二个第三方也可用）。

### AC2 云同意为真时解析为 allowed ✅

```
cloud_allowed=True  → allowed   reason=None provider=buddy2api model=workbuddy/gpt-5.4 api=openai-completions
```

### AC3 云同意为假时仍被拒 ✅（删门禁未旁路云同意）

```
cloud_allowed=False → denied_cloud_forbidden  reason=cloud_not_allowed  provider=None model=None
```

这是本任务最关键的一条：数据能否离开本机的决定权仍在空间所有者手上，
没有因为删除供应商白名单而丢失。

### AC4 结构性校验拒绝非法行 ✅

经真实 API（HTTP 422）：

| 用例 | reason |
|---|---|
| 缺 `base_url` | `provider_base_url_required` |
| `base_url` 非绝对 URL（`api.example.com/v1`） | `provider_base_url_invalid` |
| `base_url` 非法 scheme（`ftp://`） | `provider_base_url_invalid` |
| 非法 `api`（`anthropic-messages`） | schema 层 `literal_error`（进业务层前即拒） |

另在隔离库对 `provider_profile_error` 做了 12 例穷举：

```
第三方合规供应商        通过
模型在 allowlist 内     通过
模型不在 allowlist      拒绝   model_not_allowed
缺 base_url             拒绝   provider_base_url_required
base_url 为空串         拒绝   provider_base_url_required
base_url 非绝对         拒绝   provider_base_url_invalid
base_url 非法 scheme    拒绝   provider_base_url_invalid
协议非法                拒绝   provider_api_not_allowed
allowlist 为空          拒绝   provider_model_allowlist_empty
local 不校验 base_url   通过
云端点 https            通过
云端点 tailnet          通过
```

### AC5 单一供应商残留零命中 ✅

```
grep -rn "liu-dada\|AGENT_PROVIDER_STANDARD_PROFILE_ONLY\|STANDARD_PROVIDER_NAME\|STANDARD_BASE_URL\|受控的 liu" \
  backend/app backend/scripts system-admin-frontend/src frontend/src agent/src README.md docker-compose.yml
→ 零命中
```

`.trellis/spec/backend/agent-runtime.md` 仅剩一处 `liu-dada/gpt-5.6-sol`，位于
**延迟统计的历史记录**（记录 2026-09-18 对本地 Pi 约 1.2 万条消息的测量），
属于如实的历史证据，不是平台假设，故保留。

### AC6 管理后台无供应商字样 ✅

```
grep -rl "liu-dada\|gpt-5.6-sol" system-admin-frontend/dist/
→ 零命中
grep -o "OpenAI 兼容" dist/assets/AgentProviderAdminView-*.js
→ OpenAI 兼容（新的中性预设已进产物）
```

视图测试改为断言 `quick-provider-openai-compatible` 与 `quick-provider-ollama`
存在，且通用预设**不**替用户填 name/base_url/models。

### AC7 既有安全合同未回归 ✅

```
pytest -k "secret or snapshot or revive or egress or leak or has_secret or key"
→ 8 passed

全量后端套件 → 1818 passed, 3 skipped
```

覆盖：密钥只写不读（响应仅 `has_secret`）、`resolve_for_run` 快照不可复活语义、
网关出站审计、sidecar 不持有 api_key。

### AC8 定向检查全绿 ✅

| 检查 | 结果 |
|---|---|
| `ruff check .` | All checks passed |
| `ruff format --check .` | 414 files already formatted |
| `mypy app` | Success: no issues found in 208 source files |
| `pytest`（全量） | **1818 passed, 3 skipped**（408s） |
| `system-admin-frontend` lint / type-check / test / build | 全绿（16 files / 127 tests） |
| `frontend` lint / type-check / test / build | 全绿（74 files / 816 tests） |
| `agent` build（tsc） | 通过 |

## 关键安全分析（设计时核实，非事后）

### `compat_json` 未成为新的绕过通道

原白名单要求 `compat_json == {}`。删除该要求前，核对了 pi-ai 的
`OpenAICompletionsCompat` / `OpenAIResponsesCompat`
（`agent/node_modules/@earendil-works/pi-ai/dist/types.d.ts`）：全部字段都是
**行为开关**（`supportsStore`、`maxTokensField`、`thinkingFormat`、
`sendSessionAffinityHeaders`、`supportsStrictMode` 等），**不存在** `headers`、
`baseUrl`、`endpoint`、`proxy` 一类字段，无法重定向出口主机或注入任意请求头。
`openRouterRouting` / `vercelGatewayRouting` 仅在 baseUrl 本就指向那些网关时生效。

结论：删除该要求可接受，出站目标仍严格由 `target = base_url + api_path` 构造。

### 权限面变化如实记录

删掉白名单后，**平台管理员获得了把推理流量指向任意 HTTP(S) 端点的能力**。
这是「支持任意供应商」的固有含义。补偿控制：动作仅限 `/admin-api`（独立 JWT 域、
8002 listener、无公网路由）；每次注册/编辑写 `admin_access_audits`；空间所有者仍
必须显式同意云执行；每次出站一条 `agent_provider_egress` 审计。

### `local` 的语义风险已写入规范

`kind=local` 在 `resolve_for_space()` 里提前 return，**不检查 `cloud_allowed`**。
本机网关完全可能把请求转发给外部厂商，因此 `local` 描述的是**端点可达性**，
不是「数据不出网」的承诺。本任务不改该既有语义，但在代码注释与
`agent-runtime.md` 中明确写出，避免再次被当作绕过云同意的手段。

## 环境复原

隔离测试全程使用 `/tmp/fg-ac1`（独立 DATA_DIR、独立端口、全新密钥），
**未触碰开发库与线上库**：

```
清理：/tmp/fg-ac1 及其日志、token、临时 JSON 已删除；app.serve 进程已停
开发库：size=34787328、providers=1 行（未变）
线上库：providers=1 行、users=51（未变）
```

## 未运行的高成本检查及原因

- **未跑 `scripts/steward_e2e.py`**（真实模型 E2E）：需要真实上游密钥并会产生
  真实模型费用；该脚本本次只做了「去掉对已删常量/开关的引用」的机械修改，
  已通过 ruff + mypy 与其 import 路径检查，且 `pytest` 全量通过覆盖了它依赖的
  服务层。真实调用属部署/运维动作，建议在下次需要时单独执行。
- **未跑 `scripts/frontend-api-smoke.sh`**：它启动隔离 DATA_DIR 的三 listener 并
  跑家庭/后台 API 矩阵；本次改动集中在 admin 的 Provider 治理路径，已由
  `test_agent_admin_providers.py` 与 1818 项全量后端测试覆盖。
- **未做真实浏览器人工核对**：AC6 以「构建产物零命中 + 视图单测断言中性预设 +
  前端套件全绿」作为证据。若需要截图级证据，建议另立小任务。
- **未在线上/开发环境部署本次改动**：本任务只完成代码与验收；部署是显式动作
  （`bash scripts/deploy-prod.sh`），由用户决定时机。

## 回滚

- 单点回滚：恢复 `provider_profile_error` 白名单实现 + 恢复 9 个常量 +
  恢复 `AGENT_PROVIDER_STANDARD_PROFILE_ONLY=True` + 恢复两处 422 文案与前端预设。
- 无迁移，故无数据回滚需求。回滚后已注册的第三方 Provider 会被判
  `POLICY_DENIED`（fail-closed），不会静默继续使用。
