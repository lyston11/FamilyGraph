# FamilyGraph 系统架构与设计

> 更新于2026-09-14，本次Memory/RAG实现与验收基线为累计`aebee83`/`dd8157c`，已包含`main@461d691`的独立管理员平台、Provider/功能治理、个人家族视图和管家称谓闭环。主线集成、任务归档与后续边界见 [Trellis交接](../.trellis/HANDOFF.md)。
> API 签名、错误矩阵和测试清单以当前代码、迁移及测试为准；后续设计看 `.trellis/tasks/` 中对应任务，工作流见根目录 [AGENTS.md](../AGENTS.md)。已标为历史资料的 `.trellis/spec/` 不覆盖现行合同。本文将主线实现与尚在分支推进的设计分开说明。
> 数据播种、默认管理员来源与存量部署升级处置见 [DEV-DATA-SEEDING.md](./DEV-DATA-SEEDING.md)。

## 1. 系统总览

现代家谱协作平台，由三个产品面组成：

```text
┌─────────────────────────┐        ┌──────────────────────────────┐
│ 家庭用户端（唯一公开面）  │        │ 系统管理员后台（仅运维可见）    │
│ frontend/               │        │ system-admin-frontend/       │
│ 家庭账号：名字 + PIN     │        │ 管理员账号：用户名 + 强密码    │
│ 家谱协作 / 档案 / 关系    │        │ 业务监控 / 申请审批 / 平台治理 │
└───────────┬─────────────┘        └──────────────┬───────────────┘
            │ /api                                │ /admin-api
            ▼                                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ api 容器（单进程三 listener，共享 engine/lifespan，不共享路由）      │
│  family_app :8000 ── 家庭认证 + 家庭业务                          │
│  internal   :8001 ── agent sidecar 内部协议（不发布宿主）           │
│  admin_app  :8002 ── 管理员认证 + 读取/审批/平台治理（不发布宿主）    │
└───────────────────────────────┬─────────────────────────────────┘
                                │ /internal/agent
                                ▼
                       Assistant sidecar（Pi / 工具执行）
```

- **家庭用户端不提供系统管理员入口**：独立后台的路由、认证和签发域不混入家庭端；未注册深链（`/system-admin`、`/admin`、`/admin-api/*`）统一普通 404。旧 `/api/admin/agent/*` Provider 治理入口已移除。
- **后台是独立产品面**，不是家庭 SPA 中的隐藏角色：独立前端项目、独立端口与 origin、独立浏览器存储、独立 JWT 签发域、独立 API listener 与 Docker 网络。
- **Assistant**：sidecar 容器通过 Pi 执行对话与工具调用；模型 API key 不出后端（ProviderGateway 代理），与 api 之间走 internal 8001 协议。
- **Steward**：后端领域作业执行确定性关系/视图计算，可选模型辅助通过独立 batch/attempt 接受预算、Provider、租约和写回校验；不是后台读取私人聊天的入口。

## 2. 运行拓扑与端口矩阵

| 面 | 开发 | 生产 | 说明 |
|---|---|---|---|
| 家庭前端 | `5173`（Vite，代理 `/api`→8000） | `8080`（nginx 容器） | 唯一公开 Web 面 |
| 家庭 API | `8000`（uvicorn） | `8000`（发布宿主） | `/api/*` |
| agent 内部协议 | `8001` | 仅 backend 网络接口 `172.28.0.10` | 不发布宿主 |
| 管理员前端 | `5174`（Vite，代理 `/admin-api`→8002） | `127.0.0.1:8081`（nginx 容器） | 仅回环，VPN/内网反代进入 |
| 管理员 API | `8002` | 仅 admin 网络接口 `172.29.0.10` | **不发布宿主** |

Docker 网络隔离（compose）：

- `frontend`：web ↔ api（对外出口）。
- `backend`（internal:true）：api ↔ agent sidecar；sidecar 无外网 egress。
- `admin`（internal:true）：admin-web ↔ api 的 8002 接口；宿主/公网/家庭 web 均不可路由。

Nginx 合同：家庭 nginx 对 `/admin-api/`、`/system-admin`、`/admin` 返回普通 404，仅代理 `/api/`；后台 nginx 仅代理 `/admin-api/` → `api:8002`，对 `/api/`、`/internal/` 返回 404。

