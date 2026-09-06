# Design: Agent 模型治理迁移系统管理员后台与双端配置 UI

> 依据：`prd.md`（D1–D7）+ `research/current-state.md`（行号引用均指向该文件）。本设计裁定研究发现的 5 个风险点，实现必须遵守。

## 0. 关键裁定（对 PRD 的显式化/修正）

| # | 裁定 | 说明 |
|---|---|---|
| A1 | 审计落 `admin_access_audits`（经 `services/admin_audit.record_access`），**不是** `audit_log` | `audit_log.actor_id` FK 指向 users，无法表达 system_admin（research §2）。PRD R1 括注"管理员域既有机制为准"即本裁定 |
| A2 | `platform_operator` 废弃的边界 = 删除 agent 治理路径（旧 `api/admin_agent.py` + main.py 挂载 + 其测试）；`controlled_web.py` 的 `require_platform_operator`、`is_platform_operator` 在 visibility/me 投影的读路径**全部保留** | full removal 不在本任务范围（research §10）；`api/admin.py` 死代码与 `deps.require_platform_principal` 不动 |
| A3 | `agent_space_provider_settings` 唯一键改造走整表重建（0032 先例），不做 ALTER | SQLite 列级 unique 无法改复合（research §9） |
| A4 | admin-web 页面只用已提交的 `ag-*` 样式类，**不改 `main.css`**（该文件有视觉重设计任务的未提交改动）；新样式一律组件内 scoped | 避免混入 09-05 视觉任务的工作区改动（research §4） |
| A5 | 提交卫生：`backend/app/main.py` 工作区含视觉任务的未提交 hunk，提交时只提交本任务相关 hunk（主会话在 Phase 3.4 处理），其余文件无重叠 | research §4 git 基线 |

## 1. 数据模型与迁移（R2/A3）

### 1.1 `agent_space_provider_settings` 扩展

- 新列 `agent_kind`：`String(32) nullable=False server_default='assistant'`，CHECK `agent_kind IN ('assistant','steward')`（命名 `ck_asps_agent_kind`）。
- 唯一键从 `(space_id)` 列级 unique 改为复合 `UniqueConstraint(space_id, agent_kind, name="uq_asps_space_agent")`。
- 迁移 `0033_agent_space_provider_settings_agent_kind.py`（down_revision=0032）：
  1. 建新表 `agent_space_provider_settings_new`（含 agent_kind + 复合唯一 + 原 FK/CHECK/server_default 原样，参照 0009:277-297 与 0032 重建写法）；
  2. `INSERT SELECT` 拷贝存量行（agent_kind 取 'assistant'）；
  3. drop 旧表 → rename → 重建索引；
  4. 同迁移内建平台默认单行表（见 1.2）；
  5. downgrade：反向重建回单列唯一版（`agent_kind` 列丢弃；若存在 agent_kind='steward' 行，fail-closed `raise RuntimeError`，参照 0032:98-108）。
- `models/agent_provider.py`：`AgentSpaceProviderSetting` 加 `agent_kind: Mapped[str]`（default="assistant"）+ `__table_args__` 复合唯一约束。

### 1.2 平台默认单行表（D4）

```python
class AgentPlatformDefault(Base):
    __tablename__ = "agent_platform_defaults"
    __table_args__ = (CheckConstraint("id = 1", name="ck_agent_platform_default_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    assistant_provider_id: Mapped[int | None] = mapped_column(ForeignKey("agent_providers.id", ondelete="SET NULL"), nullable=True)
    assistant_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    steward_provider_id: Mapped[int | None] = mapped_column(ForeignKey("agent_providers.id", ondelete="SET NULL"), nullable=True)
    steward_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    updated_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("system_admins.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = ...
```

- 读写照 `WebPlatformConfig` 懒建先例（research §12）：`db.get(AgentPlatformDefault, 1)`，None 则懒建默认行。
- `provider_id` 与 `model` 必须成对设置/成对清除（schema 层校验）；Provider 删除时 SET NULL 留孤 model 无效（service 读取时 provider_id 为 None 即视为该 kind 无默认）。

