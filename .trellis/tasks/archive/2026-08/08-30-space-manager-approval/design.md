# 技术设计：已有空间 `space_admin` 申请审批

## 数据模型

新增 `space_manager_applications` 表（`backend/app/models/space.py`）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int PK | 申请标识 |
| applicant_user_id | FK users | 申请人 |
| space_id | FK family_spaces, 非空 | 申请人成为管理员的目标空间 |
| request_kind | str | 只有 `space_admin` |
| status | str | `pending` / `approved` / `rejected` |
| decision_note | str, nullable | 平台备注，驳回必填 |
| decided_by | FK users, nullable | 裁决平台运营者 |
| created_at / decided_at | datetime | 创建与裁决时间 |

约束：

- `request_kind IN ('space_admin')`；
- `space_id IS NOT NULL`；
- `status` 只允许 `pending`、`approved`、`rejected`；
- partial unique index `(applicant_user_id, space_id, request_kind) WHERE status='pending'`；
- 0021 迁移的父修订为并行 runtime 的 `0020_agent_runtime_profile`，发布顺序固定为 0020 → 0021。

## 命令层

`backend/app/commands/manager_applications.py`：

- `submit_manager_application(session, ctx, *, request_kind='space_admin', space_id)`：
  - 从 `ActorContext` 加载 actor；
  - 校验 `identity_confirmed`；
  - guest-only 用户拒绝；
  - 目标空间不存在、申请人非该空间 active member 统一 404；
  - 只有目标 membership.role 为 `member` 才可申请；owner/space_admin 返回 409；
  - 显式查重提供稳定错误，插入置于 savepoint，唯一索引是并发最终裁决；
  - 成功写入 `manager_application_submitted` 审计。
- `decide_manager_application(session, ctx, application_id, *, decision, note, decided_by)`：
  - 命令层再次复核当前账号拥有 `platform_operator`；
  - reject 理由为空返回 422；
  - 用 `UPDATE ... WHERE status='pending'` 原子抢占终态；第二个裁决者返回 409；
  - approve 时重新检查申请人仍是目标空间 active `member`，然后只写 `member.role = 'space_admin'`；不修改 `family_spaces.owner_id`；
  - 资格冲突或其他副作用异常由命令事务回滚，申请保持 pending；
  - 成功写入 `space.manager_application.decided` 与批准/驳回审计。
- `applications_of/list_applications/serialize_application` 只返回申请人名、空间名及申请状态，不返回家庭档案字段。

## API

用户侧（`api/spaces.py`）：

- `POST /api/spaces/manager-applications` body `{request_kind: 'space_admin', space_id}`；
- `GET /api/spaces/manager-applications/mine`。

平台侧（`api/admin.py`，统一 `require_platform_operator`）：

- `GET /api/admin/manager-applications?status=pending`；
- `POST /api/admin/manager-applications/{id}/decision` body `{decision: 'approve'|'reject', note?}`。

既有 `POST /api/spaces` 自由建空间不变，`create_space` 继续由用户路由和 MemberCreateWizard 复用。
## 邀请与其他治理流程

- `active owner/space_admin/member` 均可邀请；guest 不能邀请；受邀人本人仍需接受 pending membership；该流程不经过平台审批。
- owner 变更继续使用 `ownership_transfers` FSM；管理员申请 approve 不触碰 owner。
- `/admin` 是平台运营后台，不因 platform_operator 身份获得家庭数据浏览权；家庭空间管理页仍按目标空间 active owner/space_admin 守卫。

## 前端

- `HomeView`：当前空间 `member` 显示“申请成为管理员”，提交目标空间 `space_id`；guest 隐藏；保留自由创建空间和 member 邀请。
- `MemberCreateWizard`：保留族谱空间名称输入与 `createSpace(name, 'lineage')`，创建成功后选中新空间。
- `AdminView`：队列类型只有“申请成为空间管理员”；通过直接裁决；驳回弹窗理由为空时禁用。
- `api/spaces.ts`、`api/admin.ts`、`types/api.ts` 使用 `space_admin` 单一联合类型，不保留 `proposed_name`。

## 测试设计

后端覆盖：资格/防枚举、请求 schema、重复 pending、审批队列、approve 升级、owner 不变、reject 理由、终态 409、资格变更回滚、自由建空间回归、并发提交和并发裁决。

前端覆盖：Home member/guest 入口、目标空间提交、AdminView 通过/驳回、Wizard 族谱空间直建与已有空间选择。
