# 设计：管家推测层（Inferred Tree Layer）

## 1. 总体数据流

```
LLM candidate 池（StewardLlmCandidate, proposed）
        │  steward core 作业事务内（_execute_locked，SAVEPOINT 隔离）
        ▼
投影 StewardInferredEdge（proposed；证据哈希 + 去重 + 冷却过滤）
        │  PFV rebuild（每 viewer）
        ▼
增广图 = confirmed 边 ∪ proposed 推测单跳（fact_id 置 0 标记 inferred）
        │  relationship_resolver（不改核心算法，仅图数据增广）
        ▼
含推测步的 viewer→target 路径 → PFV payload 新增 inferred_edges 区块
（+ 仅经推测可达的节点 inclusion_reason_code=inferred_path）
        │  GET /personal-family-view（ETag 不变）
        ▼
前端画布：虚线边 + 「推测」角标 + 确认/驳回操作
        │  确认
        ▼
create_relationship_proposal / confirm_relationship_proposal
（现行 consent 合同：有权当事人直接转正；无权 → 202 提案待对方确认）
```

设计原则：**推测层是显示层投影，不是事实层**。confirmed 事实、resolver 核心算法、
prompt 红线全部不动；推测信息只在 `StewardInferredEdge`（可整体重算/清除）与
PFV payload 附加区块中存在。

## 2. 数据模型

### 2.1 新表 `steward_inferred_edges`（新文件 `app/models/steward_inferred.py`）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| space_id | FK family_spaces, CASCADE | 空间隔离 |
| subject_user_id / object_user_id | FK users, CASCADE | 原子关系两端（沿用 candidate 语义：subject—kind—object） |
| relation_kind | String(48) | 原子关系类型，白名单同 `steward_guard.validate_candidate_output`（biological_parent/adoptive_parent/step_parent/guardian/spouse/partner/direct_sibling） |
| status | String(16) | CheckConstraint `proposed/rejected/confirmed/superseded`，默认 proposed |
| origin | String(16) | v1 恒 `llm`（预留 `rule`/`intake`） |
| source_candidate_id | FK steward_llm_candidates, SET NULL | 溯源；幂等反连接键 |
| evidence_hash | String(64) | 投影时 facts brief 摘要（`steward_assist._canonical_hash` 复用）；驳回冷却的判定键 |
| evidence_json | JSON | 参与 hash 的 fact id/revision 快照（安全字段，无姓名） |
| confidence | Float, NULL | v1 恒 NULL（候选无置信度），字段预留 |
| revision | Integer | CAS 用，从 1 起 |
| created_at / updated_at / resolved_at | DateTime | |

约束与索引：

- 部分唯一索引 `uq_sie_active_triple`：`(space_id, subject_user_id, object_user_id, relation_kind) WHERE status = 'proposed'`（每三元组至多一条活跃推测）。
- `ix_sie_space_status (space_id, status)`；`ix_sie_endpoints (subject_user_id, object_user_id)`。
- CheckConstraint：subject ≠ object。

### 2.2 空间级开关列

`AgentSpaceProviderSetting` 新列 `inferred_tree: bool, default False`（与
`assist_candidate` 同排；`space_model_settings.py` GET/PUT 与 schema 同步暴露）。

### 2.3 配置（`app/config.py`）

- `STEWARD_INFERRED_TREE_ENABLED`（env，默认关）——平台级。
- `STEWARD_INFERRED_MAX_ACTIVE_PER_SPACE`（env，默认 50，校验范围 [1, 500]）——
  单空间活跃推测边上限；投影与 PFV 增广都按 created_at 升序截断，超限跳过。
- 既有区间校验机制（`config` 校验表）登记新键。

## 3. 投影入口（管家 core 事务内）

位置：`steward.py::_execute_locked` 第 5.5 步之后新增第 5.6 步
（与 `steward_suggestions.project_for_job` 同层，独立 `begin_nested` SAVEPOINT，
失败不拖垮 core）。新函数 `steward_inferred.project_for_job(db, job, *, facts, visible, now) -> int`：

1. 生效开关判定：`config.STEWARD_INFERRED_TREE_ENABLED AND space_flag.inferred_tree`；
   关闭直接返回 0。
2. 取候选池 proposed 候选（反连接：`source_candidate_id` 已被任何推测边引用的跳过，
   幂等语义与 `steward_suggestions.py:301` 一致）。
3. 逐候选经 `ProjectionContext`（重建 codename 映射，`steward_guard` 校验过的候选已
   是 user id 域）映射回 (subject, object, kind)：
   - 任一端点不在当前 `visible` → 跳过；
   - 与既有 confirmed SourceFact 同 (subject, object, kind) → 跳过（candidate 辅助
     输入本就来自 facts，防御竞态）；
   - 同三元组存在 rejected 且 `evidence_hash` 相同 → 跳过（驳回冷却，与
     STEWARD_SUGGESTION_COOLDOWN_DAYS 同款语义，v1 按证据哈希严格相等判定）；
   - 活跃数 ≥ `STEWARD_INFERRED_MAX_ACTIVE_PER_SPACE` → 停止。
