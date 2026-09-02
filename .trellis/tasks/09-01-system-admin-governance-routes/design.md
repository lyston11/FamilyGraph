# 系统管理员治理路由技术设计

## 1. 设计边界

本任务只收口系统管理员的认证体验、主体隔离和最小治理 API 验收。独立 SystemAdmin/SystemAdminAccount/SystemAdminRefreshSession、JWT principal_type、`require_system_admin`、元数据 API、申请/交接 API 和前端路由互斥作为已完成基线复用，不重复建模。

系统管理员不是家庭 User/Account，也不通过 `visibility.evaluate` 获得家庭数据权限。旧 `admin.py` 继续不注册；其中家庭数据 break-glass 另立任务。

## 2. 登录流程

### 2.1 请求和主体校验

`SystemAdminLoginView` 调用既有登录 API，提交系统管理员登录名/PIN 和可选安全 redirect。收到响应后先运行共享 API decoder，再检查 `principal_type`：

- `system_admin`：写入 auth store，清理 family caches，按 `pin_must_change` 进入 PIN 首改或后台。
- `family_user`：不写入 system-admin session；显示统一拒绝/引导家庭入口，防止家庭凭据被当作后台凭据。
- 非法/未知主体：整体拒绝并清理任何临时 auth 状态。

redirect 只允许站内已知 system-admin 路由，禁止把登录响应中的任意 URL 当作导航目标。

### 2.2 认证错误

登录失败沿用后端统一错误文案，不区分不存在账号、错误 PIN、锁定或主体类型细节。表单显示提交中、失败和成功后的不可重复提交状态；刷新页面不会把半成品凭据写入家庭 store。

## 3. PIN 首改和会话失效

`ChangePinView` 根据当前 auth principal 选择完成后的导航：system_admin 回 system-admin redirect/后台，family_user 继续回家庭 login。不得通过页面名称猜主体。

改 PIN 后后端已将 account token_version+1 并标记 claimed；前端清理旧 access/refresh 状态，按既有流程获取新会话或回登录入口。刷新遇到 system-admin token 失效时不得降级到家庭登录。

`sessionExpiredRedirect` 接收当前主体或从 auth store 读取主体，在跳转前清理对应缓存：system_admin → `/system-admin/login`，family_user → `/login`。登出也按主体撤销对应 refresh session，不触碰另一主体的会话。

## 4. SystemAdminShell

在系统后台壳中增加明确的 logout 操作，并保持当前治理导航和 RouterView。壳层不引入家庭 store、家庭成员列表、关系图或个人数据读取；登出后回 `/system-admin/login`。实现时只编辑 shell/auth/router 相关文件，不覆盖 `SystemAdminView.vue` 的 guest 删除 WIP。

## 5. 治理 API 最小权限

现有 admin metadata/system-admin routers 继续使用 `require_system_admin` 和专用 schema。测试通过 FastAPI route registration 和 HTTP 调用确认：

- system-admin token 可读取允许的账号、空间、管理员归属、成员元数据、申请和交接工单。
- family-user token、无 token、错误/缺失 principal_type 不能访问。
- `/api/me`、`/api/spaces` 等家庭端点拒绝 system-admin。
- 响应字段不含 birth、gender、bio、avatar、附件、关系图边、私人 Memory/Session 或敏感 disclosure。
- 未知 space_id 的成员查询返回安全空结果/统一不存在语义，不暴露空间存在性。
- 旧 `admin.py` 路由不存在；不通过注册旧 router 来“补齐”后台。

## 6. 兼容、回滚和测试

不新增数据库主体或改变现有认证协议；必要的前端变化可独立回滚。若登录 API 返回结构需要扩展，只增加兼容字段，不改变 principal_type 语义。任何发现旧 break-glass 路由需要主体迁移时，停止在本任务内实现并记录为后续任务。

前端单测覆盖登录主体校验、PIN 回跳、session expired、logout 和 guard；后端覆盖路由依赖、JWT 主体隔离、字段白名单和旧 router 未注册。全量验证保留当前工作树修改并在最终 diff 中复核。
