# Design: 家庭账号开通与注册流程

## 1. 边界

- **只动 8000 家庭 listener**。8002 admin listener 不新增任何路由；system-admin-frontend 零改动、零文案。
- 既有三条账号产生路径（建档、onboarding link 兑换、admin bootstrap）语义不变，与本任务的注册/码系统并存。
- 身份三条状态机（Account managed→claimed、Profile provisional→identity_confirmed、fact review）转换点保持唯一（identity_fsm.py），本任务只**调用**不新增转换点——绑定确认与「这是我」确认走 `confirm_profile_identity` 既有入口。

## 2. 数据模型（迁移 0030_add_invite_codes，纯增量）

```
invite_codes:
  id            PK
  code          String(12) unique, 无混淆字符集（去 0/O/1/I/L）随机生成
  kind          CHECK IN ('household','lineage','stranger')
  creator_id    FK users.id (RESTRICT)
  space_id      FK family_spaces.id, nullable  # household/lineage 必填；stranger 必须 NULL
  max_uses      Integer nullable               # stranger 可设上限；一次性码 = 1
  used_count    Integer default 0
  expires_at    DateTime                       # 默认 created_at + 7d
  revoked_at    DateTime nullable
  created_at    DateTime
```

- CHECK 约束：kind='stranger' ⇔ space_id IS NULL；kind IN ('household','lineage') ⇒ space_id NOT NULL 且 max_uses=1。
- 归因不建新表：兑换/注册成功写既有 audit 事件（`services/audit.py`），载荷含 code_id、code_kind、creator_id、被邀请 user_id；陌生人码场景同样落 audit。`SpaceMember.added_by` 天然记录家庭/家族码的邀请人。

**绑定请求**（决策 16）：

```
account_bindings:
  id           PK
  initiator_id FK users.id            # 发起建档/邀请的人
  target_id    FK users.id            # 已存在的自注册 user
  person_id    FK users.id nullable   # 建档产生的 provisional 人物，确认后并回 target
  status       CHECK IN ('pending','confirmed','rejected','cancelled')
  created_at / resolved_at
```

## 3. 后端端点（全部挂在 8000 家庭 API）

| 端点 | 说明 |
|---|---|
| `POST /api/auth/register` | 公开。校验 `REGISTRATION_ENABLED`（关闭→404，与未注册路由同形，不给探测信号）。单事务：用户名查重（防枚举统一文案）→ 建 User(provisional)+Account(claimed, pin_must_change=false) → 有码则处理码逻辑 → 返回 token pair（直接登录态）。 |
| `GET/POST /api/invite-codes` | 我的码列表 / 创建（household/lineage 须为该空间 active 成员；stranger 任意 active 成员；provisional 403）。 |
| `DELETE /api/invite-codes/{id}` | 撤销：creator 本人或该码 space 的 active space_admin。 |
| `POST /api/me/invite-codes/redeem` | 已登录用户填码：household/lineage → 复用既有邀请接受路径（pending→当场 active）；stranger → 400 明确提示仅注册场景有效。 |
| `POST /api/bindings` | 建档/邀请撞名时由后端命令内部创建；用户侧 GET 我的待确认绑定。 |
| `POST /api/bindings/{id}/confirm` | 被绑定人本人 + PIN 复验 + `confirm_profile_identity` → provisional 人物的成员资格/空间引用挂接 target user，person_id 并回，审计。 |

**注册命令落位**：`backend/app/commands/registration.py`（新），HTTP 层薄；未来 Agent 工具可复用（§0.6 组合事务惯例）。码原语（生成/校验/核销/撤销）落 `backend/app/services/invite_codes.py`。

**限流**：注册端点 IP 限流用进程内滑动窗口（compose 单 API 进程，无横向扩容；若未来多实例再落 DB）。登录侧 AD-2 不动。

## 4. 注册事务分支

```
POST /auth/register(username, pin, display_name, code?):
  开关关 → 404
  IP 限流 → 429
  用户名占用 → 409 防枚举统一文案
  code?:
    无 → User+Account，走 §215 空态引导
    household/lineage → 码有效(未过期/未撤销/未用尽) → User+Account + SpaceMember(status=pending) → 调用既有「接受邀请」命令转 active（同事务、同审计形状）→ 核销码 used_count+1
    stranger → 码有效 → User+Account + create_space(household, 「我的家庭」) → used_count+1 + 归因 audit
    码无效 → 400（文案区分：码无效/过期仅限"码本身"字段级提示，不暴露创建者信息）
  建档撞名（独立于注册端点）：建档命令查重命中已注册用户名 → 不建 managed 账号，改创建 account_bindings(pending) → API 返回 200 + bound_to_existing 标记，前端转「已发送绑定邀请」态
```

## 5. 前端

- `RegisterView.vue`（新，路由 `/register`，meta chrome='blank'）：用户名/PIN/确认 PIN/显示名/可选邀请码；支持 `?code=` 回填；防枚举文案与后端一致。
- `router`：guest 可达 `/register`；`meta.registrationEnabled` 由启动配置注入，开关关时路由不存在（NotFound）。
- `LoginView`：加「注册新账号」入口。
- `SettingsView`：新增「邀请码」区块——我的码（列表/创建/撤销/复制链接）+ 填码加入。
- `OnboardingView`：零账号文案从「请通过开通渠道获取账号」改为引导注册（`/register`）。
- `stores/auth.ts` 加 register action；`api/auth.ts`、`api/inviteCodes.ts`（新）client。

## 6. 权衡与拒绝项

- **注册即 identity_confirmed**：拒绝——信任链起点是家人确认，不是自证。
- **OTP（短信/邮件）**：拒绝——自托管产品新增第三方依赖为负资产；一次性 PIN+首登语义已覆盖线下转交风险。
- **持码直接 active（绕过 pending）**：拒绝——体验相同但会发明第二条状态机路径，违背「转换点唯一」沉淀。
- **「第二人加入才升级管理员」**：拒绝——现状创建即 space_admin 且 §0.8 要求创建后恰好一个 active admin，用户已确认现状即目标。
- **注册审批队列（后台）**：暂缓——先用限流+provisional 能力收敛起步，注册量起来再评估；届时后台只加"账号审批"投影，不违背后台封闭决定。

## 7. 兼容与回滚

- 迁移 0030 纯增量（新表），down migration 删表即可，不动既有列。
- `REGISTRATION_ENABLED=false` 即功能整体下线（端点 404 + 前端入口消失），等于即时回滚开关；码数据保留无害。
- 绑定流程是最大不确定项（人物挂接涉及 space_profile_refs/成员关系重挂）：实现为独立 chunk，可单独回滚（迁移独立为 0031），若评审发现膨胀可拆子任务。
