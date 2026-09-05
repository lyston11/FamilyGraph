# FamilyGraph 全局架构设计 v1.2

> 状态：审计复审后修订（2026-08-25 第二轮“有条件通过”整改）。本文档解决审计记录第三节的 10 个架构问题及复审指出的跨文档合同不一致。
> 决策来源分两层：**锁定决策**（用户确认，见 HANDOFF.md §三）与**审计默认假设**（本文标注 `[AD-n]`，实现前可推翻但需记录）。所有 M0–M4 子任务的 design.md 必须引用并遵守本文。

---

## 0. v2 权威合同（2026-08-26 起生效，取代下列 v1 条款）

> V2.0 Foundation 任务（`.trellis/tasks/08-26-v2-0-foundation`）确立以下合同。标注 **[v2 取代]** 的 v1 小节自本文档日期起作废，仅存档供追溯。后续所有任务（含 Pi Runtime / Agent 工具）必须以本节为准，不得继承被取代的 v1 语义。

### 0.1 统一可见性四级 + 字段级 disclosure **[v2 取代 §4 QU1=B、§6 矩阵与 AD-9 第 4 列规则]**

- 可见性层级统一为 `self_private | household_detail | lineage_summary | none`，由 `visibility.evaluate(actor, target, space_context, purpose)` 单点输出 level 与字段级 mask；调用方不得自行拼装可见性规则。
- **v1「直系结构边自动 full」（QU1=B）与 full/summary/invisible 三级作废**。直系结构边（elder/younger/spouse active）在无共同空间时最多授予 `lineage_summary`；peer 边本身不再授予任何可见性。
- Household active member 可见家庭详情，但凭据、私人会话/记忆、未公开关系、健康、住址等高敏感字段一律排除。
- Lineage 只见必要字段与本人公开类别：显式 `disclosure_preferences`（全局偏好可被逐空间覆盖，默认不公开）只扩展字段投影，不单独授予可见性。
- pending 成员/请求两端点、provisional 人物只见对应最小化信息（baseline 字段）。
- 未成年人默认最小披露 overlay：精确生日、住址、学校、联系方式、私人描述等对任何非本人主体遮蔽，不因 household、lineage、Agent 或 operator 身份自动开放。
- purpose（profile/graph/search/statistics/export/agent/rag）只能收紧不得放宽：agent/rag/search/statistics 投影不得超过 profile API 口径。
- 代管创建者（created_by）保有查看权，映射为 `household_detail` 层级；编辑权仍由 custody 判定。
- `platform_operator` 不进入可见性优先链（等同无关用户 none→404）；break-glass 是未来独立审计接口，不属于常规判定路径。

### 0.2 平台角色与空间角色分离 **[v2 取代 users.is_admin 全局数据权]**

- `platform_operator` 存于 `platform_role_assignments`，仅管理系统代码、Provider、工具白名单和安全策略；**默认且默认之外也无家庭数据读取权**，普通管理后台数据兜底操作走 break-glass 审计（后续任务）。users.is_admin 列已删除。
- 空间角色为 `space_admin`、`member`；旧 `owner` 只作为迁移兼容输入归一化为 `space_admin`，不参与授权或 API 输出。

### 0.3 三条独立单向状态机 **[v2 扩展 §1 ClaimState]**

- Account：`managed → claimed`（accounts.status，转换点=首登改 PIN，记 claimed_at）。
- Profile：`provisional → identity_confirmed`（users.profile_status）；Account claimed 不自动确认 Profile。
- 外部事实：`proposed → confirmed | disputed`（profile_fact_reviews 清单模型）；三条状态机各自独立完成、单向且审计。
- 未完成 identity_confirmed 的人物不具备推荐资格。
- 建档表单名字和关系必填，其余可空；自由描述保留作者/原文/时间/scope，不自动成为正式事实。

### 0.4 空间 kind 与 provisional 引用 **[v2 扩展 §3/§4]**

- `family_spaces.kind = household | lineage`；PersonalFamilyView 为派生投影而非实体。
- 创建他人时选择 no-space/household/lineage；选空间只创建 `space_profile_refs` 最小节点引用，**provisional 人物不是 SpaceMember**。
- 关系、配偶、管理员关联都不自动合并空间；桥边与共同 HouseholdSpace 均为显式对象/流程。

### 0.5 owner 保护与移交 **[v2 取代 §5 中 family_spaces.owner_id ON DELETE CASCADE]**

- owner 删除/退出/注销前必须移交（ownership_transfers FSM）；无合格继任者时走显式终止流程。owner_id FK 为 RESTRICT，**禁止 FK 级联静默删空间**。
- owner 移交、profile custody 移交、Account claim 是三个不同流程，不得混用。
- owner onboarding link（platform_operator 签发）：短期、单次、可撤销、只存 hash；兑换后创建独立 LineageSpace 并授予 owner，不授予 platform_operator，也不连接其他管理员的空间。

### 0.6 数据权利与领域命令边界

- 本人可申请结构化导出、资料更正、删除/注销（data_right_requests FSM）；异步结果继承 VisibilityPolicy，有过期下载与审计。
- 删除/撤权/争议传播经 `domain_events`（append-only）驱动缓存、附件、DerivedFact、RAG/搜索索引和 Agent 会话投影失效（投影本体在相应任务实现）。
- 认领争议保留 evidence、状态、双方最小披露与平台人工兜底；平台人工处理需 break-glass 原因与完整审计，不因此获得日常浏览权。
- 建档、档案修改、空间变更、关系请求、附件等 API 组合事务抽成 application/domain command，HTTP 与未来 Agent 工具共用同一授权/FSM/写入/事件/audit 短事务；外部网络调用不进入事务。

### 0.7 空间管理者审批（用户确认 2026-08-30，任务 08-30-space-manager-approval）

