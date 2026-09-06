# Implement: Agent 模型治理迁移系统管理员后台与双端配置 UI

> 前置阅读顺序：`prd.md` → `design.md`（裁定 A1–A5 必须遵守）→ `research/current-state.md`（文件:行号锚点）。实现顺序按阶段推进，每阶段末跑对应验证。

## 阶段 1：数据层（模型 + 迁移）

1. `backend/app/models/agent_provider.py`：
   - `AgentSpaceProviderSetting` 加 `agent_kind`（String(32) default "assistant" nullable=False）+ `__table_args__` 复合唯一 `UniqueConstraint("space_id","agent_kind",name="uq_asps_space_agent")` + CHECK 约束；
   - 新增 `AgentPlatformDefault`（design §1.2，照 models/controlled_web.py WebPlatformConfig 风格）；`models/__init__.py` 导出。
2. 新迁移 `backend/migrations/versions/0033_agent_space_provider_settings_agent_kind.py`（down_revision="0032_invite_codes_creator_set_null"）：按 design §1.1 整表重建 + 建 `agent_platform_defaults`；downgrade fail-closed（存在 steward 行 raise RuntimeError）。
3. `backend/tests/conftest.py`：`_clean_tables` 清单加 `agent_platform_defaults`。
4. 验证：`cd backend && .venv/bin/alembic upgrade head`（或直接跑一次 pytest 让 conftest 验证迁移链）。

## 阶段 2：Service 层

5. `backend/app/services/agent_provider.py` 按 design §2：
   - `resolve_for_space` 加 `agent_kind="assistant"` 参数（校验 fail-closed 422）+ 查询过滤 + 平台默认回退（虚拟 setting）；
   - `ProviderResolution.platform_default_configured` additive 字段；
   - `snapshot_for_space` / `resolve_for_run` / `resolve_runtime` 透传参数（默认 assistant，snapshot json 不动）；
   - `get_platform_defaults` / `set_platform_defaults` helpers。
6. 验证：现有 `tests/test_agent_provider.py` 全绿（默认参数路径行为不变）。

## 阶段 3：后端端点

7. 重写 `backend/app/api/admin_agent.py` 为管理员域 router（design §3.1）：prefix `/admin-api/v1`、`require_admin_ready`、runtime 503 门禁、审计走 `admin_audit.record_access`（A1）；schema 扩展进 `backend/app/schemas/agent.py`（AgentKind、平台默认请求/响应、SpaceModelSettings 系列，_Strict 风格）。
8. `backend/app/main.py`：删除 `app.include_router(admin_agent_router, prefix="/api/admin/agent")`（:208），改为 `admin_app.include_router(admin_agent_router)`（admin_app 构建块 :259-264 处，import :25 保留）。⚠️ main.py 有未提交的无关 hunk（视觉任务），只改本任务相关行。
9. 新建 `backend/app/api/space_model_settings.py`（design §3.2）：家庭域 owner 端点，权限依赖对齐 `api/spaces.py` update_space 所用依赖；挂载 main.py 家庭 app（同样注意只动本任务行）。
10. `api/agent.py:274` 消费点显式 `agent_kind="assistant"`；`PROVIDER_UNRESOLVED` detail 增补 `platform_default_configured`（design §4）。
11. 后端测试：按 design §7 重写/新增/扩展（test_admin_agent_providers、test_space_model_settings、test_agent_provider 扩展、test_system_admin_boundary 白名单）。
12. 验证：`cd backend && .venv/bin/pytest`（全量）。

## 阶段 4：admin-web

13. `system-admin-frontend/src/api/client.ts`：method 加 `'patch'`、AdminApiError 携带 detail（design §5.1）。
14. `src/api/agent-provider.ts` + `src/types/api.ts` 类型 + decode 校验函数。
15. `src/views/AgentProviderAdminView.vue`（三区块，ag-* 类 + scoped）+ `router/index.ts` + `AdminShell.vue` 导航。
16. admin-web 测试：client.wiring / module-boundary / types.aligned 对齐更新 + 新 `tests/agent-provider.view.spec.ts`。
17. 验证：`cd system-admin-frontend && npm test && npm run type-check`。⚠️ 不改 `src/styles/main.css`（A4）。

## 阶段 5：家庭前端

18. `frontend/src/api/` 新增 model-settings 封装 + `AGENT_ERROR_COPY`/`friendlyAgentError` 升级（design §6.4，消费点三处适配）。
19. 新组件 `frontend/src/components/member/SpaceModelSettingsPanel.vue` + `SpaceManagementView.vue` SECTIONS/section 挂载。
20. 前端测试：space-management.spec 加分区断言、错误文案映射 spec；`npm test && npm run type-check`。

## 阶段 6：收尾核对

21. grep 复核：`/api/admin/agent` 全库无残留引用（除 git 历史）；`require_platform_operator` 仅剩 design §3.3/A2 白名单位置；secret 字段不出现在任何响应/审计路径。
22. 全量回归：backend pytest + 两侧前端 npm test + type-check。

## 边界提醒

- 不动：`controlled_web.py`、`api/admin.py` 死代码、`deps.require_platform_principal`、`is_platform_operator` 读路径、`main.css`、runtime_snapshot_json 结构。
- 提交（Phase 3.4 由主会话执行）：只 stage 本任务文件；`main.py` 需 hunk 级分离（A5）。
