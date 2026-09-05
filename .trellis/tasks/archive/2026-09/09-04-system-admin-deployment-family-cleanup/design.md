# 双前端双 API 部署隔离与家庭端后台痕迹清理：技术设计

## 1. Deployment topology

```text
public/VPN user
  ├─ family web 127.0.0.1:8080 (or public reverse proxy)
  │    └─ /api → api:8000
  └─ admin web 127.0.0.1:8081 (VPN/internal reverse proxy only)
       └─ /admin-api → api:8002

api container
  ├─ family_app :8000
  └─ admin_app  :8002
```

family web 和 admin web 是两个镜像/构建产物；admin API listener 不发布到宿主。admin web 与 api:8002 放在 admin/backend 网络，family web 只能连接 api:8000。若 compose 网络无法做到端口级限制，则用 Nginx location、容器 firewall 和 route registration 三重 fail-closed，而不是把 8002 暴露给家庭网卡。

## 2. Build surfaces

### Family build

- 根目录 `frontend/` 只包含家庭入口、家庭 router/store/API/types、家庭 Nginx。
- `npm run dev` 默认 5173，`npm run build` 产生 family dist。
- unknown `/system-admin*`、`/admin*`、`/admin-api*` 由 Nginx 普通 404；SPA fallback 不能把这些路径送进家庭首页。
- 生成 dist 后运行禁止字符串/依赖图扫描：`system_admin`、`platform_operator`、`/admin-api`、5174、8081、SystemAdmin、AdminView 等均不得出现。

### Admin build

- `system-admin-frontend/` 独立 package/build context；npm dev 5174，Docker dist 由 admin Nginx 托管。
- admin Nginx 只允许 `/admin-api/` 到 api:8002，其他路径走 admin SPA；不反代 `/api`。
- admin bundle 可以包含后台路由和 schema，但不得 import `frontend/`，不复用家庭构建缓存。

## 3. Nginx contracts

Family Nginx：

```nginx
location ^~ /admin-api/ { return 404; }
location ~ ^/(system-admin|admin)(/|$) { return 404; }
location /api/ { proxy_pass http://api:8000; }
location / { try_files $uri $uri/ /index.html; }
```

Admin Nginx：

```nginx
location /admin-api/ { proxy_pass http://api:8002; }
location ^~ /api/ { return 404; }
location / { try_files $uri $uri/ /index.html; }
```

两个 Nginx 都禁止 `/internal/` 和目录遍历；Admin API 错误不通过 family Nginx 传播。

## 4. Compose and environment

- `web`：构建 `frontend/`，`8080:80`，只在 frontend network；依赖 api:8000 health。
- `admin-web`：构建 `system-admin-frontend/`，`127.0.0.1:8081:80`，加入 admin network；依赖 admin API health。
- `api`：继续运行双 listener，发布 `8000:8000`；不发布 8002，admin network 上暴露容器端口。
- `ADMIN_JWT_SECRET/ISSUER/AUDIENCE` 只注入 api/admin listener；不作为 Docker build arg，不进入 Vite env。
- `ADMIN_BIND_HOST` 默认 `127.0.0.1`；允许的生产配置必须是 loopback 或明确内网地址，通配地址 fail-closed。
- healthcheck：family `http://127.0.0.1:8080/api/health`、admin network 内探测 `http://api:8002/admin-api/health`、admin web root。

## 5. Family API/OpenAPI isolation

`family_app` router registration allowlist 只包含家庭认证和业务路由；公开 OpenAPI 不包含 `/admin-api`、system admin auth、admin read schemas 或后台 response fields。访问未知后台路径的 status/body/headers 与随机未知路径一致，避免存在性 oracle。

`admin_app` registration allowlist 只包含 admin auth、read model、access session、audit 和审批例外；不注册家庭 users/spaces/memory/session/attachment 等普通路由。两个 app 的 `docs_url` 可以在生产关闭；若开启，分别暴露各自 schema，不能合并。

## 6. Network and secret posture

- 8002 只在 admin/backend network 监听；宿主 `curl 127.0.0.1:8002` 默认失败。
- admin web 端口默认回环；VPN/reverse proxy 负责内网访问和额外认证层，公开家庭入口不反向代理 8081。
- bootstrap credential 文件挂载 api data volume、0600；不得 COPY 到 web image。
- source map 不发布到公网；构建扫描不能把 secret、token、原始错误或后台身份字段带入 family dist。

## 7. Verification contracts

```text
family port → family API: allowed
family port → admin API: ordinary 404/connection denied
admin port → admin API: allowed
admin port → family API: ordinary 404/connection denied
host/public → api:8002: denied
```

测试包括 Compose config、Nginx syntax、socket reachability、HTTP route matrix、静态字符串/依赖图扫描、OpenAPI allowlist、SPA unknown path ordinary 404、secret/build-arg scan 和 healthchecks。

## 8. Rollback

可停止 admin-web/admin listener 而不改变 family web；family web 不应因为 admin 不可用而显示管理员提示。不得把 admin API 临时代理到 8000，也不得把后台 dist 合回家庭镜像作为回滚。
