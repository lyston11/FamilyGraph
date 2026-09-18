# 修复家族空间成员在家族树中被丢失

## Goal

让用户在家族空间中看到所有当前有权限的 active 成员。成员即使暂时没有与当前 viewer 可解析的 confirmed 亲属路径，也必须作为独立人物节点保留；没有事实依据时不生成任何关系线、称谓或亲属路径。

用户价值：加入家族空间的成员不会因为关系事实尚未录入而从家族树中消失，用户能够区分「空间成员存在但关系尚未建立」和「成员无权查看」。

## 背景与已确认事实

- 截图 `/family-tree` 显示李氏家族空间「1 位家人」，当前账户朱元璋只显示自己。
- 远端只读数据核对确认：李氏家族空间的 `space_members` 中包含朱元璋、李贞、朱佛女、李文忠、李景隆；朱元璋 viewer 的 PFV 只有 root 节点，李贞等 viewer 的 PFV 可见李家四人。
- 当前 `backend/app/services/personal_family_view.py` 在 PFV 构建时，对非 viewer 目标调用 `resolve_relationship()`；`resolution.found` 为 false 时直接 `continue`，因此把合法空间成员从 PFV 节点集合中丢弃。
- 当前 `backend/tests/test_personal_family_topology.py` 的孤立成员断言把这一行为描述为「confirmed-reachable 合同」，该合同与空间成员资格、前端布局的「孤立成员不丢失」要求冲突。
- 连接/加入空间的领域合同允许仅有 `space_membership` 而没有新关系边的 `join_request`；因此「成员资格存在但关系路径不存在」是合法状态，不应被 PFV 当作不存在。
- 既有家族树布局规范要求保留孤立成员并按分量分离；本任务修正后端 PFV，使该前端合同真正可达。

## Requirements

### R1：节点候选集与权限边界

PFV 的候选节点必须包含：

- 当前 lineage/家庭空间的 active `space_members`；
- 当前空间的 active `space_profile_refs`（如现有图加载合同纳入）；
- 当前 viewer 本人。

候选节点仍必须逐节点经过现有 graph-purpose visibility 重验。隐藏节点、已移除成员、无效引用和无权目标不得返回。不得因为本任务扩大跨空间 bridge、资料字段或授权范围。

### R2：无关系路径成员保留为孤立节点

当候选成员不是 viewer 且 `resolve_relationship()` 找不到 confirmed 路径时：

- 仍写入 PFV node 并在 API 中返回；
- 使用明确的成员资格/孤立节点 inclusion reason（不得伪造 `confirmed_path`）；
- 使用现有授权后的 display 与 visibility level；
- 不写入个人关系 edge，不生成 term、concept code、path 或 alternative path。

有 confirmed 路径的成员保持现有个人摘要 edge 行为；viewer 保持 root 行为。

### R3：结构拓扑与推测层隔离

- `topology_edges` 只来自当前授权节点集合内的 confirmed 直接事实；孤立成员不产生结构边。
- inferred edge 仍由现有推测层独立控制，不因普通空间成员节点被保留而自动增加推测关系。
- 多个无关系成员必须作为独立连通分量保留，前端树布局负责稳定分离，不得删除、复制或虚构连接。

### R4：渐进状态、缓存与重算

- PFV 重新计算后，合法孤立成员应出现在 current/stale 安全快照中，且 stale/current 的可见性重验规则保持不变。
- 修改 PFV 计算合同或 inclusion reason 时，必须确保现有快照会被正确失效或重算，不允许继续长期服务旧的「只保留 confirmed reachable」结果。
- GET 保持只读；不得在普通读取请求中直接重建或写数据库。

### R5：兼容与前端表现

- 保留已有节点详情、viewer 标识、个人称谓、结构拓扑、推测边、树状/自由画布和刷新行为。
- 无关系路径节点可以被点击和定位，但不得显示虚假的亲属称谓或关系说明；其无路径状态应能安全显示。
- `data.nodes.length` 应统计所有实际返回的授权空间成员，而不是只统计与 viewer 有路径的成员。

### R6：种子与既有数据

本任务不把补录朱元璋—朱佛女关系事实作为 PFV 修复的前置条件。关系事实补齐可以使节点连通，但没有关系事实时，成员仍必须显示为孤立节点。

如需要调整 dev seed，仅用于让演示数据的历史关系完整；不得通过删除朱元璋的李氏空间成员资格来掩盖 PFV 问题。

## Acceptance Criteria

- **AC1 成员完整性**：构造一个 active 空间成员与 viewer 无 confirmed 路径的场景，PFV 节点包含 viewer 和该成员；该成员不是 `confirmed_path`，且没有个人/结构关系 edge。
- **AC2 截图回归**：朱元璋查看李氏家族空间时，李贞、朱佛女、李文忠、李景隆不再因朱元璋缺少关系路径而消失；如果当前数据仍无朱元璋—朱佛女事实边，他们显示为独立分量而不是被连线。
- **AC3 授权不扩大**：非 active 成员、不可见目标、无效 profile ref、跨空间未授权目标仍不出现在 nodes；现有隐私级别和 bridge 到期行为不退化。
- **AC4 拓扑不虚构**：孤立节点不生成 `edges`、`topology_edges` 或 inferred edges；已有 confirmed 直接关系端点仍正确返回且不悬空。
- **AC5 多成员稳定布局**：前端树模式保留所有孤立分量，节点不重叠、输入顺序变化位置稳定，树/自由画布和刷新流程不回归。
- **AC6 旧测试合同修正**：删除/改写「孤立成员不进入 PFV」断言，新增节点保留、无边和权限回归测试；相关 backend/frontend 定向测试通过。
- **AC7 快照收敛**：旧 PFV 视图在新计算合同下会失效并重算，重算后节点数量与 API 响应一致；不通过手工直接写生产库解决问题。
- **AC8 质量门禁**：运行受影响的 backend pytest、ruff/format/mypy（按改动范围）及 frontend family-tree 定向测试；未运行项目级高成本检查须在交付记录中说明。

## Out of Scope

- 不改变空间邀请、join_request、成员移除或关系录入产品流程。
- 不自动根据共享父母、历史常识、称谓或成员姓名补造亲属事实。
- 不扩大 visibility、bridge 或跨空间发现授权。
- 不重做家族树视觉主题、布局算法或关系事实模型；只修复节点候选集与孤立成员表达所需的最小前后端行为。
- 不直接修改生产数据库；部署和历史演示数据修复另行按验证结果执行。