- **成为已有空间的空间管理者需要经平台运营者审批**：当前空间 active `member` 可申请由 `member` 升级为 `space_admin`；已有 `space_admin` 不适用。
- **邀请与管理员审批是两条独立流程**：active member 可以直接邀请账号，邀请只创建 pending membership，受邀人本人接受后才成为 active；邀请不需要平台运营者审批。
- **空间创建沿用既有自由创建语义**：用户可通过 `POST /api/spaces` 创建 household/lineage 空间，自建者成为 owner + active 成员；MemberCreateWizard 也可直接创建族谱空间。共同家庭空间与 Owner Onboarding 邀请兑换路径保持不变。
- 同一 (申请人, 目标空间, kind) 至多一条 pending（partial unique index + 命令层查重 409 `SPACE_MANAGER_APPLICATION_EXISTS`）；已裁决申请终态不可再变（重复裁决 409）。申请人须 `identity_confirmed` 且是目标空间 active `member`。
- **现有空间的 owner 只能通过既有 owner 移交流程（ownership_transfers FSM，现任 owner 发起）变更**；平台运营者裁决 `space_admin` 申请绝不触碰任何现有空间的 `family_spaces.owner_id`，approve 只做该空间内 active member → space_admin 一升。
- 裁决动作（approve/reject，reject 理由必填 422）在同一短事务内完成：角色升级 + 审计（`manager_application_submitted/approved/rejected`，批准行带 `admin_action`）+ 领域事件 `space.manager_application.decided`；若审批时成员资格已变化，申请回滚为 pending。
- 运营者队列仅展示裁决所需最小数据（申请人名、申请类型、目标空间名），不产生任何家庭数据浏览权（延续 §0.2/§0.6 边界）。
- 实现：`backend/app/commands/manager_applications.py`、`backend/app/api/spaces.py`（用户侧提交/自查与自由建空间）、`backend/app/api/admin.py`（队列/裁决，require_platform_operator）、迁移 0021。

---


## 0.8 系统管理员与空间唯一管理员重构（2026-08-31；⚠️ 认证/路由/bootstrap 条款已被 §12/§12.1 取代，本节仅主体分离与 space_admin 语义继续有效）

本节覆盖并替代本文早期将 `platform_operator` 绑定到家庭 `User` 的兼容描述；其系统管理员认证（PIN）、路由位置（`/api/admin/*`）、首启初始化端点与共享 JWT 域条款已废止，现行合同见 §12（独立 admin listener / 用户名+密码 / 部署自动 bootstrap / 独立签发域）与 §12.1（/admin-api/v1 只读模型与审计）：

### 1. Scope / Trigger

- 系统管理员是独立平台主体，不引用 `User`、`Account`、`FamilySpace` 或 `SpaceMember`；首启只创建 `system_admins` 与 `system_admin_accounts`。
- 家庭空间管理员是空间域关系，不是用户全局属性；规范角色只有 `space_admin`。
- 系统后台只处理账号、成员关系、管理员归属、空间元数据、申请和交接工单的最小投影。

### 2. Signatures

- `POST /api/bootstrap/initialize` → 一次性系统管理员登录名和 `one_time_pin`；数据库不得创建家庭主体。
- `GET /api/admin/accounts`、`GET /api/admin/spaces`、`GET /api/admin/space-managers`、`GET /api/admin/spaces/{space_id}/members` → 仅 `require_system_admin`，使用专用 Pydantic schema。
- `GET/POST /api/admin/manager-applications...` → 仅 `require_system_admin`；申请目标必须是已存在的 `lineage` 空间。
- `SpaceManagerApplication(applicant_user_id, space_id, status)` → 申请人必须是目标空间 active 普通成员；目标已有管理员时先创建绑定当前管理员的 `ManagerTransferConsent`。
- `is_space_manager(session, space_id, user_id)` → 只按目标空间 active `space_admin` 关系判断。

### 3. Contracts

- `SpaceMember.role` 的持久化值为 `space_admin|member`；每个正常空间最多一个 active `space_admin`，创建和交接完成后必须恰好一个。
- 旧 `owner` 只允许作为迁移/旧夹具输入，在 ORM 写入事件中归一化为 `space_admin`；不得参与授权，也不得作为 API 产品角色输出。
- JWT 必须携带 `principal_type=system_admin|family_user`；家庭端依赖拒绝 system-admin 主体，系统后台依赖拒绝普通家庭主体。
- 元数据响应不得包含档案日期、性别、简介、头像、附件、关系图边、私人会话/记忆或敏感披露字段。

### 4. Validation & Error Matrix

- 非 `lineage` 管理员申请 → `422 VALIDATION_ERROR`。
- 目标空间不存在或申请人不是 active 成员 → `404 SPACE_NOT_FOUND`（防枚举）。
- 非 member、已是目标管理员 → `403/409`，不得创建申请。
- 系统管理员未完成首登 PIN 修改 → 仅允许 PIN/登出/刷新白名单。
- 已有管理员但未取得明确同意 → `409`，不得交换角色；原管理员拒绝时保持原关系不变。
- 目标空间无管理员或并发交接校验失败 → `409`，进入修复/重新核验流程，不提交零/双管理员终态。

### 5. Good/Base/Bad Cases

- Good: 同一用户在空间 A、B 各有一条 active `space_admin`，在空间 C 仍按 C 的 member 角色授权。
- Base: 系统管理员登录后读取账号与空间元数据，但 `/api/me`、`/api/spaces` 返回认证失败，不进入家庭壳。
- Bad: 用 `is_admin(user_id)`、`owner_id` 或用户在任意其他空间的管理员身份放行当前空间治理。

### 6. Tests Required

- 首启数据库只存在独立系统主体；系统 token 可访问 `/admin/accounts`，家庭 token 返回 403。
- 系统 token 访问 `/me`、`/spaces` 被拒绝；元数据响应字段白名单无家庭档案字段。
- 路由注册断言 `/admin/accounts`、`/admin/spaces`、`/admin/space-managers` 各只有一条。
- lineage 申请、同意工单、拒绝、过期、并发交接均验证唯一 active `space_admin`。
- Alembic 空库、合法存量和 owner/space_admin 冲突数据分别验证。

