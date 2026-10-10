# 实施计划：RAG 授权语料矩阵与四级可见性对齐

前置：本任务在 `10-03-multi-tenant-agent-concurrency-storage` 树下，**不依赖** PostgreSQL 迁移落地——D1–D6 的决策与 R2/R4 的断言都跑在现有 SQLite FTS5 路径上。R3 的 `public_kinship` 切片也走现有索引路径（PG 迁移落地后自动继承 PGroonga/pgvector 分支，无需本任务改动）。

## Phase A：矩阵权威化（无代码）

- [x] A1 在 `.trellis/spec/backend/memory-rag-execution-contract.md` 新增一节「授权语料矩阵」，写入 PRD 的矩阵表 + `scope` 与可见性阶梯的对应表 + `public` 的定义（不含个人数据）。
- [x] A2 写明 `profile` 的排除理由（D1 的三方案对比结论）与 D6 的硬约束（含敏感字段的自由文本不得进入 RAG）。
- [x] A3 标注 `SOURCE_TIER_FRACTIONS` 中哪几条是预留位（`family_story` / `authorized_document` / `profile`）。
- [x] A4 在 `backend/app/models/rag.py` 顶部注释指向该合同（`INDEXED_SOURCE_TYPES` 的 docstring），避免两处各说一套。

**验收**：矩阵逐格四要素齐全；评审时能只读该节回答「新增一个 source_type 要做什么」。

## Phase B：显式登记与断言（R2）

- [x] B1 新增 `INDEXED_SOURCE_TYPES` 与 `UNINDEXED_SOURCE_TYPES`（D3 的形状）。
- [x] B2 新增测试 `backend/tests/test_rag_source_type_registry.py`：
  - 两者互斥且并集等于 `RAG_SOURCE_TYPES`；
  - `SOURCE_TIER_FRACTIONS` 覆盖 `INDEXED_SOURCE_TYPES`；
  - 每个 `UNINDEXED_SOURCE_TYPES` 的理由非空。
- [x] B3 **反证**：`test_registry_detects_an_undeclared_source_type` 用未登记类别证明断言会失败（或直接断言「把 `profile` 从 UNINDEXED 移到 INDEXED 会失败」）证明断言不是同义反复。
- [x] B4 确认 `test_context_tier_budget.py` 既有断言未被削弱（未改动该文件）。

> 需要 CHECK 约束与 SQLite 整表重建成本的依据时，按需读 `.trellis/spec/backend/database-guidelines.md`（43KB，超出注入上限，不进 `implement.jsonl`）。

**验收**：新增/删除枚举值而不更新两侧 → 测试失败。

## Phase C：证明门扩展（R5，必须先于 D）

- [x] C1 公共称谓语料的用例加在 `test_memory_eval_baseline.py`（`PUBLIC_CORPUS_CASES`）而**不是** `golden_v1.json`——理由见下方「实施中的两处修正」。
  - `answerable`：一条可通过称谓文本召回的查询，期望来源指向某个称谓包；
  - `abstention`：库里不存在的称谓，必须返回空。
- [x] C2 未提升版本：`golden_v1.json` 与 `EVALUATOR_VERSION` 的语义（指标定义）未变，公共语料用例是**新增的独立层**而非 fixture 期望值改动。，并在提交信息里说明历史报告不可直接比较。
- [x] C3 接入前后基线均已落盘（`artifacts/memory-eval/baseline.json`）：contract 12/12、quality 3/3、recall 1.000、forbidden 0、citation_precision 0.5567、p50 4.16ms。**接入前后逐项相同**——公共语料没有污染记忆检索，这一点由 `test_public_corpus_does_not_pollute_memory_retrieval` 直接断言。（此时 `public_kinship` 用例应因无数据而失败或落在 gap 层）。
- [x] C4 tier 份额由既有 `test_context_tier_budget.py` 覆盖（结构性保证：任何单类别不可能占满预算、空类别不借份额）；本任务未削弱它。

**验收**：接入前基线已落盘，接入后有可比对比分。

## Phase D：`public_kinship` 纵向切片（R3）

