# 实施计划：RAG 授权语料矩阵与四级可见性对齐

前置：本任务在 `10-03-multi-tenant-agent-concurrency-storage` 树下，**不依赖** PostgreSQL 迁移落地——D1–D6 的决策与 R2/R4 的断言都跑在现有 SQLite FTS5 路径上。R3 的 `public_kinship` 切片也走现有索引路径（PG 迁移落地后自动继承 PGroonga/pgvector 分支，无需本任务改动）。

## Phase A：矩阵权威化（无代码）

- [ ] A1 在 `.trellis/spec/backend/memory-rag-execution-contract.md` 新增一节「授权语料矩阵」，写入 PRD 的矩阵表 + `scope` 与可见性阶梯的对应表 + `public` 的定义（不含个人数据）。
- [ ] A2 写明 `profile` 的排除理由（D1 的三方案对比结论）与 D6 的硬约束（含敏感字段的自由文本不得进入 RAG）。
- [ ] A3 标注 `SOURCE_TIER_FRACTIONS` 中哪几条是预留位（`family_story` / `authorized_document` / `profile`）。
- [ ] A4 在 `backend/app/models/rag.py` 或 `memory_rag.py` 顶部注释指向该合同节，避免两处各说一套。

**验收**：矩阵逐格四要素齐全；评审时能只读该节回答「新增一个 source_type 要做什么」。

## Phase B：显式登记与断言（R2）

- [ ] B1 新增 `INDEXED_SOURCE_TYPES` 与 `UNINDEXED_SOURCE_TYPES`（D3 的形状）。
- [ ] B2 新增测试 `backend/tests/test_rag_source_type_registry.py`：
  - 两者互斥且并集等于 `RAG_SOURCE_TYPES`；
  - `SOURCE_TIER_FRACTIONS` 覆盖 `INDEXED_SOURCE_TYPES`；
  - 每个 `UNINDEXED_SOURCE_TYPES` 的理由非空。
- [ ] B3 **反证**：测试里加一个临时假类别（或直接断言「把 `profile` 从 UNINDEXED 移到 INDEXED 会失败」）证明断言不是同义反复。
- [ ] B4 确认 `test_context_tier_budget.py` 既有断言未被削弱。

> 需要 CHECK 约束与 SQLite 整表重建成本的依据时，按需读 `.trellis/spec/backend/database-guidelines.md`（43KB，超出注入上限，不进 `implement.jsonl`）。

**验收**：新增/删除枚举值而不更新两侧 → 测试失败。

## Phase C：证明门扩展（R5，必须先于 D）

- [ ] C1 在 `backend/tests/fixtures/memory_eval/golden_v1.json` 增加 `public_kinship` 用例：
  - `answerable`：一条可通过称谓文本召回的查询，期望来源指向某个称谓包；
  - `abstention`：库里不存在的称谓，必须返回空。
- [ ] C2 若需要，提升 fixture `version` / `EVALUATOR_VERSION`，并在提交信息里说明历史报告不可直接比较。
- [ ] C3 跑 `test_memory_eval_baseline.py` 记录**接入前**基线（此时 `public_kinship` 用例应因无数据而失败或落在 gap 层）。
- [ ] C4 增加 tier 断言：`public_kinship` 命中受其份额约束；空类别不借份额。

**验收**：接入前基线已落盘，接入后有可比对比分。

## Phase D：`public_kinship` 纵向切片（R3）

- [ ] D1 确认 `term_entries` 的 `system` / `locale` 级是否已有可用版本信号（`design.md` 风险 1）。若无，新增显式包版本常量，**不得**用 `count(*)` / `max(updated_at)`。
- [ ] D2 实现写入入口（如 `memory_rag.index_public_kinship`）：
  - `scope='public'`、`space_id IS NULL`、`confirmation_status='authorized'`、`author_account_id IS NULL`、`owner_user_id IS NULL`、`sensitivity='normal'`；
  - `source_id = "term-pack:<locale>"`、`source_revision = 包版本`；
  - 复用 `_canonical_document` / `_materialize_chunks` / FTS 写入，**不新增第二条索引路径**。