### 7. Wrong vs Correct

#### Wrong

```python
if user.is_admin or family_spaces.owner_id == actor.id:
    allow_space_management()
```

#### Correct

```python
if is_space_manager(session, space_id, actor.id):
    allow_space_management()
```

## 0.9 同一空间人物身份唯一（2026-09-01）

同一空间内不得存在同一个人的两份档案。每个 `User` 携带一个 `Account` 与一次性 PIN，
所以重复建档等于多出一份**可登录凭据**——这是身份问题，不只是数据质量问题。

**判定口径的唯一真源是 `services/person_identity.py`**：写入门禁与 Steward 回溯审计
共用它，阈值不得在两处各写一套。

- 匹配键 = 归一化姓名 + 规范公历生日。姓名归一：NFKC → 去空白与分隔符 → **繁转简**
  （产品裁定：只留简体）→ casefold。生日归一到公历 ISO：农历行读 `mirror_date`，
  缺失（历史行）时才现算——换算口径的唯一真源是 `services/lunar.py`。
- 强度三档：`strong`（同名同生日）拒绝建档，`detail` 给出既有档案 id，调用方改为
  引用它（加 `space_profile_refs`）；`weak`（同名但任一侧生日缺失，不可判定）打断创建，
  要求创建者显式确认"这是另一个人"；`none`（不同名，或双方生日都有且不同）放行——
  双方生日都在且不同是同名的不同人，大家族跨辈同名常见，不得反复追问。
- 消歧开关 `allow_duplicate_person` **只放宽 weak**。strong 不受其影响。
- 作用域是"该空间"，同时含 `space_profile_refs`（provisional 引用）与 `space_members`
  （已认领成员）；两个不相干家庭各有一个"李秀英 1948-03-12"必须都允许。

### 为什么靠 BEGIN IMMEDIATE 而不是唯一索引

身份键派生自 `users`，而空间作用域在 `space_profile_refs`/`space_members` 上（一个 user
可属多个空间）。索引建在 `users` 上只能保证全局唯一（错的）；建在 ref 表上则键被反
规范化，改名（三处入口）与生日编辑任一漏同步就**静默失效**。

因此并发保证由 `command_transaction(immediate=True)` 提供：SQLite 单写者，写锁前置后
"检查 → 插入"之间没有竞态窗口。键在查询时现算，不落列、不建索引；空间是几十到几百人
量级，全扫开销可忽略。**回归用例必须覆盖并发建档恰好一个成功**——去掉写锁后它必须失败，
否则该用例是假的。

### 归一只作用于比对键

`users.name` 是待本人确认的 provisional 数据（`profile_fact_reviews` 的 name 必审项），
本人认领后有权改回自己要的写法。归一**永不覆写存储值**。

### 已知缺陷与限制（不在本次修复范围）

- zhconv 覆盖部分异体字（峯→峰、淩→凌）但不覆盖全部（喆 保持原样）；未覆盖者落到
  weak 要求消歧，而不是被静默并成同一人。
- 生日未知时 DB 层无法给出硬保证（否则会拦住真正的同名不同人），残留重复由 Steward
  回溯审计报出——身份重复是**涌现属性**：后补生日才暴露的重复，写入时门禁结构上不可能捕捉。

> **[v2 取代]** 本节的 users.claim_status 已迁移至 accounts.status（managed|claimed + claimed_at）；is_admin 列已删除（见 §0.2/§0.3）。其余 PersonProfile/Account 分离概念不变。

锁定决策 A3 的用户体验不变（添加关系 ≈ 创建账号 + 一次性 PIN），但内部概念分离（前两者是实体，Claim 是 users 表上的状态字段而非独立领域对象）：

```
users（PersonProfile 家谱人员档案）
  id, name, gender, birth, death, bio, avatar_path,
  privacy_mode(perpetual|handover), created_by→users.id,
  claim_status(managed|claimed), deleted_at(NULL=存活)
accounts（登录凭据，与档案 1:0..1）
  user_id UNIQUE FK CASCADE, pin_hash, pin_must_change(BOOL),
  token_version(INT), failed_attempts(INT), locked_until(DATETIME NULL)
```

- **每个 PersonProfile 建档即配发 Account**（A3 锁定），但 Account 只是"待认领凭据"：
  - `claim_status=managed`：从未登录。代管人 = created_by（handover 模式）或永久编辑者（perpetual 模式）。
  - `claim_status=claimed`：本人完成首次登录且改过初始 PIN。
- **首登强制改 PIN**：`pin_must_change=true` 时仅放行 `PUT /me/pin`、`POST /auth/logout`、`POST /auth/refresh`（会话延续所需最小集合），其余 API 一律 `403 PIN_CHANGE_REQUIRED`；改毕置 false 且 `claim_status=claimed`、token_version+1。（`GET /api/health` 为公开端点，不经此依赖管辖。）
- 已故/未成年人：自然停留在 managed 态，无需特殊逻辑。冒用风险缓解：PIN 一次性展示 + 审计留痕 + 首登强制换 PIN——本人认领改 PIN 后，**旧初始 PIN 即失效**（持旧凭据的创建者无法再登录冒用）；perpetual 归属模式下创建者的档案编辑权不受认领影响（D5 明确保留的权利，失权的只是旧凭据）。
- 认领不可逆：claimed 后不能退回 managed（v1 非目标：注销/移交）。

## 0.10 家庭端自助注册与邀请码（2026-09-05，任务 09-05-family-account-provisioning）

