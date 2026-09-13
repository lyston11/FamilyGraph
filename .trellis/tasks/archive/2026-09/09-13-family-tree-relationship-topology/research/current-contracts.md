# 家族树结构修复：源码证据与实施约束

## 核查范围与基线

- 2026-09-13，主检出 main@b7bd368。用户已同意创建任务和进入设计；此记录阶段未改产品代码。
- 后端由只读 explorer 核查，主线程抽查下列关键行；前端由主线程直接阅读。证据来自本地源码、测试和相关已归档任务，不以推断替代现有合同。
- 主检出已有管理员前端、backend/app/config.py、AGENTS.md、Trellis 配置等未提交改动，均非本任务所有。

## 根因

1. backend/app/services/personal_family_view.py:189 对每位目标解析本人到目标的关系；:249 固定 from_user_id=actor.id，:251 的 edge_kind 是路径末步类型，:497 原样序列化端点。这是个人称谓摘要，不能当直接关系。
2. frontend/src/composables/useFamilyTreeCanvas.ts:105 把摘要直接 map 成画布连线；:126 只按路径累计世代；:159 同代仅按 user_id 排列。
3. frontend/src/components/canvas/MemberNode.vue:63 / :81 只有顶部 target、底部 source。frontend/src/views/FamilyTreeView.vue:149 使用默认曲线，不区分上下与同代端口。
4. frontend/src/composables/__tests__/useFamilyTreeCanvas.spec.ts:118 固化摘要端点；:161 起的布局测试只检查世代，没有验证兄妹通过父母、孙辈逐代连线。

## 事实与权限合同

- backend/app/models/relationship_facts.py:25 / :35 是七种 SourceFact 类型和 parent 四种子类型的权威；state 只有 confirmed 才可进入已确认结构。
- backend/app/services/relationship_graph.py:198 查询 confirmed、允许空间或全局事实；:216 起把家长 subject→子女 object 变为 up/down 遍历，对称关系双向存储。可以抽取事实查询和类型映射供拓扑复用；除下述桥接到期修正外，须保持原 graph 行为。
- PFV 展示端点来自 personal_family_view.py:447 的 PURPOSE_GRAPH 重授权及已有桥接节点处理，不能换成 PURPOSE_AGENT 的 node_genders 或出生字段。
- personal_family_view.py:362 / :387 对路径逐步验证端点、confirmed 和当前空间或全局事实。新拓扑沿用这个空间边界。
- 已知边界差异：load_graph 会读取 active bridge 另一侧空间的事实，而 PFV 的路径复核仅承认当前空间或全局。本任务不扩大这一披露范围；另一空间事实不因两个端点可见而自动进入 topology_edges。
- relationship_graph.py:243 的 bridge / fact_id=0 是授权连接，不是亲属事实。direct_sibling 可在父母未知时独立成立，不反推父母（同文件:11）。
- 独立规划审查发现并经主线程确认：relationship_graph.py:170 仅筛 active，没有检查 PersonalFamilyBridge.expires_at（模型:169）。test_personal_family_view_consistency.py:693 的 expiry 用例手工改 status，未覆盖只随时间到期。新设计需在共享图读取入口过滤已到期 bridge，使节点、路径、拓扑和输入指纹都使用同一有效集合；此处仅记录待实现修复。
- relationship_resolver.py:52 / :356 限制路径深度 12、枚举 128、备选 3；现有 path 与 alternative_paths 的并集不保证完整直接关系。

## 响应与缓存

- personal_family_view.py:421 在非 current 或新鲜度失败时先返回安全空内容；不能为了绘制拓扑绕过这一分支。
- backend/app/api/personal_family_view.py:58 当前先比较投影行 ETag、后生成重授权响应；新字段应先经过最终响应构造和校验，再计算 ETag。
- backend/app/services/family_projection.py:80 已有 etag_for_json，供最终 JSON 载荷的强 ETag 复用。新盐值保留合同版本、账户/token epoch 和原投影指纹；仍仅在 current 状态允许 304。
- SourceFact 全量参与图指纹（relationship_graph.py:256），已有 source_fact/space/ref/disclosure/profile/bridge 事件负责写入触发的失效；桥接自然到期还须补读取时的时间过滤，不能仅依赖事件。响应期新增字段无须新表、迁移或改变 pfv-v3 称谓物化。
- 所有安全空态显式 topology_edges=[]；GET 保持只读，沿用原有独立重算登记机制。

## 前端复用与限制

- frontend/src/api/personalFamilyView.ts:211 是 unknown 到共享类型的唯一解码入口；坏条目被剔除，边端点必须命中已解码节点。
- frontend/src/stores/personalFamilyView.ts 已处理 space 缓存、304 和失效 epoch；不新增另一个图数据源。
- frontend/src/composables/useLayout.ts 接受旧 Relation 合同，d3-hierarchy 以树处理多根、忽略配偶和同辈，不能直接替代本任务的多父母图布局；不引入旧 graph store 或保存位置。
- frontend/package.json 已有 Vue Flow、d3-hierarchy；本方案使用现有 Vue Flow 和确定性图布局，不新增布局依赖。
- MemberNode 当前宽 192px、最小高 144px；ROW_SPACING=240、COL_SPACING=280 是现有几何基线。长称谓可能增加实际高度，浏览器验收必须覆盖。
- RelationshipDetailPanel 及 PersonProfileView 消费个人摘要；新结构边不能伪造个人 path 去适配它。结构说明与个人路径说明分别保留准确语义。

## 与推测层任务的重叠

- 另一 linked worktree：/Users/lyston/PycharmProjects/familygraph-term-autofix，分支 feat/steward-inferred-tree-layer，已提交 61c4d4f。
- 核查时其 api/personalFamilyView.ts、composables/useFamilyTreeCanvas.ts、types/api.ts 有未提交改动；后续可能继续变化，实施前必须重新核查。
- 61c4d4f 为 load_graph 增加 extra_edges=None；推测步骤 fact_id<0，bridge=0。推测节点和边仍存 PFV 表，响应单独拆出 inferred_edges。不能将全部 PFV ORM edges 解释为 confirmed。
- 已有 viewer_term、viewer_path、new_user_id、推测节点角标与 inferredEdges 渲染要保留；最终响应 ETag 也应覆盖它们。新 topology_edges 直接查询 confirmed SourceFact，与推测层并列。
- AGENTS.md 要求共享文件任务串行。规划期间只读该 worktree；本任务不得撤销、提交或直接合并别人的 WIP。实施前要确认其写入已结束并核对串行集成后的基线。

## 验证入口

- 后端：tests/test_personal_family_view.py、test_personal_family_view_consistency.py、test_personal_family_bridge.py、test_relationship_resolver.py、test_term_autofix.py。没有独立 test_relationship_graph.py。
- 前端：api/__tests__/personalFamilyView.spec.ts、composables/__tests__/useFamilyTreeCanvas.spec.ts、components/canvas/__tests__/MemberNode.spec.ts、views/__tests__/family-tree.spec.ts，加直接关系说明和图布局对应测试。
- 真实 smoke：scripts/frontend-api-smoke.sh 使用 backend/.venv，隔离 DATA_DIR 和 listener；退出 2 是环境阻塞。日志和报告不能包含 PIN、JWT 或其他秘密。
- 当前 backend/.venv 和 frontend/node_modules 存在；新 worktree 中仍需检查依赖与解释器，不复制运行中的 SQLite 主库。
- 当前可执行工作规范以根 AGENTS.md 为准；已阅读的 .trellis/spec 索引自标为历史资料，后续 agent 应以这里的源码事实和现行根指南为依据，不启用过时旧图或三布局要求。
