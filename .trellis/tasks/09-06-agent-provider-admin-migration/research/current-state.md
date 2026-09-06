# Research: 现状核对（agent provider 治理迁移到系统管理员域）

- Query: 15 项事实核查（admin_app 鉴权 / 审计 / 旧端点引用面 / 前端两侧 / service / model / schema / 迁移 / 测试基建）
- Scope: internal
- Date: 2026-09-06
- 说明: 所有行号基于 2026-09-06 工作区（含未提交改动，git status 见 §4 末尾）。

## 1. admin_app 结构与鉴权

### 构建与 router 挂载（backend/app/main.py）

- `admin_app` 构建于 main.py:254，标题 "FamilyGraph Admin API"，与家庭 app 共享 `lifespan`（bootstrap/维护循环单次执行），不共享任何 router 或签发域。
- 三 listener 拓扑：app :8000（家庭）、internal_app :8001、admin_app :8002。端口定义在 backend/app/serve.py:33-35（`ADMIN_PORT = int(os.environ.get("ADMIN_API_PORT", "8002"))`）。
- router 挂载（main.py:259-264）：

```python
admin_app.include_router(admin_auth_router)          # prefix="/admin-api"（api/admin_auth.py:34）
admin_app.include_router(health_router, prefix="/admin-api")
admin_app.include_router(admin_read_router)          # prefix="/admin-api/v1"（api/admin_read.py:51）
admin_app.include_router(admin_governance_router)    # prefix="/admin-api/v1"（api/admin_governance.py:28）
```

- 各 router prefix：**admin_auth = `/admin-api`**（登录/密码/refresh，admin_auth.py:34）、**admin_read = `/admin-api/v1`**（admin_read.py:51）、**admin_governance = `/admin-api/v1`**（admin_governance.py:28）。
- 家庭 listener 对 `/admin-api/*` 返回普通 404（Starlette HTTPException，无 error 外壳，防存在性探测）：main.py:227-240。
- 旧 agent 治理端点挂载在**家庭 app**：`app.include_router(admin_agent_router, prefix="/api/admin/agent")`（main.py:208，import 在 main.py:25）。

### 鉴权依赖（backend/app/api/admin_deps.py）

- `AdminPrincipal = tuple[SystemAdmin, SystemAdminAccount]`（admin_deps.py:32）。
- `resolve_admin_principal(request)`（admin_deps.py:35-63）：Bearer token → `admin_security.decode_admin_token`（ADMIN_JWT_SECRET 独立签发域、iss/aud 校验）→ `load_account` → `admin.status == "active"` → `password_version` 比对；任一失败返回 None，不区分原因。
- `require_admin_principal`（admin_deps.py:66-76）：失败统一 `401 ADMIN_UNAUTHORIZED` + `ADMIN_SESSION_MESSAGE`（"管理员认证失败，请重新登录"，errors.py:216）。
- **`require_admin_ready`**（admin_deps.py:79-93）= admin 业务路由统一门禁：无效令牌 401；`password_must_change=true` 时白名单（password/refresh/logout）外一律 `403 ADMIN_PASSWORD_CHANGE_REQUIRED`。09-04 起 `/admin-api/v1` 全部路由经它进入。

### 受保护端点完整写法（admin_governance.py:35-57）

```python
@router.post(
    "/manager-applications/{application_id}/approve",
    response_model=AdminManagerApplicationOut,
)
def approve_manager_application(
    application_id: int,
    payload: AdminApplicationApproveRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> dict[str, object]:
    admin, _account = identity
    application = manager_applications.decide_manager_application_as_system_admin(
        session, application_id, decision="approve", note=payload.note,
        system_admin_id=admin.id, ip=_client_ip(request),
        endpoint="/admin-api/v1/manager-applications",
    )
```

## 2. 管理员域审计机制

**关键发现：admin 域既有审计不落 `audit_log`，落独立表 `admin_access_audits`。** `audit_log.actor_id` 是 `users.id` FK（models/audit_log.py:22-24），无法表达 system_admin 主体——models/admin_access.py:5-8 docstring 明确："admin_access_audits：独立于家庭 audit_log（其 actor_id FK 指向家庭 users，无法表达 system_admin 主体）"。PRD R1 写"审计落 audit_log（管理员域既有审计机制为准）"，design 需裁定采用 `admin_access_audits`（既有机制）。

- 写入唯一入口 `admin_audit.record_access`（services/admin_audit.py:19-47）：

```python
def record_access(session, *, action, endpoint, system_admin_id, session_row=None,
                  target_type=None, target_id=None, filters=None, result_count=None, ip=None) -> AdminAccessAudit:
    entry = AdminAccessAudit(
        system_admin_id=system_admin_id, session_id=..., action=action,
        target_type=target_type, target_id=target_id, endpoint=endpoint[:255],
        filters_json=sanitize_filters(filters or {}), result_count=result_count,
        request_id=logctx.request_id_var.get() or None, ip=ip,
        created_at=timeutil.utcnow(),
    )
    session.add(entry)
```

