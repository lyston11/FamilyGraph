# 双前端双 API 部署隔离与家庭端后台痕迹清理

## Goal

把家庭前台和系统管理员后台部署为两套互不暴露的 Web/API 运行面：家庭用户访问 8080/5173，系统管理员通过受控内网/VPN访问 127.0.0.1:8081/5174；家庭端从源码、bundle、路由、OpenAPI、Nginx 和错误响应中都不知道后台存在。

## Requirements

### DEP-F1：双 Web 服务

- family web 继续对外提供容器端口 8080、开发端口 5173，构建家庭 `frontend/`。
- admin web 构建独立 `system-admin-frontend/`，开发端口 5174，生产仅绑定宿主 `127.0.0.1:8081`。
- admin web 由专用 Nginx 代理 `/admin-api` 到 api 容器 8002；不代理家庭 `/api`。
- family web 只代理 `/api` 到 8000；不代理 `/admin-api`。

### DEP-F2：双 API 网络

- api 容器同时运行 family listener 8000 和 admin listener 8002；8002 不映射宿主端口。
- admin web 使用仅限 admin/backend 的网络访问 8002；家庭 web、宿主和公网无法直接访问 8002。
- family API 8000 不注册管理员路由；admin API 8002 不注册家庭业务路由。
- 生产后台默认由 VPN/内网反代接入，错误绑定到公开网卡时 fail-closed 或健康检查失败。

### DEP-F3：家庭端零后台痕迹

- 从 `frontend/` 删除后台路由、SystemAdmin/AdminView、后台 Shell、后台 API client/types/tests、后台身份枚举和后台文案；不得删除其他家庭功能。
- 家庭未知 `/system-admin`、`/admin`、`/admin-api` 及相似深链统一普通 404；不跳转、不返回 403、不显示后台端口。
- 家庭 bundle/source map/静态字符串/路由表/OpenAPI 不包含后台代码、`/admin-api`、system_admin、platform_operator、5174、8081 等后台概念。
- 家庭公开文档只描述家庭产品和 8080/5173，不出现后台入口或管理员登录说明。

### DEP-F4：部署配置和文档

- Vite/Compose/Dockerfile/Nginx 支持 family/admin 双构建，构建参数显式指定 surface；默认命令兼容家庭项目。
- `ADMIN_JWT_SECRET`、admin issuer/audience、bootstrap credentials 和 admin bind host 不进入前端构建参数或日志。
- README 将普通用户启动与受限运维后台启动分开；后台凭据文件、VPN 和回环绑定只写运维文档。
- Compose healthcheck 分别验证 family web、admin web、8000 health；8002 仅从 admin network 探测。

## Acceptance Criteria

- [ ] Compose 启动后 family web=8080、admin web=127.0.0.1:8081；开发命令 family=5173、admin=5174。
- [ ] 家庭 web 只能访问 family API；admin web 只能访问 Admin API；网络测试证明跨面请求失败/普通 404。
- [ ] 8000 路由/OpenAPI 不含后台能力；8002 路由/OpenAPI 不含家庭业务能力。
- [ ] 家庭 frontend 构建产物、source map、依赖图和未知路径响应均不泄露后台概念；后台 frontend 不依赖家庭项目。
- [ ] admin web 在错误 host/公开绑定配置下 fail-closed；8002 不发布宿主端口；8081 默认回环绑定。
- [ ] 双 Dockerfile/build、Nginx、Compose config、healthcheck 和文档测试通过。
- [ ] 现有家庭功能和并行任务修改未被回退；`HouseholdCardView.vue` WIP 完好。

## Out of scope

- Admin 认证、只读 API、审计和访问票据的实现（子任务 1/2）。
- 后台页面和业务字段设计（子任务 3）。
- 家庭注册、联系方式字段、MFA、VPN 产品化和独立仓库拆分。
- 让技术上有主机访问权限的人无法扫描后台服务；本任务目标是产品与公网隐藏。

## Constraints

- 不把后台路由以“隐藏路由”或动态 import 形式留在家庭 bundle。
- 不把 8002 映射到 `0.0.0.0` 或公开公网端口。
- 不把管理员 secret、bootstrap password、token 或访问票据写入镜像、source map、普通日志。
- 不通过恢复旧 `backend/app/api/admin.py` 实现后台能力。
- 不覆盖家庭端并行 WIP。
