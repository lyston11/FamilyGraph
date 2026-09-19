# 技术设计：家族空间准入边界与亲属路径口径

## 0. 问题分解

用户报告的现象是两个独立缺陷的叠加，必须分开修：

| 编号 | 缺陷 | 层 | 症状 |
|---|---|---|---|
| D1 | 自行申请加入他人家族空间无亲属门禁 + 申请人可自批 | 命令层 + `space_fsm` | 常遇春可自行加入朱元璋的明皇室 |
| D2 | 目标空间解析回退到 target 的任意 active 成员资格 | 命令层 | 以普通成员为 target 时落到申请人自己的空间 |
| D3 | 亲属路径枚举要求中间人也在当前空间候选集内 | 图构建 | 朱元璋在李氏家族看不到姐姐/姐夫 |

D1/D2 是安全边界问题；D3 是投影正确性问题。D3 **不能**用「给李家补关系事实」绕过——关系事实本来就存在（`5→1`/`7→1`/`5→49`/`7→49`/`48↔49` 全部 confirmed），是图口径把它切断了。

---

## 1. D1/D2/D3 的职责边界（不改的部分）

- **节点集合**：仍只限「当前空间 active `space_members` ∪ active `space_profile_refs` ∪ viewer 本人」经 `PURPOSE_GRAPH` 重验后的授权候选。中间人**不进入**节点集合。
- **`space_member` 孤立节点合同**（09-18）：管理员邀请加入、但确无 viewer 亲属路径的成员仍保留为 `space_member` 节点，不写个人称谓边、不产生结构边，前端显示「关系待建立」。本任务不弱化这条。
- **`topology_edges`**：仍只来自两端点都在授权节点集合内的 confirmed 直接事实。
- **管理员邀请流程**：`POST /spaces/{id}/members` → pending → 受邀人 accept，语义不变。
- **可见性四级层级、披露矩阵、bridge 双边同意合同**：不变。

---

## 2. D3：路径口径与节点口径分离

### 2.1 现状根因

`backend/app/services/relationship_graph.py:423`：

```python
authorized_space_ids = {space_id, *bridge_space_ids}
participating = scoped_confirmed_facts(
    session, space_ids=authorized_space_ids, visible_ids=visible   # visible = 空间候选集
)
```

`scoped_confirmed_facts(visible_ids=...)` 要求**每条事实两端点都在 `visible` 内**。`visible` 是空间候选集，所以：

- `5→49`（朱世珍→朱佛女）被剔除：朱世珍(5) 不是李氏家族成员；
- `5→1`（朱世珍→朱元璋）被剔除；
- 结果：viewer=1 对 48/49/50/51 全部 `found=False`，四人退化为 `space_member`。

实测（隔离库，放开 `visible_ids` 后）：

```
viewer=1 -> 49  found=True  Um-Df       direct_line  你的父亲的女儿
viewer=1 -> 48  found=True  Um-Df-Sm    affinal      你的父亲的女儿的丈夫
viewer=1 -> 50  found=True  Um-Df-Dm    direct_line
viewer=1 -> 51  found=True  Um-Df-Dm-Dm direct_line
```

### 2.2 选定口径：事实范围按「接入当前空间」判定

新的事实纳入条件（取代 `visible_ids=visible` 的两端全在空间内）：

```
fact ∈ participating  ⟺  state == confirmed
                       ∧ space_id ∈ authorized_space_ids  (含全局 NULL)
                       ∧ (subject ∈ node_candidates ∨ object ∈ node_candidates)   ← 至少一端接入本空间
                       ∧ subject ∈ path_visible ∧ object ∈ path_visible            ← 两端点对 viewer 可见
```

其中：

- `node_candidates` = 空间候选集（`_visible_node_ids` 的输入，即 active 成员 ∪ active refs ∪ viewer）；
- `path_visible` = 对**本批事实端点**逐个执行 `visibility.evaluate(viewer, endpoint, space_context=space_id, purpose=PURPOSE_GRAPH)` 得到的可见集。

**为什么是「至少一端接入」而不是「两端点都可见即可」**：只要求两端点可见会把与本空间完全无关的事实子图拉进邻接表（例如两个外戚家族成员之间的边），既无必要也扩大暴露面。要求至少一端是空间候选，保证每条入图事实都真的接到本空间的人身上。

**为什么不用 `visibility.visible_user_ids(session, viewer)`**：它是全库口径（含所有共享空间成员与直接关系对端），一是与「跨 lineage 连接必须走显式 bridge」（spec §11）的边界冲突，二是实测 42ms，每次 GET 都付这个成本。按事实端点求值只有 ~7 人（李氏家族场景），成本可忽略。

