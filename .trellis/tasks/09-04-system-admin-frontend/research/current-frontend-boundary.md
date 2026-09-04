# 子任务 3 研究：现有家庭 SPA 与后台代码边界

- `frontend/src/main.ts`、`App.vue`、`router/index.ts` 当前组成单一 SPA，家庭和 system-admin 共用 bundle、router、Pinia。
- `frontend/src/components/shell/SystemAdminShell.vue` 与 `frontend/src/views/SystemAdminView.vue` 当前是后台 UI，但位于家庭项目，后续必须迁移/重写到独立项目。
- `frontend/src/api/admin.ts` 同时包含安全最小治理 API 和旧 break-glass client；独立后台只实现新 `/admin-api` client，不搬运旧 break-glass 调用。
- `frontend/src/stores/auth.ts` 当前使用 `fg.refresh_token`，system-admin 与 family_user 共享会话；后台必须使用独立 key 和 store。
- `frontend/src/views/SystemAdminLoginView.vue` 当前是同 SPA 登录页，后续不应保留在家庭项目。
- 当前 admin UI 测试可作为交互和字段白名单参考，但路由/存储/依赖断言必须改为跨项目隔离测试。
