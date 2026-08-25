# M0 工程骨架与认证基座

> 权威上下文：[.trellis/HANDOFF.md](../../HANDOFF.md)。已确认决策视为锁定。

## Goal

搭建前后端工程骨架，实现「名字 + 6 位 PIN」认证基座与首启管理员初始化。出口标准：**能登录、能改 PIN**。

## Background（锁定决策引用）

- A1：登录 = 名字 + 6 位数字 PIN 双匹配，均可自行修改。
- A2：允许重名；同名同 PIN 撞车时弹选择列表消歧；账号内部唯一 ID。
- A4：极简管理员，首次启动初始化生成；职责仅初始化、重置 PIN、数据兜底。
- T1/T2/T3：Vue3+Vite+TS+Element Plus+Pinia；FastAPI+SQLAlchemy+SQLite(WAL)+JWT；Docker Compose。

## Requirements

### 工程结构
- 单仓布局：`backend/`（FastAPI + SQLAlchemy + Alembic）、`frontend/`（Vue3 + Vite + TS）、`docker-compose.yml`、根 `README.md`。
- SQLite 启用 WAL；Alembic 管理迁移。
- Compose：`api` 服务 + `web`（nginx 托管前端构建产物并反代 `/api`）+ 数据卷（SQLite 文件 + 上传目录预留）。

### 数据模型（本阶段仅建所需）
- `users`：`id`(唯一主键)、`name`(非唯一)、`pin_hash`、`is_admin`、`created_at`。
- PIN 只存 bcrypt/argon2 哈希；随机 PIN 用 `secrets` 模块生成。

### 认证流程
- 登录：`POST /api/auth/login {name, pin}` → JWT。
  - 精确匹配 name 后校验 pin_hash；失败统一报错文案（不区分"名字不存在/PIN 错误"）。
  - 同名多账号且 PIN 都匹配该 pin 时（概率极低），返回候选列表（id/头像/创建者提示），前端弹选择列表二次确认。
- 首次启动 bootstrap：无任何用户时，系统初始化流程创建管理员账号（随机 PIN），凭据**仅展示一次**（见开放问题 #3，默认采用首启界面一次性展示 + 提示保存）。
- 修改 PIN：本人已登录状态下 `PUT /api/me/pin {old_pin, new_pin}`，new_pin 校验 6 位数字。
- 修改名字：`PUT /api/me/name`，允许改名（开放问题 #2 默认方案：随时可改，登录随新名字）。
- 前端页面：登录页（PIN 密码态输入框）、首启引导页（管理员初始化）、极简设置页（改名字/改 PIN）。

## Acceptance Criteria

- [ ] 全新克隆后 `docker compose up --build` 一条命令启动全栈，健康检查端点可访问。
- [ ] 首启进入初始化流程，创建管理员并一次性展示凭据；再次刷新不可回看。
- [ ] 名字+正确 PIN 登录成功获得 JWT；错误 PIN 返回 401 与统一文案。
- [ ] 构造两个同名同 PIN 账号时，登录出现选择列表并能正确登入所选账号。
- [ ] 已登录用户成功修改 PIN 后，旧 PIN 失效、新 PIN 可登录；改名后以新名字可登录。
- [ ] 数据库中不存在明文或可逆的 PIN 存储。
- [ ] Alembic 迁移可从空库一键建到最新 schema。

## Non-goals

- 关系、家庭空间、任何业务图功能（M1）。
- 可见性/隐私逻辑（M2）、附件（M3）。
- 管理员后台界面（M4，本阶段只有 bootstrap 初始化）。
- HTTPS/域名配置（迁云时处理）。

## Open Questions

- 开放问题 #3：管理员初始凭据交付方式——默认按"首启一次性展示"实现，若实现中发现运维不便再议环境变量注入方案。
