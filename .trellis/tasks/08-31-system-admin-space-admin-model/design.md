# 技术设计：系统管理员与空间唯一管理员模型重构

## 1. 设计目标与边界

本任务修正两个相互独立但当前被混淆的身份域：

```text
平台身份：system_admin（内部兼容 platform_operator）
家庭域身份：用户档案 + 空间成员关系
```

平台身份只允许进入系统管理后台，家庭域权限始终按 `(account/user, space)` 计算。系统管理员不是家庭域用户，不进入 `visibility.evaluate` 的家庭可见性优先链，也不通过普通成员/档案 API 读取家庭内容。

产品角色只保留一个规范角色：`space_admin`，中文统一为“空间管理员”。每个已建立且可用的 `family_space`（`lineage` 或 `household`）必须有且只有一个 active `space_admin`。同一用户可以在多个不同空间各自拥有一条 active `space_admin` 关系。`owner`、`family_spaces.owner_id` 和 owner transfer 是现有实现的兼容数据/命名，不得继续作为第二个角色或第二套授权来源。

## 2. 身份与认证边界

### 2.1 系统管理员身份

当前代码链为 `User -> Account -> PlatformRoleAssignment(platform_operator)`，其中 `bootstrap.initialize_admin` 创建 `User`，`auth` token 以 `User.id` 为 subject，前端以 `UserOut.is_admin` 派生平台入口。这与产品要求“系统管理员没有家庭用户/空间”不一致。

推荐采用**独立平台主体 + 独立凭据**的最终模型：

```text
system_admins
  id, login_name/label, created_at, status
system_admin_accounts
  id, system_admin_id, pin_hash, pin_must_change,
  token_version, failed_attempts, locked_until, status, claimed_at
system_admin_sessions / refresh_sessions
```

JWT 增加明确主体类型（例如 `sub` + `principal_type=system_admin`），认证解析返回平台主体上下文；家庭用户仍使用 `User + Account`。系统管理员登录、刷新、改 PIN、登出与家庭账号共用安全规则，但不再伪装成 `User`。

若当前认证框架无法在本任务一次性拆分 refresh/session 表，必须采用兼容过渡：在现有 `Account` 增加明确 principal 类型或新增平台身份映射，使平台管理员不会被任何家庭端点当成 `User`；迁移脚本将旧 bootstrap 平台记录提升为独立平台主体，并禁止新建/保留对应 `SpaceMember`、`FamilySpace` 和家庭资料关系。过渡方案必须把移除 `UserOut.is_admin` 的时间点和 token 版本兼容策略写入实现记录，不能永久继续依赖全局兼容布尔值。

### 2.2 家庭用户身份

普通家庭用户仍为 `User + Account`，其平台角色集合应为空或只包含明确允许的家庭无关标识。家庭端 `/me`、成员、图谱、空间、记忆和 Agent 路由只接受家庭用户主体；系统管理员调用这些端点返回统一的无权/不存在响应（按现有防枚举契约）。

### 2.3 前端身份投影

将 `UserOut.is_admin` 替换为明确的会话主体字段（如 `principal_type` 与 `platform_role`），并在 `auth` store 建立 `isSystemAdmin`。不能让 `isPlatformOperator` 通过家庭用户 `is_admin` 兼容字段继续作为长期授权源。

## 3. 空间管理员数据模型

### 3.1 规范化概念

对于任意空间 `s`：

```text
space_manager(s) = 唯一 active SpaceMember
                   且 role = 'space_admin'
                   且 user_id ∈ 家庭用户域
```

对每个已建立且可用的空间，`count(active SpaceMember where role='space_admin') == 1`。新建空间必须在同一事务内创建这条关系；管理员交接只能在同一事务内完成“新管理员升为 `space_admin`、原管理员降为 `member`”，不能提交零个或两个管理员。

授权判断统一使用：

```text
is_space_manager(actor_user_id, space_id)
```

不得使用 `is_admin(actor_user_id)`、`family_spaces.owner_id` 或“用户是否管理过任意空间”判断权限，也不得因为 actor 是某个其他空间的管理员而放行当前空间操作。

### 3.2 owner 与 space_admin 收敛

本任务不再保留两个内部管理员角色，采用以下唯一模型：

