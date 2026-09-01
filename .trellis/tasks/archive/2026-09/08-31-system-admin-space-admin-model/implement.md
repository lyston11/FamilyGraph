# 实施计划：系统管理员与空间唯一管理员模型重构

> 本任务保持 `planning`，本文件只定义执行顺序、验证和回滚点。开始编码前须由用户审阅 PRD、design.md 和本计划，并明确批准后执行 `task.py start`。

## 0. 进入实现前的审查门

- [ ] 用户确认产品模型：系统管理员独立、无家庭主体；管理员按空间绑定；每空间唯一管理员；一个用户可管理多个空间；管理员申请目标明确显示 lineage 空间；已有管理员时先经原管理员工单同意再交接；后台仅账号/成员关系/空间元数据。
- [ ] 选定并记录独立系统主体的实现方案。
- [ ] 选定并记录 owner/space_admin 的内部迁移编码与 ownership transfer 兼容方案。
- [ ] 确认现有工作区变更 `backend/tests/conftest.py` 不属于本任务，实施时不得覆盖或回退。
- [ ] 备份数据库并确认迁移冲突扫描/回滚策略。

## 1. 现状基线与测试夹具

- [ ] 盘点 `User`、`Account`、`PlatformRoleAssignment`、`FamilySpace`、`SpaceMember`、`SpaceManagerApplication` 和 refresh/JWT 的关系。
- [ ] 盘点所有 `owner`、`space_admin`、`is_admin`、`platform_operator` 的后端调用、前端调用、测试夹具和接口响应。
- [ ] 建立代表性 fixture：
  - 独立系统管理员；
  - 父亲管理 household、母亲管理另一个 lineage；
  - 同一用户管理两个不同 lineage；
  - 空间零/单/多管理员冲突数据；
  - pending/approved/rejected manager application；原管理员同意工单的 pending/accepted/rejected/expired；
- [ ] 先运行受影响的现有测试，记录改造前基线，不修改无关失败。

**回滚点：**仅新增测试夹具/测试，未改变 schema；可直接撤销测试改动。

## 2. 后端主体与认证改造

- [ ] 在 `backend/app/models/` 增加或扩展平台主体/凭据模型，明确 `system_admin` 主体类型；保留必要的 `platform_operator` 迁移兼容映射。
- [ ] 更新 bootstrap：创建独立系统管理员，不创建家庭 `User`/家庭 `Account`/`SpaceMember`/`FamilySpace`。
- [ ] 更新 login、select、refresh、logout、PIN 门禁和 token 校验，使系统主体与家庭用户主体可区分且旧 token 不越权。
- [ ] 更新专用认证 schema，移除 `UserOut.is_admin` 作为长期授权源；前端需要的主体类型/平台角色使用明确字段。
- [ ] 更新 `platform_roles` 与所有平台端点依赖，确保 platform backend 只接受 system admin。
- [ ] 保持认证安全红线：统一错误文案、token_version、refresh 轮换/重用检测、PIN 不入日志。
- [ ] 为系统管理员调用家庭 `/me`、成员、空间、图谱、记忆和 Agent 端点增加 fail-closed 回归测试。

**回滚点：**认证表/迁移落地前先完成模型和服务层测试；任何 token 主体不一致都停止推进，不在兼容层放宽权限。

## 3. 数据库迁移与唯一管理员约束

