# 实施记录：保留家族空间中的孤立成员

## 完成情况（2026-09-18）

R1-R6 全部落地，backend 全量 1699 passed / 3 skipped，frontend 758 passed + build 通过。

## 实际改动

### 后端（两条读取路径都要改，否则生产仍丢成员）

1. `app/services/personal_family_view.py`
   - `rebuild_view()`：主循环不再 `if not resolution.found: continue`；改为
     `reason = "confirmed_path" if found else "space_member"`，节点照常物化，
     仅 `found` 时写个人称谓边（新增 `if resolution is None or not resolution.found: continue`）。
   - `COMPUTATION_VERSION` `pfv-v5-term-alias` → `pfv-v6-space-members`，经
     `steward_snapshot.config_fingerprint()` 使旧 reachable-only 快照失效。
   - `_emit_inferred_projection()`：新增 `node_rows` 参数；确有活跃推测边指向的
     `space_member` 节点改标 `inferred_path`（保留 09-13 推测角标/称谓与推测摆位），
     未被指向的普通成员保持 `space_member`。
   - `progress_for()`：`space_member` 与 `inferred_path` 一样不进进度分母，不阻塞 ready。

2. `app/services/steward_snapshot.py`（生产实际服务路径）
   - 骨架节点按 `relationship_resolver.reachable_targets(graph)` 区分
     `root` / `confirmed_path` / `space_member`。

3. `app/services/steward_pipeline.py`
   - `_stage_view()`：`nodes` 用完整授权候选（含 space_member），
     `target_ids` 仍只取可达目标（称谓目标）；`facts` 与 `topology_revision`
     按授权节点集合收窄/派生。

4. `app/services/steward_overlay.py`
   - `_snapshot()` 返回 `skeleton_ids`；overlay 节点只补插不在骨架中的推测端点，
     不对骨架成员重复插行或改标。

5. `app/services/steward_views.py`
   - 读取端把被活跃推测边指向的 `space_member` 改标 `inferred_path`
     （复制新 dict，不原地改写 ORM JSON 列）。

### 前端

- `composables/useFamilyTreeCanvas.ts`：节点数据新增 `inclusionReason`。
- `views/FamilyTreeView.vue`：透传 `inclusionReason` 给节点。
- `components/canvas/MemberNode.vue`：`space_member` 显示安全「关系待建立」
  状态，绝不渲染伪称谓。

## 关键实现决定与理由

- **两条路径都要改**：`payload_for()` 优先返回 steward staged publication；
  只改 `rebuild_view()` 的话生产环境看到的仍是旧骨架。
- **`space_member` 只在被活跃推测边指向时才改标 `inferred_path`**：既满足 R3
  「普通成员不自动升级」，又保留 09-13 推测层的既有显示合同（推测角标 +
  viewer_term + 推测节点摆位），避免推测层回归。
- **进度分母不含 `space_member`**：它们不需要生成称谓，若计入会让
  `phase` 长期停在 building。
- **不新增迁移**：`inclusion_reason_code` 是开放 `str`，无 schema 变更。

## 已修正的错误合同

- `.trellis/spec/architecture/5--5.md`：新增 PFV 成员资格与关系路径分离条目。
- `.trellis/spec/architecture/11--11-personalfamilyview-bridge-2026-09-01.md`：
  「同空间无确认路径的人不进入个人树」改为授权候选全保留 + `space_member`。
- `.trellis/tasks/archive/2026-09/09-13-family-tree-relationship-topology/design.md`：
  加 2026-09-18 修正说明。

## 测试改动

- 改写旧合同断言：`test_personal_family_view.py`（原
  `..._contains_only_confirmed_reachable_people`）、
  `test_personal_family_topology.py`、
  `test_personal_family_view_consistency.py`、`test_steward_input_versions.py`。
- 新增回归：`test_personal_family_view.py` 三个孤立成员用例（保留/无边/撤权消失）、
  `test_steward_staged_pipeline.py::test_staged_view_keeps_authorized_members_without_viewer_paths`
  （生产读取路径）、`MemberNode.spec.ts` 的 `space_member` 安全状态。
- 版本常量断言更新为 `pfv-v6-space-members`。

## 隔离库端到端核验（DATA_DIR=/tmp/fg-iso-0918，主库 .backup 副本）

真实李氏家族空间（space 4，4 名 active 成员）逐个 viewer 重建并读取：

- 王德海（3，无任何关系路径）：nodes=4，3 个 `space_member`，edges=0；
  李家三人的 confirmed 拓扑边（spouse/2×parent）仍正确返回。
- 李国强（14）/孙桂芳（15）/李念（16）：各自 root + 2 个 `confirmed_path`，
  王德海为 `space_member`，个人称谓边（妻子/儿子/丈夫/爸爸/妈妈）不退化。

stale 收敛：把视图 computation_version 回写成 `pfv-v5-term-alias` 并只留 root 节点后，
`get_current_view` 判定失效、`view_payload` 返回 stale+空 nodes；`rebuild_view` 后
恢复 4 节点（3 个 `space_member`）。

生产库未改动：核验前后 `personal_family_views` 仍为 `1|stale|pfv-v1`、`2|stale|pfv-v1`，
节点 7 行不变。所有写操作只发生在 `/tmp/fg-iso-0918` 副本。

## 未运行 / 待办

- 未做真实远端部署与浏览器页面复核（需按项目规则核对 systemd 作用域与配置来源）。
- 未补 dev seed 中朱元璋—朱佛女关系边；按 R6 这不是本任务前置条件。