## 3. 认证与主体模型

### 3.1 两个互斥的主体域

| | 家庭用户 | 系统管理员 |
|---|---|---|
| 主体模型 | `users` + `accounts`（名字 + PIN） | `system_admins` + `system_admin_accounts`（用户名 + 密码哈希） |
| API 面 | 8000 `/api/auth/*` | 8002 `/admin-api/auth/*` |
| JWT 签发域 | `SECRET_KEY` + family issuer/audience | `ADMIN_JWT_SECRET` + `ADMIN_JWT_ISSUER/AUDIENCE`（缺失/过弱拒启） |
| 会话存储 | `fg.refresh_token`（家庭 origin） | `fg.admin.refresh_token`（后台 origin） |
| access token | 内存 | 内存 |
| 首登 | PIN 首改（家庭合同不变） | `password_must_change` 强制改密 |

- JWT claims 携带 `principal_type`（family 侧恒 `family_user`；admin 侧 `system_admin`）+ `token_version` + `jti`；**issuer/audience/secret 物理隔离**——把任一方 token 复制到另一个 listener 必然被拒，不能只靠 `principal_type` 区分。
- 会话撤销触发器：改密码/用户名、锁定、运维恢复 → `password_version+1` 且 refresh session 全撤销；refresh 轮换不续期（绝对有效期）。
- 登录失败达到阈值锁定返回 429；错误文案统一，不泄露账号存在性。

### 3.2 管理员账号生命周期（无公开初始化）

- 部署启动 preflight 在事务锁内保证唯一 active 管理员：空库时创建用户名 `admin`，CSPRNG 随机强密码**只**写入数据卷 `/data/bootstrap/admin-credentials`（`0600`，原子写）。
- 首次登录强制改密；改密事务提交后删除凭据文件，删除失败写安全告警且不标记完成。
- 忘记密码走受限运维恢复：`docker compose exec api python -m app.admin_recovery`（一次性密码只落 0600 文件，旧会话全撤销）。
- 密码只存哈希；初始/恢复密码不进日志、数据库明文或任何前端产物。MFA 为后续任务。

## 4. 后台数据边界与平台治理

### 4.1 信息架构

后台以 **active `space_admin` 为聚合根**：空间管理员 → 其管理的家族空间 → 成员/档案/关系/已确认事实 → 空间健康与治理状态。授权与聚合只认 `SpaceMember(role='space_admin', status='active')`；`owner_id`、`is_admin`、其他空间角色、家庭 visibility 链一律不作后台依据。无管理员/双管理员/锁定或被删管理员的空间进入**异常队列**，只读展示、不自动修复。

### 4.2 数据分级

**允许（普通读取 + 审计）**：账号与空间运营状态、成员关系、基础档案（姓名/生卒/性别/简介/档案状态）、结构化关系边与已确认事实、附件安全元数据（id/type/title_safe/created_at）、运营队列与通知、Agent/job 运行元数据（状态/耗时/重试/错误码/工具名/资源 ID）。

**敏感详情（需 30 分钟目标绑定访问会话 + 理由）**：单用户/单空间档案钻取、头像缩略图（专用鉴权端点，不暴露路径）、附件列表；响应 `Cache-Control: no-store`，票据只存后台前端内存。

**永久禁止**：PIN/密码哈希、JWT/refresh token、`SECRET_KEY`/`ADMIN_JWT_*`、Memory/Session/AgentMessage/RAG 原文、prompt 与模型上下文、`RawRelationInput.text` 与关系证据原文、附件原文件与 `url_or_path`、精确地址/学校单位/健康信息/未成年人敏感字段、批量导出与全库快照。Agent 原始错误仅服务端保留，API 只返回二次脱敏诊断（脱敏器 fail-closed：不可靠即只给 error_code + 安全位置）。

**合同红线**：所有响应走专用 Pydantic schema（`extra="forbid"`）+ 显式列投影，禁止 ORM 直接序列化或复用家庭 visibility API。字段白名单以模型实况为准（如 `User` 无 `updated_at`、`Attachment` 无 `size`，不得虚构）。

### 4.3 审计与审批