4. `evidence_hash = _canonical_hash(facts brief)`（facts brief 与
   `steward_assist._confirmed_facts_brief` 同源，保证证据口径一致）。
5. 证据变化失效：同三元组存在 proposed 但 `evidence_hash` 变化 → 旧行置 superseded
   后再插入新行（R5：confirmed facts 变化即重投影）。
6. 每次状态转换 emit domain event（`steward.inferred_projected` /
   `steward.inferred_superseded`，聚合 `steward_inferred_edge`）。

intake_extractor 的 supported 提案 **v1 不并入**：其「最后一跳无对应人物」语义需要
新建人物流程，不是既有可见成员间的单跳边（PRD Notes 的开放问题在此定案）。

## 4. PFV 集成

### 4.1 增广图构建

`relationship_graph.load_graph` 增加可选参数
`extra_edges: Sequence[ExtraEdge] | None = None`（dataclass：
subject/object/kind/fact_id=0/inferred=True）。缺省 None 时行为与现在逐字节一致
（所有既有调用方与测试不受影响）。

- 边有效性：status=proposed、两端均在 `_visible_node_ids`、开关生效；
- 与 confirmed 边重复 (subject, object, kind) 的推测边不进入增广图。

### 4.2 rebuild_view 改动（`personal_family_view.py::rebuild_view`）

1. 开关生效时取本空间活跃推测边，构造 `extra_edges` 传入 `load_graph`；
   关闭时与现在完全一致。
2. 既有循环不变（confirmed 路径优先，节点 inclusion_reason_code=root/confirmed_path）。
3. 新增推测循环：对 `resolution.found == False` 的 target（以及图中因推测边新出现的
   可见 user），用增广图重跑 `resolve_relationship`；主路径含 inferred 步
   （`fact_id <= 0`）时：
   - 节点落库，`inclusion_reason_code="inferred_path"`；
   - 边写入 **inferred_edges 区块**（见 4.3），不进 `PersonalFamilyViewEdge` 表
     （v1 推测边不物化到视图表，payload 现算现返回——推测边每空间至多
     MAX_ACTIVE_PER_SPACE 条，逐 viewer 现算成本可控；物化留待性能证据出现）。
4. 已有 confirmed 边的 target 的称谓**永不**被推测路径覆盖（先 confirmed 后推测的
   循环顺序天然保证）。

### 4.3 payload 合同（`schemas/personal_family_view.py` + 前端 decode）

`PersonalFamilyViewOut` 新增可选字段 `inferred_edges: list[InferredEdgeOut] = []`
（additive，旧客户端忽略）。`InferredEdgeOut`：

```python
id: int                      # 推测边 id（操作端点用）
subject_user_id: int
object_user_id: int
relation_kind: str
term: str | None             # 确定性单跳称谓（kind+性别 → TermRegistry）
path: list[dict]             # viewer→target 全路径（若适用），inferred 步 fact_id<=0
inferred_hops: list[dict]    # [{"subject","object","kind"}] 本路径中的推测跳
status: str                  # 恒 proposed（rejected/confirmed 不下发）
revision: int
evidence_summary: dict       # 安全字段：fact id + kind 摘要，无姓名/原文
created_at: datetime
```

- `COMPUTATION_VERSION` `pfv-v2` → `pfv-v3`（payload 形状变化触发全量重算）。
- 推测节点在 `nodes` 中正常下发（display 沿用可见性脱敏管线），仅
  `inclusion_reason_code` 区分；前端 decode 红线「none 已丢弃」扩展为同时保留
  inferred_path 节点。

### 4.4 单跳称谓解析

`term` 用单步路径（subject→kind→object）走与 4.2 相同的
`concept_code_for_path` + `resolve_term_or_structural` 管线（viewer 维度词典等级）。
模型产物只提供 kind，称谓文本 100% 确定性解析——红线「模型不产出称谓」不破。

## 5. 操作端点（新 `app/api/steward_inferred.py`，前缀 `/api/steward-inferred-edges`）

- `POST /{id}/confirm`：body `{expected_revision, confirm: true}` + Idempotency-Key。
  鉴权 = 空间 active 成员。转正走 `app/commands/relationship_proposals.py`：
  - actor 是关系有权当事人（subject/object 本人，或未成年人合法代管人
    `_custody_access`）→ `create_relationship_proposal` + 同事务
    `confirm_relationship_proposal`（200，返回 fact_id）；
  - 否则 → 仅 `create_relationship_proposal`（202 语义，返回 proposal 状态；
    推测边保持 proposed，后续提交态由 confirmed fact/proposal 生命周期驱动，
    下次作业投影时见 confirmed fact 即 superseded）。
  - 命令层已有资格校验，路由绝不直接 `transition_source_fact`。
