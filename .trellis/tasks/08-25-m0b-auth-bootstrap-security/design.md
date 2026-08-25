# m0b 技术设计

> 安全合同权威来源：[architecture.md](../../spec/architecture.md) §1/§2 `[AD-1][AD-2]`。规范遵循 spec/backend/error-handling.md、logging-guidelines.md。

## 组件落位

| 关注点 | 位置 |
|---|---|
| 登录/刷新/登出/challenge/select | api/auth.py |
| 限流与锁定 | services/auth_guard.py（DB 字段 locked_until + failed_attempts，进程内缓存加速） |
| PIN 哈希/校验/JWT 签发/token_version 校验 | utils/security.py |
| challenge 存储与单次使用 | models 层 auth_challenges 表 + services/challenge.py：select 时单事务校验未过期且 used_at IS NULL 后原子置 used_at（数据库保证防重放，无状态签名方案废弃） |
| refresh 持久化/轮换/重用检测 | models 层 refresh_sessions 表（token_hash、rotated_from、revoked_at）+ services/refresh_session.py：提交已 revoked token → 全会话撤销 + 审计 |
| 改名/改PIN/me 接口 | api/users.py |
| bootstrap 检测与首启初始化 | services/bootstrap.py（main.py 启动时探测空库 → 前端引导流程接管） |
| audit 写入 | services/audit.py（独立函数，禁止裸 insert 散落） |

## 关键流程

- 登录：查 name → 锁定检查 → pin verify（失败计数+统一文案）→ 单命中发 token；多命中写 auth_challenges 行并返回 409 challenge_id。
- challenge select：事务内 `UPDATE ... SET used_at=now() WHERE id=? AND used_at IS NULL AND expires_at>now()`，影响行数=0 即拒绝（过期/重放同一处理路径）。
- 会话失效：敏感操作调 `bump_token_version(user_id)`，get_current_user 依赖项比对 version。
- 首登强制改 PIN：accounts.pin_must_change → FastAPI 依赖 `require_pin_changed` 挂全局，白名单 = {PUT /me/pin, POST /auth/logout, POST /auth/refresh}（与 architecture §1、prd 一致；health 为公开端点不经此依赖）。

## 兼容性

users 表字段是 M1 档案模型的子集；m1a 迁移只增列。JWT payload 含 {sub, ver, adm}——adm 供 m4b 使用。

## 回滚形态

auth 功能整体 feature 分支；限流参数集中在 config 可热调（env），最坏情况 env 关闭限流恢复可用性（仅开发态允许，生产禁用开关需二次确认日志）。