- request id 来自 `logctx.request_id_var`（main.py:107 中间件每请求注入）；actor 归属 = `system_admin_id`；filters 经 `admin_sanitizer.sanitize_filters` 脱敏（键黑名单含 token/password/note 等）。
- 表模型 `AdminAccessAudit`（models/admin_access.py:55-88）：`system_admin_id` FK system_admins SET NULL、`session_id` FK SET NULL、action/target_type/target_id/endpoint/filters_json(JSON)/result_count/request_id/ip/created_at；审计行永久保留（SET NULL 不级联）。
- 只读审计 helper：admin_read.py:60-85 `_audit_read(...)`（每次读取写一行并 `session.commit()`）。
- 写操作审计先例（审批唯一写例外）：commands/manager_applications.py:604-637 `_admin_decision_audit`——与领域事务同提交；决策理由 note 属自由文本按脱敏红线不写入审计表，只落 filters 的白名单字段。
- 家庭域 `audit_log` 写入口 `services/audit.py:16-34 write_audit(session, action, actor_id, target_id, ip, detail)`（actor_id 指向 users）——旧 admin_agent.py 用的是这个（admin_agent.py:116-123 等）。

## 3. admin_governance.py 全文结构（新 router 模板）

- 模块头 docstring 声明"仅有的两个业务写端点"（admin_governance.py:1-11）；`router = APIRouter(prefix="/admin-api/v1", tags=["admin-governance"])`（:28）；`_client_ip` helper（:31-32）。
- schema 来自 `app.schemas.admin_read`：`AdminApplicationApproveRequest/RejectRequest`（schemas/admin_read.py:343-357），核心校验 `confirm: Literal[True]`（二次确认）+ note 约束。
- schema 风格两种并存：
  - `app/schemas/agent.py:17-19`：`class _Strict(BaseModel): model_config = ConfigDict(extra="forbid")`，各请求模型继承 _Strict（fail-closed，额外字段 422）。
  - `app/schemas/admin_read.py`：每类显式 `model_config = ConfigDict(extra="forbid")`；响应是"字段白名单专用投影，绝不直接序列化 ORM"（docstring 1-12 行），新增字段须同步 `tests/test_admin_read_model.py` 精确集合断言。
- 错误处理：路由层不 try/except，由 command/service 层 `raise_api_error` 抛出；main.py 全局 handler 统一展开 `{"error":{code,message,detail}}` 外壳（main.py:156-172）。
- 分页/列表约定（admin_read.py:91-146）：`page: int = Query(default=1, ge=1)`、`page_size: int = Query(default=50, ge=1, le=PAGE_SIZE_MAX)`（PAGE_SIZE_MAX=100，schemas/admin_read.py:20）；响应 envelope `AdminPageOut[T]`（schemas/admin_read.py:22-29）= `{items, page, page_size, total, has_more}`，组装函数 `admin_read_model.page_envelope`（admin_read_model.py:73-81）。
- 注意：admin_governance.py 本身无分页列表（只有两个 POST）；列表+分页+审计的完整范本在 admin_read.py。

## 4. system-admin-frontend 前端结构

### 目录树（src/）

```
api/        auth.ts client.ts decode.ts governance.ts read.ts
components/ AdminShell.vue DecisionModal.vue ListPagination.vue MemberProfilePanel.vue
            PageState.vue ReasonModal.vue SpaceFactsPanel.vue SpaceNotificationsPanel.vue SpaceRelationsPanel.vue
composables/useAccessTicket.ts
router/     index.ts safeRedirect.ts
stores/     accessSession.ts auth.ts
styles/     main.css          ← 当前有未提交改动（见下）
types/      api.ts
utils/      passwordPolicy.ts
views/      AccessAuditView AccountSettingsView AdminLoginView AgentMonitorView AnomalyQueueView
            ForceChangePasswordView NotFoundView OperationsView OverviewView SpaceAdminSpacesView
            SpaceAdminsView SpaceDetailView (共 12 个 .vue)
tests/（仓库根 system-admin-frontend/tests/）: access-session.store / agent.monitor / auth.store / client.wiring /
            governance.view / login.view / module-boundary / router.guard / space-detail.sensitive / types.aligned (.spec.ts)
```

### router 注册

- `system-admin-frontend/src/router/index.ts:17-90` routes 数组，每项 `{path, name, component: () => import('@/views/X.vue'), meta: {requiresAuth: true}}`；现有页面名：login / force-change-password / account-settings / overview / space-admins / space-admin-spaces / space-detail / anomaly-queue / operations / agent-monitor / access-audit / not-found。
- guard `setupAdminRouterGuards`（index.ts:102-150）：未登录→login（带 redirect）、mustChangePassword 只放行 force-change-password。
- 导航入口在 `components/AdminShell.vue:44-51`（`<RouterLink :to="{name:'agent-monitor'}">Agent 监控</RouterLink>` 等）——新页面要同时加路由 + AdminShell 导航链接。

### api/client.ts 封装

