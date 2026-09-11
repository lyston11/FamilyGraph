# Release Evidence — 09-11 frontend-system-admin-audit-remediation

执行时间：2026-09-11 晚（本地开发机，macOS arm64）
执行人：lyston（ZCode 会话）

## 门禁结果（全部真实执行）

| 门禁 | 结果 |
| --- | --- |
| 家庭前端 `npm run lint` | 通过（零输出） |
| 家庭前端 `npm run type-check`（vue-tsc） | 通过 |
| 家庭前端 `npm run test` | 通过：63 文件 / 521 用例；stderr 警告 **0**（jsdom XHR、Vue router injection、navigation 警告全部消除） |
| 家庭前端 `npm run build` | 通过且零 chunk 警告（见性能基线） |
| 后台前端 `npm run lint` / `type-check` | 通过 |
| 后台前端 `npm test` | 通过：13 文件 / 83 用例（含新增 admin-shell.mobile 3 例、steward.ops 4 例） |
| 后台前端 `npm run build` | 通过 |
| 真实 API smoke `./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json` | 通过：30/30 用例，退出码 0（脱敏报告见下） |
| 后端 `pytest` | 873 passed / 3 skipped |
| 后端 `ruff check` / `ruff format --check` / `mypy` | **存在失败，但全部位于本任务未触碰的文件**（`app/api/space_model_settings.py`、`app/api/admin_agent.py`、`app/services/steward_assist.py`、`tests/test_space_lineage_link.py`、`tests/test_space_model_settings.py`、`tests/test_steward_assist.py` 等）——属工作区内其他进行中任务（09-11 steward 系列、space_model_settings）的未提交改动，按任务边界本任务不修改、不提交、不代为修复。 |

## 真实 API smoke 环境

- 隔离临时 `DATA_DIR`（mkdtemp）+ alembic upgrade head + 随机 SECRET_KEY/ADMIN_JWT_SECRET
  （不同签发域）+ DEV_SEED_DEMO_DATA=1 合成数据 + BCRYPT_ROUNDS=4；
- 随机端口真实三 listener（`python -m app.serve`）；运行结束删除临时目录；
- AGENT_RUNTIME_ENABLED=1（AGENT_SERVICE_SECRET 随机）、PERSONAL_FAMILY_VIEW/MEMORY/RAG 开启；
  Steward worker 关闭（与 dev 默认一致）。

### 覆盖用例（30）

家庭：login / refresh / me / spaces / household-card（200 + ETag 304）/ pfv / stats /
notifications / read-all / memories / agent session 创建 / SSE 未知 run 404 终态 /
附件上传（Pillow 生成真实 PNG）→ 授权 raw 200 → 无关主体 raw 404（防枚举）→ 删除 204。
后台：bootstrap 凭据登录 / 首登改密 / 重新登录 / refresh / me / overview / 空间详情 /
治理写探针（未知申请 reject → 404 统一外壳）。
边界：家庭 token 打后台 401；admin token 打家庭 401；家庭 listener 上 `/admin-api/*`
404 与随机未知路由 404 **content 字节一致**；未认证 spaces 401。

### 外部依赖说明

- smoke 不调用外部模型 provider：Agent 会话创建（本地合同）+ 未知 run SSE 终态断言；
  provider 链路的真实成功不在本任务证据范围（与 PRD out-of-scope 一致，未伪造）。
- 未宣称部署/发布完成；仅完成本地隔离环境的真实链路验证。

## 性能基线（家庭端生产构建）

| 指标 | 整改前 | 整改后 |
| --- | --- | --- |
| 业务主 chunk `index.js` | 624.85 kB | 358 kB（最大业务块；另有 vue-core 110 kB 框架拆分） |
| 动态/静态 import 冲突警告 | 存在（stores/spaces、stores/agent 被动态+静态混合引用） | **消除**（auth.ts 全部改为静态导入；AppShell 改 defineAsyncComponent） |
| chunk >500kB 警告 | index 625kB + three 735kB | **零**（`chunkSizeWarningLimit: 750` 仅为 three.module 734.52 kB 单一三方库保留，属经记录的阈值例外；three 由 CosmicBackdrop 按需 `await import('three')`，仅星空视图加载） |

## 测试噪音治理

- settings.spec：19 次 jsdom 真实 XHR AggregateError → 补 `@/api/bindings`、`@/api/inviteCodes` mock；
- notifications.spec / AgentPrimitives.spec / AssistantPanel.spec：router injection 警告 →
  注入内存路由 / mock vue-router；
- DataRightsPanel 注销跳转 `window.location.assign` → `router.push`（消除 navigation 警告）；
- 全量套件 stderr 警告归零（未使用 console 静默）。

## Blocked / Deferred / 依赖记录

1. **后端 lint/format/mypy 失败**：均属其他任务未提交改动（见上表），本任务边界外。
2. **Steward 重跑 UI**：后端 `POST /admin-api/v1/steward/spaces/{id}/rerun` 合同已存在
   （reason + Idempotency-Key + expected_policy_version），但现行策略版本未在
   `steward/status` DTO 暴露，前端无法安全填参（会稳定 409）。面板显示明确 disabled
   入口 + 原因说明；待 steward ops 子任务在 DTO 暴露 policy_version 后接线。
3. **README.md 提交**：工作区 README 已有他人未提交改动（dev-up 一键启动文档），
   为避免混入无关 hunk，本次提交不含 README（smoke runbook 段落在工作树中，随
   dev-up 文档一并提交或由用户确认后补提）。

## 提交清单（仅本任务文件）

- frontend：附件媒体认证（attachments.ts + AttachmentsSection.vue + 2 个新 spec）、
  错误语义（loadError.ts 新增 + 4 个 view + types/api.ts + 3 个 api 注释）、
  性能（App.vue、stores/auth.ts、vite.config.ts、DataRightsPanel.vue）、测试修复
  （settings/notifications/AgentPrimitives/AssistantPanel spec）
- system-admin-frontend：shared token 接入（main.css）、移动导航（AdminShell.vue + spec）、
  Steward 观测面板（api/steward.ts、StewardOpsPanel.vue、AgentMonitorView.vue、spec）
- shared/brand-tokens.css（新增）
- scripts/smoke/run_api_smoke.py、scripts/frontend-api-smoke.sh（新增）
- .trellis/spec/frontend/quality-guidelines.md（4 条新约定）
- .trellis/tasks/09-11-frontend-system-admin-audit-remediation/（任务工件 + 本文件）
