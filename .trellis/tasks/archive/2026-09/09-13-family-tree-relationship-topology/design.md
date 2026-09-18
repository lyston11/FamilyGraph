# 家族树结构连线与布局修复：技术设计

## 1. 边界与数据流

本任务修复现有家族树的直接关系与摆位，涉及后端 PFV 响应、前端解码/画布/关系说明。事实、权限、个人称谓算法保持各自现有职责。

数据流：授权候选成员（active space_members / active profile refs / viewer）经 PFV 的 `PURPOSE_GRAPH` 可见性重验后形成完整 nodes 集合；confirmed SourceFact 只生成其中两端均获准的 topology_edges。原 edges 继续用于有 confirmed viewer→target 路径时的个人称谓和路径说明；无路径但有权限的空间成员保留为孤立节点，不生成个人 edge；inferred_edges 继续由已有推测层处理。

**成员资格与关系路径修正（2026-09-18）**：此前把 PFV 节点误写成 confirmed-reachable 集合，导致合法的 `join_request`/跨空间成员在没有关系事实时从家族树消失。空间成员资格可以独立于关系边存在；`resolve_relationship()` 只决定个人路径/称谓是否可生成，不决定授权节点是否存在。孤立节点必须保留并由布局分量分离，不能伪造关系事实。


## 2. 新增响应合同

GET /api/personal-family-view 增加 topology_edges，原字段语义保持兼容。

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| id | string | 由规范化 kind、subtype、from、to 生成的稳定结构 key |
| from_user_id | int | parent 为家长；对称关系为较小 ID 的端点 |
| to_user_id | int | parent 为子女；对称关系为较大 ID 的端点 |
| edge_kind | parent / spouse / partner / sibling | 直接关系类型 |
| subtype | biological / adoptive / step / guardian / null | parent 保留类型；对称关系必须 null |

例如本人 10、妹妹 20、共同父亲 30：输出 parent 30→10、parent 30→20；不会仅因旧摘要包含 10→20 就生成兄妹直接事实。若已有 direct_sibling SourceFact，可另有 sibling 10—20，不反推父母。

规则：

- parent 一律家长→子女；spouse/partner/sibling 无向，端点升序只用于规范化，画面不展示方向箭头。
- 按 (edge_kind, subtype, from, to) 去重并稳定排序。全局/空间重复事实、反向申报的对称事实合并；不同 parent subtype 不合并。
- key 不依赖 viewer、遍历顺序或某个重复 SourceFact 的 ID；撤回重复证据之一但关系仍成立时 key 稳定。
- 不新增 fact_ids、个人称谓、生日、空间身份或源文本字段。现有个人路径证据仍由原合同提供。
- Pydantic 新模型 extra=forbid，Literal 限制类型；topology_edges 使用 default_factory=list，所有安全空态也显式返回空数组。

## 3. 后端生成与授权

选择响应期生成，不新增投影表、迁移或后台拓扑任务。

1. 保持 get_current_view 的账号/空间授权，以及 view_is_current 的新鲜度检查。共享 load_graph 的桥接集合须同时满足 active 且 expires_at 为空或严格晚于当前 UTC 时间；到期边界等于 now 时即失效。复用项目 utcnow 约定，在图读取入口过滤，不等后台写 expired 状态，也不由 topology 单独补筛。
2. 非 current、版本漂移或安全空态直接给空 topology_edges，不另行查询并拼入事实。
3. 对 current 快照，复用 _view_payload_for_view 已按 PURPOSE_GRAPH 重验的 visible_ids。只读查询 confirmed SourceFact，限定两端都在这个集合、space_id 为当前空间或 NULL，类型属于现有原子亲属类型。
4. 复用 relationship_graph 的 scoped fact 查询和类型映射；抽取必要的纯 helper 时，让原 load_graph 继续传自己的 allowed_space_ids/visible IDs。除本节明确的到期剔除外，保持有效桥接范围、排序与图指纹生成规则不变。新拓扑显式使用 PFV 的更窄空间范围。
5. 规范化、去重后写入最终响应。排除无效/自环端点、未知类型、非 confirmed 状态，不读取 SocialRelation。

不会从全量 PFV ORM edges 收集 confirmed，因为推测层可能在该表存 inferred_path。bridge 授权边和推测负数 fact_id 不是新的亲属事实。直接 SourceFact 查询自然隔离这两类边。