## 2. Service 层：`agent_kind` 与平台默认回退（R2/A4 链路）

`services/agent_provider.py`：

1. `resolve_for_space(db, space_id, agent_kind: str = "assistant") -> ProviderResolution`
   - `agent_kind` 不在 `{"assistant","steward"}` → `raise_api_error(422, VALIDATION_ERROR, ...)`（fail-closed）。
   - 查询改为 `WHERE space_id == space_id AND agent_kind == agent_kind`。
   - **无空间行时的回退**（新增）：读平台默认行；该 kind 存在有效默认（provider_id+model 且 provider 存在且 enabled）→ 构造虚拟 setting（cloud_allowed=False, local_required=False, enabled=True）走既有判定链 5-10（research §6）。即：
     - local 类默认 Provider → 直接 allowed（现有规则：local 不受 cloud_allowed 约束）；
     - 云类默认 Provider → `POLICY_DENIED_CLOUD_FORBIDDEN` reason=`cloud_not_allowed`（owner 须显式同意云）。
   - 无空间行且无有效平台默认 → 维持 `POLICY_DENIED` reason=`no_space_setting`。
   - 空间行存在时的全部行为不变（`enabled=False` = owner 显式停用，reason=`setting_disabled`）。
2. `ProviderResolution` 增加只读字段 `platform_default_configured: bool`（additive，默认 False）——喂给错误 detail（见 §5）。
3. `snapshot_for_space` / `resolve_for_run` / `resolve_runtime`：签名加 `agent_kind: str = "assistant"` 并透传，**runtime_snapshot_json 结构本任务不动**（快照合同不加字段；steward child run 的快照扩展属子任务 B）。
4. 新增平台默认读写 helpers：`get_platform_defaults(db)`、`set_platform_defaults(db, *, assistant, steward, updated_by_admin_id)`（成对校验、model 必须在该 provider allowed_models 内，否则 VALIDATION_ERROR）。
5. 消费点：`api/agent.py:274` 显式传 `agent_kind="assistant"`；internal context 链路依赖默认值不变。

## 3. 端点设计

### 3.1 管理员域（重写 `api/admin_agent.py`，挂 admin_app）

- `router = APIRouter(prefix="/admin-api/v1", tags=["admin-agent"], dependencies=[Depends(_require_runtime_enabled)])`（门禁照 api/agent.py:86-94；**admin_app 侧首个 runtime 门禁**，research §11）。
- 鉴权：统一 `identity: AdminPrincipal = Depends(require_admin_ready)`。
- 端点：
  - `POST /agent/providers` → 201 `AgentProviderOut`（注册校验全量平移：openai_compatible 必填 base_url、strict-profile 门禁、allowlist，research §8/§10）
  - `GET /agent/providers` → `list[AgentProviderOut]`
  - `PATCH /agent/providers/{provider_id}` → `AgentProviderOut`（`model_fields_set` 语义、secret 清除/轮换平移）
  - `GET /agent/platform-defaults` → `AgentPlatformDefaultsOut`
  - `PUT /agent/platform-defaults` → `AgentPlatformDefaultsOut`（成对校验 + model ∈ allowlist）
  - `GET /agent/spaces/{space_id}/provider-settings` → 两 kind 的行级设置 + 平台默认状态（只读排查视图）
- 审计（A1）：写操作与平台默认/Provider 全部经 `admin_audit.record_access(action="agent.provider.create"| "agent.provider.update" | "agent.platform_defaults.update", target_type=..., target_id=..., endpoint="/admin-api/v1/agent/...", ip=_client_ip(request))`，与领域事务同提交；filters 只放白名单字段（secret 绝不入审计）。
- `main.py`：删除家庭 app 挂载（:208），改为 `admin_app.include_router(admin_agent_router)`（import :25 保留）；admin_app 对 `/api/admin/agent` 无需特殊处理（家庭 listener 404 即可）。