**`PURPOSE_GRAPH` 而非默认 `PURPOSE_PROFILE`**：展示口径必须与 PFV/Steward 同源。两者 `_PURPOSE_LEVEL_CAP` 当前都是 `household_detail`，但显式传参避免未来分叉。

### 2.3 `RelationshipGraph` 契约变化

```python
@dataclass(frozen=True)
class RelationshipGraph:
    viewer_user_id: int
    space_id: int
    node_genders: Mapping[int, str]      # 语义不变：仅空间授权候选（= 节点集合）
    adjacency: Mapping[int, Sequence[GraphEdge]]   # 现在覆盖 path_visible 内的路径节点
    snapshot_hash: str
    path_user_ids: frozenset[int] = frozenset()    # 新增：路径证据可见集
    bridge_user_ids: frozenset[int] = frozenset()
    confirmed_facts: tuple[GraphFact, ...] = ()
    ...
```

关键约束：**`node_genders` 的键必须仍是空间授权候选**。`steward_snapshot.viewer_from_session` 用 `for user_id in sorted(graph.node_genders)` 生成骨架节点（§137），把中间人放进去就会让外人变成家族树节点。

`adjacency` 更宽是安全的：路径枚举需要能穿过中间人；节点集合另由 `node_genders` 决定。

`GRAPH_SNAPSHOT_VERSION`：`authorized-graph-v2` → `authorized-graph-v3`。`snapshot_hash` 的 `nodes` 分量仍取 `sorted(genders.items())`（节点集合不变），但 `facts` 分量随新口径变化 → hash 变化 → `DerivedFact` 的 `evidence_hash` 自然失效。

### 2.4 目标集合的消费方必须显式收窄

`adjacency` 变宽后，所有「可达目标」消费方都会把中间人当成目标。逐点收窄：

| 位置 | 现状 | 改为 |
|---|---|---|
| `steward_snapshot.viewer_from_session` | `reached = reachable_targets(graph)`，`user_id in reached → confirmed_path` | 判定条件加 `user_id in graph.node_genders` |
| `steward_pipeline._stage_view` | `target_ids = sorted(uid for uid in reached if uid != root)` | 过滤 `uid in authorized_ids` |
| `steward_demand.register` | `if focus_user_id not in reached: 422` | 同时要求 `focus_user_id in graph.node_genders` |
| `personal_family_view.rebuild_view` | 遍历 `sorted(graph.node_genders)` | 不变（已正确） |
| `personal_family_view._emit_inferred_projection` | `genders = graph.node_genders` | 不变 |

`intake_extractor._matched_people`、`steward_overlay._snapshot` 已用 `graph.node_genders`，不变。

### 2.5 路径重验需要两个集合

`_path_evidence_valid`（PFV GET）与 `_path_valid`（Steward 读取）都做「中间节点当前可见」校验。中间人现在不在节点集合内，必须改用路径可见集：

- **端点校验**：仍用节点集合（`visible_ids` / skeleton nodes）——保证不返回指向外人的边；
- **中间节点校验**：用 `graph.path_user_ids`；
- **`topology_edges` 两端点校验**：仍用节点集合（`scoped_confirmed_facts(..., visible_ids=节点集合)` 保持不变，AC6）。

Steward 读取路径的 `path_user_ids` 必须持久化：`steward_pipeline._stage_view` 把 `path_user_ids` 写入 `skeleton_json`（JSON 列，无迁移），`steward_views.payload_for` 读出后传给 `_path_valid`。

### 2.6 版本与缓存失效

- `personal_family_view.COMPUTATION_VERSION`：`pfv-v6-space-members` → `pfv-v7-path-visible`
  - 经 `steward_snapshot.config_fingerprint()` 进入 `presentation_hash` → `_reuse_view` 不再复用旧骨架；
  - 经 `_current_input_hash` 使旧 `PersonalFamilyView` 行判为不新鲜 → 走既有重算路径；
  - `backend/tests/test_term_autofix.py:472` 的常量断言同步更新。
- 无数据库迁移：`path_user_ids` 只进 `skeleton_json`（JSON）与内存快照。
- GET 保持只读；`path_visible` 求值只用 `session.get`/`evaluate`，不写库。

---

## 3. D2：目标空间解析

`backend/app/commands/spaces.py::request_join_by_user` 的「TA 的家庭空间」改为：

```python
owned = query(FamilySpace).filter(
    FamilySpace.owner_id == target.id, FamilySpace.id.in_(active_ids)
).order_by(FamilySpace.id).first()
if owned is not None:
    primary_space_id = owned.id
else:
    managed = query(FamilySpace).join(SpaceMember, SpaceMember.space_id == FamilySpace.id).filter(
        SpaceMember.user_id == target.id,
        SpaceMember.role == "space_admin",
        SpaceMember.status == "active",
        FamilySpace.id.in_(active_ids),
    ).order_by(FamilySpace.id).first()
    primary_space_id = managed.id if managed is not None else None
```