已存在的跨空间差异留在原边界内：graph loader 可纳入 bridge 对方空间，PFV 路径复核目前仅允许当前空间/全局。本任务沿用后者，不扩展披露范围；这一限制不通过前端拼接绕过。

桥接时效是本次授权验收所需的有限修正：节点桥接授权、旧路径、新拓扑和 view_is_current 指纹都通过同一有效桥接集合。只依赖到期 bridge 的节点/关系应在下一次读取消失。其他独立有效授权资格保留，但旧投影因指纹变化失效期间，首次 GET 仍按现有规则返回整个安全空态；重算后重新显示这些获准节点，不要求在 stale 响应中保留部分旧内容。GET 不修改 bridge 状态；已有事件流程继续工作。若串行集成后的基线已实现这一过滤，则复用而不重复实现。

## 4. ETag、只读与缓存

路由顺序调整为：空间授权 → 视图新鲜度 → 安全响应构造 → Pydantic 校验与序列化 → ETag → 条件响应。

- 复用 family_projection.etag_for_json；合同版本增加 PFV topology 版本标识，盐值绑定 account/token epoch 和原 etag_for 的投影指纹，摘要覆盖实际最终 JSON（包括 topology_edges 和已有 inferred_edges）。
- 仅最终状态 current 且新鲜度检查通过时允许 304；stale、queued、failed 等不命中旧 304。
- 相同授权、事实与序列化响应得到相同 ETag；事实撤回、授权/显示变化、推测载荷变化等不能沿用不再匹配的缓存。
- 现有 source_fact/space/ref/disclosure/profile/bridge 事件与 input_hash 保留；自然到期的 bridge 通过读取时过滤离开图指纹，使旧 current 投影变为不新鲜，不能命中旧 304。GET 不重建、不创建投影行、不写拓扑或 bridge 状态，重算仍走现有独立事务登记。
- 新字段读取 current 事实，因此旧 current 物化行即可服务，无需升级 pfv-v3 或强制全量重建。

## 5. 前端数据与交互

### 5.1 解码和画布模型

- types/api.ts 增加明确的 TopologyEdge 类型；API decoder 是唯一 unknown 解码入口。
- 新后端总返回数组。前端允许旧载荷缺字段，以 null 表示结构数据未提供，区别于合法空数组；更新相关测试 fixture，避免以摘要边作为回退连线。
- 字段存在但不是数组时拒绝载荷；数组内坏条目按现有 decoder 风格丢弃。验证整数正 ID、不同端点、kind/subtype 组合、端点均在已解码节点内，并对重复 key 防御去重。
- FamilyCanvasModel 将 topology edges、个人摘要和 inferred edges 明确分开。个人摘要仅提供 node.term 和个人路径说明，不再进入普通 flowEdges。
- topology 缺失时可用现有摘要估计节点几何位置，但不绘制星形替代边，展示“亲属连线暂未加载，请刷新重试”。

### 5.2 连线与端口

- parent：家长底部 source → 子女顶部 target，使用 Vue Flow smoothstep，保留 parent subtype 的准确文字。
- spouse/partner/sibling：使用节点左右端口；按实际 x 位置选择左右侧，保持规范化数据端点稳定，无箭头。同辈线不能从卡片底部绕到另一张卡片顶部。
- 结构边标签使用亲子、配偶、伴侣、兄弟姐妹及子类型，不使用“我的孙子”等 viewer 称谓。所有新样式复用 --fg-*。
- 所有端口仅用于展示，不能在本页面拖线创建事实。自由画布改变位置，不改变结构边身份和含义。

### 5.3 关系说明

- 新增纯展示 StructuralRelationshipPanel，接收 topology edge、当前快照端点 display 及时间信息，展示两个实际端点、关系类型与已确认状态，保留关闭/待办导航。
- 个人 RelationshipDetailPanel 与 PersonProfileView 仍消费原个人摘要，不伪造 PathStep、称谓或证据来适配结构边。
- FamilyTreeView 的选择态区分结构边与已有推测边；切换账号/空间、刷新使已选边消失时清空面板，避免残留旧端点信息。
- 已有节点点击、回到自己、家庭卡入口、树状/自由画布、缩放、适应画布、刷新继续工作。

