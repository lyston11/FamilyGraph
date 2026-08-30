# 实施记录：空间管理员申请审批（08-30-space-manager-approval）

## 最终范围

本任务只实现“已有空间 active member 申请成为该空间 `space_admin`，由 `platform_operator` 审批”。以下语义明确不变：

- active member（除 guest）可以直接邀请账号；邀请不需要平台审批，受邀人接受后才成为 active membership；
- `POST /api/spaces` 继续自由创建 household/lineage 空间，创建者即 owner；
- MemberCreateWizard 保留族谱空间直建；
- owner 变更仍只能通过 ownership transfer FSM；approve 管理员申请绝不修改 `family_spaces.owner_id`；
- platform_operator 只进入平台运营后台，不因平台角色获得家庭资料浏览权。

## 改动清单

### 后端

| 文件 | 改动 |
| --- | --- |
| `backend/app/models/space.py` | 新增 `SpaceManagerApplication`；申请类型只允许 `space_admin`；`space_id` 非空；pending partial unique index 为 `(applicant_user_id, space_id, request_kind)`。 |
| `backend/app/models/__init__.py` | 注册导出新模型。 |
| `backend/app/errors.py` | 增加申请重复、未找到、已裁决、驳回理由必填错误码；未保留空间创建审批错误码。 |
| `backend/app/commands/manager_applications.py` | 实现 active member 申请、guest/资格门禁、savepoint+唯一约束竞态处理、platform_operator 二次复核、原子终态抢占、member→space_admin 升级、审计和领域事件。 |
| `backend/app/commands/spaces.py` | `create_space` 保持自由创建；邀请授权保持 active member（除 guest）。 |
| `backend/app/api/spaces.py` | 保留自由 `POST /spaces`；新增用户侧管理员申请与本人状态接口。 |
| `backend/app/api/admin.py` | 新增平台管理员申请队列和 approve/reject API。 |
| `backend/app/schemas/space.py` | `ManagerApplicationCreate` 只接受 `space_admin + space_id`；输出不含 `proposed_name`。 |
| `backend/migrations/versions/0021_space_manager_application.py` | 新表与约束；必须在父修订 `0020_agent_runtime_profile` 已落地后发布。 |
| `backend/tests/test_manager_applications.py` | 改为只覆盖已有空间管理员申请，新增提交/裁决并发回归。 |
| `backend/tests/conftest.py` | 清表加入申请表，保留空间直建 fixture。 |

### 前端

| 文件 | 改动 |
| --- | --- |
| `frontend/src/types/api.ts` | `ManagerRequestKind` 只保留 `space_admin`，删除 `proposed_name`。 |
| `frontend/src/api/spaces.ts` | 保留 `fetchSpaces/createSpace`；增加管理员申请提交与本人查询。 |
| `frontend/src/api/admin.ts` | 增加平台管理员申请列表与裁决 API。 |
| `frontend/src/stores/spaces.ts` | 保留自由建空间 action；邀请仍由 active member（除 guest）可用。 |
| `frontend/src/views/HomeView.vue` | 保留创建空间和邀请入口；member 显示当前空间管理员申请；guest 隐藏。 |
| `frontend/src/views/AdminView.vue` | 队列只展示“申请成为空间管理员”，支持通过及必填理由驳回。 |
| `frontend/src/components/member/MemberCreateWizard.vue` | 恢复族谱空间直建输入、按钮和 `createSpace(name, 'lineage')`。 |
| 相关前端测试 | 删除旧字段和“新空间审批”断言，恢复族谱直建断言。 |

### 规范

- `.trellis/spec/architecture.md` §0.7 已同步为单一 `space_admin` 晋升审批合同，并明确邀请、自由建空间、owner transfer 不变。
- `.trellis/spec/frontend/component-guidelines.md` 保留审批表的 Naive UI、领域徽章、最小披露约定。

## 并发安全

- 提交：显式查重只提供友好错误；实际 INSERT 在 savepoint 中执行，partial unique index 最终裁决，竞争请求稳定返回 409 且不污染外层事务。
- 裁决：条件更新 `status='pending'` 原子抢占；只有获胜者执行角色升级、事件和审计，第二裁决者稳定返回 `SPACE_MANAGER_APPLICATION_DECIDED`；副作用失败由命令事务回滚，申请保持 pending。

## 迁移协调

`0021_space_manager_application` 的 `down_revision` 为 `0020_agent_runtime_profile`。0021 不能脱离 0020 独立部署，发布顺序固定为：

```text
0020_agent_runtime_profile → 0021_space_manager_application
```

## 最新门禁（2026-08-30）

- backend：`.venv/bin/python -m pytest -q` → **587 passed**。
- backend：`.venv/bin/python -m mypy app` → **Success: no issues found in 121 source files**。
- backend：本任务白名单 `ruff check` → **All checks passed**；`ruff format --check` → **10 files already formatted**。
- frontend：`npm run lint` → 通过；`npm run type-check` → 通过；`npm run test` → **40 files / 249 tests passed**；`npm run build` → 成功（Vite，2940 modules transformed）。构建仅提示既有动态/静态 import chunk warning。
- task validation：`python3 .trellis/scripts/task.py validate 08-30-space-manager-approval` → **All validations passed**。
- 前端本任务聚焦回归：**4 files / 33 tests passed**，包含 Home、Admin、MemberCreateWizard、spaces store。