### 1. Scope / Trigger
- 家庭端（8000）新增公开注册端点、邀请码域（三类型）与并流绑定域（account_bindings）；新增迁移 0030/0031。**8002 admin listener 零新增路由、system-admin-frontend 零痕迹**（延续 §12 拓扑）。
- 身份三条状态机**不新增转换点**：注册是初始态直接落库（非 managed→claimed 转换）；`identity_confirmed` 仅经 `identity_fsm.confirm_profile_identity` 既有入口（绑定确认是唯一新增调用点）。
- §0.6 owner onboarding link 兑换端点保留不变；其 admin-web 签发入口留作后续选项（陌生人码已覆盖新家庭进入）。

### 2. Signatures
- `POST /api/auth/register`（公开）：`{username, pin, display_name(接收不持久化，单名模型并入 users.name), invite_code?}` → token pair。命令层 `commands/registration.py::register_user`（单立即事务）。
- `GET|POST /api/invite-codes`、`DELETE /api/invite-codes/{id}`、`POST /api/me/invite-codes/redeem`；码原语 `services/invite_codes.py`（生成去 0/O/1/I/L、resolve 字段级文案、`consume_code` 条件 UPDATE 原子核销）。
- `GET /api/bindings`、`POST /api/bindings/{id}/confirm|reject`、`DELETE /api/bindings/{id}`；命令层 `commands/bindings.py`。
- DB：`invite_codes(code UNIQUE, kind∈{household,lineage,stranger}, creator_id nullable SET NULL（迁移 0032，原 RESTRICT 会永久挡住创建者删除——产品无删码入口，已撤销码同样触发 RESTRICT）, space_id nullable, max_uses, used_count, expires_at, revoked_at)`，CHECK：`stranger ⇔ space_id IS NULL`、household/lineage ⇒ `space_id NOT NULL AND max_uses IS NOT NULL AND max_uses=1`（SQLite CHECK 对 NULL 放行，必须显式排除）。`account_bindings(initiator_id CASCADE, target_id CASCADE, person_id SET NULL, status∈{pending,confirmed,rejected,cancelled})`。
- 配置：`REGISTRATION_ENABLED`（默认 True）经 `GET /api/bootstrap/status` 加法字段 `registration_enabled` 投影。

### 3. Contracts
- 注册初始态：`Account(status='claimed', pin_must_change=False)`（自选 PIN 无需强制改）+ `User(profile_status='provisional')`。能力边界（决策 13 修订 2026-09-05，用户拍板撤销 provisional 建码限制）：**每个已登录账号都可建码与邀请**——household/lineage 码须为该空间 active 成员，stranger 码任何账号可建（纯归因）；接受码无身份门槛；身份确认（identity_confirmed）影响的是推荐资格与空间管理员申请等能力，不影响码域。
- 码语义：household/lineage 码一次性，加入**只走 SpaceMember FSM**（`space_fsm.invite`(pending) → `transition("accept")`，`added_by`=码创建者，审计 `space_invite_accepted`+`invite_code_redeemed`）；stranger 码多人次可设上限，仅归因（audit `invite_code_redeemed`，scene=register）——持码注册者经 `create_space(commit=False)` 得自己独立 household 空间（创建者即 space_admin，符合 §0.8 唯一管理员不变量），与码创建者**零** SpaceMember/可见性关系。设置页兑换与注册时填码同一 `join_space_with_code` 路径（scene=redeem）。
- 并流绑定：建档撞名判定作用域 = 建档既有查重门禁口径（非全局用户名匹配，§0.9 同名不同人必须放行）；目标资格 = `Account.claimed` 且 `created_by IS NULL`。确认 = 本人 + PIN 复验（登录侧 auth_guard 同源失败计数）+ `confirm_profile_identity`，人物并回仅迁身份承载行（refs/members/边/附件/事实），不覆写存储值；拒绝/取消即删除撞名人物。
- 删除联动：删除用户时 `delete_profile_core` 调用 `auto_revoke_for_creator_delete` 自动撤销其全部未撤销码（audit `invite_code_auto_revoked_on_delete`，自删场景 actor_id=None）；码行经 0032 `creator_id SET NULL` 保留使用计数与撤销历史，归因由 audit_log 快照承载。

### 4. Validation & Error Matrix
- 开关关 → 404 与随机未知路径逐字节一致（body 校验前短路）；前端 `/register` 同形 404（URL 保留）
- 注册限流 → 429 + Retry-After（进程内滑窗）；用户名占用 → 409 防枚举统一文案 + dummy-bcrypt 时序对齐
- 码无效/过期/撤销/用尽 → 400 字段级文案（不含创建者信息）；stranger 码在设置页兑换 → 400 且不核销
- provisional 建码 → 403；已是空间成员再兑码 → 409 且**不核销**码
- 绑定：非本人与不存在同形 404；重复确认 409；confirmed/rejected 终态不可逆

### 5. Good/Base/Bad Cases
- Good：零账号库注册首号 → 自建空间正常使用（冷启动闭环）；家人持家庭码注册即 active 成员。
- Base：无码注册 → §215 空态引导建「我的家庭」。
- Bad：任何绕过 SpaceMember FSM 直接写 active 成员；注册即 identity_confirmed；码文案泄露创建者/空间信息；8002 出现注册/码路由。

### 6. Tests Required
- `tests/test_register_api.py`（冷启动/四分支/开关 404/限流/防枚举/初始态断言）、`tests/test_invite_codes_service.py` + `test_invite_codes_api.py`（约束/核销竞态/撤销权限/provisional 403）、`tests/test_bindings.py`（撞名转绑定/确认并回无残留/终态）、`tests/test_bootstrap_api.py`（registration_enabled 投影）、`test_members_api.py`（bound_to_existing 白名单）；前端 `register.spec.ts`、`InviteCodeSection.spec.ts`、`guard.spec.ts`。

### 7. Wrong vs Correct
#### Wrong
```python
SpaceMember(space_id=..., user_id=..., role="member", status="active")  # 持码加入直接造 active
```
#### Correct
```python
space_fsm.invite(...)                      # pending，added_by=码创建者
space_fsm.transition("accept", ...)        # 唯一路径转 active，审计同形
```

