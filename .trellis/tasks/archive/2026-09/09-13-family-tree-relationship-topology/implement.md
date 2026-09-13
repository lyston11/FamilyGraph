# 家族树结构连线与布局修复：执行与验收

## 当前阶段

- 2026-09-13：实现完成（用户指示主线程直接执行，未派发子代理）。后端/前端均已实现并通过检查；smoke 与真实页面验收已做；**任务分支未合并 main**，等待串行集成。
- 分支/工作树：`feat/09-13-family-tree-relationship-topology` @ `/Users/lyston/PycharmProjects/fg-09-13-family-tree-relationship-topology`，基线 main@20d0308。提交：d56b6ff（后端）、fc926cc（worktree 依赖 symlink 的 gitignore）、b81d6ae（前端）、c6b327a（端口绑定修复）。
- **基线事实**：main 不含推测层（无 inferred_edges/extra_edges）。本任务按 design §7 的条件分支在 pre-integration 基线实现；推测层分支 `origin/feat/steward-inferred-tree-layer`（领先 main 3 提交，任务状态 planning、无活动 worktree）未并入。两任务共享 personal_family_view / useFamilyTreeCanvas / api.ts 等文件，**合并必须串行**，后合并方负责解决冲突并保留 topology_edges 与 inferred_edges 并列（design §2：topology 直接查询 confirmed SourceFact，与推测层正交）。

## 0. 实施前检查（已完成）

- [x] 重新读取 prd.md、design.md、research/current-contracts.md；用户「执行」即批准按已评审范围实施。
- [x] 基线核查：main@20d0308 无推测层代码；推测层分支领先 3 提交未合并、其原 worktree 已移除、任务状态 planning——判定其写入未进入本基线，按串行集成处理，不取走其 WIP。
- [x] task.py validate 通过；主检出 task.py start（planning→in_progress），after_start 钩子建分支与 worktree，核对 task.json 的 branch/worktree_path 后进入 worktree 写码。
- [x] worktree 缺依赖（gitignore 不检出）：backend/.venv、frontend/node_modules symlink 到主检出，并提交 .gitignore 条目防误提交（fc926cc）。测试导入均来自本任务 worktree 代码；测试库用隔离 DATA_DIR。
- [x] 主线程直接执行（用户明确不使用子代理），故「派发 implement agent」不适用；复核由主线程完成并记录如下。

## 1. 后端直接关系与缓存（已完成）

- [x] 严格 schema：`PersonalFamilyViewTopologyEdgeOut`（extra=forbid，Literal kind/subtype），`PersonalFamilyViewOut.topology_edges` default_factory=list；`empty_view_payload`/`_safe_empty_payload` 显式携带空数组。
- [x] 拓扑生成：`relationship_graph.scoped_confirmed_facts`（抽取的事实查询 helper，load_graph 改为复用同一实现，行为不变）+ `topology_edges_from_facts`（parent 定向家长→子女、对称 min/max 规范化、(kind,subtype,from,to) 去重稳定排序、id=四元组）。PFV `_view_payload_for_view` 用本次 PURPOSE_GRAPH 重授权的 visible_ids + 当前空间/NULL 范围调用；不从 ORM 摘要边、bridge 或推测数据反推。
- [x] bridge 时钟到期：`load_graph` 的 active_bridges 查询补 `expires_at IS NULL OR expires_at > utcnow()`（等号即失效）；节点、路径、拓扑与 input_hash 指纹共用同一有效集合；GET 不写 bridge 状态。
- [x] ETag：GET 改为「构造最终载荷 → model_validate → etag_for_json(合同版本+token epoch+原 etag_for 指纹, model_dump_json) → 仅 payload.status==current 允许 304」；非 current 仍走 request_view_recompute 独立短事务；GET 全程只读。
- [x] 测试：新增 `tests/test_personal_family_topology.py` 11 例（共同父母兄妹、配偶/子女/孙辈多起点、备选路径覆盖外的 partner 事实——先断言其确实不在保存 path/alternative_paths、重复/反向去重与撤证后 id 稳定、direct_sibling 不造父母、多父母/再婚/无悬空边、非 confirmed/隐藏/跨空间排除、bridge 仅时钟到期（不改 status 不发事件，首 GET 安全空态→重算后移除→独立授权保留→旧 ETag 不 304）、304 往返与事实变化失效、安全空态显式空数组）。