- 独立审计表 `admin_access_sessions` / `admin_access_audits` 直接 FK `system_admin_id`；记录 reason/scopes/endpoint/filters/result_count/request_id/IP；永久保留（业务删除 SET NULL 不级联）；不保存响应正文与敏感值。
- **家庭业务裁决**：空间管理员申请 approve（理由可选）/ reject（理由必填），均需显式二次确认；复用既有单事务命令（状态 + 原管理员 consent + 唯一 active `space_admin` + 领域事件 + 双侧审计）；终态不可改判（重复裁决 409）。不触碰 `family_spaces.owner_id`；旧 `backend/app/api/admin.py` 的 break-glass 能力永不注册。
- **平台治理另有受控写入口**：Provider 注册/更新、双 Agent 平台默认、Memory/RAG 与四类 Steward assist 开关、Steward 作业重跑，均在 `/admin-api/v1` 下鉴权并审计。不能再用“后台除审批外没有写端点”描述现状；这些操作不授予私人 Memory、会话或关系证据原文的读取权。
- 系统管理员提供 Provider 和平台默认，空间管理员通过家庭侧 `/api/spaces/{space_id}/model-settings` 管理本空间选择与云同意。后台空间 `provider-settings` 是只读排查接口，不能替空间管理员开启 `cloud_allowed`。

## 5. 前端架构

```text
frontend/                    家庭 SPA（Vue3 + Vite + TS + Pinia + Naive UI）
system-admin-frontend/       后台 SPA（同技术栈，独立项目与构建）
```

- **零共享**：两个 SPA 不互相 import（CI 有模块图断言）；API client、store、router、types、构建产物完全独立。
- 认证合同：登录响应硬校验主体/白名单字段后才落会话；`password_must_change` 守卫只放行改密页；session expired 回各自登录页；redirect 只接受站内白名单。
- 后台交互合同：敏感详情先弹理由表单 → 创建访问会话 → 票据仅内存（`X-Admin-Access-Session`）；过期/403 引导重新授权。申请审批保留二次确认，Provider、平台开关与作业运维使用各自的受控治理界面。
- Agent 监控 5 秒轮询，`document.hidden` 暂停，卸载清理 timer 与 in-flight 请求。

## 6. 后端分层

```text
backend/app/
├── main.py          # 三 app（family/internal/admin）+ lifespan preflight + 普通 404 catch-all
├── serve.py         # 三 listener 启动/端口预检/共享信号优雅停机
├── api/             # 路由层：家庭业务（users/spaces/graph/...）
│                    #   admin_auth.py admin_deps.py        → 8002 认证与门禁
│                    #   admin_read.py admin_governance.py   → 8002 只读模型 + 审批
│                    #   admin_agent.py / admin_platform_features.py → 8002 模型与功能治理
│                    #   admin_steward.py / admin_agent_latency.py → 8002 作业运维/只读指标
│                    #   controlled_web.py → 8000 受控联网与遗留平台配置面
│                    #   admin.py（旧 break-glass，永不注册，仅作回归断言对象）
├── services/        # 领域服务：visibility（家庭授权单点）
│                    #   admin_auth / admin_bootstrap / admin_read_model
│                    #   admin_access_sessions / admin_audit / admin_sanitizer
├── models/          # ORM：业务表 + system_admin + admin_access（0028/0029 起持续演进）
├── schemas/         # Pydantic：admin_read.py 为字段白名单唯一来源
└── utils/           # security.py（家庭 PIN/JWT）；admin_security.py（admin JWT 签发域）
```

规则：api → services / application commands → models；家庭数据出口必须经`visibility.py`；admin读模型禁止复用家庭visibility链。本次累计验证迁移单头为`0047_rag_lifecycle_integrity`，已包含RAG与称谓0044分叉的合并。渐进重算仍有独立迁移；后续集成按 [交接检查点](../.trellis/HANDOFF.md) 核对实际DAG，代码版本不等于生产数据库已执行迁移。

路由实况以 [main.py](../backend/app/main.py) 为准。受控联网的遗留 `/api/admin/web/platform` 仍由原 `platform_operator` 路径处理，并未随 Provider 治理迁移成为系统管理员后台接口；不能据此恢复已删除的 `/api/admin/agent/*`。

## 7. 关系、个人家族视图与称谓

全局人物/关系数据与空间成员授权分开；`PersonalFamilyView` 是绑定查看者和空间的派生视图。已确认结构拓扑、个人称谓摘要和独立推测层分别输出，模型候选不覆盖已确认 `SourceFact`。