- `adminApiClient = axios.create({ baseURL: '/admin-api', timeout: 20000 })`（client.ts:116-120）；request 拦截器注入 Bearer（wiring 由 auth store 注册）+ 可选 `X-Admin-Access-Session` 票据；response 拦截器 401 ADMIN_UNAUTHORIZED 自动 refresh 重试一次。
- 统一入口 `adminRequest<T>({method, url, data?, params?, accessTarget?, signal?})`（client.ts:185-208）。**method 类型目前只有 `'get' | 'post' | 'put'`（client.ts:185-186）——Provider 更新需要 PATCH 时要先扩类型**（现有唯一写调用 POST 见 api/governance.ts:58-79）。
- **AdminApiError 不携带 detail**：`AdminApiError(status, code, serverMessage)`（client.ts:23-35），从错误外壳提取时丢弃 `error.detail`（client.ts:176-178 只取 code/message）。Provider 页若要呈现结构化错误（如 allowed_models 校验失败）需扩展此类或改解码。
- 现有 API 调用示例（api/governance.ts:58-68）：

```ts
export function apiApproveManagerApplication(applicationId: number, note: string | null) {
  return adminRequest<unknown>({
    method: 'post',
    url: `/v1/manager-applications/${applicationId}/approve`,
    data: { confirm: true, ...(note ? { note } : {}) },
  }).then(decodeApplication)   // decode.ts expectXxx 逐字段运行时校验
}
```

- 页面数据加载写法（views/SpaceAdminsView.vue:12-43）：`data = ref<AdminPageOut<T> | null>`、`state = ref<'loading'|'ready'|'error'>`、`async load()` try/catch 切状态、`watch([page, statusFilter, searchCommitted], () => void load())` + `onMounted(load)`；模板用 `PageState`（loading/error/empty 三态）+ `ag-table` + `ListPagination`。只读监控页范本 `AgentMonitorView.vue`（5 秒轮询 + AbortController 防重入 + visibility 暂停）。

### 新增页面要动的文件

1. `src/views/AgentProviderAdminView.vue`（或类似名，新文件）
2. `src/router/index.ts`（routes 数组加一项）
3. `src/components/AdminShell.vue`（admin-nav 加 RouterLink）
4. `src/api/`（新建 agent-provider 封装，复用 adminRequest；类型进 `src/types/api.ts`）
5. `system-admin-frontend/tests/` 对应 spec（module-boundary.spec.ts / types.aligned.spec.ts 有边界与类型对齐断言）

### 样式约定（cosmic-glass）

- 全部在 `src/styles/main.css`（718 行）：`:root` 设计 token（第 8 行起）+ `ag-*` class 体系：`ag-card`(:173)、`ag-page-title`(:230)、`ag-page-subtitle`(:236)、`ag-toolbar`(:242)、`ag-table`(:271)、`ag-tag`(:356)、`ag-metric`(:387)、`form-field`(:479)、`ag-btn-primary`(:560)、`ag-btn-danger`(:584)、`ag-inline-form`(:594)、`ag-heading-section`(:610) 等。
- **main.css 有未提交改动**：`git status` 显示 ` M system-admin-frontend/src/styles/main.css`（diff 约 +111/-X 行，cosmic-glass 重设计尾巴）。新页面若依赖其中新 class/tokens，需确认这些改动随本任务一起提交或先落地。

### git 工作区基线（2026-09-06，影响实现落点）

- 未提交修改：backend/app/{config.py, dev_seed.py, main.py}、backend/tests/test_dev_seed.py、frontend 多处（App.vue/AppShell/SettingsView/tokens.css/tokens.ts/naive-themes 等）、`system-admin-frontend/src/styles/main.css`。
- 未跟踪：`.trellis/tasks/09-06-*` 三个任务目录、`docker-compose.dbx.yml`、`frontend/src/components/canvas/CosmicBackdrop.vue`。

## 5. 家庭前端空间管理

### 页面与组织

- "空间管理" = `frontend/src/views/SpaceManagementView.vue`，路由 `/spaces/:spaceId/manage`，name `space-management`，`meta: { spaceManagerOnly: true }`（frontend/src/router/index.ts:92-97；守卫逻辑 router/index.ts:162-186 校验 active membership + canManageSpace）。
- 页面为**五分区侧栏**（SpaceManagementView.vue:26-34）：`overview / members / invites / bridge / settings`（概览、成员、邀请与申请、Bridge 通知、空间设置）。每个分区是同文件内一个 `<section v-else-if="activeSection === 'xxx'">`。
- 现有"空间设置"分区（SpaceManagementView.vue:246-271）只有空间名一个字段，保存走 `spaces.rename` → `updateSpace` → `PATCH /spaces/{space_id}`（api/spaces.ts:29-32；saveName 见 :96-109，ApiError message 直接 message.error）。
- **新增"模型设置"区块的自然落点**：`SECTIONS` 数组加一项 + 新增 `<section v-else-if>`；组件可抽到 `frontend/src/components/member/`（该目录已有 SpaceGovernancePanel.vue、SpaceManagerApplicationPanel.vue 等分区组件先例）。
- **`SettingsView.vue`（frontend/src/views/SettingsView.vue）是全局个人设置页**（五分区：profile/privacy/invite/account/display），文件头注释明确"空间管理不放进全局设置"——模型设置不应落在这里。

### API client 模式

