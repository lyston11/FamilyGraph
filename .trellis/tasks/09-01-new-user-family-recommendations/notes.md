# 新认领用户的个人家族初始化与亲属推荐：延期决策记录

## 2026-09-01 · 为什么独立成任务

用户提出 Steward 应给刚注册的用户推荐家族用户，并为每个用户生成独有家族树和亲属称谓。讨论中确认：推荐必须依赖已经正确计算的 PersonalFamilyView，但“刚注册”的准确产品事件尚未决定，因此不能把触发点塞进 assistant-only 或 PersonalFamilyView 任务中提前实现。

## 已确认边界

- Steward 是执行初始化和推荐规划的底层 Agent，Assistant 负责解释和收集用户操作。
- 推荐只能基于当前用户合法可见、证据可解释且当前有效的 PersonalFamilyView。
- 推荐限于允许的家族/lineage 授权边界，不能全平台找人。
- “可能认识的血亲”路径只要包含 `spouse/partner` 就必须排除。
- 同一治理空间但没有确认关系路径的人不能仅凭同空间进入亲属推荐。
- 推荐是候选，不会自动把人物加入正式个人树、授予可见权、发送申请或建立成员关系。
- 已拒绝、处于冷却、事实失效、不可见或被 supersede 的推荐不得重复出现。
- 推荐展示不得通过姓名、关系或解释泄漏当前用户无权查看的人物或空间。
- 用户明确接受后，仍由 FastAPI 领域命令重新校验权限、事实版本和当前状态。

## 仍暂定的产品问题

“新用户”可能对应不同事件，尚未选定：

1. 亲属代建的 managed account 首次登录并完成 `managed → claimed`；
2. 用户首次获得某个 LineageSpace/HouseholdSpace 的有效访问权；
3. 未来开放的完全自助注册；
4. 已有用户因新关系或新桥接首次形成可计算个人树。

这些事件可能需要不同的隐私、身份发现、幂等和重跑语义。正式规划时必须先选择产品触发点，不能默认把“注册成功”当作唯一事件。

## 依赖

- `09-01-personal-family-view`：提供当前有效个人树、关系路径、称谓和推荐资格输入。
- `09-01-agent-runtime-assistant-only`：保留 Steward 独立 Agent 身份与运行边界。
- `09-01-person-identity-dedupe`：避免重复人物产生错误推荐。
- Steward/ActionCard 既有链路：承载候选、解释、接受/拒绝和冷却状态。

当前 `prd.md` 保持占位状态，`design.md`/`implement.md` 暂不创建。以后进入本任务时，应先围绕触发点完成需求讨论，再写完整验收和技术设计。

## 2026-09-03 · 正式规划决策（触发点收敛 + 深入代码后的事实修正）

用户批准围绕触发点完成规划。深入代码调查后确认并决策：

### 产品决策

- **触发点选定 `managed → claimed`（认领）**：identity_fsm 显示这是唯一转换点明确、隐私边界最清晰的入口；其余三个候选（首次获得空间访问、自助注册、已有用户首次成树）写入 PRD Out of scope 留待后续任务。
- **推荐 v1 是只读投影**（`GET /api/family-recommendations`），不 ActionCard 化——扩 `CARD_KINDS` 需要迁移与卡片合同变更，且血亲推荐本身没有可执行领域动作；"不再推荐"记忆落 `BehaviorProjection` 新前缀 + `STEWARD_COOLDOWN_DAYS`。
- 待确认关系线索复用既有 proposed SourceFact 确认流，推荐层不承载执行动作。

### 代码调查修正的事实（已写入 prd/design）

- 认领有**两个转换点**：`commands/identity.py` `claim_and_confirm_own_identity`（已发 `account.claimed`）与 `commands/members.py` `change_own_pin` 强制改 PIN 分支（**不发事件**）——触发合同必须补齐后者，否则走 PIN 路径认领的用户永远不会初始化。
- `schedule_steward_job_for_event` 对无空间事件的现状是**全空间 fan-out**；认领事件需要收窄为"认领者 active 空间"，否则触碰无关空间的活跃 job cursor。
- 初始化的懒语义已存在：`get_view` 对 `never_computed` 自动重建，Steward 作业的 `rebuild_space_views` 覆盖 `never_computed/queued/stale/failed`——初始化只需保证"认领 → 每空间作业"的管道打通。
- 同空间无路径人物不进入视图（`load_graph` 可见集合 ≠ 视图纳入）,"同空间不推荐"边界由数据口径天然保证,但必须有测试守住。
- 血亲推荐的称谓/路径字段直接复用视图边（`term`/`concept_code`/`path_class`）,不重算。
