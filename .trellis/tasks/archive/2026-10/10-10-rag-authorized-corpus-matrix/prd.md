# RAG 授权语料矩阵与四级可见性对齐

## Goal

把 RAG 从「助手记忆库」升级为「授权语料库」：定死 `(source_type × scope)` 的写入方、允许值域、读取判据与生命周期锚点；把「声明了但没有写入方」的类别从偶然状态变成**显式且可测的决策**；并让 steward 的可读集从一维 `scope` 升为 `(scope, source_type)` 二维，使新增来源类别默认不进入 steward。

## Background（现状事实，均已核对代码）

### 1. 声明的能力远大于实现

- `RAG_SOURCE_TYPES = ("memory", "family_story", "authorized_document", "profile", "public_kinship")`（`backend/app/models/rag.py`），并有 CHECK 约束与 `SOURCE_TIER_FRACTIONS` 五类份额。
- `RAGDocument.scope ∈ ('private','household','lineage','public')`（CHECK `ck_rag_documents_scope`）。
- 但 `index_memory` 全树只有 3 个调用点（`confirm_candidate`、撤销取代恢复、索引换版回填），**全部由 `Memory` 驱动**；`ingest_authorized_document` 除测试外**没有任何生产调用方**；`family_story` / `profile` / `public_kinship` **没有写入路径**。
- 推论：生产环境 `scope='public'` **恒为空**；五类 `source_type` 中四类恒为空；`SOURCE_TIER_FRACTIONS` 的四条份额与 `_SOURCE_TYPE_RANK_WEIGHT` 的四项权重**空转**。
- 前端 `CitationList.vue` 已给出五类中文标签（家族故事／授权文档／个人档案／公共称谓知识），说明产品意图存在，但后端没有对应数据源。

### 2. 两套可见性模型未对齐

| | 域 | 形状 |
|---|---|---|
| `services/visibility.py` | 档案 | 四级阶梯 `self_private > household_detail > lineage_summary > none` + **字段级 mask**（`CONTENT_FIELDS`、未成年人 overlay、披露类别、`HIGH_RISK_CATEGORIES`）；purpose 只能收紧，`PURPOSE_RAG` 上限 `lineage_summary` |
| `RAGDocument.scope` | 记忆/知识 | 扁平枚举 private/household/lineage/public + 空间成员判据；敏感度仅 `normal/sensitive/high/local_required` |

`memory_sources._author_visible` 确实调用 `visibility.evaluate(purpose=PURPOSE_RAG)`，但它只作为**作者可见/不可见的一道门**使用，四级阶梯与字段级 mask **没有落到 chunk 文本上**。因此未成年人 overlay 与 `HIGH_RISK_CATEGORIES`（health/address/school/contact/private_notes）在 RAG 侧**没有等价机制**；`sensitivity='high'` 只是把可读 scope 收紧到 `private`（见 `_restrict_scopes`），不是遮蔽字段。

### 3. 已有的可复用接缝（本任务不应重复造）

- `search_rag` 已具备 `scope_allowlist`（集合语义）、`private_reader_account_id`（显式读者）、`source_types`（目的限定）三个参数；`_ELIGIBILITY_SQL` 的每个 scope 分支已各有开关。
- `_ELIGIBILITY_SQL` 的 `public` 分支由 `:is_assistant = 1` 控制，即**公开材料刻意不对 steward 开放**（既有决定，本任务不改）。
- assistant 已有结构化档案读取工具 `familygraph.get_profile_summary`（`VisibilityPolicy` 投影，只读）。
- 权限过滤有两层且必须各自独立挡住：`memory_rag._ELIGIBILITY_SQL`（SQL 层，filter-then-ANN 的 `authorized` CTE 也走它）与 `memory_sources._can_read_document`（投影复核层）。

## 权威矩阵（本任务交付物 1）

`(source_type × scope)` 四元组：写入触发 / 允许 scope / 生命周期锚点 / 读取判据。

| source_type | 真源 | 允许 scope | 写入触发 | 生命周期锚点 | 本任务动作 |
|---|---|---|---|---|---|
| `memory` | `memories` | private / household / lineage | 候选确认（既有） | `Memory.revision`、`superseded_by_id`、`valid_to`、`status` | **冻结**，不改行为 |
| `public_kinship` | `term_entries` 的 `system` / `locale` 级（无个人数据） | public（`space_id IS NULL`） | 内置称谓包 seed / 包版本升级 | 称谓包版本（`source_id = term-pack:<locale>`、`source_revision = 包版本`） | **实现**（第一个纵向切片） |
| `authorized_document` | `attachments` + 授权记录 | private / household / lineage | 用户对某附件显式授权给 agent | 附件 revision + 授权记录 revision | **只登记合同**，不实现 |
| `family_story` | 尚不存在 | household / lineage | 待产品定义 | 待定 | **只登记合同**，标注为依赖不存在的写入功能 |
| `profile` | `users` 档案字段 | ——（不索引） | —— | —— | **明确排除**，改走 `get_profile_summary` |