## 2. 认证安全合同 `[AD-2]`

| 项 | 规则 |
|---|---|
| 登录限流 | 按 name 失败计数（实现取账号名级、比 name×IP 更保守；同名多账号联动锁定），连续 5 次失败锁定该 name 15 分钟（accounts.locked_until，锁定窗口过期自动归还预算），错误文案统一 `用户名或 PIN 码错误` |
| JWT | access 2h + refresh 30d 轮换；refresh 凭据持久化于 `refresh_sessions` 表（user_id FK、token_hash、rotated_from、expires_at、revoked_at） |
| Refresh 轮换与重用检测 | 每次 refresh 将旧行置 revoked_at 并签发新行（rotated_from 链）；提交已 revoked 的 token 视为重用攻击 → 撤销该用户全部活跃会话 + 审计告警 |
| 注销/失效 | 登出 = revoke 对应 refresh_session 行；access 短期自愈。**改 PIN / 重置 PIN / 删除档案 → token_version+1**，校验时比对，旧 access 全部失效 |
| 同名同 PIN 消歧 | 两步：①`POST /auth/login{name,pin}` 多命中 → 写入 `auth_challenges` 表（id/jti、candidate_ids_json、ip、expires_at=5min、used_at NULL）并返回 `409 {challenge_id, candidates[{id,name,created_by_name}]}` ②`POST /auth/login/select{challenge_id,user_id}` → 服务端在单事务内校验未过期且 used_at IS NULL 后原子置 used_at（数据库保证单次使用、防重放）→ JWT；过期/已用一律拒绝并审计 |
| 审计 | audit_log(actor_id, action, target_id, ip, detail_json, created_at)；记录 login_failed≥3、pin_reset、全部 admin 操作、档案删除。保留 ≥180 天，仅 admin 可读 |

## 3. 新用户的家庭空间生成规则 `[AD-3]`

「以我为中心的家庭空间」（默认首页）是**派生聚合视图**，不是新实体：

1. 登录后首页 = 我拥有 active 成员资格的所有空间的卡片**去重并集**，按全局图布局渲染（U1 第一人称）。
2. 空间切换器可查看单个空间。
3. **默认进入空间优先级**：最近活跃的空间 > 我 own 的第一个 > 被拉入的第一个 active 空间。
4. 若我无任何空间成员资格（如被建档时未勾选加入任何空间）：首登引导创建「我的家庭」默认空间（owner=我，初始成员仅自己），随后基于 active 关系给出"一键邀请家人"建议列表——邀请走 D4 正常确认流，**绝不静默拉人入空间**（可见性升级必须经对方同意）。

## 4. 连接与空间成员状态机 `[AD-4]`

### Relation FSM
```
pending ──accept──> active        pending ──reject──> rejected(终态)
pending ──cancel──> cancelled(终态, 发起方)
active  ──revoke──> revoked(终态, 任一方; 断连轨 D8)
约束: 同一对用户最多一条非终态边 (partial unique index);
      自环禁止 (CHECK from_user != to_user); elder 边成环检测拒绝
反向显示: 不存反向行; 展示时结构类反译 elder↔younger / peer,spouse 对称,
      称谓标签始终显示创建者视角原文 [D3]
```

### SpaceMember FSM
```
pending ──accept──> active    pending ──reject/withdraw/expiry(30d)──> 终态
active  ──remove──> removed(终态, owner 或本人)
幂等: UNIQUE(space_id, user_id) 仅一行, 重复申请返回既有 pending
```

### 合并请求（回答"一次申请还是两次申请"）
- **connection_request（M1/M2 主流程）**：一份请求同时携带 `relation{dir_class,label}` + 可选 `space_membership(space_id)`。对方一次接受 → 两者同时 active；拒绝 → 同时取消。消除"关系 active 但空间 pending"的中间权限态。
- **新建账号例外（复审澄清）**：由代管人创建 **managed 新档**时，relation 与可选的 space_membership **直接 active**，不走确认流——创建者即代管人，D4 锁定语义本就如此；只有目标为**已存在或已 claimed 的账号**才进入 pending 合并确认流。
- **join_request（M2 家族视图摘要卡）**：仅携带 space_membership，无新关系边。目标空间 owner 审批。

### 权限授予判定（服务端实时计算，无缓存）
> **[v2 取代]** 以下 QU1=B「直系边完整互见」规则已被 §0.1 四级可见性取代：直系结构边跨空间最多 lineage_summary，不再自动 full。

**QU1 已裁定为修订版 U5（2026-08-25，用户确认；v2 已取代）**：

完整数据访问 ⇔ **双方在同一空间且均为 active 成员 ∨ 两端点之间存在至少一条 dir_class ∈ {elder, younger, spouse} 的 active 关系边**。

- 直系结构边（亲子/配偶）是信任代理：对端互见完整档案——家谱核心语义是血缘链上的信息可见。
- peer 边对端与仅 clan 连通可达者 → 摘要（名字/称谓/世代）；其余 invisible；搜索遵循同一基线。
- pending 期间：发起方可看接收方摘要（通知需要），反之亦然。断连/移出即时降级。

## 5. 数据库契约

> **[v2 取代]** family_spaces.owner_id 由 ON DELETE CASCADE 改为 RESTRICT（owner 删除必须先移交，禁止级联静默删空间）；新增 v2 基础表（platform_role_assignments、space_profile_refs、owner_invitations、disclosure_preferences、ownership_transfers、claim_disputes、data_right_requests、domain_events、profile_fact_reviews）见迁移 0008_v2_foundation。