### 3.2 家庭域（owner 侧，新文件 `api/space_model_settings.py`）

- 路由挂载与 `spaces.py` 同款（main.py `prefix="/api"`），权限依赖 = `PATCH /spaces/{space_id}` 所用的同一个 space-manager 依赖（实现时对齐 spaces.py update_space 的依赖名）。
- 端点：
  - `GET /spaces/{space_id}/model-settings` → `SpaceModelSettingsOut`：
    ```
    { settings: {assistant: SpaceAgentSettingOut|inherit-无行, steward: ...},
      catalog: [{provider_id, name, kind, api, models}],
      platform_default: {assistant: {provider_id, model}|null, steward: ...|null} }
    ```
    catalog 只含 `enabled=True` 的 Provider（`AgentModelCatalogEntryOut`，无密钥字段）。
  - `PUT /spaces/{space_id}/model-settings` body `AgentSpaceModelSettingsRequest(_Strict)`：
    `{agent_kind: Literal["assistant","steward"], provider_id: int|None, model: str|None, cloud_allowed: bool=False, local_required: bool=False, enabled: bool=True}`
    - `enabled=true`：provider_id 与 model 必填且 model ∈ 该 provider allowed_models（422）；upsert 行。
    - `enabled=false`：显式停用（行保留 enabled=False，provider/model 可空）→ resolve 走 `setting_disabled`。
    - `provider_id=None, model=None, enabled=true` → 422（继承平台默认请用 DELETE）。
  - `DELETE /spaces/{space_id}/model-settings/{agent_kind}` → 删行 = 恢复平台默认继承。
- 响应不含任何密钥形态字段；space_id 归属校验失败 404 `SPACE_NOT_FOUND`。

### 3.3 旧端点处置（D6/A2）

- 删除旧 `api/admin_agent.py` 内容（整文件重写为 §3.1）、`main.py:208` 家庭挂载、`backend/tests/test_agent_admin_providers.py`（重写为 §6 admin 域版本）。
- `services/platform_roles.py` 保留（is_platform_operator 仍有家庭域消费方）；`test_system_admin_boundary.py:85,95` 白名单中 `/api/admin/agent` 移除，并新增断言：家庭 client 访问 `/api/admin/agent/providers` 404、admin 域端点在 8002 存在。

## 4. 错误码与 detail（D7 基础）

- `PROVIDER_UNRESOLVED` detail 由 `{"policy_result", "reason"}` 增补 `"platform_default_configured": bool`（api/agent.py:283-288 处从 resolution 取）；其余错误码不动。
- `reason` 词表不变（`no_space_setting` 语义 = 空间行与平台默认均无）。

## 5. 前端：admin-web（R4/A4）

1. `src/api/client.ts`：`adminRequest` method 联合加 `'patch'`；`AdminApiError` 增加可选 `detail: unknown` 字段，解码处（client.ts:176-178）保留 `error.detail`。
2. 新 `src/api/agent-provider.ts`：`listProviders / createProvider / updateProvider / getPlatformDefaults / putPlatformDefaults / getSpaceProviderSettings`，响应经 `decode.ts` 风格逐字段校验（新增 expect 函数），类型进 `src/types/api.ts`。
3. 新 `src/views/AgentProviderAdminView.vue`（单页三区块，全部用既有 `ag-*` 类 + 组件内 scoped 补充）：
   - Provider 注册表：ag-table 列表（名称/kind/api/模型数/allowed_models/enabled/has_secret/updated_at）+ 注册/编辑表单（PATCH 语义：仅提交变更字段；secret 留空=不变、显式输入=轮换、勾选清除=清除）；
   - 平台默认：assistant / steward 两行（Provider 下拉 + model 下拉联动 allowed_models + 清除按钮）；
   - 空间设置排查：输入 space_id → 展示两 kind 行级设置与平台默认状态（只读）。