- `frontend/src/api/` 每个域一个文件（spaces.ts、agent.ts…），函数式导出 async 函数，内部用共享 `apiClient`（api/client.ts:12-15，baseURL '/api'，request 拦截器注入 Bearer，401 单飞行静默刷新）。示例（api/spaces.ts:29-32）：

```ts
export async function updateSpace(spaceId: number, name: string): Promise<FamilySpace> {
  const { data } = await apiClient.patch<FamilySpace>(`/spaces/${spaceId}`, { name })
  return data
}
```

- 错误对象 `ApiError(status, code, message, detail?)`（api/errors.ts:6-18）——**detail 已保留在实例上**，但现有文案函数不用它。
- 当前前端**没有任何 Provider/模型设置 UI**（全量 grep 仅命中 naive-ui 的 provider 组件）——agent.ts:63 的文案"请联系空间所有者在管理页选择 Provider"承诺的管理页不存在，即 PRD 要修复的闭环缺陷。

### AGENT_ERROR_COPY 机制全文（frontend/src/api/agent.ts:58-97）

```ts
const AGENT_ERROR_COPY: Record<string, string> = {
  AGENT_RUN_LIMIT: '并发任务较多，请稍后再试',
  AGENT_RUNTIME_DISABLED: '助手功能当前未启用',
  PROVIDER_UNRESOLVED: '当前空间还没有可用的模型配置，请联系空间所有者在管理页选择 Provider',
  PROVIDER_LOCAL_REQUIRED_UNAVAILABLE: '该空间要求本地模型执行，但本地服务暂不可用',
  ...
  PROVIDER_DENIED_NO_LOCAL: '该空间要求本地模型执行，但本地服务暂不可用',
  PROVIDER_DENIED_CLOUD_FORBIDDEN: '该空间未开放云端模型，请联系空间所有者调整配置',
  SIDECAR_ERROR: '助手服务暂时不可用，请稍后重试',
}
export const CLIENT_AGENT_ERRORS = { STREAM_LOST, AUTH_EXPIRED, RUN_FAILED, SEND_FAILED } as const
const CLIENT_ERROR_COPY: Record<string, string> = { ... }
export function friendlyAgentError(code: string | null | undefined, fallback?: string): string {
  if (code && code in AGENT_ERROR_COPY) return AGENT_ERROR_COPY[code] as string
  if (code && code in CLIENT_ERROR_COPY) return CLIENT_ERROR_COPY[code] as string
  return fallback ?? '操作失败，请稍后重试'
}
```

- **映射只按 `code`，完全不消费 `detail`**。后端 detail 载荷：`PROVIDER_UNRESOLVED` → `{"policy_result", "reason"}`（backend/app/api/agent.py:283-288）；`PROVIDER_LOCAL_REQUIRED_UNAVAILABLE` → `{"reason", "provider_id"}`（agent.py:277-282）。R5 要求"错误码承载足够信息区分两种态（通道未配置 vs 空间未选/未同意云）"——`reason` 字段已能区分（no_space_setting / cloud_not_allowed 等），前端需改为按 code+detail 映射。
- 消费点：stores/agent.ts:376（SSE `run.failed` 事件按 error_code 映射）、stores/agent.ts:581-586 `describeApiError`（发消息 catch：`friendlyAgentError(error.code, error.message)`）、components/agent/ErrorNotice.vue:17。

## 6. services/agent_provider.py 全文精读

### resolve_for_space（services/agent_provider.py:131-277）

签名：`def resolve_for_space(db: Session, space_id: int) -> ProviderResolution`。**当前没有任何 agent_kind 参数**——直接按 space_id 查单行设置（:137-139）：

```python
setting = db.scalar(
    select(AgentSpaceProviderSetting).where(AgentSpaceProviderSetting.space_id == space_id)
)
```

判定链（每个 denied 分支都构造带 reason 的 ProviderResolution，永不抛错、无静默替补）：
1. `setting is None` → POLICY_DENIED，reason `no_space_setting`
2. `not setting.enabled` → POLICY_DENIED，`setting_disabled`
3. `db.get(AgentProvider, setting.provider_id) is None` → POLICY_DENIED，`provider_missing`
4. `not provider.enabled` → POLICY_DENIED，`provider_disabled`
5. `provider_profile_error(provider, setting.model)` 非 None → POLICY_DENIED，reason=profile_error 词表（:91-128：provider_name/api/base_url/model/allowlist/context_window/max_tokens/reasoning/modalities/thinking/compat `_not_allowed`；受 `config.AGENT_PROVIDER_STANDARD_PROFILE_ONLY=True`（config.py:94）门控，local kind 豁免）
6. `setting.model not in provider.allowed_models_json` → POLICY_DENIED，`model_not_allowed`
7. `provider.kind == "local"` → 直接 `_allowed`（cloud_allowed 不约束本地，:240-242）
8. 云 Provider + `setting.local_required` → POLICY_DENIED_NO_LOCAL，`selected_provider_not_local`（:244-260）
9. 云 Provider + `not setting.cloud_allowed` → POLICY_DENIED_CLOUD_FORBIDDEN，`cloud_not_allowed`（:261-276）
10. 否则 `_allowed(provider, setting.model)`：secret_ref = `f"agent_providers/{provider.id}/secret"`（:280-295）