- PRAGMA：`foreign_keys=ON, journal_mode=WAL, busy_timeout=5000, synchronous=NORMAL`（api 启动时统一设置）。
- FK/CASCADE：accounts.user_id、relations.from/to、space_members.user_id/space_id、node_positions、attachments.user_id 均 `ON DELETE CASCADE`。
- 约束：dir_class CHECK 枚举；UNIQUE(accounts.user_id)、UNIQUE(space_members.space_id,user_id)；partial unique index 保证单一非终态关系。
- 布局确定性规则（树状视图）：
  - 多根：各 elder 根并列顶层；
  - 子女归属：由子女自身的 elder/younger 边直接决定（父、母各有独立边），不通过配偶推导；
  - 多配偶：按关系创建序并列同行展示；
  - 冲突/异常数据：布局失败回退画布自由模式并提示（M1 验收项）。

## 6. 授权矩阵（visibility.py 单点实现）

> **[v2 取代]** 下表 full/summary/invisible 三级口径及第 3 列「直系自动 full」已被 §0.1 四级合同取代；AD-9 家族空间外披露开关的存储已迁至 disclosure_preferences（全局+逐空间 scope），开关类别扩至高敏感类。

| 资源 \ 主体 | 本人 | 同空间 active 成员 | 直系结构边对端（elder/younger/spouse active） | peer 对端 / clan 连通可达 | 其余 |
|---|---|---|---|---|---|
| 档案详情字段 | full | full | full | summary(name/称谓/世代) | invisible |
| 图节点+关系边 | full | full | full | 仅摘要节点 | 不返回 |
| 头像原图 | full | full | full | 占位图 | 占位图 |
| 附件元数据/下载 | full | full | full | invisible | invisible |
| 搜索命中 | — | — | 允许(full 详情) | 允许(摘要) | 不可命中 |
| 统计聚合 | — | — | 计入范围 | 计入范围 | 不计入 |
| join_request | 目标空间 owner 可见审批 | — | — | — | — |
| 空间邀请（invite） | — | active 成员可邀请；受邀人需接受 | — | — | — |
| 空间管理者申请 | 提交（identity_confirmed active member）与查看本人申请 | — | — | — | — |
| 管理者申请裁决 | platform_operator only（队列/approve/reject + audit；见 §0.7） | — | — | — | — |
| 管理 API | is_admin only + audit | — | — | — | — |

- IDOR 集成测试逐行覆盖矩阵（普通 JWT 直打 API 断言遮罩/invisible）。
- 文件下载走授权端点流式返回（禁止 nginx 直链 uploads 目录），响应头 `Content-Disposition` + `X-Content-Type-Options: nosniff`。

#
#
#
 
家
族
空
间
外
披
露
开
关
 
`
[
A
D
-
9
]
`
（
2
0
2
6
-
0
8
-
2
5
 
用
户
裁
定
）




-
 
适
用
对
象
：
非
同
空
间
且
无
直
系
结
构
边
的
家
族
可
达
者
（
p
e
e
r
 
对
端
、
远
房
）
。


-
 
必
要
字
段
始
终
可
见
：
名
字
、
称
谓
标
签
、
世
代
角
标
。


-
 
其
余
字
段
按
*
*
五
个
类
别
开
关
*
*
由
归
属
者
决
定
是
否
在
家
族
空
间
公
开
：
`
a
v
a
t
a
r
`
 
/
 
`
p
h
o
t
o
s
`
(
相
册
)
 
/
 
`
d
a
t
e
s
`
(
生
卒
)
 
/
 
`
b
i
o
`
 
/
 
`
a
t
t
a
c
h
m
e
n
t
s
`
(
链
接
附
件
)
，
存
储
于
 
`
u
s
e
r
s
.
c
l
a
n
_
d
i
s
c
l
o
s
u
r
e
_
j
s
o
n
`
，
*
*
默
认
全
部
不
公
开
*
*
。


-
 
开
关
修
改
权
 
=
 
该
档
案
的
 
D
5
 
编
辑
权
主
体
（
c
l
a
i
m
e
d
 
本
人
；
m
a
n
a
g
e
d
 
档
案
为
代
管
人
）
。
A
P
I
：
`
P
U
T
 
/
u
s
e
r
s
/
{
i
d
}
/
d
i
s
c
l
o
s
u
r
e
`
。


-
 
v
i
s
i
b
i
l
i
t
y
.
p
y
 
在
矩
阵
第
 
4
 
列
（
p
e
e
r
/
c
l
a
n
 
可
达
）
判
定
时
消
费
此
配
置
：
开
放
的
类
别
返
回
 
f
u
l
l
，
未
开
放
返
回
 
M
A
S
K
E
D
 
结
构
；
搜
索
与
统
计
口
径
一
致
。


-
 
直
系
结
构
边
对
端
不
受
开
关
限
制
（
Q
U
1
=
B
：
完
整
互
见
）
。




## 7. 删除语义（实现在 M1，非 M4）`[AD-5]`

- API：`DELETE /users/{id}`。权限：本人 ∨ 代管创建者（perpetual 模式或 handover 未 claimed）∨ admin。二次确认（前端输入名字确认）。
- 单事务级联：关系边删、space_members 删、node_positions 删、attachments 记录删、涉及该用户的 pending 请求删；audit_log **保留**（target 引用改为快照文本）；token_version+1 使其会话即刻失效。
- 物理文件删除在事务提交后异步执行，失败记清扫日志（孤儿文件由 m3a 的清扫任务兜底）。
- v1 采用硬删除，无回收站（HANDOFF 非目标）；备份文件中的残留数据随备份轮转淘汰。

## 8. 备份恢复（修正 WAL 直接复制缺陷）`[AD-6]`

- 备份命令：`python -m app.backup`（容器内执行），使用 **SQLite online backup API**（`Connection.backup`）产出一致性快照至 `/data/backups/familygraph-YYYYmmdd-HHMMSS.db`，随后与 `/data/uploads` 一同 tar 归档。
- 恢复演练是 M4 出口条件：restore 后 `PRAGMA integrity_check` 通过 + 用户数/关系数与源库一致。
- README 写明：**禁止**运行期直接 cp 主库文件。

## 9. 附件安全边界 `[AD-7]`

