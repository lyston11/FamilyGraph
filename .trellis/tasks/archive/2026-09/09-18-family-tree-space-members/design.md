# 技术设计：保留有权限但无关系路径的空间成员

## 1. 行为边界

当前 PFV 把「授权候选节点」和「viewer 可解析的关系路径」错误地合并成同一集合。修复后拆成两个独立集合：

```text
authorized_candidates
  = active space_members ∪ active space_profile_refs ∪ {viewer}
  → visibility.PURPOSE_GRAPH 重验

nodes
  = 所有通过授权重验的候选人

personal edges
  = 仅对 viewer 之外且 resolve_relationship().found 的节点生成

topology_edges
  = 仅对 nodes 两端都在授权集合内的 confirmed 直接事实生成
```

因此，关系路径只决定「是否有个人称谓/路径摘要」和「是否有结构事实连接」，不再决定合法空间成员是否存在。

## 2. 后端 PFV 重建

### 2.1 复用现有候选与授权

继续复用 `relationship_graph.load_graph(session, viewer_user_id, space_id)` 产生的 `node_genders` / bridge 元数据，以及 `_view_payload_for_view()` 的实时 `PURPOSE_GRAPH` visibility 重验。不得直接用全量 users 或前端空间列表拼节点。

重建循环应按稳定 user id 遍历候选目标：

1. viewer：写入 root node，保持现有 `self_private` 处理。
2. 非 viewer：先执行 `_node_display` / visibility 所需的数据准备；再执行 `resolve_relationship()`。
3. `resolution.found`：按现有逻辑写入节点和个人 edge。
4. `resolution.found == false`：仍写入节点，使用新的明确 inclusion reason（建议 `space_member` 或 `authorized_member`），不写 edge。

节点的 `visibility_level`、`display_json`、`authorization_basis_json` 必须来自现有授权决策。孤立节点不能借用 `confirmed_path` 名义，也不能使用跨空间 bridge 权限绕过普通可见性。

### 2.2 inclusion reason 与 API 兼容

`PersonalFamilyViewNodeOut.inclusion_reason_code` 当前为开放 `str`，无需数据库迁移。新增值应在后端常量/注释中定义，并让所有消费者按「confirmed_path / inferred_path / root / 其他授权成员」安全分支；未知值不能被当作 confirmed relationship。

建议语义：

- `root`：当前 viewer；
- `confirmed_path`：存在 viewer → target confirmed path；
- `space_member`：通过空间成员/授权候选进入，但无 viewer confirmed path；
- `inferred_path`：现有推测层端点。

如果实现发现已有前端对 inclusion reason 做穷举判断，应优先扩展安全默认分支，而不是把 `space_member` 映射成虚假关系。

### 2.3 个人边、拓扑边和推测层

- 个人 `PersonalFamilyViewEdge` 只在 `resolution.found` 时写入；孤立节点没有 term/path。
- `topology_edges` 继续从 confirmed SourceFact 读取，端点必须属于最终授权 nodes；空间成员本身不是结构事实。
- `_emit_inferred_projection()` 的 `confirmed_reached` 参数语义需要改为「已有 confirmed path 或已授权节点」时谨慎处理：普通 space_member 不应自动升级为 inferred 节点，也不应因为它存在而抑制合法的推测端点。实现前依据现有推测测试决定最小兼容调整，并为两种状态各加测试。
- 不从共同空间成员资格推断 sibling/spouse/parent，不修改 SourceFact。

## 3. 读取、渐进协议与缓存

`_view_payload_for_view()` 继续对物化节点逐行重验 visibility，确保旧快照不会绕过实时授权。孤立节点在快照中存在时应正常返回；若权限已撤回则与其他节点一样被过滤。

进度协议不能把孤立节点误报为「待生成关系称谓」而阻塞整体完成。建议：

- `total_count` 可继续统计需要个人 edge 的目标，或显式把孤立成员标记为 `no_path` 且不影响 ready；
- 完成判定必须与已有渐进协议兼容，不能因孤立节点永久没有 term 而持续 running。

修改 `COMPUTATION_VERSION` 或等价输入哈希，使旧的 reachable-only 物化结果失效并进入既有重算队列。不得在普通 GET 中直接补写。

## 4. 前端数据与布局

前端已经有孤立成员分量不丢失的布局合同。确认以下行为：

- decoder 接受新的 `inclusion_reason_code` 字符串；
- `useFamilyTreeCanvas` 不因没有个人 edge 而过滤节点；
- topology edge 仍只使用 confirmed topology；
- `MemberNode` 对 `space_member` 节点使用安全的无关系状态，不显示“我的某某”伪称谓；
- 树状布局把孤立节点/分量稳定分开，自由画布仍保留拖动位置。

只有在现有组件无法表达孤立状态时才新增最小 UI 文案或状态字段，不把个人关系路径伪造成结构边。

## 5. 数据修复与种子

本任务首要修复 PFV 语义，不依赖补充朱元璋—朱佛女关系。可单独评估 dev seed 中历史关系边是否缺失，但：

- 不删除李氏家族空间中的朱元璋成员资格；
- 不直接改远端生产 SQLite；
- 若更新 seed 清单，必须使用现有 insert-only 收敛语义，并验证重算后的节点和边；
- 生产部署前必须按项目规则确认正确 systemd 作用域、配置来源、迁移/重算状态和真实页面结果。

## 6. 测试设计

### Backend

在 `backend/tests/test_personal_family_topology.py` 增加/改写：

- viewer 与 active space member 无路径：节点存在，inclusion reason 为 `space_member`，无个人 edge；
- 多个孤立成员均存在且无悬空拓扑边；
- 可见性撤销/成员移除后孤立节点不返回；
- confirmed path 成员保持原 edge/term 行为；
- 推测层不因普通 space_member 被错误升级或过滤；
- PFV computation version / stale → rebuild 后旧快照收敛。

### Frontend

补充 `family-tree` / `useFamilyTreeCanvas` 测试：

- 节点数包含孤立 space_member；
- 没有 topology edge 的分量仍布局并渲染；
- 输入乱序位置稳定；
- 节点点击、回到自己、树/自由画布和刷新不回归。

## 7. 风险、回退与发布

主要风险是把孤立节点加入 PFV 后增加节点数，暴露已有前端对「每个节点必有 edge」的隐含假设，或让进度计数长期等待。通过先改后端单测、再检查 decoder/canvas/进度协议来控制。

回退顺序：

1. 回退前端孤立节点消费（若出现视觉/交互回归）；
2. 保留或回退后端节点候选集变更；
3. 不回退既有事实、成员资格或生产数据库数据。

发布前必须在隔离数据库完成 rebuild 与接口验证，再按真实部署环境验证家族树页面；不得仅以 health endpoint 或任务归档状态判断完成。