- 不再回退到 `active_ids[0]`；
- 无任何 target 管理者的空间 → 保持 `409 SPACE_JOIN_NO_TARGET_SPACE`；
- 稳定次序（`order_by(FamilySpace.id)`）保证确定性，避免同一 target 命中不同空间。

---

## 4. D1：亲属关系门禁

在 `request_join_by_user` 内、目标空间解析之后、`space_fsm.invite` 之前插入：

```python
graph = load_graph(session, viewer_user_id=actor.id, space_id=primary_space_id)
reached = relationship_resolver.reachable_targets(graph)
member_ids = {active members of primary_space_id}          # 含 owner
if not (reached.keys() & member_ids - {actor.id}):
    raise_api_error(403, SPACE_JOIN_NO_RELATION, "你与该家庭空间没有已确认的亲属关系")
```

- 复用 `load_graph` + `reachable_targets`（一次 BFS），不新增图算法；
- 「有亲属路径」= 与空间内至少一名 active 成员（含 owner）可达；viewer 自己在空间内时天然可达但不算「有联系」，故 `- {actor.id}`；
- 门禁在 `command_transaction` 内执行，与 pending 行创建同一写锁，无检查-插入竞态；
- 新错误码 `SPACE_JOIN_NO_RELATION`（`backend/app/errors.py`），403 语义；
- 防枚举：目标不可见仍是既有 `404 USER_NOT_FOUND`（在门禁之前判定），门禁只对「已可见目标」生效，不泄露空间存在性。

**不覆盖 `request_lineage_membership`**：该入口由已接受的 ActionCard 驱动，卡片生成已基于关系，且执行时校验 `target` 是该 lineage 的 active 成员 + actor 对 target 可见。它同样受 §5 的审批权修复保护（`added_by == user_id` → 需管理员审批），因此不再具备自批能力。作为 Out of scope 记录。

---

## 5. D1 后半：审批权与自批禁止

### 5.1 FSM 判定（`backend/app/services/space_fsm.py::transition`）

区分「本人申请」与「他人邀请」沿用既有推断（`notifications.record_membership_request_notification` 已用 `member.user_id != member.added_by` 同一判据）：

```python
if action == "accept":
    self_requested = member.added_by == member.user_id     # 本人申请加入
    if self_requested:
        if not is_manager:
            raise_api_error(403, SPACE_FORBIDDEN_ACTOR, "仅该家庭空间管理员可批准加入申请")
    elif not is_self:
        raise_api_error(403, SPACE_FORBIDDEN_ACTOR, "仅受邀人本人可接受")
    ...
```

`reject` 保持 `is_self or is_manager`（申请人可撤回自己的申请，管理员可拒绝）；`withdraw` 保持 `member.added_by == actor_id`。

**连带影响（有意）**：`invite_codes.join_space_with_code` 用 `added_by=code.creator_id`。household/lineage 码的创建者必须是该空间 active 成员，兑码时若已 active 会先 409，正常路径不受影响；若创建者已被移出后再兑自己的码，现在会被拒绝——这是正确语义（不得自行重新加入）。

### 5.2 命令层（`backend/app/commands/spaces.py::respond_invitation`）

现状 `if member is None or member.user_id != actor.id: raise 404` 会**挡住管理员**。改为：

```python
is_self = member.user_id == actor.id
is_manager = space_fsm.is_space_manager(session, member.space_id, actor.id)
if member is None or not (is_self or is_manager):
    raise_api_error(404, SPACE_NOT_FOUND, "邀请不存在或已处理")
```

精确的「谁能 accept 哪一类」由 §5.1 的 FSM 判定承担，命令层只做归属过滤（保持防枚举 404）。

审计：accept 时在 `space_invite_accepted` 的 detail 增加 `by_manager: bool`（不改 action 名，不破坏既有审计读取）。

### 5.3 前端（`SpaceGovernancePanel.vue`）

管理员需要能处理本人申请。现状：pending 行只渲染「撤回」按钮，走 `leaveOrWithdrawMembership` → `withdraw` 分支 → `member.added_by != actor_id` → 403，即**管理员当前无法处理本人申请**。

最小改动：pending 行按 `added_by === user_id` 区分——

- 他人邀请（`added_by !== user_id`）：保持「撤回」（`spaces.leaveOrRemove`）；
- 本人申请（`added_by === user_id`）：渲染「批准 / 拒绝」两个按钮，复用既有 `spaces.resolve(memberId, 'accept' | 'reject')`（已存在，调 `POST /space-memberships/{id}/{action}`）。

`spaces.resolve` 目前无 UI 调用方，本任务首次接上，无需改 API 层。

---

## 6. 测试设计

