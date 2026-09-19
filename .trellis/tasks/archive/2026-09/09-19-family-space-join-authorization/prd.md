# 家族空间准入边界：自行申请须有亲属关系且由空间管理员审批

## 背景与已核实事实（2026-09-19，隔离库 + 生产只读副本实测）

用户报告：朱元璋（user 1）能进入李氏家族空间，但家族树里他与李贞（48）之间没有任何连线；如果两人没有联系，他凭什么能进入别人的家族空间？用户明确定性这是**安全边界问题**。

实测证据（`DATA_DIR=/tmp/fg-iso-0919`，主库 `.backup` 副本；生产库未改动）：

1. **准入缺陷已复现（越权）**：常遇春（37）与朱标（3）共享常府/常氏家族 → `visibility.evaluate` 为 `household_detail`（可见）→ `POST /api/spaces/join-by-user {target_user_id: 朱标}` 落库到 **space 1 明皇室**（`owner_id=朱元璋`）的 `space_members` pending 行 → 再用**申请人自己的 token** 调 `POST /api/space-memberships/{id}/accept` → **status=active**。即：任何对某成员可见的人，都能不经该空间任何人同意、也不需与该家族有任何亲属关系，自行加入别人的家庭空间。
2. **文档合同与实现不一致**：`.trellis/spec/architecture/4--4-ad-4.md` 写「join_request（M2 家族视图摘要卡）：仅携带 space_membership，无新关系边。**目标空间 owner 审批**」，`.trellis/spec/architecture/6--6-visibility-py.md` 写「join_request：**目标空间 owner 可见审批**」。实现里 `space_fsm.transition(action="accept")` 要求 `is_self`，所以只有申请人本人能 accept；`backend/tests/test_m2c_flows.py::test_join_by_user_full_flow_and_idempotency` 注释写「owner 审批接受」，实际用申请人自己的 header 调用。
3. **目标空间解析过宽**：`backend/app/commands/spaces.py::request_join_by_user` 里「TA 的家庭空间」= target 拥有的空间，否则 `active_ids[0]`（任意 active 成员资格）。朱元璋是明皇室 owner，但以普通成员朱标为 target 时 `active_ids[0]` 命中的正是朱元璋的明皇室。
4. **无任何亲属关系门禁**：唯一门槛是 `visibility.evaluate(session, actor, target).visible`（防枚举 404）。朱元璋与李贞同属李家/李氏家族（`added_by=48`），所以初始数据合法；但**任何**可见关系（含仅共享 household、仅 ref、仅 pending）都足以发起申请。
5. **家族树路径被非本空间成员的中间人切断（另一个缺陷）**：`source_facts` 中 `5→1`、`7→1`（朱世珍、陈氏→朱元璋）与 `5→49`、`7→49`（→朱佛女）均为 confirmed 全局事实，`48↔49`（李贞⇄朱佛女）confirmed。但 `relationship_graph.load_graph` 用 `scoped_confirmed_facts(..., visible_ids=visible)` 要求**每条事实两端点都在当前空间候选集内**，朱世珍/陈氏不是李氏家族成员 → viewer=1 对 48/49/50/51 全部 `found=False`。放开中间人限制后实测得：朱佛女=`U-Df`（姐姐）、李贞=`U-Df-Sm`（姐夫）、李文忠=`U-Df-Dm`、李景隆=`U-Df-Dm-Dm`。同类问题出现在朱棣看徐达家（岳父徐达）、朱标看常府/吕府等所有跨空间亲属。
6. **09-18 已锁定的产品决策必须保留**：`.trellis/tasks/archive/2026-09/09-18-family-tree-space-members` 与 `.trellis/spec/architecture/11--11-personalfamilyview-bridge-2026-09-01.md` 规定「管理员邀请加入的成员即使暂无亲属路径，也保留为 `space_member` 孤立节点，不写个人称谓边、不产生结构边」。本任务**不改**这条：孤立节点是管理员显式授权的合法状态。

## Goal

1. **堵住自行加入他人家族空间的越权入口**：`join-by-user` 申请必须与该空间至少一名 active 成员存在 confirmed 亲属路径，目标空间只按 owner/space_admin 解析，pending 只能由该空间的 space_admin 审批，申请人不得自批。
2. **修好家族树亲属称谓**：viewer 与空间内成员的亲属路径不再因为「中间人不是本空间成员」而被切断，同时**不**把外人塞进别人的家族树节点集合。

## Requirements

### R1 自行申请（join-by-user）的亲属关系门禁

- 申请人必须与**目标空间的 active 成员**（含 owner/space_admin）存在至少一条 confirmed 亲属路径；路径按申请人授权可见图解析（口径见 R3），不得凭共享空间、`space_profile_refs`、pending 关系或仅姓名可见发起申请。
- 无亲属路径 → 明确拒绝（新增稳定错误码），与「不可见目标」的既有 404 防枚举语义区分开，但不得借错误差异泄露不可见人物或空间的存在性。

### R2 目标空间解析

- 「TA 的家庭空间」只解析 target 为 **owner 或 active space_admin** 的空间；owner 优先，其次 target 任管理员的 space_admin 空间（按稳定次序取第一个）。
- target 不是任何空间的管理者 → 保持既有 `409 SPACE_JOIN_NO_TARGET_SPACE` 语义。
- 不得再回退到 target 的任意 `active_ids[0]`。

