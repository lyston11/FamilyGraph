# 实施计划：家族空间准入边界与亲属路径口径

> 依赖顺序：D3（图口径）与 D1/D2（准入）互相独立，可并行；但 D1 的亲属门禁复用 `load_graph`，因此图口径若先合入，门禁语义与展示一致。**建议顺序：D3 → D2 → D1 门禁 → D1 审批权 → 前端。**

## 0. 准备

- [ ] 0.1 确认工作区在任务 worktree（`task.py start` 后由 `after_start` 钩子创建 `feat/09-19-family-space-join-authorization` + `../fg-09-19-family-space-join-authorization`），写代码前 `cd` 进去。
- [ ] 0.2 建隔离库：`mkdir -p /tmp/fg-iso-0919/db && <主库 .backup 副本>`，所有端到端核验用 `DATA_DIR=/tmp/fg-iso-0919`。生产库只读，核验前后对计数。
- [ ] 0.3 记录基线：李氏家族空间（生产为 space 20）viewer=1 的当前 nodes/edges/称谓，以及 `space_members` 计数。

## 1. D3：图口径与版本（`relationship_graph.py` + 版本常量）

- [ ] 1.1 `load_graph`：新增 `path_visible` 计算 —— 先按「至少一端在空间候选集内」筛选 `scoped_confirmed_facts` 的输入集合，再对这批事实的端点逐个 `visibility.evaluate(..., space_context=space_id, purpose=PURPOSE_GRAPH)` 求可见集，用该集合过滤事实。
- [ ] 1.2 `RelationshipGraph` 新增 `path_user_ids: frozenset[int] = frozenset()`，语义注释写清「路径证据可见集；**不是**节点集合」。
- [ ] 1.3 确认 `node_genders` 仍只含空间授权候选（不得把中间人写入）；`snapshot_hash` 的 `nodes` 分量保持 `sorted(genders.items())`。
- [ ] 1.4 `GRAPH_SNAPSHOT_VERSION` → `authorized-graph-v3`。
- [ ] 1.5 `personal_family_view.COMPUTATION_VERSION` → `pfv-v7-path-visible`；同步 `backend/tests/test_term_autofix.py:472` 的常量断言。
- [ ] 1.6 消费方收窄（逐个改，每处加注释说明「可达 ≠ 授权目标」）：
  - `steward_snapshot.viewer_from_session`：`user_id in reached` → `user_id in reached and user_id in graph.node_genders`；
  - `steward_pipeline._stage_view`：`target_ids` 过滤 `uid in authorized_ids`；
  - `steward_demand.register`：`focus_user_id not in reached` → 同时要求 `focus_user_id in graph.node_genders`。

**验证**：`pytest backend/tests/test_relationship_resolver.py backend/tests/test_personal_family_topology.py backend/tests/test_personal_family_view.py backend/tests/test_personal_family_view_consistency.py backend/tests/test_steward_staged_pipeline.py backend/tests/test_term_autofix.py -q`

## 2. D3：路径重验改用 `path_user_ids`

- [ ] 2.1 `personal_family_view._path_evidence_valid`：新增参数区分「端点集合」与「路径可见集合」——端点校验用节点集合，中间节点校验用 `graph.path_user_ids`。
- [ ] 2.2 `_view_payload_for_view` 把 `graph.path_user_ids` 传入上述调用点；`topology_edges` 的两端点校验保持用节点集合（**不变**）。
- [ ] 2.3 `steward_pipeline._stage_view`：把 `path_user_ids` 写入 `skeleton_json`（`sorted(graph.path_user_ids)`）。
- [ ] 2.4 `steward_views.payload_for`：读出 `skeleton["path_user_ids"]`（缺省 `frozenset()` 兼容旧骨架），传给 `_path_valid`；端点仍用 skeleton `nodes`。
- [ ] 2.5 `steward_views._path_valid`：签名加 `path_visible`，中间节点改查该集合。

**验证**：同上 + `pytest backend/tests/test_steward_input_versions.py -q`

## 3. D2：目标空间解析

- [ ] 3.1 `commands/spaces.request_join_by_user`：owner 优先 → 其次 target 任 `space_admin` 的空间 → 否则 `None`；删除 `active_ids[0]` 回退；`order_by(FamilySpace.id)` 保证确定性。
- [ ] 3.2 保持 `409 SPACE_JOIN_NO_TARGET_SPACE` 语义不变。

## 4. D1：亲属关系门禁

- [ ] 4.1 `errors.py` 新增 `SPACE_JOIN_NO_RELATION`（403）。
- [ ] 4.2 `request_join_by_user`：在目标空间解析之后、`space_fsm.invite` 之前插入门禁 —— `load_graph` + `reachable_targets`，取目标空间 active 成员集合，去掉 actor 本人，交集为空则 403。
- [ ] 4.3 门禁必须在 `command_transaction`（BEGIN IMMEDIATE）内执行，无检查-插入竞态。
- [ ] 4.4 确认门禁在「目标不可见 → 404」之后，不新增可枚举信号。

**注意**：`load_graph` 只读 **confirmed SourceFact**（`relations` 是 v1 遗留层，不是结构真源）。因此 `backend/tests/test_m2c_flows.py::test_join_by_user_full_flow_and_idempotency` 现在只建了 `Relation`，必须改为建 confirmed SourceFact（或走 connection accept 真实流程），否则会 403。