- [ ] 新增 Alembic 迁移（版本号接续当前迁移链），建立平台主体结构/主体类型约束。
- [ ] 实现迁移前冲突扫描：每个空间统计 active owner/space_admin 候选，并输出可诊断冲突。
- [ ] 设计并落地每空间唯一 active 管理员约束（partial unique index 或规范化 manager 表）；约束必须按 `space_id` 生效，不限制同一用户管理多个空间。
- [ ] 收敛 `owner` 与 `space_admin` 的存量投影，确保迁移提交后不存在双管理员；冲突不随机选择、不静默删除，按任务决策失败或进入显式修复。
- [ ] 更新 owner transfer 与 manager application 命令，使单事务中不会提交零/双管理员终态；已有管理员的申请必须先创建绑定目标空间和当前管理员的同意工单，未获明确同意不得 approve 或交换。
- [ ] 为管理员同意工单建立家庭用户侧的查看、同意、拒绝命令；校验处理者仍是目标空间当前唯一 active `space_admin`，旧管理员或其他空间管理员不能处理。
- [ ] 为空库、现有合法数据、多管理员冲突、平台管理员无家庭主体建立迁移测试。
- [ ] 运行 `alembic upgrade head` / 测试 conftest 的迁移往返和 SQLite pragma/约束检查。

**回滚点：**生产/真实库只使用 online backup；迁移冲突或唯一索引创建失败时停止，恢复备份，不手工删行。

## 4. 后端空间管理员与申请审批

- [ ] 抽取/复用按 `(actor_user_id, space_id)` 的唯一空间管理员判定服务。
- [ ] 调整 `commands/manager_applications.py`：申请人资格、明确目标空间、已有管理员时的同意工单、并发提交/裁决和审计。
- [ ] 调整 `commands/ownership.py`：owner transfer 完成后的角色落位，保持唯一管理员不变量。
- [ ] 调整 `commands/spaces.py`、`space_fsm.py` 和所有治理端点：跨空间管理员不获得当前空间管理权限。
- [ ] 将 API schema 中 `owner`/`space_admin` 的产品投影统一为“空间管理员”，或按设计提供兼容内部值但明确 manager 字段；不得让前端继续把两者当作两个产品级别。
- [ ] 新增/更新后端测试：
  - 同一用户跨两个空间分别拥有管理员资格；
  - 母亲在当前 household 为 member、在母系 lineage 为 manager；
  - 当前空间非 manager 调用治理 API 失败；
  - 每空间唯一管理员数据库和命令层双重保证；
  - owner transfer、申请提交、系统管理员发出交接工单、原管理员同意/拒绝、最终 approve、并发竞态和终态审计；
  - 原管理员拒绝或同意前不发生角色变化，过期/旧管理员同意不能用于交换。

**回滚点：**若旧 ownership transfer 语义与唯一 manager 约束冲突，先暂停并回到第 0 步重新锁定内部编码，不通过临时 if 放过双管理员。

## 5. 系统管理员最小元数据后台 API

- [ ] 将现有 `/admin/users` 从平铺家庭用户详情改为专用账号元数据 schema，或新增 `/admin/accounts` 并为旧路径设置安全兼容别名。
- [ ] 新增账号—空间管理员归属查询、空间元数据查询、成员关系/角色元数据查询。
- [ ] 使用显式列选择和专用 Pydantic 响应，禁止返回家庭档案字段、关系图、附件、私人会话/记忆和敏感披露。
- [ ] 实现管理员申请最小投影、原管理员交接工单/站内通知及其同意/拒绝接口；系统管理员不能绕过同意直接变更管理员关系。
- [ ] 不在本任务中新增 Provider、系统策略等其他平台运维功能；既有范围如继续存在，必须使用其既有安全边界，不得被本任务纳入家庭数据后台。
- [ ] 为每个读取和写入操作记录最小审计信息；审计不包含 PIN/JWT/PII 详情。
- [ ] API 测试覆盖 schema 字段白名单、IDOR、无关家庭用户、平台管理员访问家庭资源、查询越权和敏感字段反射。

**回滚点：**先新增接口和测试，再切前端；若旧接口消费者未知，不删除兼容 URL，但必须让旧 URL 也只返回安全元数据。

## 6. 前端独立系统后台壳