```text
获权 SourceFact → relationship_resolver → Terms → PersonalFamilyView
                                           ↑
                         本人词条 / 空间词条 / 有效管家个人投影
认证查看者 → kinship_presentation → 通知 / 建议 / 推测详情 / 档案呈现
```

- 称谓优先级为本人词条→生效空间词条→有效 Steward 个人投影→locale/system 与长幼、长链、结构回退。TermRegistry 是词条真源，自动投影是可重建显示缓存。
- 自动计算与合法称谓改善无需逐条批准。新 `term_preference` 不发待办通知；档案称谓区提供“保留为我的叫法”和“恢复默认叫法”，后者以当前语义和版本校验后抑制重复应用。
- 服务端 `presentation` 绑定认证查看者，统一方向句、参照人、来源和证据状态。前端不将内部原子关系枚举直接翻译为面向用户的称谓；无合法路径时安全降级，不能显示旧授权结果。
- 真实关系确认、成员加入和资料授权继续走既有状态机；自动投影不生成事实、不伪造人类用词证据。
- 通知、建议详情、推测树和允许动作按当前账号有效状态解码；跨账号/空间/目标切换拒收迟到响应。普通 GET 不调用模型或创建事实。

实现入口：[kinship_presentation.py](../backend/app/services/kinship_presentation.py)、[steward_terminology.py](../backend/app/services/steward_terminology.py)、[personal_family_view.py](../backend/app/services/personal_family_view.py)。需求与验证见 [称谓闭环归档](../.trellis/tasks/archive/2026-09/09-13-steward-kinship-capability-closure/prd.md)及其 [质量审核](../.trellis/tasks/archive/2026-09/09-13-steward-kinship-capability-closure/research/quality-review-2026-09-14.md)。

**渐进视图属于实施中的后继设计**：一致读快照、事务外计算、版本化目标结果、CAS 原子发布、水位与交付待办，配合先骨架后称谓的前端。主线不能冒称已具备这套完整协议；状态、迁移冲突和未完成性能验收见 [渐进重算任务](../.trellis/tasks/09-13-steward-snapshot-progressive-recompute/implement.md)。

## 8. 双 Agent、Memory/RAG 与开关

### 8.1 已验收实现与后续能力

| 范围 | 已验收行为 | 尚在独立任务推进的部分 |
| --- | --- | --- |
| Assistant | 同会话文字恢复进Pi的同一个manager；一次FTS5预取支持中文短词、受控别名与唯一明确前文；签名attempt贯穿实际执行，服务端精确引用统一投影 | 主动检索工具、通用语义检索、全请求预算和跨Run持久摘要仍需独立采用；RAG子预算不等于全模型请求预算 |
| Memory | 手工/RAG来源经候选→明确确认→保存；服务端重验来源、revision、scope和敏感度，安全重试不重复，legacy/unverified保持隔离 | 自动聊天候选、显式聊天保存与授权导入仍有单独采用门槛；物理擦除和新revision编辑未默认实施 |
| RAG索引 | 规范来源唯一键、完整输入摘要、不可变chunk与活动版本；有界lease维护支持晚开启补建，FTS修复与换版分离，历史合法引用精确读取 | 缺乏可信完整输入的旧来源保留并报告；不以重建认证未知来源或恢复source tombstone，生产规模延迟未测 |
| Steward | 确定性领域计算；候选、排序、解释、称谓四类受约束 assist；持久化批次、去重和有限反馈 | shared RAG、通用反馈学习、MR-23 候选证据版本及 MR-26 行为投影键族仍按各自任务推进，不能写成已接通 |

称谓偏好是已实现的领域记忆。它不意味着 Steward 已能检索私人聊天，也不意味着 Assistant 的会话已自动进入长期 Memory。Pi 压缩一致性修复与全请求预算、跨 Run 持久摘要分别验收。

后续 shared RAG 设计仅允许 Steward 以自己的 job/space/consumer 身份使用当前获权的 confirmed shared 内容，保留来源撤销、引用版本、本地 Provider 要求及成本边界；不伪造 Assistant Run、不读取 private memory/session、不覆盖确定性事实。

