# 前端目录结构（2026-09-05 按真实代码校正；09-04 双前端架构落地后）

仓库有两个完全独立的前端应用——零共享代码、零共享存储、零共享构建产物：

```
frontend/                    家庭用户端（唯一公开面；dev 5173 / 生产 8080）
system-admin-frontend/       系统管理员后台（dev 5174 / 生产 127.0.0.1:8081；仅访问 /admin-api）
```

## frontend/（家庭端，family_user 单主体）

```
frontend/src/
├── main.ts / App.vue        # 全局 providers + 主题 token；App 只渲染家庭壳（无后台分支）
├── router/                  # index.ts(路由+守卫) redirect.ts(站内回跳白名单)
│                            #   未注册深链（/system-admin、/admin 等）→ NotFoundView 普通 404
├── api/                     # client.ts(拦截器/409 challenge/刷新单飞) auth.ts errors.ts
│                            #   agent members spaces graph household memory notifications
│                            #   governance kinship actionCards familyRecommendations
│                            #   personalFamilyView spaceStats lunar attachments decode
├── stores/                  # Pinia：auth(单主体) spaces graph ui members household
│                            #   memory governance kinship actionCards familyRecommendations
│                            #   personalFamilyView notifications spaceStats agent
├── views/                   # HouseholdCardView FamilyTreeView PersonProfileView LoginView
│                            #   OnboardingView(静态说明；家庭注册另立任务) ChangePinView
│                            #   IdentitySetupView NotificationsView MemoryView SettingsView
│                            #   SpaceManagementView StatsView NotFoundView
├── components/              # member/ canvas/(Vue Flow) memory/ agent/ shell/(AppShell) common/
├── composables/             # useLayout useFamilyTreeCanvas useSpaceContext useChallenge …
└── types/                   # api.ts 与后端 schema 人工同步（UserOut 只含 family_user 主体）
```

家庭端红线（09-04）：不包含任何后台视图/路由/API client/身份字段（`is_admin`/`platform_role`/
`system_admin` 主体枚举）；UserOut.is_admin 等兼容键已删；构建后 dist 禁止字符串扫描。

## system-admin-frontend/（后台，system_admin 单主体）

```
system-admin-frontend/src/
├── api/                     # client.ts(baseURL /admin-api + 票据拦截器) auth read governance decode
├── stores/                  # auth(独立 key fg.admin.refresh_token；access token 仅内存)
│                            #   accessSession(敏感票据仅内存、30 分钟、单目标绑定)
├── router/                  # 登录/首改密守卫；password_must_change 只放行改密页
├── views/                   # AdminLogin ForceChangePassword AccountSettings Overview
│                            #   SpaceAdmins → SpaceAdminSpaces → SpaceDetail(管理员→空间主线)
│                            #   AnomalyQueue Operations AgentMonitor AccessAudit NotFound
├── components/              # AdminShell ReasonModal(理由弹窗) DecisionModal(审批二次确认)
│                            #   MemberProfilePanel SpaceRelationsPanel SpaceFactsPanel …
└── types/                   # 与 backend/app/schemas/admin_read.py 精确对齐(白名单字段集)
```

后台红线：不 import 家庭 `frontend/`（模块图断言）；票据/access token 不写 localStorage；
approve/reject 是唯一写 UI（confirm:true + 不可逆提示）；Agent 监控 5 秒轮询、hidden 暂停、
卸载清理。

规则（两个应用通用）：views 不直接调 axios，一律经 api/ 层；画布组件禁止引入业务请求逻辑
（数据由 store 注入）。
