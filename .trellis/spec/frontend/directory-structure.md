# 前端目录结构（初始规范 v0，M0 完成后以真实代码校正）

```
frontend/src/
├── main.ts / App.vue
├── router/            # 路由 + 登录守卫(含 PIN_CHANGE_REQUIRED 强制跳转)
├── api/               # axios 封装: 拦截器统一错误结构解包、409 challenge 流程、token 刷新
├── stores/            # Pinia: auth, spaces, graph, ui
├── views/             # 页面级: Login, Onboarding, FamilySpace, ClanView, Profile, Settings, Stats, Admin
├── components/
│   ├── member/        # 成员卡片/档案抽屉/建档向导
│   ├── canvas/        # Vue Flow 画布、三布局切换器、节点定位
│   └── common/
├── composables/       # useVisibility(遮罩渲染), useLayout(tree/canvas/list), useChallenge
└── types/             # API 响应类型 + 运行时校验
```

规则：views 不直接调 axios，一律经 api/ 层；画布相关组件禁止引入业务请求逻辑（数据由 store 注入）。

> M0 校正：views 采用 `XxxView.vue` 多词命名（LoginView/OnboardingView/SettingsView/ChangePinView/HomeView），路由 name 用单词小写（login 等）；api 层实际文件为 client.ts（拦截器）/auth.ts/errors.ts（ApiError）。