- 上传校验链：扩展名白名单(jpg/jpeg/png/webp) → Content-Length ≤10MB → magic bytes 校验 → Pillow `verify()` 真实解码 → 最大像素 8000×8000（防解压炸弹）→ 重编码输出（strip EXIF/脚本元数据）。SVG 一律拒绝。
- 外链附件：URL scheme 白名单 http/https；**服务端不抓取外链**（无 SSRF 面），前端 `<a target=_blank rel=noopener>` 外跳。
- 删除一致性：先事务删记录，后异步删文件 + 定期孤儿清扫脚本。
- 依赖新增：Pillow（m3a 引入）。

## 10. 数据权利与威胁模型边界 `[AD-8]`（v1 明确不做部分见 HANDOFF 非目标）

- 未成年人分级隐私：**v2 待定**。v1 依赖 U5 基线 + 家庭信任模型，写入 HANDOFF 默认假设。
- 敏感缓存清理：logout 清空 Pinia state + localStorage(JWT) + 内存中的图数据；路由守卫兜底。
- 数据导出/更正：v1 提供管理员协助通道（admin 数据修正后台），自助导出列 v2。

## 11. PersonalFamilyView 与跨族谱 Bridge（2026-09-01）

- PersonalFamilyView 按 `viewer_account + root_user + space` 建立可重建授权投影；它不是 SourceFact、Relation、SpaceMember 或公共图真源。
- 视图只消费 confirmed 结构事实和当前 VisibilityPolicy；同空间无确认路径的人不进入个人树，SocialRelation 不入图，`none` 节点完全省略。
- 跨 LineageSpace 连接必须使用显式 bridge。bridge 绑定两侧空间与 anchor，只有两位相关用户本人双向同意后 active；空间管理员只接收通知，不具备审批、否决、修改或撤销权。未认领账号不能代签。
- active bridge 只在当前 viewer 是 anchor 时作为跨空间边进入图遍历，并允许沿另一侧 anchor 可达的 confirmed 结构路径计算；跨空间节点只能使用最小 `lineage_summary` 字段。
- 关系事实、成员/引用、bridge consent/revoke 和权限收紧事件将相关投影标为 stale；读取时再次校验当前成员资格、bridge 状态和字段级可见性，撤权后不得返回旧节点。
- Steward 在 space-scoped job 中重建受影响投影；Assistant、platform_operator 和空间管理员均不能借此扩大读取权或直接写入 SourceFact。

## 12. 独立系统管理员平台与三 Listener 拓扑（2026-09-04，取代 §0.8 的认证/路由/bootstrap 条款）

§0.8 中"系统管理员与空间唯一管理员"的主体分离与 `space_admin` 语义继续有效；本节取代其认证协议（PIN → 用户名+密码）、路由位置（`/api/admin/*` → `/admin-api/*`）、首启方式（公开 initialize 端点 → 部署自动 bootstrap）与 JWT 签发域（共享 → 独立）条款。

### 1. Scope / Trigger

- 新增任何后台 API、管理员认证、跨 listener 路由调整时适用本节。
- 三个 FastAPI app 同进程：family `app`（8000，仅家庭认证+业务）、internal `internal_app`（8001，Agent）、admin `admin_app`（8002，仅系统管理员）。共享 engine/lifespan，不共享 router 对象。
- 家庭端产品边界：家庭 bundle、路由表、OpenAPI、认证响应不出现任何后台痕迹；`/admin-api/*` 在 8000 必须与随机未知路径逐字节一致的普通 404（存在性 oracle 红线）。

### 2. Signatures

- admin 路由面（仅 8002）：`POST /admin-api/auth/login`（username+password）、`POST /admin-api/auth/refresh`（轮换）、`POST /admin-api/auth/logout`、`GET /admin-api/auth/me`、`PUT /admin-api/auth/password`、`PUT /admin-api/auth/username`、`GET /admin-api/health`。
- 模型：`system_admins(username 唯一, status)`；`system_admin_accounts(password_hash, password_must_change, password_version, failed_attempts, locked_until, status, claimed_at)`；`system_admin_refresh_sessions(token_hash, rotated_from_id, expires_at, revoked_at, last_seen_at)`。迁移 `0028_admin_password_credentials`；存量 PIN 数据 fail-closed（RuntimeError 拒启），永不静默转换。
- 恢复 CLI：`python -m app.admin_recovery [--username admin]`。

### 3. Contracts

- 独立签发域（强制）：`ADMIN_JWT_SECRET`（≥32 且 ≠ 家庭 `SECRET_KEY`）、`ADMIN_JWT_ISSUER`、`ADMIN_JWT_AUDIENCE`（必填且互不相等）；缺失/过弱启动失败，无开发逃逸开关。access TTL 900s（`ADMIN_ACCESS_TOKEN_TTL_SECONDS`），refresh 绝对有效期（`ADMIN_REFRESH_TOKEN_TTL_SECONDS`，轮换不续期）。admin claims：`sub/principal_type=system_admin/iss/aud/token_version/jti/iat/exp/typ`。
- 交叉拒绝：admin token 在 8000 任意家庭路由 401；family token 在 8002 任意 admin 路由 401。不能只靠 `principal_type` 区分——iss/aud/secret 必须物理隔离。
- 会话撤销触发器：修改用户名、修改密码、锁定、运维恢复都必须 `password_version+1` + 撤销全部 refresh session（锁定分支也不例外）。
- bootstrap 凭据文件：`DATA_DIR/bootstrap/admin-credentials`，`mkstemp(0600)+os.replace` 原子写；初始用户名固定 `admin`，密码 CSPRNG，只存哈希；首次改密事务提交后删除，删除失败写安全告警 + `admin_credential_file_delete_failed` 审计 + 回置 `password_must_change=true`；已有 active/disabled 管理员时重启不生成第二账号。
- `AdminSessionOut` 白名单精确集合：`id/username/password_must_change/status`；家庭 `UserOut` 不含 `is_admin/platform_role`，`principal_type` 收窄为 `Literal["family_user"]`。