- [x] D1 确认 `term_entries` **没有**可用版本信号（无 pack version 列）→ 新增 `public_kinship_revision()`，由包内容 sha256 派生 31 位正整数。未用 `count(*)`/`max(updated_at)`。
- [x] D2 实现 `index_public_kinship` / `index_public_kinship_packs`：
  - `scope='public'`、`space_id IS NULL`、`confirmation_status='authorized'`、`author_account_id IS NULL`、`owner_user_id IS NULL`、`sensitivity='normal'`；
  - `source_id = "term-pack:<locale>"`、`source_revision = 包版本`；
  - 复用 `_canonical_document` / `_materialize_chunks` / FTS 写入，**不新增第二条索引路径**。
- [x] D3 生产接线两处：`terms.seed_builtin_packs`（迁移 0012/0041 与启动种子都走它）+ 维护循环 `ensure_public_kinship_packs` 一次性补建（既有安装迁移早已跑完、种子不会重跑）。由 `test_maintenance_backfills_public_kinship_exactly_once` 断言。
- [x] D4 测试（`tests/test_rag_public_kinship.py`，12 条）：
  - `scope='public'` 且 `space_id`/`author_account_id`/`owner_user_id` 为 NULL；
  - 正文不含任何用户姓名或账号 id；
  - 幂等（同 revision 重复调用返回同一 document）；
  - revision 冲突被拒；撤权后带过滤查询不再返回（反证：按 id 直读行仍在、`status='revoked'`）；`rebuild_index` 后结果一致；
  - 向量路径绑定参数齐全（去掉 `now` → 必须失败）。
- [x] D5 assistant 侧端到端：`test_assistant_recalls_public_kinship_with_a_citation` 断言命中与 `rag:term-pack:...` 句柄。

**验收**：`scope='public'` 在生产查询中不再恒空。

## Phase E：steward 二维可读集（R4）

- [x] E1 `STEWARD_READABLE_SOURCE_TYPES: tuple[str, ...] = ("memory",)`；`steward_tools._search_memory` 显式引用它而不是写死类别。
- [x] E2 既有交集语义与空集语义保持不变（未改动 `effective_scopes`/`readable_scopes`）。
- [x] E3 既有行为已正确（`STEWARD_MEMORY_SCOPE_DENIED` 403），既有测试覆盖。
- [x] E4 **不需要迁移**：一维 `scope` 配置的存储与语义未变，类别维度是新增的**代码常量**而非新配置列。旧配置值映射到的类别集恒为 `("memory",)`——即只能收窄不能放宽，且这一点由 E1 的默认值直接保证。
- [x] E5 测试（`tests/test_steward_memory_tool.py` 新增 3 条）：
  - 默认配置下 steward 不可读 `public_kinship`；
  - 把 `public_kinship` 加入可读集后，`public` 分支仍因 `:is_assistant = 0` 不可读（边界不得被二维配置绕过）；
  - 平台配置加入、空间配置未加入 → 有效集为空、工具不广告；
  - 未知 `scope` / 未知 `source_type`：写入 422、读取忽略并告警。
- [x] E6 `test_steward_memory_tool.py`、`test_steward_memory_scopes.py`、`test_steward_tools.py` 全绿；全量套件 2397 passed 覆盖 steward 与 digest 稳定性。

> 需要 attempt / fence / 身份拆分的接线依据时，按需读 `.trellis/spec/backend/steward-child-run.md`（44KB，超出注入上限，不进 `implement.jsonl`）。

**验收**：新增 source_type 默认不进入 steward 可读集，且有反证。

## Phase F：回归与收尾