### scope 与可见性阶梯的对应（必须写死）

| RAG `scope` | `visibility` 层级 | 说明 |
|---|---|---|
| `private` | `self_private` | 唯一作者本人（`author_account_id`），不是「哪些 kind 能读」 |
| `household` | `household_detail` | 空间双方 active 成员 |
| `lineage` | `lineage_summary` | 空间 active 成员，`PURPOSE_RAG` 上限即此层 |
| `public` | **无对应层级** | 不是可见性层级，而是「**不含个人数据**」的声明 |

`public` 的定义是承重的：**任何含个人数据的 source_type 不得写 `scope='public'`**。`visibility` 没有 `public` 层，`scope` 没有 `none` 层，两者的差集必须由这条规则闭合。

## Requirements

### R1 矩阵权威化

- 把上面的矩阵与其理由写入可被引用的合同文档（`.trellis/spec/backend/memory-rag-execution-contract.md` 新增一节，或独立叶文件），作为新增 `source_type` 的唯一入口。
- 矩阵必须逐格给出写入触发、允许 scope、生命周期锚点、读取判据；缺任一项即视为未登记。

### R2 「活跃索引集」显式化并可测

- 在 `models/rag.py`（或 `memory_rag.py`）显式登记：
  - `INDEXED_SOURCE_TYPES`：有真实写入方的类别；
  - `UNINDEXED_SOURCE_TYPES: dict[str, str]`：声明但无写入方的类别 → **理由字符串**。
- 断言（新增测试）：两者互斥，并集等于 `RAG_SOURCE_TYPES`。新增 `source_type` 而不更新任一侧必须失败。
- 断言：`SOURCE_TIER_FRACTIONS` 覆盖 `INDEXED_SOURCE_TYPES`（保留既有的「所有 `RAG_SOURCE_TYPES` 已登记」断言不动）。
- 不得删除既有 `source_type` 枚举值或 CHECK 约束（迁移风险 > 收益；本任务把「未使用」变成显式决定，而不是删列）。

### R3 `public_kinship` 纵向切片（唯一真实接入）

- 新增 `index_public_kinship(db, *, locale, pack_version, terms)`（或等价入口），从 `term_entries` 的 `system`/`locale` 级生成：
  - `scope='public'`、`space_id IS NULL`、`confirmation_status='authorized'`、`author_account_id IS NULL`、`owner_user_id IS NULL`、`sensitivity='normal'`；
  - `source_id = "term-pack:<locale>"`，`source_revision` = 包版本；
  - 同一 `(source_type, source_id, revision)` 幂等（复用 `_canonical_document` 语义）。
- 该切片必须走**与 `memory` 相同的** chunk 物化、`index_version`、FTS 写入、`rebuild_index`、revision 冲突与撤权路径；不得为它开第二条索引路径。
- 必须在真实执行路径上被调用（seed 或版本升级命令），**不得只在测试里被调用**——否则等于复制 `ingest_authorized_document` 的现状。
- 无个人数据：文档正文只含称谓编码与称谓文本，不含任何 `user_id`/`account_id`/姓名。

### R4 steward 可读集二维化

- steward 的可读集配置形状从 `scope` 集合改为 `(scope, source_type)` 二维集合；有效集 = 部署 env 上界 ∩ 平台列 ∩ 空间列（**交集**，任一为空即整体为空）。
- **新增 `source_type` 默认不进入 steward 可读集**：默认值必须显式枚举，不得由「`scope` 允许了就把该 scope 下所有类别都放行」推导出来。
- `public` 类别继续不对 steward 开放（沿用 `:is_assistant = 1`），本任务不改。
- 有效集为空时维持既有语义：工具不广告、不注册进 run allowlist；检索入口对「配置不允许」返回 403，**不得**返回空列表（那会把授权拒绝伪装成「没有相关内容」）。
- 未知 `scope` / 未知 `source_type` 在写入配置时 422，读取时忽略并告警。

### R5 证明门扩展（先 fixture 后灌数据）