### R3 审批权与自批禁止

- join-by-user 产生的 pending 行只能由**该空间当前 active space_admin**（或 owner 语义等价的管理者）审批接受/拒绝。
- 申请人本人不得 accept 自己的申请；管理员邀请（`POST /spaces/{id}/members` + 受邀人 accept）路径保持现状不变。
- 审批动作必须写审计，并在事务内维护既有 FSM 不变量（每空间至多一个 active 管理员等）。
- 管理员侧需要能看到并处理这些申请：既有空间成员列表/治理面板可承载，不新建独立审批系统。

### R4 家族树亲属路径解析（中间人）

- viewer 与当前空间授权节点的亲属路径解析，改用 viewer 的**授权可见图**口径：路径中间人可以不是当前空间的 active 成员/ref，只要该事实与中间人按现行可见性规则对 viewer 可见。
- **节点集合不变**：PFV/Steward 骨架节点仍只限「当前空间 active 成员 ∪ active space_profile_refs ∪ viewer 本人」经 `PURPOSE_GRAPH` 重验后的授权候选；本任务不得把中间人变成家族树节点。
- 结构拓扑 `topology_edges` 仍只来自两端点都在授权节点集合内的 confirmed 直接事实（不因 R4 扩大）。
- 中间人不得因此获得任何字段投影提升：字段仍按该中间人对 viewer 的实际可见性判定，脱敏照旧。
- 09-18 的 `space_member` 孤立节点合同保持：确无亲属路径的成员仍显示「关系待建立」，不伪造称谓。

### R5 缓存与投影失效

- R4 改变了亲属路径计算输入 → 必须让旧快照/派生缓存正确失效并重算（`COMPUTATION_VERSION` 或等价版本 + `DerivedFact.evidence_hash` 语义），不允许继续长期服务旧的「路径被切断」结果。
- 普通 GET 保持只读，不得在读取请求里重建或写库。
- 缓存失效后首次读取仍必须是安全态，不得返回半成品。

### R6 兼容与前端

- 前端家族树继续只消费服务端 `term`/`topology_edges`，不改画布语义；`space_member` 安全状态保持。
- 申请入口（`frontend/src/api/spaces.ts::joinByUser` + `stores/graph.ts::requestJoin`）保持可用，但需能呈现新拒绝原因；管理员审批入口在既有成员/治理面板内可用。
- 既有 API 形状、通知 kind（`space_membership`）与领域状态投影不破坏。

## Acceptance Criteria

- **AC1 越权关闭**：构造「对 target 可见但与目标空间任何 active 成员都无 confirmed 亲属路径」的申请人 → join-by-user 被拒（新错误码），`space_members` 不新增 pending 行。
- **AC2 自批关闭**：存在合法 pending 申请时，申请人本人调用 accept → 被拒（403/409 语义明确），成员状态保持 pending；该空间 active space_admin 调用 accept → active。
- **AC3 目标空间解析**：以普通成员（非 owner/space_admin）为 target 发起申请时，落库空间是 target 管理者的空间；target 无管理者空间 → 409 `SPACE_JOIN_NO_TARGET_SPACE`；不得再落到申请人的自有空间。
- **AC4 亲属路径门禁正向**：与目标空间某 active 成员存在 confirmed 亲属路径的申请人可正常发起申请并进入 pending（不误伤合法场景）。
- **AC5 家族树称谓恢复**：李氏家族场景（朱元璋 viewer，空间 active 成员含李贞/朱佛女/李文忠/李景隆）返回的亲属边/称谓体现真实关系（朱佛女=姐姐/`U-Df` 类、李贞=姐夫/`U-Df-Sm` 类），而不是全部 `space_member` 无路径。
- **AC6 节点集合不扩大**：同一场景下 PFV/Steward 骨架节点仍恰好是授权候选（不含朱世珍/陈氏等非本空间成员）；`topology_edges` 仍只含授权节点内的 confirmed 直接事实。
- **AC7 无路径成员不回归**：管理员邀请但确无亲属路径的成员仍为 `space_member` 孤立节点、无个人称谓边、无结构边，前端显示安全无路径状态。
- **AC8 撤权即时**：成员被移出/可见性收紧后，依赖该成员或中间人的路径在下次读取不再返回（逐条重验保持有效）。
- **AC9 缓存收敛**：版本变更后旧快照判为失效并触发既有重算路径，重算后节点/边与 API 响应一致；不通过手工写生产库解决。
- **AC10 质量门禁**：受影响 backend pytest + ruff/format/mypy 通过；受影响 frontend lint/type-check/定向 vitest 通过。未运行的项目级高成本检查在交付记录中说明原因。

## Out of scope

- 不改管理员邀请流程本身（`POST /spaces/{id}/members` → 受邀人 accept 保持现状）。
- 不改 `space_profile_refs`、披露矩阵、可见性四级层级定义与 bridge 合同。
- 不因「亲属关系门禁」把成员资格与亲属事实合并为同一真源；不自动创建/补录 SourceFact。
- 不删除李家/李氏家族中朱元璋的既有成员资格，也不改生产库数据（部署与数据处置另行按验证结果执行）。
- 不做跨空间人物发现或推荐扩权（见 09-11-steward-cross-space-discovery 的延期结论）。