### 4. Validation & Error Matrix

- 用户名或密码错误 / 账号不存在 / 主体错误 → 统一 `401 ADMIN_INVALID_CREDENTIALS`（文案"用户名或密码错误"，防枚举，dummy 哈希对齐时序）。
- 失败达阈值 → `429` + `Retry-After`，同时撤销全部会话。
- `password_must_change=true` → 仅 password/refresh/logout 可用，其余 admin 路由 403。
- refresh 重用 → 401 并撤销该主体全部会话；refresh 过期（即使 JWT 未到真实时钟）→ 401 不签发新会话。
- 家庭 listener 上任何 admin 路径 → 普通 404（不是 403/重定向/自定义错误页）。

### 5. Good/Base/Bad Cases

- Good: 空库启动生成唯一 `admin` + 0600 文件；首登改密后文件消失、旧会话全部失效；把 admin token 复制到 8000 被拒。
- Base: 已初始化部署重启不生成第二账号；8002 独立健康检查 `/admin-api/health`。
- Bad: `admin/admin` 内置密码、把 PIN 当密码、admin router 挂回 8000、共享 issuer/audience 只靠 principal_type 放行、密码进日志/DB 明文/审计正文。

### 6. Tests Required

- 路由注册断言：8000 无 admin 业务路由；8002 恰好七条 admin 路由；旧 `admin.py` 全部 break-glass 路径在两个 listener 均不存在。
- 交叉拒绝矩阵（双向）；`/admin-api/*` 在 8000 与随机未知路径逐字节一致。
- bootstrap：唯一账号、文件 0600、日志 grep 无明文、改密后删除、删除失败回置 must_change、lifespan 接线空库启动。
- refresh：轮换后 `expires_at` 不续期（绝对有效期）、重用拒绝并撤销全部会话、行到期 401。
- 密码生命周期：首登强制、改密/改用户名/锁定/恢复后版本+1 且会话全撤销。

### 7. Wrong vs Correct

#### Wrong

```python
# 共享签发域，仅靠 principal_type 区分——复制 token 即越界
token = issue_jwt(sub=admin.id, principal_type="system_admin", secret=SECRET_KEY)
app.include_router(system_admin_router, prefix="/api")  # 后台路由挂上家庭 listener
```

#### Correct

```python
token = issue_admin_jwt(sub=admin.id, iss=ADMIN_JWT_ISSUER, aud=ADMIN_JWT_AUDIENCE,
                        secret=ADMIN_JWT_SECRET)  # 独立签发域
admin_app.include_router(admin_auth_router, prefix="/admin-api")  # 仅 8002
family_app.include_router(admin_api_404_catchall)  # 家庭面普通 404，无后台语义
```

## 12.1 /admin-api/v1 只读模型、访问会话与审计（2026-09-05）

### 1. Scope / Trigger

新增任何 admin 读端点、敏感详情端点或审批能力时适用。8002 的 `/admin-api/v1` 是系统管理员唯一业务数据面：15 条 GET + `POST /access-sessions` + `POST /manager-applications/{id}/approve|reject` 三条写。所有响应用专用 schema（`extra="forbid"`）+ 显式列投影，禁止 ORM 直接序列化、禁止复用家庭 visibility API；聚合根只按 `SpaceMember(role='space_admin', status='active')` 判定。

### 2. Signatures

- 列表统一 envelope `AdminPageOut[T]{items,page,page_size,total,has_more}`，`page_size ≤ 100`，稳定排序。
- `admin_access_sessions(token_hash UQ, system_admin_id FK CASCADE, target_type CHECK(user|space), target_id, reason, scopes_json, issued_at, expires_at, revoked_at)`；`admin_access_audits(system_admin_id/session_id FK SET NULL, action, target_type, target_id, endpoint, filters_json, result_count, request_id, ip, created_at)`。迁移 0029。
- 审批复用 `commands/manager_applications.py::decide_manager_application_as_system_admin` 单事务，同事务追加独立 admin 审计。

### 3. Contracts

- 字段白名单以当前模型实况为准：`AdminProfileOut` 无 `updated_at`（User 无此列）、附件元数据无 `size`（Attachment 无此列）；`RawRelationInput.text`、证据原文、`url_or_path`、description、Agent `error_json/result_json` 原文永不查询/序列化。
- 访问会话：TTL 30 分钟、SHA-256 hash-only、绑定单 user/space 不可跨用；敏感详情要求票据 header，五类失败（无/错目标/过期/撤销/跨管理员）统一 403 且审计拒绝尝试；详情响应 `Cache-Control: no-store`；明文票据只在创建响应出现一次。
- 脱敏器在 schema 之前运行且 fail-closed：键黑名单（token/secret/key/authorization/prompt/message/content/email/phone/address…）与值模式（Bearer/JWT/URL secret/credential-like/PII）清理；无法可靠脱敏只留 error_code + 安全位置。
- 审计永久保留（业务/管理员删除 SET NULL 不级联）；filters 经脱敏，理由正文不落审计；`/audit/access` 本身受 admin auth + 分页。

### 4. Validation & Error Matrix

`page<1|page_size>100` → 422；未知 admin → 200 空页、未知 space/user detail → 统一 404 防枚举；会话五类失败 → 403；`confirm≠true` → 422；reject 理由空 → 422；终态重复裁决 → 409；未知申请 → 404；双 active 管理员/无管理员/锁定或删除管理员 → 异常队列只读展示，不自动修复。

### 5. Tests Required

每响应精确集合断言 + 禁止字段负向断言；overview 查询数恒定（N+1 回归）；会话 TTL/hash/跨目标/跨管理员/拒绝审计/no-store；审计在管理员与业务对象硬删后保留；approve→consent→终批两阶段闭环；v1 写端点恰为三条的注册断言。