## 6. 确定性世代与家庭布局

在独立纯函数 familyTreeLayout.ts 中完成图几何，useFamilyTreeCanvas.ts 负责数据适配。使用现有 Vue Flow，不新增布局库，不复用旧 Relation/d3 单父树。

1. 对所有节点及 canonical topology 建邻接；每人唯一节点。parent 约束 rank(child)=rank(parent)+1，对称亲属约束 rank(a)=rank(b)。共享父母自然使兄弟姐妹同代，不通过称谓字符串、性别或生日推断。
2. 按稳定 key 排序后遍历每个连通分量分配 rank，并检测矛盾。本人分量完成后整体平移令本人 rank=0；其他分量独立排布，不编造与本人的亲缘。
3. 每代将配偶/伴侣关联的人组织成横向布局块；兄弟姐妹按已有父母集合及稳定 ID 排序。布局块不是新事实或新人物，不计入人数。
4. 使用父母/子女连接的横向重心做固定次数的上下行整理；按块宽度和最小间距消除同代卡片重叠。优先夫妻邻近、共同父母的子女靠近父母下方；多个配偶无法同时相邻时保留所有真实连线并稳定排布。
5. 沿用 192px 卡片与现有 280/240 间距量级。长称谓/字体缩放以实际页面检查校准行距，不把身份证明字段作为布局输入。断开分量保留间隔，孤立节点不丢失。
6. 检测到无法一致分代时保留所有真实节点/边，回退现有自由画布并提示“部分亲属关系暂时无法按世代排列，已切换自由画布”。不得静默删除关系、复制人物或无限迭代。

推测关系仅作为独立虚线叠层：已确认节点的 rank 不被推测边重新约束。若推测任务已集成，只对其新增的推测节点沿已验证的推测方向做有界几何摆位，冲突或未锚定时稳定放置并保留推测标识，不升级为 confirmed。

不承诺任意复杂族谱完全没有交叉；正常截图场景必须清晰体现两代父母、夫妻及逐代子女，且每张卡片只有一份。

## 7. 兼容、实施顺序与回退

- 后端字段是增量合同；原个人摘要、资料和统计继续可用。新前端在旧后端下提供明确的连线未就绪状态。
- 保留并列 inferred_edges、推测节点标识和原确认/驳回流程；无需以推测层功能启用作为基础家族树正确性的条件。
- 共享文件任务必须串行。当前推测层 worktree 有 WIP；本任务只完成规划，实施前重新核查其状态和串行集成后的主基线，不操作他人工作区或分支历史。
- 用户批准最终方案后才 task.py start，在 task.json 指定 worktree 写代码；生命周期命令留在主检出。任务文档以主检出目录为权威，子代理使用绝对任务路径读取。
- 先验证后端增量合同和缓存，再接前端布局，最后跑集成与截图验收。无数据迁移，代码回退不改变事实或资料。
- 需要回退时按任务提交做前进式恢复，不 reset/rebase 他人历史；先回退前端，再回退新增后端响应逻辑。上线/合并不是本轮设计授权。

## 8. 预计改动位置

| 位置 | 必要性 |
| --- | --- |
| backend/app/services/personal_family_view.py | 生成拓扑及安全空态 |
| backend/app/services/relationship_graph.py | 小范围抽取事实查询/映射复用，并补共享 bridge 到期过滤；其余图行为不变 |
| backend/app/schemas/personal_family_view.py | 新字段与严格模型 |
| backend/app/api/personal_family_view.py | 最终响应 ETag 与 304 顺序 |
| frontend/src/types/api.ts、api/personalFamilyView.ts | 新合同和 decoder |
| frontend/src/composables/useFamilyTreeCanvas.ts、familyTreeLayout.ts | 摘要/结构分离与确定性图布局 |
| frontend/src/components/canvas/MemberNode.vue、StructuralRelationshipPanel.vue、relationshipDisplay.ts | 端口、直接关系说明与安全标签 |
| frontend/src/views/FamilyTreeView.vue | 结构边渲染、选择态、回退提示和已有推测层兼容 |
| 对应后端/前端测试与本任务记录 | 验证可观察的结构、权限与兼容性 |

不预设需要更改 shared、管理员端、数据库模型/迁移或业务配置。若研究后的实现超出这些边界，先记录具体原因和影响。
