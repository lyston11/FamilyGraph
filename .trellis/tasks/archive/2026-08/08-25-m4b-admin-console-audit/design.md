# m4b 技术设计

- 后端 admin 路由（is_admin only + audit）：
  GET /admin/users（全量列表）、POST /admin/users/{id}/reset-pin（随机 PIN 一次性返回，
  token_version+1 + refresh 全撤销 + audit pin_reset）、PATCH /admin/users/{id}（改名/改归属/转移代管权）
  GET /admin/audit-logs?limit=200（audit_log 倒序只读）
- 权限依赖 require_admin（复用 require_authenticated_user + is_admin 校验，403 非 admin）
- 前端 /admin 路由守卫（非 admin 重定向）+ AdminView：用户表（重置按钮）、审计时间线