4. `router/index.ts` 加 `agent-providers` 路由（requiresAuth）；`AdminShell.vue` 导航加链接；`tests/` 更新 `module-boundary.spec.ts` / `types.aligned.spec.ts`（如有路径/类型断言）并新增 `agent-provider.view.spec.ts`（列表渲染/表单提交 payload/平台默认保存/错误 detail 呈现）。

## 6. 前端：家庭端（R5/D7）

1. 新组件 `frontend/src/components/member/SpaceModelSettingsPanel.vue`：assistant / steward 两个区块，各含：当前状态（继承平台默认 / 自选 / 显式停用）、模型下拉（catalog 内 provider+model 两级联动）、云同意开关（仅 kind=openai_compatible 且选中云模型时可见）、"恢复平台默认"（DELETE）、"停用"（enabled=false）。平台默认存在时给出"同意并启用平台默认"一键（PUT 复制默认行 + cloud_allowed=true）。
2. `SpaceManagementView.vue`：`SECTIONS` 加 `models`（label 模型设置）+ `<section v-else-if="activeSection === 'models'">` 挂载面板。
3. API：`frontend/src/api/spaceModelSettings.ts`（或并入 agent.ts，实现定，保持域文件惯例）。
4. `AGENT_ERROR_COPY` 升级（agent.ts）：`friendlyAgentError(code, fallback?, detail?)` 增加可选 detail 参数：
   - `PROVIDER_UNRESOLVED` + `detail.platform_default_configured === false` → "助手模型尚未由平台管理员配置，请联系平台管理员"；
   - `PROVIDER_UNRESOLVED` + `detail.reason === 'cloud_not_allowed'` → "该模型需要云端执行同意，请到 空间管理 → 模型设置 开启"；
   - 其余 `PROVIDER_UNRESOLVED` → "请到 空间管理 → 模型设置 选择模型"；
   - 消费点 `stores/agent.ts:376/581-586`、`components/agent/ErrorNotice.vue:17` 传参适配（SSE 路径无 detail 时落第三句兜底）。

## 7. 测试计划（R6）

后端（`.venv/bin/pytest`，cwd=backend）：
- 重写 `tests/test_admin_agent_providers.py`（admin 域）：admin_client + `admin_session_headers`（conftest.py:317-340）；覆盖旧用例矩阵（403 矩阵改为 401 未登录/403 must_change、注册/secret 双不回显、base_url 422、strict profile、flag off 503、平台默认 PUT/GET 成对校验与 allowlist 校验、空间设置只读视图）+ 审计行断言（admin_access_audits 落库、secret 不入审计）。
- 新 `tests/test_space_model_settings.py`：家庭域权限（未登录 401、非 manager 403）、GET catalog 只含 enabled、PUT 两 kind 互不干扰、enabled=false 显式停用、DELETE 恢复继承、model 不在 allowlist 422。
- 扩展 `tests/test_agent_provider.py`：agent_kind 隔离（steward 行不影响 assistant）、平台默认回退（local 默认直接 allowed / 云默认 denied cloud_not_allowed / 无默认 no_space_setting）、未知 agent_kind 422、显式停用优先于平台默认。
- `tests/test_system_admin_boundary.py`：白名单更新 + 新端点归属断言。
- conftest `_clean_tables` 加 `agent_platform_defaults`。

前端：admin-web `npm test`（client.wiring 扩 method/detail 断言、新 view spec、module-boundary/types.aligned 对齐）；家庭 `npm test`（space-management.spec 分区断言 + agent 错误文案映射 spec）与 `npm run type-check`。

## 8. 明确不做（本任务边界）

- Steward 消费 `agent_kind="steward"` 的执行链/child run 审计（子任务 B）。
- runtime_snapshot_json 结构变更（子任务 B）。
- `controlled_web.py`、`api/admin.py` 死代码、`is_platform_operator` 家庭读路径的清理。
- `main.css` / tokens 的任何改动（A4）。
