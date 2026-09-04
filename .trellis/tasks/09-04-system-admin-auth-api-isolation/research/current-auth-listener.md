# 子任务 1 研究：当前认证与 listener 基线

- `backend/app/main.py` 当前公开 app 注册 auth、bootstrap、system_admin、admin_metadata 和家庭路由；`backend/app/api/admin.py` 未注册。
- `backend/app/serve.py` 已提供公开 listener 与 internal agent listener 的双 listener 生命周期，可复用端口预检和优雅停机模式。
- `backend/app/api/auth.py` 当前在同一 `/api/auth/login` 中先匹配 `SystemAdmin/SystemAdminAccount`，但 system_admin 仍使用 pin_hash、UserOut 和共享公开 API。
- `backend/app/models/system_admin.py` 当前含 `pin_hash`、`pin_must_change`、`token_version`；新任务要替换为 password 语义，不改变家庭 Account。
- `frontend/src/stores/auth.ts` 当前唯一 refresh key 是 `fg.refresh_token`；独立 admin frontend 将使用独立 key，家庭 frontend 删除后台主体字段。
- 当前没有后台自动 bootstrap 文件交付流程；`/api/bootstrap/initialize` 是公开首启接口，需从 family app 移除并改为部署启动 bootstrap。