- `POST /{id}/dismiss`：body `{expected_revision}`。置 rejected + resolved_at +
  evidence_hash 冷却；幂等；emit `steward.inferred_dismissed`。
- `POST /{id}/reinstate`：body `{expected_revision}`。rejected → proposed（仅当
  无同三元组活跃行且不超上限）；emit `steward.inferred_reinstated`。
- 统一错误结构；终态（confirmed/superseded）409；不可见/不存在 404；revision 冲突
  409。确认写入走命令层既有 audit；状态转换均写 domain_event。

## 6. 前端改动（frontend/）

1. **类型 + decode**：`types/api.ts` 增 `PersonalFamilyViewInferredEdge`；
   `api/personalFamilyView.ts` decode `inferred_edges`（逐字段运行时校验，风格同
   现有 decode；`inferred_path` 节点保留入 nodes）。
2. **画布**：`useFamilyTreeCanvas.ts` 纯函数扩展——`FamilyCanvasEdge` 增
   `inferred: boolean`（来自 inferred_edges 的边）；布局把推测跳按 kind 方向
   （parent=up/down、spouse/partner/sibling=sym）并入世代带计算；节点 `term`
   对 inferred_path 节点取推测路径 term。
3. **渲染**：`FamilyTreeView.vue` 推测边加 class `fg-view-edge-inferred`
   （`stroke-dasharray` 虚线 + token 化配色），label 前缀「推测·」；成员卡片
   （MemberNode）对 inferred_path 节点渲染「推测」角标（领域状态徽章体系，
   tokens.ts 新增语义 token，不改既有 token 值）。
4. **操作**：关系面板（现有 `openRelationshipPanel` 路径）对推测边显示
   「确认为事实 / 驳回 / 撤销驳回」——两步确认沿用 ActionCard 两步确认交互范式；
   成功后 `pfv.refresh(spaceId)` 强制重载（无乐观更新，红线保持）。
5. **开关面板**：`SpaceModelSettingsPanel.vue` steward 区块新增「推测关系上树」
   开关（翻动即 PUT）；平台级未开启且空间级打开时显示平台级未开启提示
   （提示机制与 09-13-steward-assist-platform-switch-admin 的 AC 对齐，本任务先
   落提示，治理入口归该任务）。
6. **测试**：decode 单测、canvas 纯函数单测（推测边渲染规格/布局带）、面板与
   操作组件测试；`npm run lint && npm run type-check && npm test && npm run build`。

## 7. 红线对照

| 红线 | 本设计 |
|---|---|
| 模型产物不改变确定性结论 | 候选只经投影成为 proposed 推测边；称谓/路径/概念码全部确定性解析 |
| confirmed 事实只经显式确认写入 | 确认走 `relationship_proposals` 命令层资格校验；无权仅 202 提案 |
| prompt 无真实姓名、白名单输入 | 不新增任何模型调用，输入侧不变 |
| LLM 禁止产出称谓等派生概念 | 候选 kind 白名单不变；推测边称谓为确定性解析文本 |
| PFV 读路径不隐式重算、授权复核 | 改动仅在 rebuild 计算侧；GET 契约不变 |
| 前端无乐观更新 | 操作成功后强制 refresh |

## 8. 兼容性 / 回滚

- 迁移纯增量（新表 + 一列），不触碰既有行；隔离库 `alembic upgrade head` 先行。
- 回滚形态 = 双开关关闭：payload `inferred_edges` 恒为空、投影不发生、画布无推测
  元素；前端对空区块零行为差异。迁移可独立 downgrade（表无外部写入依赖）。
- `COMPUTATION_VERSION` 升版导致一次全量 PFV 重算（既有机制，量级 = 单空间人数）。

## 9. 权衡记录

- **独立表 vs 复用 StewardSuggestion**：Suggestion 是审核队列语义（收件人/冷却/
  提交端点），推测层需要被 PFV 计算按三元组高效查询并参与图增广；独立表避免把
  显示层投影塞进审核状态机。两者共享候选池来源与证据哈希口径，后续可在
  suggestion 侧反链推测边去重（不在本任务）。
- **推测边参与多跳枚举 vs 仅单跳呈现**：选择前者（增广图重跑 resolver）但限制
  推测跳来源为单跳原子边——多跳链的不确定性不复合（推测跳之间不可互联，因
  增广边只有 proposed 单跳），路径与称谓仍由确定性引擎产出，节点能落到正确的
  世代带。备选「仅画 pairwise 虚线、不改路径计算」被否：viewer 视角称谓缺失，
  达不成「完整树」目标。
- **推测边不物化到视图表**：payload 现算，避免 per-viewer 物化行膨胀；上限
  50/空间内成本可控，出现性能证据再物化。