- `SpaceMember.role='space_admin'` 是唯一的空间管理员角色；`owner` 不再是合法的长期角色值；
- `family_spaces.owner_id` 迁移期只能作为旧数据/旧 API 的兼容镜像或历史来源，不能参与授权判断；若暂时保留，必须与当前 `space_admin.user_id` 在同一事务内同步，并标记移除条件；
- 现有 owner transfer 改为“空间管理员交接”：目标 active member 升为 `space_admin`，原 `space_admin` 降为 `member`，必要时同步兼容 `owner_id`，并保留审计；
- `manager application approve` 和管理员交接都必须由同一空间唯一性约束保护；不得让 `owner` 与 `space_admin` 并存，也不得让原管理员交接后继续保留管理权限；
- 唯一性索引以 `space_id` 为第一维，因此同一用户可以在多个空间各有一条 `space_admin` 关系。

产品 API、前端类型、文案和授权服务只使用“空间管理员”；旧 `owner` 字段/接口若保留，只能是兼容投影，不得让调用方把它解释成第二种管理员。

### 3.3 迁移冲突处理

迁移按以下顺序执行：

1. 建立/扩展平台主体结构和角色投影；
2. 扫描现有平台角色与空间成员；
3. 对每个空间计算 active `owner`/`space_admin` 兼容候选；旧 `owner_id` 只有在对应 active 成员存在且没有歧义时才能作为候选来源；
4. 候选必须恰好为一个。候选为零或大于一时，迁移必须失败并报告 `space_id`、候选 user id 和 role，不得随机选择、静默删除或自动把某人升/降级；
5. 将唯一候选规范化为 `role='space_admin'`，再创建“每空间一个 active space_admin”的数据库约束；
6. 将新建空间、管理员申请和管理员交接命令改为只写规范角色，并为并发操作增加原子状态/唯一性保护；管理员申请必须增加“原管理员同意工单”的状态和过期校验；
7. 迁移后运行空库、现有合法数据和冲突数据回归。

## 4. 管理员申请、原管理员同意与交接数据流

### 4.1 申请提交

家庭用户提交 `{space_id}`：

1. 认证解析为家庭用户；
2. 命令层验证目标是明确且已存在的 `lineage` 家族空间，申请人是该空间 active 普通成员并已完成身份确认；尚未加入目标空间的用户先走成员加入流程；
3. `household` 不接受本申请类型；申请只能针对申请人所属的目标 `lineage` 空间，不能使用当前空间或全局管理员语义替代目标空间；
4. 服务端根据 `space_id` 读取并返回 `target_space_name`、`target_space_kind`，供申请卡片、确认页和状态记录展示；客户端提交的名称只作为显示输入，不能作为授权或目标依据；
5. 同一申请人对同一目标空间最多一条 pending 申请；已有管理员不使申请直接成功，也不允许申请人绕过原管理员同意；
6. 在短事务内创建 pending 申请并审计，返回申请目标空间的最小投影。

### 4.2 系统管理员审核与原管理员工单

申请表保留申请状态；原管理员同意不直接复用申请终态，而由独立的交接同意记录承载：

```text
manager_transfer_consents
  id, application_id, space_id, current_manager_user_id,
  status(pending|accepted|rejected|expired),
  requested_at, responded_at, response_reason, version
```

该记录本身就是可审计的站内工单/待办，不依赖外部短信、邮件或推送系统。`application_id`、`space_id` 和 `current_manager_user_id` 必须一致且只能有一条有效 pending 工单。

1. 系统管理员在独立后台读取申请人账号标识、目标 `lineage` 空间元数据、当前管理员账号标识、成员关系/角色元数据和申请理由；不得读取家庭档案详情；
2. 系统管理员审核拒绝时，申请进入 rejected，必须记录理由，当前管理员关系不变；
3. 系统管理员审核通过进入交接准备时，创建一条绑定 `application_id`、`space_id`、`current_manager_user_id` 的管理员同意工单，并向当前 active `space_admin` 发送可追踪的站内通知/待办；同一申请不得重复生成有效工单；
4. 工单至少展示申请人账号标识、目标空间名称、目标空间类型和“同意后将由申请人接任、本人降为普通成员”的说明，不展示家庭档案或关系图内容；工单发送、查看、同意、拒绝均审计。

### 4.3 原管理员处理与最终批准

