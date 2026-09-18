# 实施计划：保留家族空间中的孤立成员

## Phase 1：设计与基线核对

1. 阅读本任务 `prd.md`、`design.md`，以及 backend/frontend/architecture 相关规范。
2. 核对当前主分支和已有任务变更，确认不与未合并的 PFV、推测层或家族树任务重叠。
3. 在隔离测试数据库复现：active space member 存在、viewer 无 confirmed path、当前 PFV 只返回 root。
4. 记录当前 `COMPUTATION_VERSION`、进度协议、inclusion reason 消费点和重算触发点。

## Phase 2：后端最小修复

1. 修改 `backend/app/services/personal_family_view.py`：候选节点通过授权后全部物化；无关系路径目标写入明确的孤立成员 inclusion reason，不写个人 edge。
2. 检查 `_emit_inferred_projection`、`view_payload`、progress payload、topology payload 对新 reason 的兼容性。
3. 按现有版本/输入哈希约定使 reachable-only 旧快照失效并进入既有重算流程；不在 GET 中写库。
4. 如确有必要，更新 `backend/app/schemas/personal_family_view.py` 的说明/常量，但保持 wire contract 向后兼容。

## Phase 3：测试先行回归

1. 改写 `backend/tests/test_personal_family_topology.py` 中错误的「孤立成员不进入 PFV」断言。
2. 增加孤立成员节点、无 edge、多个孤立分量和权限撤回测试。
3. 增加朱元璋/李氏家族等价的最小 fixture；不依赖生产库、不把历史关系补录作为测试前置。
4. 运行定向 backend pytest；失败时先区分 PFV 语义、推测层、progress 或 topology 影响，再做最小修复。

## Phase 4：前端兼容与回归

1. 检查 `frontend/src/types/api.ts`、decoder、`useFamilyTreeCanvas.ts`、`FamilyTreeView.vue` 和 `MemberNode.vue` 对无个人 edge 节点的处理。
2. 仅在需要时增加安全的孤立成员状态展示；不得伪造称谓、关系线或个人 path。
3. 增加/更新 family-tree、canvas、layout 测试，验证孤立分量保留、确定性、树/自由画布切换和刷新。
4. 运行前端家族树定向测试、lint/type-check；必要时再运行 build。

## Phase 5：端到端隔离验收

1. 用 SQLite `.backup` 创建隔离副本，显式设置 `DATA_DIR`，在隔离环境执行 PFV rebuild 和 API 查询。
2. 验证：朱元璋查看李氏空间返回所有有权限成员；无关系成员没有 edge；李贞自身的关系视图不退化。
3. 验证 stale → rebuild → current 的节点数量、ETag/304、progress 和前端首屏/刷新状态。
4. 核对原生产库计数未被验证脚本污染；不直接写生产库。

## 质量门禁

- `cd backend && ruff check <受影响文件> && ruff format --check <受影响文件> && mypy app && pytest <定向测试>`
- `cd frontend && npm run lint && npm run type-check && npm test -- src/composables/__tests__/familyTreeLayout.spec.ts src/composables/__tests__/useFamilyTreeCanvas.spec.ts src/views/__tests__/family-tree.spec.ts`
- 按实际改动决定是否运行完整 backend pytest、frontend npm test/build；未运行项在交付说明中明确列出。
- 使用 `python3 ./.trellis/scripts/task.py validate .trellis/tasks/09-18-family-tree-space-members` 检查工件完整性。

## 风险与回滚点

- 如果新增节点导致进度协议等待无 path，先修正 ready 判定，再继续前端。
- 如果推测层把普通空间成员当作新推测节点，保留现有推测开关/状态约束并增加隔离测试。
- 如果旧 PFV 快照未失效，禁止用手工生产库更新掩盖；修正版本/失效入口后在隔离库重跑。
- 任何跨模块扩展、数据迁移或生产数据补录，先回到任务设计并记录影响，不在实施中隐式扩大范围。