## 2. 前端结构与布局（已完成）

- [x] 类型与唯一 decoder：`PersonalFamilyViewTopologyEdge` + `topology_edges: [] | null`——旧载荷缺字段→null（结构未提供），字段非数组→拒绝载荷，坏条目丢弃（正整数端点、不同端点、kind/subtype 组合），按 id 去重、悬空端点丢弃。
- [x] 摘要/结构分离：`FamilyCanvasModel{nodes, edges(结构), summaryEdges, topologyAvailable}`；画布连线只来自 topology_edges；摘要边只注入 node.term 与几何估计（topology 缺失时旧世代带估计保留，不画星形线，视图显示「亲属连线暂未加载」提示）。
- [x] 确定性布局：新增纯函数 `familyTreeLayout.ts`——连通分量、parent/sym 世代约束传播（矛盾→null 回退）、本人分量平移 rank=0、同代配偶并查集横向块、4 轮重心整理、块间距消重叠、断开分量独立成带、孤立节点不丢、输入乱序结果不变。
- [x] 端口：MemberNode 四端口全部**显式 id**（top-target/bottom-source/left-target/right-source，常量单一来源）。验收发现 Vue Flow 把未指定 handle 的边绑到「该类型第一个端口」（handleBounds[0]），曾导致亲子边从右侧同代端口出线、视觉上把妻子—姐姐/儿媳—女儿连起来；c6b327a 修复为全端口显式 id + 亲子边显式绑定 bottom→top。
- [x] 结构边渲染：parent=smoothstep 底出顶入，对称=straight 左右端口（按实际 x 选左右，数据端点不变，无箭头）；标签用 亲子·亲生/收养/继亲/监护、配偶、伴侣、兄弟姐妹（relationshipDisplay.structuralEdgeLabel 单一来源），不用 viewer 称谓。
- [x] 面板：新增 `StructuralRelationshipPanel.vue`（两端实际成员、关系类型+子类型、已确认状态、版本/时间、关闭/申请更正/查看待办，只读无写操作）；个人 RelationshipDetailPanel 保留给 PersonProfileView；选择态随快照变化自动清理（结构边消失→清空面板）。
- [x] 视图：世代冲突→自动切自由画布 + 「部分亲属关系暂时无法按世代排列」提示；旧载荷缺字段→「亲属连线暂未加载，请刷新重试」提示；既有导航/定位/缩放/刷新/双画布保留。测试更新：decoder 5 例、画布模型/布局（含冲突回退、乱序确定性）、视图（摘要不画线、结构边传画布、面板、刷新清空、缺失提示、冲突回退、375px 契约）、面板组件 5 例、布局纯函数 13 例。

## 3. 必须验证的场景（结果）

| 用例 | 结果 |
| --- | --- |
| 共同父母＋本人＋妹妹 | ✅ 后端拓扑仅 parent 双亲→子女；真实页面：兄妹同代、各连父母、无本人→妹妹结构边（王德海/王秀兰） |
| 两组配偶＋子女＋孙辈 | ✅ 页面：夫妻横连（配偶标签）、孙辈逐代连接实际父母（王小虎/王朵朵） |
| 备选路径覆盖之外的事实 | ✅ 专项测试：partner 事实不在任何保存 path/alternative_paths（前提断言）仍在 topology |
| 重复/反向事实去重与稳定 id | ✅ 撤销一条重复证据 id 不变 |
| direct_sibling/多父母/再婚 | ✅ 不造父母；subtype 不合并；无悬空边 |
| proposed/disputed/revoked/隐藏/跨空间 | ✅ 全部排除 |
| ETag 相同 304 / 变化不命中 | ✅ |
| bridge 仅时钟到期 | ✅ 无状态写入/事件即失效；独立授权保留 |
| 旧载荷缺字段/空快照 | ✅ null 语义 + 安全提示，不恢复星形线 |
| 375px 与桌面、树状/自由画布切换 | ✅ 真实页面验收（树状/自由画布切换的 DOM 交互在真机被背景层遮挡，由组件测试覆盖） |