- [ ] 在 `frontend/src/router/` 增加 system admin 路由前缀和主体类型守卫；`/admin` 兼容重定向到后台首页。
- [ ] 在 `frontend/src/components/shell/` 增加 `SystemAdminShell`，与 `AppShell` 分离；系统管理员默认不加载家庭导航、家庭搜索和家庭 store。
- [ ] 更新 `App.vue`，按主体类型渲染系统后台壳或家庭应用壳。
- [ ] 将现有 `AdminView` 的数据加载改为账号/成员关系/空间元数据接口；实现申请审核、原管理员同意工单状态和审计，不扩展 Provider 等其他平台功能。
- [ ] 系统后台明确展示管理员归属空间；家庭用户侧当前空间只显示该空间 manager，别的空间 manager 身份不能改变当前权限。
- [ ] 家庭用户侧提供目标明确的管理员申请卡片和原管理员待办工单；申请卡片逐张显示目标 `lineage` 空间名称/类型，工单逐张显示目标空间名称、申请人和同意/拒绝动作。
- [ ] 更新 auth store、登录恢复、PIN 门禁、logout 和敏感 store 清理；不把 `is_admin` 作为新权限来源。
- [ ] 更新前端类型、API 模块和测试：系统管理员默认路由、普通用户不能进入后台、系统管理员不能进入家庭路由、母亲/父亲示例、无家庭数据字段。

**回滚点：**先保留 `/admin` 兼容入口和旧家庭壳，确认主体类型守卫测试通过后再移除旧平台导航逻辑。

## 7. 规范与交接记录

- [ ] 更新 `.trellis/spec/architecture.md`：系统管理员独立主体、空间唯一 manager、跨空间管理员权限、owner/space_admin 产品合并、后台最小元数据边界。
- [ ] 更新 `.trellis/spec/backend/database-guidelines.md` / `error-handling.md` / `quality-guidelines.md`（若形成新约束）。
- [ ] 更新 `.trellis/spec/frontend/directory-structure.md` / `state-management.md`（独立后台壳和主体状态）。
- [ ] 在本任务 `notes.md` 记录最终迁移选择、兼容字段保留期、失败冲突示例、验证结果和未纳入范围。
- [ ] 如发现可复用的长期架构事实，写入项目 memory；不要把实现临时细节写成长期规则。

## 8. 验证顺序

### 后端窄测

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_bootstrap_api.py tests/test_auth.py
.venv/bin/python -m pytest -q tests/test_manager_applications.py <新增系统管理员/空间管理员测试>
.venv/bin/python -m pytest -q <新增迁移与最小元数据 API 测试>
```

### 前端窄测

```bash
cd frontend
npm run type-check
npm run lint
npm test -- --run <受影响测试文件>
```

### 全量门禁

```bash
cd backend
.venv/bin/python -m pytest -q
.venv/bin/python -m mypy app
.venv/bin/ruff check .
.venv/bin/ruff format --check .

cd ../frontend
npm run type-check
npm run lint
npm test
npm run build
```

### 手工验收矩阵

- [ ] 系统管理员首次登录 → 直接到独立后台；无家庭导航/家庭数据。
- [ ] 普通用户登录 → 家庭应用；不能访问系统后台。
- [ ] 父亲：household manager；母亲：该 household member + 母系 lineage manager；两者权限互不串线。
- [ ] 用户申请成为管理员的卡片明确显示目标 lineage 空间名称；系统管理员审核后向该空间原管理员发送可追踪工单；原管理员同意前不能发生角色交换。
- [ ] 同一用户管理多个 lineage；每个 lineage 只显示一个 manager。
- [ ] 系统管理员可查看账号、成员关系和空间元数据，但页面/API 响应没有档案详情、关系图内容、附件、私人会话/记忆。
- [ ] 多管理员冲突迁移拒绝并可诊断；合法数据迁移后唯一索引生效。

## 9. 完成前检查

- [ ] 所有 PRD acceptance criteria 有对应测试或人工验收证据。
- [ ] `git diff` 只包含本任务文件、代码改动和原有工作区变更，不覆盖 `backend/tests/conftest.py`。
- [ ] 运行 `task.py validate`，更新 implement/check manifest。
- [ ] 实现、检查和规范更新完成后才能进入提交/归档阶段；本计划不授权当前创建任务直接开始实现。
