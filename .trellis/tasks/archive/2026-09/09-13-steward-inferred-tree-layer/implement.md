# 实施计划：管家推测层

按依赖排序；每步含验证点。规范命令在 backend/ 或 frontend/ 目录内执行。
全程遵守 AGENTS.md 数据与安全约束（迁移先在隔离库跑；不动用户未提交改动）。

## 0. 准备

- [ ] 分支：`git checkout -b feat/steward-inferred-tree-layer`（main 有未提交业务改动，
  只携带本任务触及文件，冲突文件先与用户确认归属）。
- [ ] 隔离数据库准备（临时 SQLite），后续迁移步骤在其上验证。

## 1. 后端：数据层

- [ ] `app/config.py`：`STEWARD_INFERRED_TREE_ENABLED`、
  `STEWARD_INFERRED_MAX_ACTIVE_PER_SPACE`（默认 50，区间校验登记）。
- [ ] `app/models/steward_inferred.py`：`StewardInferredEdge`（design §2.1：
  CheckConstraint 状态机 + 部分唯一索引 + subject≠object）；`models/__init__.py` 导出。
- [ ] `app/models/agent_provider.py`：`AgentSpaceProviderSetting.inferred_tree` 列。
- [ ] Alembic 迁移（新表 + 一列）；隔离库 `alembic upgrade head` 通过后跑受影响测试。
- [ ] 验证：`ruff check . && mypy app`；迁移 downgrade→upgrade 幂等。

## 2. 后端：投影入口

- [ ] 新 `app/services/steward_inferred.py`：`project_for_job`（design §3：开关 AND、
  候选反连接幂等、可见性/confirmed 去重、驳回冷却、证据变化 superseded、上限截断、
  domain event `steward.inferred_projected/superseded`）。
- [ ] `steward_events.py`：新增事件常量与聚合类型。
- [ ] `steward.py::_execute_locked` 第 5.6 步接线（begin_nested SAVEPOINT，失败仅记日志）。
- [ ] 测试：真值表（平台/空间开关组合）、幂等（同候选重复投影 1 行）、冷却（同证据
  rejected 不重投影）、证据变化 superseded、上限截断、SAVEPOINT 隔离（投影抛错不回滚 core）。

## 3. 后端：PFV 集成

- [ ] `relationship_graph.load_graph` 增可选 `extra_edges`（缺省 None 行为不变；
  既有调用方与测试全绿为准）。
- [ ] `personal_family_view.py::rebuild_view`：推测循环 + `inferred_path` 节点 +
  `inferred_edges` payload 区块（现算不物化，design §4）；confirmed 称谓不被覆盖的
  顺序保证；`COMPUTATION_VERSION` → `pfv-v3`。
- [ ] `schemas/personal_family_view.py`：`InferredEdgeOut` + `PersonalFamilyViewOut
  .inferred_edges`（默认空列表，extra=forbid）。
- [ ] 测试：开关关 = payload 与现行为逐字段一致；开关开 = 推测节点/边/称谓断言；
  推测跳不互联（无多跳推测链）；confirmed 边优先；超上限截断。

## 4. 后端：操作端点

- [ ] 新 `app/api/steward_inferred.py`：confirm / dismiss / reinstate（design §5：
  revision CAS、Idempotency-Key、资格分层 200/202、终态 409、domain event + 命令层审计）。
- [ ] `app/commands/relationship_proposals.py` 复用核对：不新增绕过资格校验的路径。
- [ ] 路由注册（listener 装配处，随家庭 API 域）。
- [ ] 测试：有权确认 → confirmed SourceFact + 推测边消亡 + PFV 下轮出现 confirmed 边；
  无权确认 → 202 提案；驳回冷却与 reinstate；CAS/幂等/错误码。

## 5. 后端：模型设置 API

- [ ] `schemas/space.py` / `api/space_model_settings.py`：`inferred_tree` 读写；
  平台级未开启时 GET 返回生效状态提示字段（供前端提示，不暴露 env 细节）。
- [ ] 测试：默认 False；PUT 翻转；提示字段真值表。

## 6. 前端

- [ ] `types/api.ts` + `api/personalFamilyView.ts`：decode `inferred_edges` 与
  `inferred_path` 节点（逐字段运行时校验）。
- [ ] `composables/useFamilyTreeCanvas.ts`：推测边规格（inferred 标记、kind→世代方向）、
  inferred_path 节点 term。
- [ ] `views/FamilyTreeView.vue`：`fg-view-edge-inferred` 虚线样式（token 化）、
  「推测·」label 前缀；MemberNode「推测」角标。
- [ ] 关系面板：确认（两步确认）/驳回/撤销驳回，成功后 `pfv.refresh(spaceId)`。
- [ ] `components/member/SpaceModelSettingsPanel.vue`：「推测关系上树」开关 +
  平台级未开启提示；`api/spaces.ts` / stores 同步字段。
- [ ] 测试与门禁：`npm run lint && npm run type-check && npm test && npm run build`。

## 7. 收尾核验

- [ ] `cd backend && ruff check . && ruff format --check . && mypy app && pytest`
- [ ] `cd frontend && npm run lint && npm run type-check && npm test && npm run build`
- [ ] `./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json`
  （退出码 2 = 环境阻塞，如实记录，不算通过）
- [ ] 未运行的高成本检查如实写明（system-admin-frontend 本任务无改动则跳过并说明）。
- [ ] spec 更新评估：steward-action-card / relationship-intelligence / state-management
  是否需要补推测层语义（Phase 3.3）。
- [ ] `task.py finish` 前按 check.jsonl 跑 trellis-check。

## 回滚点

- 步骤 1-5 任意失败：迁移 downgrade，删除新文件即可（纯增量，无既有行改动）。
- 步骤 6 失败：前端独立于后端回滚（payload 新字段被旧前端忽略）。
- 上线后异常：双开关关闭 = 行为回到现状（design §8）。

## 审查门

- 步骤 1-4 完成后：后端自查 + 关键红线核对（§7 红线表）再进前端。
- 步骤 6 完成后：全量门禁 + 视觉核对（树上虚线/角标/面板提示）。