### 常量

- POLICY_*（:24-27）：`POLICY_ALLOWED="allowed"`、`POLICY_DENIED="denied"`、`POLICY_DENIED_NO_LOCAL="denied_no_local"`、`POLICY_DENIED_CLOUD_FORBIDDEN="denied_cloud_forbidden"`。
- STANDARD_*（:32-40）：`STANDARD_PROVIDER_NAME="liu-dada"`、`STANDARD_MODEL="gpt-5.6-sol"`、`STANDARD_API="openai-responses"`、`STANDARD_BASE_URL="https://api.liu-dada.com/v1"`、`STANDARD_CONTEXT_WINDOW=272_000`、`STANDARD_MAX_TOKENS=60_000`、`STANDARD_REASONING=True`、`STANDARD_INPUT_MODALITIES=("text","image")`、`STANDARD_THINKING_LEVELS=("low","medium","high","xhigh","max")`。

### 其他函数

- `ProviderRuntime`（:43-64，sidecar 注入对象，唯一解密出口）/ `ProviderResolution`（:67-88，context 下发，无密钥；`reason` additive）。
- `snapshot_for_space`（:298-327）：把非密钥解析快照固化进 AgentRun.runtime_snapshot_json（含 `provider_revision=provider.updated_at.isoformat()`）。
- `resolve_for_run`（:330-503）：denied 决议对 queued run 不可变（`runtime_snapshot_policy_denied`）；allowed 快照字段完整性 + provider_revision 比对（`runtime_snapshot_invalid` / `runtime_snapshot_mismatch`）。
- `find_local_provider`（:506-510）、`resolve_runtime`（:513-566）：policy==allowed 且 base_url 非空才解密 secretbox；密文损坏 fail-closed 返回 None。
- **隐藏耦合（设计需注意）**：`snapshot_for_space`/`resolve_for_run`/`resolve_runtime` 都直接调 `resolve_for_space(db, space_id)`（无 agent_kind）；`api/agent.py:274` 是 assistant 消费点；internal 协议 context 端点同样经此链。给 resolve_for_space 加 agent_kind 参数时，这些调用点（含 run 快照合同）都要联动。

### 单元测试

- `backend/tests/test_agent_provider.py`（1-80+）：secretbox roundtrip/篡改（:50-62）、policy 矩阵（cloud allowed/denied 等）、`resolve_for_run` 快照不可变；造数 helper `_provider`/`_setting` 直建 ORM（:20-47）。
- `backend/tests/test_agent_admin_providers.py`：端点级（详见 §10）。

## 7. models/agent_provider.py 全文

### AgentProvider（models/agent_provider.py:31-67）

| 列 | 类型/约束 |
|---|---|
| id | PK |
| name | String(64) **unique** nullable=False |
| kind | String(32)，CHECK `kind IN ('openai_compatible','local')`（ck_agent_providers_kind） |
| api | String(48) default 'openai-responses'，CHECK `IN ('openai-completions','openai-responses')`（ck_agent_providers_api） |
| base_url | String(500) nullable |
| compat_json | JSON default dict |
| context_window | Integer default 272000 |
| max_tokens | Integer default 60000 |
| reasoning | Boolean default True |
| input_modalities_json | JSON default ["text","image"] |
| thinking_levels_json | JSON default 5 档 |
| secret_ciphertext | Text nullable（secretbox 密文 `nonce||ciphertext||tag` base64url；local 可空） |
| allowed_models_json | JSON **nullable=False（无 default，写入方必须给值）** |
| enabled | Boolean default True |
| created_at / updated_at | DateTime nullable=False |

### AgentSpaceProviderSetting（models/agent_provider.py:70-91）

| 列 | 类型/约束 |
|---|---|
| id | PK |
| space_id | FK family_spaces.id ondelete CASCADE，nullable=False，**unique=True（列级唯一约束 = 现行唯一键，等价 (space_id) 单列唯一）** |
| provider_id | FK agent_providers.id ondelete CASCADE |
| model | String(120) |
| cloud_allowed | Boolean default False |
| local_required | Boolean default False |
| enabled | Boolean default True |

- **唯一键现状**：`space_id` 列级 `unique=True`（0009 迁移同款，0009_agent_runtime.py:281-286），没有命名的 UniqueConstraint。扩成 `(space_id, agent_kind)` 复合唯一在 SQLite 上不能 ALTER——需按 0032 先例重建表（建新表→拷贝→删旧→改名→重建索引），见 §9。
- JSON 列用法：`Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)` 与 `Mapped[list[str]] = mapped_column(JSON, default=lambda: [...])`，服务层以 `list(row.allowed_models_json or [])` 防御式读取。

## 8. schemas/agent.py 相关 schema 全文