- 在 `backend/tests/fixtures/memory_eval/golden_v1.json` 增加 `public_kinship` 用例，至少覆盖：
  - `answerable`：一条可通过称谓文本召回的查询；
  - `abstention`：库里没有的称谓不得凭向量或词法猜测返回。
- 增加 tier 断言：`public_kinship` 命中受 `SOURCE_TIER_FRACTIONS['public_kinship']` 约束；空类别不借份额给其它类别（既有结构性保证，补断言）。
- 记录 `EVALUATOR_VERSION` / fixture `version` 是否需要提升；若提升，写明历史报告不可直接比较。

### R6 边界（不可越）

- 不改变 `memory` 类的任何既有行为（`_ELIGIBILITY_SQL` 对 assistant 的语义、`_can_read_document` 两层判据、`private` 读者语义）。
- 不把记忆或档案内联进 steward 的静态 prompt（`prompt_digest` / `input_hash` 稳定性是硬合同）。
- 不新增第二份 `_ELIGIBILITY_SQL` 副本；取代/有效区间谓词保持单处真源。
- 不引入跨空间读取；`space_id` 只来自 fenced run 或显式入参。
- 不放宽 #307（跨空间桥接）、#321（家庭卡成员资格）、#584（授权主体独立）。
- 不实现 `authorized_document` 与 `family_story` 的数据源；不实现 `profile` 的 RAG 索引。
- 不触碰 `.trellis/tasks/archive/**` 与 `artifacts/`。

## Acceptance Criteria

- [ ] 矩阵文档存在且逐格完整（写入触发／允许 scope／生命周期锚点／读取判据四要素齐全）；`profile` 的排除理由写明「逐 viewer 求值 + 自由文本无法承载字段级 mask」。
- [ ] `INDEXED_SOURCE_TYPES` / `UNINDEXED_SOURCE_TYPES` 存在、互斥、并集等于 `RAG_SOURCE_TYPES`；新增/删除任一枚举值而不更新两侧时测试失败（反证：手动加一个假类别 → 必须失败）。
- [ ] `public_kinship` 文档在生产执行路径上被创建：断言 `scope='public'` 且 `space_id IS NULL`，且 `author_account_id`/`owner_user_id` 均为 NULL，正文不含任何用户姓名或账号 id。
- [ ] `public_kinship` 走完 `memory` 相同的生命周期：revision 冲突被拒、撤权后带过滤查询不再返回、`rebuild_index` 后结果一致（反证：按 id 直读证明行仍在、`status='revoked'`——可见性由查询层过滤承担，不靠删除）。
- [ ] `scope='public'` 在生产查询中不再恒空：assistant 能召回 `public_kinship` 命中并生成合法 citation handle。
- [ ] steward 侧：默认配置下 `public_kinship` **不可读**（断言工具返回不含该来源）；显式把 `public_kinship` 加入可读集后，`public` 分支仍因 `:is_assistant = 0` 不可读——即二维配置不得绕过既有 kind 边界。
- [ ] 新增 `source_type` 默认不进入 steward 可读集（反证：把一个新类别加入平台配置而不加入空间配置 → 有效集为空、工具不广告）。
- [ ] 配置不允许时返回 403 而非空列表（断言错误码，不是断言结果为空）。
- [ ] golden set 新用例在 `contract` 层通过；`quality` 层记录基线；`forbidden_hits == 0`。
- [ ] 既有回归全绿：`test_rag_acceptance_contract.py`、`test_rag_acceptance_bindings.py`、`test_rag_lifecycle_acceptance.py`、`test_memory_rag_service.py`、`test_context_tier_budget.py`、`test_memory_eval_baseline.py`、`test_steward_memory_tool.py`。
- [ ] `cd backend && ruff check . && ruff format --check . && mypy app && pytest` 通过。
- [ ] 交付说明中写明未运行的高成本检查及原因。

## Constraints

- 迁移：本任务原则上不新增列。若 `public_kinship` 接入确实需要新列，必须可空或带默认值，`upgrade head` 不改变既有行为，且降级不得丢配置语义。
- 迁移先在隔离数据库执行 `alembic upgrade head`（`DATA_DIR` 指向隔离目录），再跑受影响测试；**禁止**在远端 backend 目录直接跑写库脚本（见 project memory #359）。
- 证明义务先于实现：`public_kinship` 的 tier 份额与检索质量必须先有基线分，再有对比分；「感觉更好了」不是证据。
- 放宽任何 `MIN_*` 阈值或修改 fixture 期望值，必须在同一次提交里写明为什么是 fixture 的期望错了。