1. 只有工单绑定的当前管理员，且其在处理时仍是目标空间 active `space_admin`，才可同意或拒绝；其他空间管理员、历史管理员和系统管理员不能代替原管理员同意；
2. 原管理员拒绝时，工单进入 rejected，申请进入 rejected，当前管理员保持不变；系统管理员不能绕过拒绝直接交换；
3. 原管理员同意时，工单进入 accepted，但申请仍保持 pending，等待系统管理员执行最终 approve；同意记录绑定当前管理员、目标空间和申请版本；
4. 系统管理员最终 approve 前必须原子复核：申请仍为 pending、工单为 accepted、目标空间仍为同一空间、申请人仍为 active `member`、同意人仍为当前唯一 active `space_admin`；任一条件不满足都拒绝交换并使同意失效，不能使用过期同意；
5. 复核通过后，在同一事务中把申请人升级为唯一 `space_admin`，把原管理员降为普通 `member`，同步必要的 `owner_id` 兼容镜像，并将申请标记 approved。整个事务不得提交零个或两个本空间管理员；角色变化、工单处理和裁决全部写审计与领域事件。

### 4.4 工单失效、并发与异常

- 当前管理员被移除、降级、删除，目标空间状态改变，或申请/工单超时后，工单必须失效；管理员交接不得依据旧同意继续执行。申请可保持 pending 供系统管理员重新核验，或在明确理由下进入终态。
- 同一申请只能有一个有效的待处理同意工单；并发发送、同意、拒绝和最终 approve 必须使用条件更新/版本校验，只有一个状态转换获胜。
- 原管理员拒绝或系统管理员最终拒绝不删除申请、工单和审计历史；申请人可在规则允许时重新提交新的申请，但新申请必须重新获得新的原管理员同意。
- 正常已建立空间不得通过管理员申请进入无管理员状态。迁移/修复产生的零管理员数据只能由独立、可审计的修复命令处理。

### 4.5 管理员交接与其他流程

现有 ownership transfer 在实现上改名/投影为“空间管理员交接”，与上述申请区分：

- 当前管理员主动发起的交接继续使用交接 FSM；由于原管理员是主动发起方，不重复创建申请工单，但仍须目标管理员接受并在同一事务内完成唯一角色交换；
- 管理员申请审批和管理员交接都只改变唯一 `space_admin` 关系，不产生 `owner` 第二角色；
- 交接完成时新管理员成为 `space_admin`，原管理员降为 `member`，必要时同步旧 `owner_id` 镜像；
- 交接不能产生两个 active manager，也不能把已完成空间留成零 manager；任何零/双管理员中间状态只能存在于同一短事务内部，提交后不允许暴露。

审批和交接不修改家庭档案内容，不让系统管理员进入家庭可见性逻辑。


## 5. 平台后台与最小数据投影

### 5.1 后端 API

在 `backend/app/api/admin.py` 或拆出的平台 API 中保留独立前缀（现有 `/api/admin` 可继续作为 URL 兼容前缀，但语义改为系统后台）：

- `GET /admin/accounts`：账号状态、账号标识、主体类型；
- `GET /admin/space-managers`：空间管理员账号与其管理空间归属；
- `GET /admin/spaces`：空间 id、名称、kind、生命周期/创建时间、唯一管理员账号元数据；
- `GET /admin/spaces/{id}/members`：成员账号 id、成员关系/角色/状态、加入时间等元数据；
- `GET/POST /admin/manager-applications...`：那一脉 `lineage` 家族空间的申请队列、最小目标空间投影和裁决；系统管理员审核后只能创建/查看原管理员同意工单，不能跳过同意直接交换；
- `GET/POST /admin/manager-transfer-consents...`：仅用于系统管理员发送/查看工单状态，不返回家庭档案内容；原管理员通过家庭用户侧的目标工单接口同意或拒绝，接口必须校验其仍是目标空间当前唯一 active `space_admin`。

系统后台的业务数据范围固定为账号、成员关系、管理员归属、空间元数据、管理员申请记录和交接工单的最小元数据；本任务不扩展 Provider、Agent、系统策略等其他平台运维功能。具体 URL 和字段可沿用现有接口以降低前端迁移成本，但必须取消平铺家庭用户详情的接口契约；至少不能返回 `gender`、`birth`、`death`、`bio`、`avatar_path`、附件、关系图边、私人会话/记忆或披露内容。

