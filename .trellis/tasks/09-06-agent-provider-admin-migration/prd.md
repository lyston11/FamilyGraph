# Agent 模型治理迁移系统管理员后台与双端配置 UI

> 父任务：`09-06-agent-model-config-system`（D1–D7、D10 决策记录见父 PRD）。本任务 = 决策 D1/D2/D3/D4/D5/D6/D7 的实现。

## Goal

把 Agent 模型 Provider 治理整体迁到系统管理员后台（:8002 admin_app / ADMIN_JWT），admin-web 新增 Provider 管理页；家庭前端"空间管理"新增模型设置页（assistant / steward 双 agent 维度）。修复"报错文案承诺的管理页不存在、platform_operator 无授予入口、治理 API 零调用方"的闭环缺陷。

## 现状锚点（实现前核对）

- 旧端点：`backend/app/api/admin_agent.py`（挂 `/api/admin/agent`，main.py:208），鉴权 `require_platform_operator`（services/platform_roles.py）。注册/列表/更新：POST/GET/PATCH `/providers`；空间绑定：PUT `/spaces/{space_id}/provider-settings`。密钥经 `utils/secretbox` 加密落库，响应只含 `has_secret`。
- 解析链：`services/agent_provider.resolve_for_space`（assistant 在 `api/agent.py:285` 消费）；策略常量 POLICY_ALLOWED / DENIED / DENIED_NO_LOCAL / DENIED_CLOUD_FORBIDDEN。
- 模型表：`agent_providers`、`agent_space_provider_settings`（models/agent_provider.py）。
- 管理员域：`admin_app`（main.py:251+，serve.py ADMIN_PORT 8002，`/admin-api` 前缀）；现有 router：admin_auth / admin_read / admin_governance；鉴权 ADMIN_JWT（独立签发域）。admin-web 前端：`system-admin-frontend/`（用户名+强密码登录）。
- 标准云档位（代码固定）：`liu-dada` / `gpt-5.6-sol` / `openai-responses` / `https://api.liu-dada.com/v1`（services/agent_provider.py:32-38）。

## Requirements

### R1. 后端：治理端点迁移（D1/D6）

- 新增 admin_app router（建议 `api/admin_agent_admin.py` 或并入 admin_governance，命名在 design 定），前缀 `/admin-api/v1`，ADMIN_JWT + `require_admin_ready` 门禁，沿用 `AGENT_RUNTIME_ENABLED` 关闭即 503 的行为。
- 能力平移并扩展：
  - Provider 注册/列表/更新/启用停用（secret 只写不读，响应仅 has_secret；写审计，operator 归属改为 system_admin 归属）；
  - 平台默认模型设置（见 R3）；
  - 空间 Provider 设置的**只读**视图（供管理员排查，不代替 owner 选择）。
- 删除旧 `api/admin_agent.py`、`require_platform_operator` 依赖链及其测试；`platform_role_assignments` 表与数据保留，代码不再读写（迁移不加删表 DDL，仅注释说明废弃）。
- 审计落 `audit_log`（管理员域既有审计机制为准）。

### R2. 数据模型：agent 维度与平台默认（D4/D5）

- `agent_space_provider_settings` 增加 `agent_kind` 列（`assistant | steward`，默认 `assistant` 回填存量行），唯一键扩为 `(space_id, agent_kind)`；迁移走 Alembic。
- 平台默认：新增平台级配置存储（单行表或等价机制，design 定）：默认 provider_id + 每 agent_kind 的默认 model；允许为空（未设默认时新空间维持 PROVIDER_UNRESOLVED 现状）。
- `resolve_for_space` 扩展：按 agent_kind 解析（assistant 既有调用传 `"assistant"`，steward 由子任务 B 消费）；顺序 = 空间显式设置 → 平台默认（cloud_allowed 语义见 R3）→ 无则 PROVIDER_UNRESOLVED。无静默 fallback 合同不变。

### R3. 权限与同意语义（D3/D4）

- 管理员：维护 Provider 注册表与平台默认；**不**替 owner 打开云同意。
- 平台默认只决定"通道与模型档位"；空间级 `cloud_allowed` 仍归 owner（默认 False 不变）。即：平台默认继承后，云模型仍需 owner 在空间设置里显式同意云执行才可路由云。
- owner 可随时改掉或明确停用继承（停用 = 该 agent_kind 显式无模型，报错文案走 D7 第一句）。

### R4. admin-web：Provider 管理页

- 新页面：Provider 列表（名称/类型/接口/允许模型/启用/has_secret）、注册与编辑表单、平台默认设置区。
- 风格沿用 system-admin-frontend 现有 cosmic-glass 体系（09-05 任务已重设计）；所有操作有确认与错误呈现。

### R5. 家庭前端：空间模型设置页（D3/D5/D7）

- "空间管理"新增"模型设置"区块：assistant 与 steward 两个独立选择器（仅列管理员允许目录内的模型）、每 agent 的云同意开关、恢复平台默认/明确停用操作。
- 报错文案改造（`frontend/src/api/agent.ts` AGENT_ERROR_COPY）：通道未配置 →"助手模型尚未由平台管理员配置，请联系平台管理员"；通道已有但空间未选/未同意云 →"请到 空间管理 → 模型设置 选择"。错误码承载足够信息区分两种态（后端 error detail 配合）。

### R6. 测试与迁移

- Alembic 空库迁移链可复现（加列 + 平台默认存储 + 存量回填）。
- 迁移/解析/权限测试：agent_kind 隔离、平台默认继承与覆盖、cloud_allowed 门禁、旧端点 404、admin JWT 门禁、AGENT_RUNTIME_ENABLED=0 时 503、审计写入、secretbox 只写不读回归。

## Acceptance Criteria

- [ ] AC-1：admin-web 可完成 Provider 注册→更新→停用全流程，响应无任何密钥明文/密文，操作可在 audit_log 追溯到 system_admin 账号。
- [ ] AC-2：owner 在空间设置分别为 assistant/steward 选模型并开云同意后，助手发消息走通（空间绑定生效）；关闭云同意后云模型请求被拒且错误可解释（DENIED_CLOUD_FORBIDDEN 语义）。
- [ ] AC-3：管理员设平台默认后，新建空间未做任何 owner 操作即可用默认模型（云模型仍需 owner 同意云执行）；owner 覆盖/停用行为正确。
- [ ] AC-4：旧 `/api/admin/agent/*` 全部 404/410，platform_operator 代码路径无残留引用；家庭域用户即使持有有效 token 也无法调用治理端点。
- [ ] AC-5：`resolve_for_space` 对 assistant/steward 两 kind 的解析顺序、继承、停用均有测试；无任何静默 fallback 路径。
- [ ] AC-6：空库迁移链通过；AGENT_RUNTIME_ENABLED=0 时治理端点 503。

## Out Of Scope

- Steward 消费模型配置的执行层（子任务 B）。
- Provider/密钥的品牌扩展与计费（沿用 STANDARD_* 代码档位机制）。
- 多管理员细粒度权限（admin 域现有单一角色模型不变）。