## 4. 检查命令与结果（实际运行）

后端（worktree backend，.venv 为主检出 symlink）：

```
pytest tests/test_personal_family_view.py tests/test_personal_family_view_consistency.py tests/test_personal_family_bridge.py tests/test_relationship_resolver.py tests/test_term_autofix.py tests/test_personal_family_topology.py -q
→ 75 passed
ruff check . → 仅 migrations/versions/0041_term_pack_expansion.py E501（main 既有，非本任务文件，未改动）
ruff format --check . → 本任务文件全部通过（0041 既有漂移）
mypy app → Success: no issues found in 182 source files
```

前端（worktree frontend）：`npm run lint` 通过；`npm run type-check` 通过；`npm test` → 66 files / 574 tests passed；`npm run build` 成功。未运行全量后端 suite：改动面集中于 PFV/关系图，上述 6 个文件为其直接合同面，mypy/ruff 覆盖全仓。

smoke：`./scripts/frontend-api-smoke.sh --report /tmp/familygraph-topology-smoke.json` → **verdict=pass，30/30**。注：smoke 首跑 BLOCKED（listener 60s 未就绪）为环境阻塞——`app.serve` 的内部 listener 固定 127.0.0.1:8001 被用户 dev 环境 SSH 隧道占用；以 `INTERNAL_AGENT_API_PORT=18799` 重跑通过（脚本不消费内部端口，无代码改动）。

## 5. 页面验收（已完成，隔离环境）

- 环境：隔离 DATA_DIR 后端（18901/18902，STEWARD 开启）+ 构建产物静态托管（18900，/api 反代），演示种子「王氏家族」（11 人）；未触碰用户 5173/5174/8000/8001 开发环境。演示视图行经应用自身 initialize_account_views + rebuild_space_views 初始化（种子未走注册事件，属环境初始化而非产品代码改动）。
- 桌面 1440：世代带正确（祖辈/本人代/子女代/孙辈）、夫妻横连、兄妹同代各连父母、结构边标签非 viewer 称谓、卡片唯一。
- 结构边点击：面板显示两端成员（王德海—周秀英）、配偶、已确认状态、版本时间；待办跳转可用。
- 375px：工具栏紧凑单行、适应画布后完整世代结构可读、缩放控件可用。
- 缺陷发现与修复：初次页面验收发现同代幻影连线（亲子边从右侧同代端口出线），定位为 Vue Flow handleBounds[0] 绑定语义，c6b327a 修复并复验通过（幻影连线消失，亲子边底出顶入）。
- 截图证据：验收过程截图在会话内；雾青主题切换未逐项截图（主题 token 由既有样式变量承载，结构布局与主题无关）。
- 验收临时进程未自动清理（用户正在查看）：后端 DATA_DIR=/tmp/fg-accept-976r（端口 18901/18902/18799）、静态服务 /tmp/fg_accept_server.py（端口 18900）；确认查看完毕后可 `pkill -f app.serve; pkill -f fg_accept_server` 并删除临时目录。

## 6. 待办与移交

- [ ] 串行集成：本分支与 `feat/steward-inferred-tree-layer` 共享文件，按 AGENTS.md 由单一集成通道先后合并；后合并方保留 topology_edges 与 inferred_edges 并列，并为推测层补「推测不重排 confirmed 世代」的布局约束（design §6 末段）。
- [ ] 合并后跑受影响最小回归 + smoke；集成时检查 migration 序号（本任务无迁移）。
- [ ] `task.py archive` 与 worktree/分支清理在合并进 main 后执行。

