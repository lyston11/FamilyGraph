# 实施记录：家族空间准入边界与亲属路径口径

## 结论（2026-09-19）

D1/D2/D3 全部落地；backend ruff/format/mypy 绿，受影响定向 pytest 全绿，前端 lint/type-check + 定向 vitest 绿。生产库未被改动（计数核验一致）。

## 缺陷与修复

### D3 亲属路径被非本空间成员的中间人切断（投影正确性）

`relationship_graph.load_graph` 原先用 `scoped_confirmed_facts(..., visible_ids=visible)`，
要求每条事实**两端点都在当前空间候选集内**。共享父母（如朱世珍/陈氏不是李氏家族成员）
因此把整条边剔除，朱元璋看亲姐姐显示「关系待建立」。

改为：事实至少一端接入节点集合，且两端点按 `PURPOSE_GRAPH` 可见（`_path_visible_user_ids`
只对本批事实端点求值——不用 `visible_user_ids`，它是全库口径且越「跨 lineage 走 bridge」的边界）。

关键约束是**邻接表变宽不能污染节点集合**。因此新增 `path_genders`（路径证据可见集，
`RelationshipGraph.path_user_ids` 为其 id 视图），`node_genders` 语义不变；路径编码/描述、
PFV/Steward 的逐步重验改读路径可见集，而两端点校验与 `topology_edges` 仍只用节点集合。
可达目标的消费方全部显式收窄（`steward_snapshot`、`steward_pipeline._stage_view`、
`steward_demand.register`）。

版本：`GRAPH_SNAPSHOT_VERSION` → `authorized-graph-v3`；`COMPUTATION_VERSION` →
`pfv-v7-path-visible`（经 `config_fingerprint` 使旧骨架不复用）。`skeleton_json` 增写
`path_user_ids`（JSON 列，**无迁移**），旧骨架缺该键时退化为节点集合。

### D1 可自行加入他人家族空间（安全边界）

复现：常遇春与朱标共享常府/常氏家族 → 可见 → `POST /spaces/join-by-user` 落到**朱元璋的
明皇室** pending → 申请人自己的 token accept → active。即任何对某成员可见的人都能不经该
空间任何人同意、也无须与该家族有任何亲属关系，自行加入别人的家庭空间。

修复三点：
1. **亲属门禁**：新增 `shares_confirmed_kinship`（只返回布尔值），事实口径为申请人自己的
   active 空间（含全局 NULL），亲属链按无向连通。**不按逐人可见性剪枝**——亲缘是事实而非
   可见性属性，否则经不可见长辈的链条会单向断掉（姐姐能看到弟弟、弟弟看不到姐姐）。
   与事实图逐组合核对：全库 0 mismatch。无亲属链 → 403 `SPACE_JOIN_NO_RELATION`。
2. **目标空间解析**：只取 target 的 owner / active `space_admin` 空间（owner 优先，
   `order_by(FamilySpace.id)`），删除 `active_ids[0]` 回退。
3. **审批权**：`space_fsm.transition` 的 accept 按 `added_by == user_id` 区分本人申请
   （需该空间管理员）与他人邀请（需受邀人本人）；`respond_invitation` 归属过滤放开给
   管理员（旧代码只允许 `member.user_id == actor.id`，管理员根本进不来）。审计 detail 增
   `by_manager`。

**连带影响（有意）**：`invite_codes.join_space_with_code` 用 `added_by=code.creator_id`；
household/lineage 码创建者被移出后再兑自己的码会被拒——正确语义（不得自行重新加入）；
现有邀请码测试全跑通过。

### D2 目标空间解析过宽

并入 D1 第 2 点。

## 前端

`SpaceGovernancePanel` 的 pending 行按 `added_by === user_id` 分支：本人申请渲染「批准/拒绝」
（复用既有无调用方的 `spaces.resolve`），他人邀请保持「撤回」。旧界面在这里只给「撤回」，
而 `withdraw` 对本人申请会因 `added_by != actor_id` 被拒——管理员实际上无法处置任何加入申请。