- 基类 `_Strict`（schemas/agent.py:17-19，`extra="forbid"`）。
- `AgentProviderCreateRequest`（:225-245）：name(1-64)、`kind: Literal["openai_compatible","local"]`、`api: Literal["openai-completions","openai-responses"]="openai-responses"`、base_url(≤500, None)、compat dict、context_window(默认 272000, 1024..10_000_000)、max_tokens(默认 60000, 16..1_000_000)、reasoning=True、input_modalities(1-4 项)、thinking_levels(1-8 项)、`secret: str|None ≤4096`（只写不读）、allowed_models(1-50 项)、enabled=True。
- `AgentProviderPatchRequest`（:248-259）：全字段 Optional；路由层用 `body.model_fields_set` 区分"未提供"与"显式 null"（admin_agent.py:149）——secret 空=清除、非空=轮换（admin_agent.py:171-173）。
- `AgentProviderOut`（:262-278）：id/name/kind/api/base_url/compat/context_window/max_tokens/reasoning/input_modalities/thinking_levels/**has_secret: bool**/allowed_models/enabled/created_at/updated_at——永无密钥字段。
- `AgentSpaceProviderSettingsRequest`（:281-287）：`provider_id: int|None ge=1`（None=清除该空间选择）、`model: str|None (1..120)`、cloud_allowed=False、local_required=False。
- `AgentSpaceProviderSettingsOut`（:290-297）：space_id/provider_id|None/model|None/cloud_allowed/local_required/enabled。

## 9. Alembic 迁移约定

- **当前 head = `0032_invite_codes_creator_set_null`**（全链 down_revision 线性：0001→…→0031_add_account_bindings→0032；无任何迁移指向 0032）。 revisions 目录 0032 为最新。
- 文件名 `NNNN_slug.py`；头三行元数据 `revision`（字符串=文件名主体）/`down_revision`/`branch_labels`/`depends_on`（0032:26-29）。
- `migrations/env.py`：URL 从 `app.config.DATABASE_URL` 注入（禁止 ini 硬编码），`target_metadata = Base.metadata`。
- SQLite 约定（database-guidelines 口径，多份迁移 docstring 重申）：
  - **不切换 PRAGMA foreign_keys**；FK/约束变更用"建新表 → INSERT SELECT 拷贝 → drop 旧表 → rename → 重建索引"（0032:16-18, 89-95；0026/0028 同款）。
  - 纯加列可直接 `op.add_column(..., server_default=...)`（0020_agent_runtime_profile.py:17-32 给 agent_providers 加 api/compat_json/context_window/max_tokens/reasoning 等）。
  - `op.batch_alter_table` 仅用于简单场景（空表加列 0009:246、加列/改约束 0005:80、0023:26,41）。
  - downgrade 可 fail-closed：检测不可逆数据即 `raise RuntimeError`（0032:98-108）。
- `agent_space_provider_settings` 建表迁移（0009_agent_runtime.py:277-297）：space_id `unique=True` + FK CASCADE、provider_id FK CASCADE、cloud_allowed/local_required `server_default=sa.false()`、enabled `server_default=sa.true()`；downgrade 直接 drop_table（0009:300-308）。
- 空库迁移链回归：conftest.py:47-53 session fixture `alembic downgrade base` + `upgrade head`（`.venv/bin/alembic`，cwd=backend）——新迁移写完跑 pytest 即自动验证。
- 0022_system_admin_space_manager.py:87-118 有对 `platform_role_assignments` 的读取与 `DELETE FROM`（历史迁移，不动）；PRD 要求新迁移不删表、仅注释废弃。

## 10. 旧端点与 platform_operator 引用面

### `/api/admin/agent`（旧端点本体，待删）

| 位置 | 处置 |
|---|---|
| `backend/app/api/admin_agent.py`（全文 1-275） | 删除（路由 + `_require_runtime_enabled` + `_require_operator` + `_provider_out`） |
| `backend/app/main.py:25`（import）、`:208`（mount） | 删除 |
| `backend/tests/test_agent_admin_providers.py`（全文） | 删除/重写到 admin 域 |

覆盖用例清单（test_agent_admin_providers.py）：非 operator 全端点 403 矩阵（:41-70）、注册/列表 secret 明文与密文双不回显（:73-99）、openai_compatible 必填 base_url 422（:102-109）、strict profile 只收 liu-dada 档（:112-142）、flag off 503 AGENT_RUNTIME_DISABLED（:145-152）、空间设置校验（未知空间 404/model 不在 allowlist 422/清除 provider_id=None 删行 :168-206）、策略矩阵经消息创建验证 PROVIDER_UNRESOLVED(PROVIDER detail.policy_result=denied_cloud_forbidden)/PROVIDER_LOCAL_REQUIRED_UNAVAILABLE/本地 ok/停用后 provider_disabled（:228-313）。

### `require_platform_operator`（services/platform_roles.py:36-40）