- [x] F1 `mypy app` → Success（228 files）；`pytest` → **2397 passed, 38 skipped**。
- [x] F2 重点回归全绿（含 `test_rag_lifecycle_acceptance.py`、`test_memory_eval_baseline.py`、`test_context_tier_budget.py`、`test_memory_supersede.py`、`test_terms.py`）。`test_rag_acceptance_contract.py`、`test_rag_acceptance_bindings.py`、`test_rag_lifecycle_acceptance.py`、`test_memory_rag_service.py`、`test_context_tier_budget.py`、`test_memory_eval_baseline.py`、`test_steward_memory_tool.py`、`test_memory_source_migration.py`。
- [x] F3 `agent/` 无改动（未触碰 sidecar 工具声明——steward 记忆工具的输入 schema 未变），故未运行。
- [x] F4 已落盘并对比，见 C3。
- [x] F5 交付说明见任务 `implement.md` 末尾与提交信息。写明未运行的高成本检查及原因、矩阵结论、以及**未实现**的三类来源（`family_story` / `authorized_document` / `profile`）各自的阻塞理由。
- [x] F6 未实现；`UNINDEXED_SOURCE_TYPES` 的每条理由即是待办入口。

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

## 实施中的两处修正（规划与实际的差异）

### 1. 公共语料用例放在 `test_memory_eval_baseline.py`，而不是 `golden_v1.json`

规划 C1 写的是「在 `golden_v1.json` 增加 `public_kinship` 用例」。实施时发现这会**破坏
弃答门的语义**：

`golden_v1.json` 的 `abstain-unknown-person` 是「小舅妈的手机号码是多少？」，
期望整条返回空。接入公共语料后它命中了 `term-pack:zh-CN`——因为「舅妈」**确实**是公共
语料里的一个词条。这不是缺陷：**任何关于亲属的问题都含称谓词**，因此公共语料必然与
记忆弃答用例字面重叠。把这条算作弃答失败，等于要求「公共知识库不得包含任何亲属称谓」。

两种做法：

- 放宽 `abstain-unknown-person` 的期望（加 `allowed_sources`）→ **拒绝了**。它把
  「弃答必须是绝对语义」这条不变量削弱成「除称谓词以外必须弃答」，而弃答门正是
  P0 建立的、不允许余量的合同层断言。
- **把两种语料分开度量** → 采用。记忆用例限定 `source_types=("memory",)`，因此
  `expect_empty` 的绝对语义完全不变；公共语料有自己的用例与门。

分开度量还带来一个**更强**的断言：`test_public_corpus_does_not_pollute_memory_retrieval`
在「有/无公共语料」两种状态下跑完整记忆 golden set，要求逐字相同。公共语料与个人记忆
共用同一个 FTS 表与同一个重排，所以「新增语料污染个人记忆检索」是完全可能的失败形态，
而它在没有公共语料时无法被观测。

### 2. steward 二维可读集不需要迁移

规划 E1/E4 预期「配置形状从一维升二维 + 旧值映射迁移测试」。实施时确认：**类别维度是
代码常量，不是配置列**。一维 `scope` 配置的存储与语义未变，旧值映射到的类别集恒为
`("memory",)`，因此「只能收窄不能放宽」由默认值直接保证，无需迁移。

这是更好的形状：放宽管家的读取面成为一次**代码改动 + 评审**，而不是管理员界面上的一次
点击。E1 的反证断言（三层配置放开全部 scope，`public_kinship` 仍不可读）证明它生效。

## 明确未做的（交付边界）

- `authorized_document` 数据源：需要段落级授权记录（D6 硬约束），未实现。
- `family_story` 数据源：依赖尚不存在的写入功能，未实现。
- `profile` 的 RAG 索引：D1 明确排除，改走 `get_profile_summary` 结构化投影。
- 放宽 `_ELIGIBILITY_SQL` 的 `public` 分支对 steward 的排除：未触碰。
- PostgreSQL / pgvector / PGroonga 接线：属 `10-03-postgres-migration` 与
  `10-04-lexical-search-migration`；本任务的公共语料自动继承这两条路径（走同一物化与
  同一 `_ELIGIBILITY_SQL`）。

## 未运行的高成本检查

- `frontend` / `system-admin-frontend` 的 lint/type-check/test/build：本任务未改动任何
  前端文件（`CitationList.vue` 的五类中文标签已存在且未变）。
- `agent/` 的 lint/type-check/test：未改动 sidecar（steward 记忆工具的输入 schema 与
  `TOOL_VERSIONS` 均未变）。
- `scripts/frontend-api-smoke.sh`：需要真实 listener 环境；本任务改动是后端服务层与
  spec，未改 API 契约或前端消费形状。
