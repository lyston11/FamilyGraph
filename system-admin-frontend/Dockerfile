# 系统管理员前端镜像（多阶段：Node 构建 → nginx 托管静态产物）
#
# 独立于家庭 frontend/ 镜像：只代理 /admin-api 到 api:8002（admin listener）。
# /api（家庭 8000）与 /internal（内部 8001）一律 404，绝不转发。

FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
