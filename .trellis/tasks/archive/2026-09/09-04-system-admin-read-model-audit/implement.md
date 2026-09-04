# 系统管理员全业务只读模型、访问票据与审计：实施计划

## 1. Preconditions

- [ ] 子任务 1 的 `admin_app`、管理员 token、`require_system_admin` 和 `/admin-api/auth/*` 已验收。
- [ ] 阅读父任务设计、backend 数据库/错误处理/日志规范和当前所有业务模型。
- [ ] 复核 `HouseholdCardView.vue` 等并行 WIP，不覆盖其他任务改动。

## 2. Ordered implementation

- [ ] 新增 admin read schemas、分页 envelope、筛选参数和字段黑名单测试。
- [ ] 抽取 `space_admin → managed spaces` 聚合查询与异常检测，建立 overview/space-admin/space 资源 API。
- [ ] 建立成员、基础档案、头像缩略图、关系边、confirmed SourceFact、附件元数据 read model。
- [ ] 建立运营/通知/申请/交接/ActionCard/统计和 audit timeline 的最小状态投影。
- [ ] 建立 Agent run/job 监控查询、五秒轮询需要的分页接口和二次错误脱敏器。
- [ ] 创建 `admin_access_sessions` / `admin_access_audits` migration、service、依赖和审计 writer。
- [ ] 实现敏感访问会话：理由、单 user/space 绑定、30 分钟 TTL、hash-only token、no-store 和每次使用审计。
- [ ] 将 manager application approve/reject 迁移到独立 admin API；reject 理由必填，approve/reject 二次确认，其他写路由不实现。
- [ ] 删除/隔离旧 admin.py 的依赖，不注册或转发旧 break-glass 路由。
- [ ] 补齐 schema、防枚举、分页/N+1、脱敏、票据、永久审计和审批回归测试。

## 3. Verification

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_admin_read_model.py tests/test_admin_access_sessions.py tests/test_admin_audit.py
.venv/bin/python -m pytest -q tests/test_manager_applications.py tests/test_authz_matrix.py
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
.venv/bin/python -m mypy app
```

重点 SQL/HTTP 检查：

```bash
# 未知空间与真实空间返回不可区分的安全语义
# 敏感详情无票据/错目标/过期票据均 403
# response headers 含 Cache-Control: no-store
# route table 不含旧 /api/admin break-glass 路由
```

## 4. Stop points

- 如果必须调用家庭 visibility API 才能让 system_admin 读取，停止并建立独立 read model。
- 如果 schema 需要返回 RawRelationInput、Memory、Session、AgentMessage、RAG、附件原文或认证秘密，停止并缩小范围。
- 如果错误脱敏不能证明移除 token/secret/prompt/message/PII，只返回错误码和安全位置。
- 如果访问会话可以跨 user/space 复用、持久化明文或不写审计，停止。
- 如果批准申请会绕过 consent/唯一 active admin/单事务，停止。
- 如果任何实现触碰 `family_spaces.owner_id` 或自动修复异常空间，停止。

## 5. Rollback

- 只读 API 可按 endpoint feature flag 停用；不删除永久审计和已创建会话历史。
- 访问会话 API 出现漏洞时立即停发新 session，敏感详情统一 403；普通 overview 仍可继续。
- 审批写例外出现错误时停用 approve/reject，不回退已提交的领域事务；使用审计和命令层修复。
- 不把旧 `admin.py` 重新挂载作为回滚手段。

## 6. Handoff

输出所有 admin endpoint/schema/migration、字段白名单、脱敏策略、审计表、票据 TTL、查询上限和测试结果，供子任务 3 构建页面，供子任务 4 配置 `/admin-api` 代理。
