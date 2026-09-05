# 独立系统管理员后台与全业务监控平台：实施计划

## 1. Phase 0：实施前检查

- [ ] 阅读本任务 `prd.md`、`design.md`、研究文件和 `.trellis/spec/architecture.md`、backend/frontend 相关规范。
- [ ] 确认工作树并行修改；特别保护 `frontend/src/views/HouseholdCardView.vue` 等非本任务 WIP。
- [ ] 记录当前单 SPA、单 API、现有 `serve.py` 双 listener、system_admin PIN 模型和旧 `admin.py` 未注册基线。
- [ ] 建立任务 1→2→3→4 依赖；未满足前置任务时不得启动后续任务。

## 2. Phase 1：子任务执行顺序

### 2.1 子任务 1：认证与 Admin API listener

- 新增/替换系统管理员用户名、密码哈希、密码版本、锁定和 refresh session 合同；不迁移旧 PIN 生产账号。
- 新增 `admin_app` 和 8002 listener；将管理员认证和 Admin API 路由从 family app 移出。
- 实现部署自动 bootstrap：唯一 `admin`、随机初始密码、0600 文件、首次改密删除文件、受限恢复命令。
- 实现独立 JWT secret/issuer/audience、15 分钟 access、refresh 轮换和版本失效。
- 调整 family response/schema，移除后台身份字段和管理员路由；家庭端后台路径统一普通 404。
- 添加跨 listener token rejection、凭据文件权限、锁定、恢复、密码变更和路由 registration 测试。

### 2.2 子任务 2：只读模型、访问会话与审计

- 新增独立 admin read schemas、显式列查询和分页/搜索/筛选参数。
- 实现 `space_admin → spaces` 聚合查询、成员/档案、关系/事实、运营、通知、Agent/job、异常队列和审计接口。
- 新增 `admin_access_sessions` / `admin_access_audits`（或等价实现），实现 30 分钟单用户/单空间绑定、理由、每次使用审计和 no-store。
- 实现头像鉴权缩略图、附件元数据、关系事实和 Agent 二次脱敏诊断；禁止私密正文和认证秘密。
- 将空间管理员申请 approve/reject 作为唯一业务写例外；reject 理由必填，approve/reject 二次确认、单事务、不可改判。
- 添加精确字段白名单、防枚举、分页上限、N+1、错票据、过期票据和审计永久保留测试。

### 2.3 子任务 3：独立后台前端

- 创建 `system-admin-frontend/` 独立项目、构建、router、Pinia、API client、测试和 Dockerfile。
- 实现 5174 开发入口、`/admin-api` client、独立存储 key、管理员登录/首次改密/账号设置。
- 实现管理员→空间主导航、概览、异常队列、空间钻取、关系/事实、运营治理、Agent 监控和读取审计页面。
- 敏感详情先申请 30 分钟访问会话；票据只在内存；错误、空态、加载态和 no-store 行为可见。
- 仅保留申请 approve/reject 写 UI；reject 理由、二次确认和不可逆提示清晰。
- 375px/桌面/键盘可达性测试；确认后台项目不导入家庭 frontend。

### 2.4 子任务 4：部署与家庭端清理

- Docker Compose 增加 admin web，映射 `127.0.0.1:8081`；admin web 通过内部网络访问 api:8002；family web 保持 8080。
- 开发脚本分别启动 family `5173→8000` 和 admin `5174→8002`；生产 Nginx 分别反代 `/api` 和 `/admin-api`。
- 从家庭 frontend 删除 system-admin/admin view、shell、API、types、路由和后台文案；后台代码只在独立项目。
- 家庭未知后台路径统一普通 404；后台不复用家庭 web 的静态资源或 API 代理。
- 加入 bundle 字符串/依赖图、Compose 网络、端口绑定、Nginx 和 OpenAPI 隔离测试。

## 3. Quality and verification

### Backend

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_admin_auth_listener.py tests/test_admin_read_model.py tests/test_admin_access_audit.py
.venv/bin/python -m pytest -q tests/test_authz_matrix.py tests/test_manager_applications.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m mypy app
.venv/bin/python -m pytest -q --ignore=tests/test_space_stats.py
```

### Family frontend

```bash
cd frontend
npm run type-check
npm run lint
npm test -- --run
npm run build
```

### Admin frontend

```bash
cd system-admin-frontend
npm run type-check
npm run lint
npm test -- --run
npm run build
```

### Deployment

```bash
docker compose config
# 按环境变量启动后检查：family web 8080、admin web 127.0.0.1:8081、API 8000
# 确认宿主无法直连 8002，admin web 容器可访问 api:8002
```

## 4. Stop points

- 如果 family app 仍必须导入 system-admin 模块才能启动，停止并重新拆分入口。
- 如果 8000 仍注册任何 `/admin-api`、system-admin auth 或旧 admin router，停止，不用 principal_type 作为替代隔离。
- 如果认证方案只能继续使用 PIN、固定 `admin/admin` 或共享 JWT secret，停止并回到父任务决策。
- 如果后台 schema 需要返回 Memory/Session/AgentMessage/RAG/关系证据原文、认证秘密或附件原文，停止并缩小字段。
- 如果敏感详情可以绕过访问会话，或审计只能写家庭 `actor_id`，停止并修复数据结构。
- 如果当前工作树的其他任务修改被覆盖，停止并恢复后再继续。
- 如果当前数据库存在旧 system_admin PIN 行而实现试图静默迁移，停止；本任务不做生产账号迁移窗口。

## 5. Rollback

- 子任务 1 回滚：恢复旧 system_admin schema/认证和单 listener，但不得在未同步前端前部署；家庭数据不回滚。
- 子任务 2 回滚：停用 admin read endpoints 和访问票据表写入，保留认证 listener；不删除永久审计。
- 子任务 3 回滚：停止 admin web，家庭 frontend 保持独立构建；不把后台 bundle 合回家庭项目。
- 子任务 4 回滚：恢复家庭 web 8080 和 API 8000 的部署配置；后台服务停用，不把 8002 暴露到公网。

## 6. Final integration checklist

- [ ] 四个子任务均通过各自验收并保持父任务依赖顺序。
- [ ] Family bundle/source/OpenAPI/404 不泄露后台存在；Admin bundle 只含后台功能。
- [ ] 8000/8002 路由、JWT、存储、网络和日志边界均由测试覆盖。
- [ ] 管理员→空间主视图和异常队列满足监控目标；无管理员空间不自动修复。
- [ ] 读模型、访问会话、字段白名单、错误脱敏和永久审计可追责。
- [ ] 只读边界和审批唯一写例外无额外高风险操作。
- [ ] 不注册/复用旧 `backend/app/api/admin.py`，不新增家庭 break-glass。
- [ ] 父任务 PRD、design、implement、notes 与代码实际一致后再归档。