## 测试改动

- **改写** `test_m2c_flows.py::test_join_by_user_full_flow_and_idempotency`：旧版只建 v1
  `Relation`（可见性靠它），门禁基于 confirmed SourceFact 图，故补 confirmed 事实；新增
  自批 403 + 管理员 accept 200 断言。
- **新增** `test_join_requires_confirmed_kinship_with_space_member`（AC1：403 + 无 pending 行）、
  `test_join_target_space_resolved_from_manager_not_any_membership`（AC3：409 而非落到 owner 空间）。
- **补** `test_notifications.py::test_join_request_notification_to_manager` 的 confirmed 事实前置。
- **新增** `test_personal_family_topology.py` 三条：经非成员中间人的称谓（AC5）、
  中间人不成为节点/不产生结构边（AC6）、中间人撤权后路径消失（AC8）。
- `test_term_autofix.py` 版本常量断言 → `pfv-v7-path-visible`。
- **新增** `SpaceGovernancePanel.spec.ts` 三条：本人申请渲染批准/拒绝且不渲染撤回、
  批准/拒绝各调 `resolve(id, 'accept'|'reject')`。

排查记录：新用例初次失败是**清表夹具会清掉 `term_entries` 内置种子**，需在夹具内补
`terms.seed_builtin_packs`（与 `test_terms` / `test_term_autofix` 同款），不是产品缺陷。

## 隔离库端到端核验

`DATA_DIR=/tmp/fg-verify-0919`（远端生产库 `.backup` 副本，写入只发生在副本）：

- **AC5/AC6** 李氏家族空间 viewer=1：nodes 恰 5（root + 4 名授权成员，无朱世珍/陈氏），
  edges = 姐姐 / 姐妹的丈夫 / 外甥 / 外甥的儿子（`Um-Df`、`Um-Df-Sm`、`Um-Df-Dm`、
  `Um-Df-Dm-Dm`）；其余 4 名 viewer 的称谓与 `space_member` 计数无退化。
- **AC1/AC2/AC3**：常遇春 → 朱标 → 409 `SPACE_JOIN_NO_TARGET_SPACE`（不再落到明皇室）；
  李贞 → 朱元璋 → pending（`added_by == user_id`）→ 自批 403 `SPACE_FORBIDDEN_ACTOR`
  → 朱元璋（space_admin）批准 → active。
- **门禁与事实图一致性**：全库逐 (空间, 非成员) 组合比对 `shares_confirmed_kinship` 与
  事实图连通性，0 mismatch。
- **生产库未污染**：`space_members` 92 / `source_facts` 74 / `personal_family_views` 92 /
  `users` 51 / `family_spaces` 20，核验前后一致；`alembic_version` 仍 `0051_run_event_timing`。

## 未运行 / 待办

- 未跑 backend 全量 `pytest`（用户要求不做大规模测试）；已跑受影响范围：m2c/notifications/
  invite_codes/personal_family_view(+consistency/topology)/steward_staged_pipeline/
  relationship_resolver/term_autofix，以及 ruff/format/mypy 全包。
- 未跑 frontend 全量 `npm test`/`npm run build`；已跑 lint、type-check 与 5 个相关 spec 文件
  （SpaceGovernancePanel / space-management / spaces store / family-tree / notifications）。
- 未部署远端，未做浏览器页面复核（需按项目部署规则另行执行）。
- `request_lineage_membership`（ActionCard 驱动的家族加入）**未加亲属门禁**（PRD Out of scope），
  但同样受审批权修复保护，故不再可自批。

## 回滚点

逐层可独立回退：前端审批按钮 → 命令层审批归属 → FSM accept 判定 → 亲属门禁
（`request_join_by_user` 内自包含段落）→ 图口径（两个 commit + 版本常量回退）。
回退不删除任何 SourceFact 或成员资格，不改生产数据。