### Backend（新增/改写）

`backend/tests/test_m2c_flows.py`
- 改写 `test_join_by_user_full_flow_and_idempotency`：申请人本人 accept → 403；空间 `space_admin` accept → active。
- 新增：申请人与该空间无亲属路径 → 403 `SPACE_JOIN_NO_RELATION`，且 `space_members` 无新增行。
- 新增：以普通成员（非 owner/space_admin）为 target → 落库到 target 管理者的空间，不落到申请人自有空间。
- 新增：target 无任何管理者空间 → 409 `SPACE_JOIN_NO_TARGET_SPACE`。
- 保留 `test_join_invisible_target_404` 语义不变。

`backend/tests/test_personal_family_topology.py`
- 新增：viewer 与空间成员之间经**非本空间成员**的中间人连通时，个人称谓边与 `concept_code` 正确返回（AC5）。
- 新增/强化：同一场景下 `nodes` 恰为授权候选（不含中间人），`topology_edges` 两端点都在 `nodes` 内（AC6）。
- 保留 `test_topology_excludes_non_confirmed_hidden_and_cross_space` 的 `outsider not in node_ids` 断言（中间人不得成为节点）。
- 保留 09-18 的孤立成员断言（AC7）。

`backend/tests/test_steward_staged_pipeline.py`
- 新增：生产读取路径（`payload_for`）在同一场景下返回真实称谓（AC5），且骨架节点不含中间人（AC6）。
- 保留 `test_staged_view_keeps_authorized_members_without_viewer_paths`。

`backend/tests/test_personal_family_view.py` / `test_personal_family_view_consistency.py`
- 保留 09-18 孤立成员三例；补一条「中间人被撤权后路径消失」（AC8）。

`backend/tests/test_invite_codes_service.py`
- 补一条：码创建者被移出后兑自己的 household 码 → 被拒（§5.1 连带影响的显式回归）。

`backend/tests/test_term_autofix.py`
- 更新 `COMPUTATION_VERSION` 常量断言为 `pfv-v7-path-visible`。

### Frontend

`frontend/src/components/member/__tests__/SpaceGovernancePanel.spec.ts`
- 本人申请行渲染「批准/拒绝」并调用 `spaces.resolve(id, 'accept')`；他人邀请行仍渲染「撤回」。

### 隔离库端到端（交付前必做）

`DATA_DIR=<隔离目录>`，主库 `.backup` 副本，逐 viewer 重建 + 读取李氏家族空间，核对：

- 朱元璋 viewer：48/49/50/51 有真实称谓（`Um-Df` 系），nodes 恰为 5（授权候选）；
- 其余 viewer：`space_member` 计数与个人称谓边不退化；
- 生产库计数在核验前后一致（未污染）。

---

## 7. 风险与回退

| 风险 | 缓解 |
|---|---|
| `adjacency` 变宽后某个「可达目标」消费方漏收窄，中间人变成家族树节点或 Steward 目标 | §2.4 表格逐点覆盖 + AC6 断言 + 隔离库端到端核对 nodes 数量 |
| 路径可见集求值在 GET 路径上变慢 | 只对「已按空间接入筛选后」的事实端点求值（~7 人量级），不是全库 |
| 门禁误伤合法申请 | AC4 正向用例；门禁用 `reachable_targets`（与展示同一图口径），不另立规则 |
| FSM 判定改动影响邀请码路径 | §5.1 显式回归用例 + 现有邀请码测试全跑 |
| 版本 bump 导致大量重算 | 这是期望行为（旧快照本就是错的）；重算走既有 Steward 队列，不新增机制 |

回退顺序（逐层可独立回退）：

1. 前端审批按钮（`SpaceGovernancePanel`）——纯 UI，回退无数据影响；
2. 命令层审批归属（`respond_invitation`）；
3. FSM accept 判定（`space_fsm.transition`）；
4. 亲属门禁（`request_join_by_user` 内独立段落）；
5. 图口径（`load_graph` + 版本常量）——影响面最大，最后回退。

**不回退**：既有 SourceFact、成员资格、生产数据。回退不删除李家/李氏家族中朱元璋的成员资格。

---

## 8. 发布顺序与验证

1. 隔离库 `alembic upgrade head`（本任务无迁移，仅确认版本一致）；
2. 隔离库跑受影响 backend 测试 + 端到端核对；
3. 部署后端 → 核对真实运行环境（systemd 作用域、生效配置来源、`alembic_version` 与列一致性）；
4. 触发受影响空间重算，核对浏览器页面实际结果（李氏家族树称谓、朱元璋 viewer）；
5. 前端部署（顺序无硬约束：审批按钮与后端门禁互不依赖）；
6. 不直接改生产库；数据处置（如需要）另行按验证结果执行。