| 调用点 | 处置建议依据 |
|---|---|
| `api/admin_agent.py:37,54` | 随旧端点删除 |
| `api/admin.py:37,45,67` | admin.py 是**死代码**（router prefix="/admin"（admin.py:40），main.py 不再 import/挂载；test_system_admin_boundary.py:80-104 断言其家庭路由不注册）——引用随本任务或后续清理 |
| `api/controlled_web.py:38,105,132` | **保留**：`/api/admin/web` 平台配置属家庭 listener platform_operator 面（不在本任务范围；boundary 测试白名单含它） |
| `commands/admin.py:27,35` | 待查（同属旧家庭 admin 面；admin.py 死代码路径） |
| `commands/manager_applications.py:41,147` | **保留**：147 行在旧函数 `decide_manager_application`（:128，家庭 operator 路径）；admin 域用 `decide_manager_application_as_system_admin`（:454，不经 platform_operator） |
| `api/deps.py:107-117` `require_platform_principal` | 仅被死代码 admin.py:17,359,376 使用 |
| `services/platform_roles.py` 其余（`platform_roles()`/`is_platform_operator` :21-33） | **保留**：被家庭域消费——schemas/user.py:200-211 与 api/users.py:232（me 投影 is_admin）、api/deps.py:21,114、services/visibility.py:30,124-139,307（operator 在可见性中等同无关用户）、services/memory_rag.py:38,556 |

### `platform_role_assignments` / `PlatformRoleAssignment`

- 模型：models/v2_foundation.py:41-63（role CHECK 仅 'platform_operator'，account_id unique）；导出 models/__init__.py:60,102。
- 引用：security.py:103,114（JWT claim `adm`）、上表全部 platform_roles 消费方、migrations 0008:46,70,515（建表/迁移/降级）与 0022:87-118、models/user.py:5（docstring）。
- 测试：conftest.py:153（清表清单）、:233-270（`create_user_with_pin(is_admin=True)` 造 PlatformRoleAssignment）；test_agent_query_tools.py:575、test_steward.py:41,47,362、test_owner_invitations.py:63。
- **PRD 裁定：表与数据保留，代码不再读写（迁移仅注释）**；但注意 `is_platform_operator` 在家庭域（可见性/me 投影）仍在读写该表——"代码不再读写"实际范围 = require_platform_operator 依赖链 + admin_agent 端点，design 需写清楚边界。
- 前端（frontend、system-admin-frontend、agent sidecar）对 `admin/agent`、`platform_operator`、`provider-settings` **零引用**（grep 证实；agent sidecar 只走 /internal/agent）。
- 其他：`test_system_admin_boundary.py:85,95` 白名单 `/api/admin/agent`——旧端点删除后该测试要同步改；spec `.trellis/spec/backend/agent-runtime.md:41` 记载旧端点行为（实现后应更新 spec）。

## 11. AGENT_RUNTIME_ENABLED 门禁

- **admin_app 侧现在没有任何 runtime 门禁**（admin_read.py / admin_governance.py 均无）。
- 门禁模板（api/agent.py:86-94，admin_agent.py:41-46 同款）：

```python
def _require_runtime_enabled() -> None:
    """RT-6：Agent 能力由服务端 feature flag 总开关控制，默认整体关闭。"""
    if not config.AGENT_RUNTIME_ENABLED:
        raise_api_error(503, AGENT_RUNTIME_DISABLED, "Agent Runtime 未启用")

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(_require_runtime_enabled)])
```

- flag 定义：config.py:90 `AGENT_RUNTIME_ENABLED: bool = os.environ.get("AGENT_RUNTIME_ENABLED", "").lower() in ("1", "true")`（默认 False）。router 级 `dependencies=[...]` 挂法即 PRD R1 要求的"关闭即 503"。

## 12. 平台级配置存储先例

- **有单行表先例，无通用 kv 表**：`WebPlatformConfig`（models/controlled_web.py:24-44）——

```python
class WebPlatformConfig(Base):
    __tablename__ = "web_platform_configs"
    __table_args__ = (CheckConstraint("id = 1", name="ck_web_platform_config_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ...
    provider_secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
```

- 读取/懒建模式（api/controlled_web.py:99-122）：`row = db.get(WebPlatformConfig, 1)`；None 时 new 一个 id=1 默认行。PUT 全量覆盖 + secret 只写（:125+）。
- "平台默认模型"（默认 provider_id + 每 agent_kind 默认 model）可照此做单行表；无其他 platform_config/system_config/kv 先例（全库 grep 仅此一家）。

## 13. 错误码清单（backend/app/errors.py）

| 常量 | 行 | 语义 / detail 载荷 |
|---|---|---|
| `AGENT_RUNTIME_DISABLED` | :135 | flag 关闭 503；无 detail |
| `PROVIDER_UNRESOLVED` | :140 | 无可用 Provider 配置，409；detail `{"policy_result": <POLICY_*>, "reason": <词表>}`（api/agent.py:283-288） |
| `PROVIDER_LOCAL_REQUIRED_UNAVAILABLE` | :141 | 要求本地但不可用，409；detail `{"reason","provider_id"}`（api/agent.py:277-282） |
| `AGENT_PROVIDER_NOT_FOUND` | :142 | 404；无 detail（admin_agent.py:148,235） |
| `AGENT_PROVIDER_PROXY_UNAVAILABLE` | :143-145 | 代理 fail-closed 拒绝 |
| `AGENT_PROVIDER_REQUEST_INVALID` | :146 | 代理请求非法 |
| `VALIDATION_ERROR` | :49 | provider 相关 detail：`{"reason": profile_error}`（admin_agent.py:108-113）、`{"allowed_models": [...]}`（admin_agent.py:239-244） |