所有列表都应使用专用 Pydantic schema，而不是 `dict[str, Any]`，以防未来误加家庭字段。

### 5.2 前端

拆分当前 `AppShell` 与平台后台：

```text
App.vue
├── SystemAdminShell + SystemAdminView（system_admin）
└── FamilyAppShell + 家庭视图（family_user）
```

路由守卫按主体类型 fail-closed：

- system_admin 默认 `/system-admin`；访问 `/`、`/home`、`/spaces/*`、`/graph`、`/memory` 等家庭路由回系统后台；
- family_user 访问 `/system-admin` 或 `/admin` 返回家庭首页/403；
- 登录恢复、PIN 门禁和登出必须按主体类型清空正确 store；
- 平台后台页面展示账号、成员关系、空间元数据、申请和交接工单，不展示家庭档案详情。
- 家庭用户侧增加“我的管理员申请”卡片和“待我处理的管理员交接工单”卡片。每张申请卡片必须显示目标 `lineage` 空间名称和类型；每张工单必须显示目标空间名称、申请人账号标识、当前状态和同意/拒绝操作。
- 当前空间是 `household` 时，不显示将其申请为管理员的入口；用户应从明确列出的 `lineage` 目标卡片发起申请。

平台后台可以复用通用主题、按钮和表格组件，但不能复用会自动加载家庭数据的家庭应用壳或 store。

## 6. 授权与防越权

- `platform_operator/system_admin` 在 `visibility.evaluate` 中继续返回 `none`，不能通过 self、custody、membership 或 relationship 分支绕过；平台后台的最小元数据查询使用独立的系统管理员授权和专用投影，不复用家庭档案可见性接口；
- 平台元数据查询必须使用专用命令/查询对象，严格选择列；
- 前端隐藏只是 UX，不是授权；后端每个 endpoint 都调用系统主体依赖；
- “母亲是母系家族空间管理员”只影响她在母系空间的 manager 查询结果；她在父亲管理的家庭空间中仍按该空间的 `member` 角色；
- 账号 id、space id 和 manager application id 的错误返回遵循统一错误结构和防枚举规则；无关平台身份访问家庭资源返回 `404 SPACE_NOT_FOUND` 或统一无权响应。

## 7. 兼容、部署和回滚

### 7.1 兼容

- 迁移期可读 `platform_operator`，但产品显示使用“系统管理员”；保留期必须记录；
- API 旧字段/旧路由只有在不会造成家庭权限误判时才保留；`is_admin` 不能继续作为授权源；
- 旧前端会话 token 应因主体类型/版本变化安全失效并要求重新登录；不能让旧 `User` token 获得新平台后台权限。

### 7.2 回滚

- 迁移前执行 SQLite online backup；
- 迁移先在副本执行，确认冲突扫描结果为空后再应用；
- 如果发现多管理员冲突，停止迁移，不自动修复；
- 应用回滚只能回滚代码，不应对已写入的管理员裁决做破坏性逆操作；必要时通过新的显式审计修复命令恢复。

## 8. 需要在实现前锁定的技术选择

以下不是产品开放问题，但实现计划必须在编码前做出并记录：

1. 独立系统主体是新增表，还是在现有 `accounts` 上增加主体类型并解除 `User` 依赖；推荐新增表，安全边界最清楚。
2. `owner`/`owner_id` 只作为迁移期兼容镜像，还是在本任务内完成删除；产品结果已锁定为唯一 `space_admin`，实现必须先保证规范角色和唯一性，兼容字段的最终删除时间需结合现有 API 消费者确定，但兼容字段不得参与授权。
3. 旧 `/admin/users` 是否改为账号元数据接口或保留兼容别名；推荐保留 URL 但改专用 schema，另增清晰命名接口。
4. 是否拆出独立 `system-admin` 前端路由前缀；推荐拆出，同时保留 `/admin` 重定向兼容。
5. 不新增系统管理员同意工单的外部通知渠道；至少实现可审计的站内通知/待办，外部短信、邮件或推送可另立任务。

这些选择不改变 PRD 的产品规则：系统管理员无家庭主体；每个空间只有一个本空间管理员；管理员资格按空间绑定；后台只看最小元数据。