- [ ] D3 在**生产执行路径**上接线（seed 或称谓包版本升级命令），确保不是只在测试里被调用。
- [ ] D4 测试：
  - `scope='public'` 且 `space_id`/`author_account_id`/`owner_user_id` 为 NULL；
  - 正文不含任何用户姓名或账号 id；
  - 幂等（同 revision 重复调用返回同一 document）；
  - revision 冲突被拒；撤权后带过滤查询不再返回（反证：按 id 直读行仍在、`status='revoked'`）；`rebuild_index` 后结果一致；
  - 向量路径绑定参数齐全（去掉 `now` → 必须失败）。
- [ ] D5 assistant 侧端到端：`search_rag` 能召回 `public_kinship` 命中并生成合法 citation handle；`ContextBuilder` 纳入受 tier 份额约束。

**验收**：`scope='public'` 在生产查询中不再恒空。

## Phase E：steward 二维可读集（R4）

- [ ] E1 配置形状从 `scope` 集合改为 `(scope, source_type)` 集合；默认值显式枚举（新增类别默认不可读）。
- [ ] E2 有效集 = env 上界 ∩ 平台列 ∩ 空间列；空集 → 工具不广告、不注册进 run allowlist。
- [ ] E3 「配置不允许」返回 403（断言错误码），不得返回空列表。
- [ ] E4 升级路径：旧的一维配置值映射到已显式枚举的组合，**只能收窄不能放宽**；补一条迁移测试。
- [ ] E5 测试：
  - 默认配置下 steward 不可读 `public_kinship`；
  - 把 `public_kinship` 加入可读集后，`public` 分支仍因 `:is_assistant = 0` 不可读（边界不得被二维配置绕过）；
  - 平台配置加入、空间配置未加入 → 有效集为空、工具不广告；
  - 未知 `scope` / 未知 `source_type`：写入 422、读取忽略并告警。
- [ ] E6 回归 `test_steward_memory_tool.py` 与 6 个 steward 只读工具；`prompt_digest` / `input_hash` 稳定性测试保持全绿。

> 需要 attempt / fence / 身份拆分的接线依据时，按需读 `.trellis/spec/backend/steward-child-run.md`（44KB，超出注入上限，不进 `implement.jsonl`）。

**验收**：新增 source_type 默认不进入 steward 可读集，且有反证。

## Phase F：回归与收尾

- [ ] F1 `cd backend && ruff check . && ruff format --check . && mypy app && pytest`。
- [ ] F2 重点回归：`test_rag_acceptance_contract.py`、`test_rag_acceptance_bindings.py`、`test_rag_lifecycle_acceptance.py`、`test_memory_rag_service.py`、`test_context_tier_budget.py`、`test_memory_eval_baseline.py`、`test_steward_memory_tool.py`、`test_memory_source_migration.py`。
- [ ] F3 若 `agent/` 侧有任何改动（预期无），跑 `cd agent && npm run lint && npm run type-check && npm test`。
- [ ] F4 重新评测 `public_kinship` 接入后的分数，与 C3 基线对比落盘。
- [ ] F5 交付说明：写明未运行的高成本检查及原因、矩阵结论、以及**未实现**的三类来源（`family_story` / `authorized_document` / `profile`）各自的阻塞理由。
- [ ] F6 不在本任务实现 `authorized_document` / `family_story` 数据源；若实施中确认需要，另建子任务并在 PRD 的 `Notes` 记录。

## 回滚

- Phase B/C/E 都是**增量断言与配置形状**：回滚只需还原配置形状与移除断言，不影响既有检索行为（默认值收窄，回滚会放宽——因此回滚必须同时确认没有已写入的 `public_kinship` 文档依赖新配置）。
- Phase D 的 `public_kinship` 文档可通过把 `scope='public'` 的文档置 `status='revoked'` 关闭；**不删除**行，保留可审计性与重建能力。
- 关闭 `public_kinship` 后 `scope='public'` 回到恒空状态，与引入前逐字等价。

## 明确不在本任务范围

- `authorized_document` 的段落级授权记录与 token 语义（需要独立任务；本任务只登记 D6 的硬约束）。
- `family_story` 的写入功能（产品功能，尚不存在）。
- `profile` 的 RAG 索引（D1 明确排除）。
- 放宽 `_ELIGIBILITY_SQL` 的 `public` 分支对 steward 的排除（`design.md` 风险 2）。
- PostgreSQL / pgvector / PGroonga 的实际接线（属 `10-03-postgres-migration` 与 `10-04-lexical-search-migration`）。