## 5. D1：审批权与自批禁止

- [ ] 5.1 `space_fsm.transition` accept 分支：`self_requested = member.added_by == member.user_id`；自申请要求 `is_manager`，他人邀请要求 `is_self`；错误码 `SPACE_FORBIDDEN_ACTOR`。
- [ ] 5.2 `commands/spaces.respond_invitation`：归属过滤改为 `is_self or is_manager`，否则保持 404 防枚举；精确判定交给 FSM。
- [ ] 5.3 accept 审计 detail 增加 `by_manager: bool`（不改 action 名）。
- [ ] 5.4 回归 `invite_codes.join_space_with_code`：household/lineage 码创建者被移出后兑自己的码 → 被拒（§5.1 连带影响，须显式用例钉住）。

**验证**：`pytest backend/tests/test_m2c_flows.py backend/tests/test_invite_codes_service.py backend/tests/test_notifications.py -q`

## 6. 前端：管理员处理本人申请

- [ ] 6.1 `components/member/SpaceGovernancePanel.vue`：pending 行按 `added_by === user_id` 分支——他人邀请保持「撤回」；本人申请渲染「批准 / 拒绝」，调用既有 `spaces.resolve(memberId, 'accept' | 'reject')`。
- [ ] 6.2 错误提示覆盖 `SPACE_JOIN_NO_RELATION`（申请入口）与 `SPACE_FORBIDDEN_ACTOR`（审批）。
- [ ] 6.3 `components/member/__tests__/SpaceGovernancePanel.spec.ts`：两条分支各一例。

**验证**：`cd frontend && npm run lint && npm run type-check && npx vitest run src/components/member/__tests__/SpaceGovernancePanel.spec.ts src/views/__tests__/space-management.spec.ts`

## 7. 后端测试补齐

- [ ] 7.1 `test_m2c_flows.py`：改写既有 join 用例（自批 403 / 管理员 accept 200 / 幂等保留）；新增无亲属路径 403、普通成员 target 落库到管理者空间、无管理者空间 409、正向合法申请 201。
- [ ] 7.2 `test_personal_family_topology.py`：新增「经非本空间成员中间人连通」的称谓边用例（AC5）；强化 nodes 恰为授权候选 + topology 端点闭合（AC6）；补中间人被撤权后路径消失（AC8）。
- [ ] 7.3 `test_steward_staged_pipeline.py`：生产读取路径 `payload_for` 同一场景返回真实称谓 + 骨架不含中间人。
- [ ] 7.4 确认 09-18 的孤立成员用例（`test_personal_family_view.py` 三例、`test_steward_staged_pipeline.py::test_staged_view_keeps_authorized_members_without_viewer_paths`）全部保持通过（AC7）。

## 8. 隔离库端到端核验

- [ ] 8.1 `DATA_DIR=/tmp/fg-iso-0919` 下对李氏家族空间逐 viewer `rebuild_view` + `view_payload`，核对朱元璋 viewer 的 48/49/50/51 有真实称谓、nodes 恰为 5。
- [ ] 8.2 复现 §1 的越权场景：常遇春 → 朱标 join → 应被拒（无亲属路径 / 或落到常遇春管理者的空间）；若构造合法路径则 pending 只能由空间管理员 accept。
- [ ] 8.3 核对生产库计数未变（`space_members` / `source_facts` / `personal_family_views`）。

## 9. 质量门禁与提交

- [ ] 9.1 `cd backend && ruff check . && ruff format --check . && mypy app && pytest`
- [ ] 9.2 `cd frontend && npm run lint && npm run type-check && npm test && npm run build`
- [ ] 9.3 小步 commit（按 §1/§2/§3-5/§6 分段），尽早 push 到 `feat/09-19-family-space-join-authorization`。
- [ ] 9.4 规范更新：`.trellis/spec/architecture/4--4-ad-4.md`（join_request 审批主体）、`6--6-visibility-py.md`（join_request 行）、`11--11-personalfamilyview-bridge-2026-09-01.md`（路径口径与节点集合分离）。
- [ ] 9.5 交付说明写明：未运行的检查及原因、隔离库核验结论、生产库未改动证据。

## 10. 发布（另按项目部署规则执行）

- [ ] 10.1 部署后端 → 核对 systemd 作用域、生效配置来源、`alembic_version` 与列一致性（本任务无迁移）。
- [ ] 10.2 触发受影响空间重算 → 浏览器核对李氏家族树称谓与朱元璋 viewer 实际页面结果。
- [ ] 10.3 部署前端（顺序无硬约束）。
- [ ] 10.4 不直接改生产库。

## 回滚点

| 步骤 | 回滚方式 |
|---|---|
| §6 前端审批按钮 | revert 该 commit（纯 UI） |
| §5 审批权 | revert §5 commit；FSM 判定与命令层归属过滤可分别回退 |
| §4 亲属门禁 | 删除 `request_join_by_user` 内门禁段落（自包含） |
| §3 目标空间解析 | revert 该 commit |
| §1/§2 图口径 | revert 两个 commit（含版本常量回退）；旧快照会因版本回退重新判定新鲜度 |
