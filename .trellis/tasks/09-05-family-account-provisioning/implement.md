# Implement: 家庭账号开通与注册流程

> 前置：`task.py start` 后才动手。顺序执行；每步验证通过再进下一步。

## Chunk A：邀请码底座

- [ ] A1 迁移 `0030_add_invite_codes.py`（含 stranger⇔space_id NULL、一次性码 max_uses=1 CHECK）+ `models/invite_code.py`
- [ ] A2 `services/invite_codes.py`：生成（无混淆字符集）/校验（过期/撤销/用尽）/核销/撤销原语 + 归因 audit 事件
- [ ] A3 后端单测：码生成唯一性、三种 kind 的约束、核销竞态（used_count 并发上界）、撤销权限（creator vs space_admin）
- 验证：`cd backend && python -m pytest tests/ -k invite_code`
- 回滚点：迁移 down。

## Chunk B：注册端点

- [ ] B1 `config.py` 加 `REGISTRATION_ENABLED`（默认 True）
- [ ] B2 `commands/registration.py`：注册命令（查重防枚举 → User(provisional)+Account(claimed) → 码分支：无码 / household-lineage（pending→接受复用）/ stranger（create_space+归因））
- [ ] B3 IP 滑窗限流（进程内）挂注册端点
- [ ] B4 `POST /api/auth/register`（开关关→404）+ schemas
- [ ] B5 后端单测：四分支（无码/家庭码/家族码/陌生人码）+ 开关关 404 + 用户名占用防枚举 + 限流 429 + 冷启动（零账号库首个注册）
- 验证：`cd backend && python -m pytest tests/ -k register`

## Chunk C：码管理端点

- [ ] C1 `GET/POST /api/invite-codes`、`DELETE /api/invite-codes/{id}`（creator 或空间 space_admin）
- [ ] C2 `POST /api/me/invite-codes/redeem`（复用 B2 码分支的加入逻辑；stranger→400）
- [ ] C3 单测：provisional 建码 403、空间管理员撤销他人码、过期/用尽/撤销后拒绝文案
- 验证：`cd backend && python -m pytest tests/ -k "invite or redeem"`

## Chunk D：并流绑定（独立回滚单元，迁移 0031）

- [ ] D1 迁移 `0031_add_account_bindings.py` + model
- [ ] D2 建档/邀请命令撞名改造：不建 managed 账号 → 建 binding(pending)，响应带 `bound_to_existing`
- [ ] D3 `POST /api/bindings/{id}/confirm`：本人 + PIN 复验 + `confirm_profile_identity` + 人物挂接（space_profile_refs / SpaceMember 重挂到既有 user）
- [ ] D4 单测：撞名转绑定、确认后人物唯一（§0.9）、确认前发起人零数据可见、重复确认 409
- 验证：`cd backend && python -m pytest tests/ -k binding`
- ⚠️ 评审门：D3 挂接语义若发现涉及面超预期（如 provisional 人物已有关系边），停止并回报，考虑拆子任务。

## Chunk E：前端

- [ ] E1 `api/auth.ts` register + `api/inviteCodes.ts` client
- [ ] E2 `RegisterView.vue` + 路由 `/register`（guest 可达、开关关时路由不存在）+ `?code=` 回填
- [ ] E3 `LoginView` 注册入口；`OnboardingView` 零账号改注册引导
- [ ] E4 `SettingsView`「邀请码」区块（我的码列表/创建三类/撤销/复制链接 + 填码加入）
- [ ] E5 前端测试：注册表单校验/回填、码区块权限态（provisional 隐藏创建）、防枚举文案
- 验证：`cd frontend && npm run test && npm run type-check && npm run lint`

## Chunk F：收尾全量检查

- [ ] F1 `cd backend && python -m pytest`（全量，含既有建档/邀请回归）
- [ ] F2 `cd frontend && npm run test && npm run type-check && npm run lint`
- [ ] F3 手动冒烟：docker-compose 起 → 全新库 → 注册首号 → 建空间 → 造家庭码 → 第二注册人持码加入 → 陌生人码开独立空间归因核对
- [ ] F4 spec 更新：architecture.md 新增「注册与邀请码」节（trellis-update-spec）
- [ ] F5 commit（Phase 3.4）
