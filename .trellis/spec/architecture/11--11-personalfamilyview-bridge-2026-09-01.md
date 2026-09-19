# 11. PersonalFamilyView 与跨族谱 Bridge（2026-09-01）

- PersonalFamilyView 按 `viewer_account + root_user + space` 建立可重建授权投影；它不是 SourceFact、Relation、SpaceMember 或公共图真源。
- 视图只消费 confirmed 结构事实和当前 VisibilityPolicy；SocialRelation 不入图，`none` 节点完全省略。**节点候选集与关系路径分离（09-18 修正）**：当前空间 active 成员/引用与 viewer 本人经 `PURPOSE_GRAPH` 授权重验后均进入节点集；无 viewer→target confirmed 路径的成员保留为 `space_member` 孤立节点（不写个人称谓边、不产生结构边），不再因「无确认路径」而从个人树中丢弃。仅当确有活跃推测边指向该端点时才改标 `inferred_path`。
- **路径中间人可与节点集合不同（09-19 修正）**：亲属路径的事实口径不要求「两端点都在本空间节点集合内」，否则共享父母这类不是本空间成员的中间人会把整条边剔除，路径被切断（同一族事实已在库中，却显示「关系待建立」）。事实入图的判据是「至少一端接入节点集合」且两端点按 `PURPOSE_GRAPH` 可见；中间人只进入路径证据可见集（`path_genders` / `path_user_ids`），**绝不进入节点集合**——否则外人会变成别人家族树的节点。路径重验同样分两集合：两端点用节点集合，中间节点用路径可见集；`topology_edges` 仍只取两端点都在节点集合内的直接事实。`steward_generation_views.skeleton_json` 持久化 `path_user_ids` 供读取端重验（无迁移）。
- 跨 LineageSpace 连接必须使用显式 bridge。bridge 绑定两侧空间与 anchor，只有两位相关用户本人双向同意后 active；空间管理员只接收通知，不具备审批、否决、修改或撤销权。未认领账号不能代签。
- active bridge 只在当前 viewer 是 anchor 时作为跨空间边进入图遍历，并允许沿另一侧 anchor 可达的 confirmed 结构路径计算；跨空间节点只能使用最小 `lineage_summary` 字段。
- 关系事实、成员/引用、bridge consent/revoke 和权限收紧事件将相关投影标为 stale；读取时再次校验当前成员资格、bridge 状态和字段级可见性，撤权后不得返回旧节点。
- Steward 在 space-scoped job 中重建受影响投影；Assistant、platform_operator 和空间管理员均不能借此扩大读取权或直接写入 SourceFact。