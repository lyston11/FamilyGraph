# 系统管理员前端镜像（多阶段：Node 构建 → nginx 托管静态产物）
#
# 独立于家庭 frontend/ 镜像：只代理 /admin-api 到 api:8002（admin listener）。
# /api（家庭 8000）与 /internal（内部 8001）一律 404，绝不转发。
#
# 构建上下文是**仓库根**（见 docker-compose.yml 的 admin-web.build.context）：
# src/styles/main.css 用相对路径 @import 仓库根的 shared/brand-tokens.css，
# 它是设计 token 的单一定义源（R4）。若上下文收窄到 system-admin-frontend/，
# 该文件落在上下文之外，vite build 阶段会以 ENOENT 直接失败。

FROM node:22-alpine AS build

WORKDIR /app
# 包清单先拷，让 npm ci 层可缓存
COPY system-admin-frontend/package.json system-admin-frontend/package-lock.json ./
RUN npm ci
COPY system-admin-frontend/ ./
# 保持共享 token 在 ../.. 相对高度上：/app/src/styles/ + ../../../shared/
# 解析到 /shared/brand-tokens.css，与源码里的 @import 路径一致。
COPY shared/brand-tokens.css /shared/brand-tokens.css
RUN npm run build

FROM nginx:1.27-alpine

COPY system-admin-frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