- `reason` 词表（services/agent_provider.py）：`no_space_setting` / `setting_disabled` / `provider_missing` / `provider_disabled` / `provider_{name,api,base_url,model,context_window,max_tokens,reasoning,input_modalities,thinking_levels,compat}_not_allowed` / `provider_model_allowlist_not_allowed` / `model_not_allowed` / `selected_provider_not_local` / `cloud_not_allowed`；resolve_for_run 另有 `runtime_snapshot_{policy_denied,invalid,mismatch}`。
- `raise_api_error(status_code, code, message, detail=None, headers=None)`（errors.py:219-233）抛 `HTTPException(detail={"__api_error__": payload})`；`extract_api_error`（:235-239）供 handler/内部转换。前端 `ApiError.detail` 已携带外壳 detail（frontend/src/api/errors.ts:32）。

## 14. 测试基建

- **环境注入必须在导入 app 前**（conftest.py:11-27）：`ADMIN_JWT_SECRET/ISSUER/AUDIENCE`（:15-17）、`AGENT_RUNTIME_ENABLED=1`（:20）、`AGENT_SERVICE_SECRET`（:19）、`STEWARD_ENABLED=1` 等；`config.AGENT_PROVIDER_STANDARD_PROFILE_ONLY = False` 进程级放宽（:36）+ autouse 复位 fixture（:63-73）。
- **库**：session 级 autouse `_migrated_database`（conftest.py:47-53）执行 `.venv/bin/alembic downgrade base → upgrade head`（cwd 必须 backend/）；每测试 `_clean_tables` 按子→父序清空全部业务表（含 `agent_space_provider_settings`/`agent_providers`/`admin_access_audits`/`platform_role_assignments`，:94-163）。
- **admin 域拿 token 的标准方式**：
  - 造主体：`create_system_admin(db_session, username="admin", password="FixtureAdmin-2026x", password_must_change=False)`（conftest.py:283-314，直建 SystemAdmin+SystemAdminAccount）。
  - 登录：`admin_login(admin_client, ...)` → POST `/admin-api/auth/login`（:317-325）；`admin_session_headers(admin_client)` 一步返回 `{"Authorization": "Bearer ..."}`（:332-340）。
  - client fixture：`admin_client` = TestClient(admin_app)（:201-206）；家庭面另有 `client`/`internal_client`。
- **agent 域造数**：`create_agent_fixture(db, name=...)` → (user, space)（带 active space_admin 成员行，:346-372）；`create_agent_session`（:375-385）；service 层测试直建 ORM（test_agent_provider.py:20-47 `_provider`/`_setting`）；端点级测试经旧 API 注册（test_agent_admin_providers.py:23-35 `_register_cloud`）。
- **admin 域测试范例**：test_admin_audit.py（v1_headers fixture = create_system_admin + admin_session_headers，:26-30）、test_system_admin_boundary.py（8000/8002 路由面隔离断言）。
- **运行命令**：无 Makefile；backend 用 `.venv/bin/pytest`（pyproject `[tool.pytest.ini_options] testpaths=["tests"]`，backend/pyproject.toml:45-46；alembic/pytest 都在 backend/.venv/bin/）。前端两侧 `npm test` = `vitest run`，`npm run type-check` = `vue-tsc --noEmit`（frontend/package.json:6-14 与 system-admin-frontend/package.json:6-14 相同脚本集）。

## 15. 家庭前端路由与页面挂载

- 路由表（frontend/src/router/index.ts:26-115）：`/spaces/:spaceId/manage` → name `space-management` → `SpaceManagementView.vue`，`meta.spaceManagerOnly`（:92-97）；守卫 :162-186（spaces store 校验 membership + canManageSpace，fail-closed 回 family-space）。其他相关：`/settings`（:104-107，全局个人设置）、`/memory`、`/notifications`。
- AppShell 承担"当前空间管理"入口（SpaceManagementView.vue 注释 :7）。
- 测试栈：vitest + @vue/test-utils（views/__tests__/space-management.spec.ts 已有分区/守卫测试；api/__tests__ 与 stores/__tests__ 同栈）；命令 `npm test`（vitest run）、`npm run type-check`、`npm run lint`。

## Caveats / Not Found

- `backend/app/api/admin.py` 与 `api/deps.py:require_platform_principal` 是死代码（router 未挂载），但仍在编译/引用面上——删除 require_platform_operator 时需一并处置或保留裁定。
- PRD R1"审计落 audit_log"与 admin 域既有机制（admin_access_audits）冲突，见 §2——design 必须显式裁定。
- adminRequest method 只支持 get/post/put、AdminApiError 丢弃 detail——Provider PATCH 与结构化错误呈现需小改 system-admin-frontend/src/api/client.ts。
- system-admin-frontend main.css 与 frontend 多文件存在未提交改动，行号/样式基线以 2026-09-06 工作区为准。
- agent sidecar（agent/src）与治理端点零耦合（只走 /internal/agent），迁移对其无影响。
- 未运行 alembic/pytest 实际验证（只做静态链核对 + conftest 佐证），head 判定基于 down_revision 全链闭合。
