# m0b 认证、首启和凭据安全

> 父任务：[08-25-m0-scaffold-pin-auth](../08-25-m0-scaffold-pin-auth/prd.md)｜依赖：m0a｜安全合同：architecture.md §2 `[AD-2]`

## Goal

「名字 + 6 位 PIN」认证基座完整落地，含全部安全合同——不只是能登录，而是**爆破不动、凭据不泄、会话可控**。

## Requirements

- 表：users(档案基础字段) + accounts（AD-1 结构）。PIN 仅存 bcrypt/argon2 哈希；随机 PIN 用 `secrets` 模块。
- 登录：`POST /auth/login {name,pin}` → JWT(access 2h + refresh 30d 轮换)。限流：按 name 失败计数 5 次/锁 15 分钟；统一错误文案防枚举。
- 同名同 PIN 两步消歧：409 challenge_token（绑 IP、5 分钟、单次使用）→ `/auth/login/select`。
- 首启引导：无用户时初始化管理员（随机 PIN），凭据一次性展示不可回看（待定决策 Q3 默认方案）。
- 改名字/改 PIN（旧 PIN 验证）；改 PIN/重置 → token_version+1 即刻失效全部旧 access。
- 登出 = refresh 撤销；audit_log 记录 login_failed≥3、pin 变更。
- 前端：登录页（密码态输入）、首启引导页、设置页（改名/改 PIN）、409 选择列表弹窗、强制改 PIN 拦截跳转。

## Acceptance Criteria

- [ ] 名字+正确 PIN 登录成功；错误 PIN 统一文案 401；第 5 次失败后该账号锁 15 分钟。
- [ ] 构造同名同 PIN 双账号出现候选选择流程，challenge 过期/重放被拒。
- [ ] 改 PIN 后旧 access token 下一次请求即 401；refresh 无法再换新。
- [ ] pin_must_change=true 时除改 PIN 外全部 API 403 PIN_CHANGE_REQUIRED，前端强制跳转。
- [ ] 数据库无明文/可逆 PIN；日志中无 PIN/token 痕迹；审计记录可查。
- [ ] Alembic 迁移覆盖 users/accounts/audit_log。

## Non-goals

- 关系/空间业务（M1）；管理员后台界面（m4b，此处仅 bootstrap 初始化）。