需求与后续边界见 [Memory/RAG总任务](../.trellis/tasks/09-13-agent-memory-rag-remediation/prd.md)、[最终独立验收](../.trellis/tasks/09-14-memory-rag-acceptance-audit/research/final-acceptance.md)及 [能力采用登记](../.trellis/tasks/09-13-agent-memory-capability-plan/research/capability-decision-register.md)。原B/D20组缺口和追加恢复回归均已闭合；检索扩展集仍为7/10，真实模型质量和生产状态未据此推定。

### 8.2 开关与模型准入

当前 [platform_features.py](../backend/app/services/platform_features.py) 的有效值规则：平台行不存在时使用环境值；平台行存在时，Memory、RAG 和各类 Steward assist 均为数据库值与部署环境值的 AND，环境关闭是部署级总开关。

- Steward 四类为 `candidate`、`ranking`、`explanation`、`terminology`，还须通过相应空间开关、引擎/runtime、Provider 选择、云同意/本地要求、预算与实时授权；开关有效不等于请求已获授权。
- 系统治理响应同时提供有效值和来源/原因；旧客户端遗漏新增可选字段时保留现值，不静默关闭其他能力。
- `candidate` 仍只产原子线索；`terminology` 只为一个 viewer 的有界目标优化叫法，服务端重验路径、方向、语义和长幼，不信模型自报概念码。
- `STEWARD_ASSIST_TERMINOLOGY` 默认关闭。已合入并用 fake transport 验证的调用链，不代表当前环境已开启或真实模型质量已验证。

## 9. 安全不变量（涉及边界改动时复核）

修改认证、授权、路由注册、数据投影、附件、日志或部署网络时，复核以下不变量并运行对应回归测试；纯文档、样式或不触及这些边界的局部改动无需逐条重读。

1. 独立系统后台与家庭端隔离：家庭 bundle、路由表、OpenAPI 不提供系统管理员入口或签发域；保持构建扫描与 listener 回归。
2. 未注册路径统一普通 404，不用 403/重定向/自定义错误页（存在性 oracle 红线）。
3. 8002 不发布宿主；admin 网络仅 admin-web 可达；生产 admin web 仅回环绑定。
4. 两套签发域互拒；`ADMIN_JWT_*` 缺失或与家庭 `SECRET_KEY` 同值即拒启，无开发逃逸。
5. 后台业务读取响应、审计和日志不包含密码哈希、密钥、私人会话/记忆、证据原文或附件原文；认证与受控凭据交付遵循各自专用通道，不能借只读模型输出。
6. 后台业务裁决与平台治理分别按显式白名单注册、鉴权、审计；Provider、功能开关和作业运维不授予家庭内容写入权；旧 `admin.py` 永不注册。
7. 敏感详情必须带 30 分钟目标绑定会话 + 理由，全部读取留痕。
8. 字段白名单精确集合断言；模型没有的字段不得虚构。

## 10. 按风险选择验证

验证范围与改动面匹配。提交前完成受影响包的针对性检查；跨层、认证授权、迁移、部署或无法确定影响面的改动再扩大到完整门禁。低风险可逆改动不强制运行全部套件。

| 风险/范围 | 检查 |
|---|---|
| 后端局部逻辑 | 相关 pytest + `ruff check`；公共类型变化加 `mypy app` |
| 任一前端局部改动 | 对应项目 `npm run lint`、`npm run type-check`；测试行为变化加 `npm test` |
| 构建、依赖或发布配置 | 对应项目完整检查并 `npm run build` |
| 认证、授权、listener 隔离、迁移、跨前后端契约 | 相关回归红线测试；必要时执行真实 API smoke |
| 发布或影响面不明 | 三个包完整检查，并执行 API smoke |

```bash
cd backend                 && .venv/bin/python -m pytest -q && ruff check . && ruff format --check . && mypy app
cd frontend                && npm run type-check && npm run lint && npm test && npm run build
cd system-admin-frontend   && npm run type-check && npm run lint && npm test && npm run build
```

回归红线测试（节选）：授权矩阵 IDOR（`test_authz_matrix.py`）、双 listener 路由注册与旧 admin.py 未注册、交叉签发域拒绝、凭据文件 0600 生命周期、字段白名单精确集合、访问会话拒绝矩阵、家庭 dist 禁止字符串扫描。只在对应边界改动时运行相关集合；未运行或环境阻塞的检查应在交付说明中注明。
