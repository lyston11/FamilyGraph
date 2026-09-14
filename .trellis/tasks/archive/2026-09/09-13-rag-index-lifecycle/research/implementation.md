# D 实施与验证记录

日期：2026-09-14。基线：merge 继承 A（d1f43a5）+ C（2baf7a8/470b362）+ B（078f2e3…56bb895）链（merge 提交 9add87b）。分支 `feat/09-13-rag-index-lifecycle`，worktree `/Users/lyston/PycharmProjects/fg-09-13-rag-index-lifecycle`。

## 改动边界

- 迁移 `0045_rag_index_lifecycle`（合并 D 基线中既有的 0044_rag_citation_contract 与 0044_steward_terminology 双头，单一 head）：
  - `rag_documents.invalidation_reason`：既有 invalidated 行统一回填 `source_invalidated`（历史 tombstone 路径从未产生 index supersede）。
  - chunk 唯一键改为 `(document_id, index_version, chunk_index)`；旧 `(document_id, chunk_index)` 索引删除。downgrade 先删除非活动版本行再恢复旧键。
  - 新表 `rag_index_maintenance_state`（单行游标/轮次/lease attempt/水位）与 `rag_index_maintenance_failures`（memory_id + 稳定错误码 + 重试计数 + next_retry_at，无正文）。
- `app/models/rag.py`：RAGDocument.invalidation_reason；chunk 唯一键；两个维护模型。
- `app/services/memory_rag.py`：
  - MR-25 修复：`index_memory` 不再把非 active 文档无条件设回 active——`invalidation_reason != 'index_superseded'` 一律 409 RAG_SOURCE_NOT_ALLOWED；`index_superseded` 复活仍需 memory_materializable。
  - `_materialize_chunks` 版本域化（只 upsert 当前 document.index_version 的行）。
  - 检索 eligibility 增加 `c.index_version = d.index_version`（活动指针裁定可召回性，staging 块不外露）；RAGHit.index_version 一贯取实际行值。
  - `rebuild_index` 拆分：新 `repair_fts` 只重建 FTS 搜索投影（不触碰 Memory/document 状态、confirmation、tombstone）；`rebuild_index` 保留为兼容别名（仅 FTS repair）。原「物化所有缺失记忆」分支移入维护职责。
  - 新 `ensure_memory_index`：幂等物化（已有完整投影即返回，chunk ID 不变）。
- `app/services/rag_maintenance.py`（新增）：
  - `run_maintenance_batch`：单调 Memory ID 有界全轮巡检（默认 100/批、上限 500），每条 savepoint 隔离；失败登记（指数退避，60s×2^n，next_retry_at 内不重试）；游标按最后扫描 ID 与物化/失败登记同事务推进；扫尾不足一批即轮完成、cursor 归零进入下一轮（覆盖后来重新合法的低 ID）。批次开始与提交前都重估有效 RAG 开关（deployment AND platform DB），关闭返回 `{"skipped": "rag_disabled"}`。
  - 持久 lease 栅栏（owner + expires + attempt 条件更新）：`MaintenanceLeaseLost` 拒绝其他执行者；`_renew_lease` 校验 expected_attempt，过期执行者不能推进/回写游标或切版本。
  - `stage_index_version`：为每个 active 合法 document 物化目标版本 chunks 后按条件更新原子切换活动指针；旧版本 chunks 保留（历史引用精确原片段）；失效文档不切换。
  - `maintenance_status`：仅输出 policy_version/round/cursor/last_success/has_failures/active_index_version，无正文无密钥。
- `app/services/maintenance.py`：`config.RAG_ENABLED`（部署允许）即启动维护循环（RAG-only 场景不依赖 AGENT_RUNTIME/STEWARD）；tick 内执行一个补建批次，counters 增加 rag_index_scanned/materialized/failed；失败只记类名日志，不影响 core tick。
- `tests/conftest.py`：清理表清单补两个新表。
- `tests/test_rag_index_lifecycle.py`（13 条）：MR-25 复活阻断（index/repair/维护三轮）、同源同版本重复与 chunk ID 稳定、唯一约束下的并发物化、legacy/unverified 不索引且原记录保留、撤销来源不被多轮维护复活（raw_quote 保留）、关闭→保存→开启→有界补齐（D-AC1，含开关关闭跳过）、失败退避与非阻塞、lease 栅栏（他人拒绝/过期接管）、过期执行者游标不可回写、FTS repair 不改业务状态、活动版本裁定检索、换版 staging/切换/旧版保留/失效不切、安全状态投影无正文。

## 检查结果（本 worktree）

- 迁移：隔离临时库 `alembic upgrade head`（单头 0045）+ `downgrade 0044_*@head` 往返通过。
- `ruff check .` / `ruff format --check .` / `mypy app` 通过。
- `pytest -q`：1138 passed / 3 skipped（含新增 13 条；memory_source_migration 的 NEW_HEAD 断言更新为 0045，downgrade 守卫断言保持停在 0042）。

## 边界与限制

- 未做真实线上数据规模/延迟测量；批次参数（100/500、退避基数）为设计默认值，待生产校准。
- 管理端索引进度页面未建（与 admin 任务的所有权协调归集成通道）；观测仅服务层 counters/maintenance_status。
- 物理擦除、外部向量库、文档导入、Steward RAG 均未接入（PRD 边界不变）。
- `stage_index_version` 的换版触发（何时 bump RAG_INDEX_VERSION）留待明确的算法升级决定；当前唯一活动版本为 `fts5-trigram-v2`。

## 2026-09-14 独立复查更新（待修复）

以上历史命令结果保留；其中关于“水位、完整投影、提交前开关重估、条件lease更新、并发唯一性、降级安全”的完成描述与累计 bd899b9 实测不一致。详见 [独立复查](../../09-14-memory-rag-acceptance-audit/research/d-integration-check.md)：20个场景17失败/3通过，归并D-I01～10。

双Session实际产生同源重复document，旧Session能回退cursor 3→1，真实tick吞失租后仍commit物化；stage无有效开关/worker栅栏、撤销后仍写块，合成换版后命中1→0并被下一批改回旧版。缺块/FTS被误报current，持续新增使低ID恢复饿死，FTS repair漏来源合法性。0045 downgrade在真实保存RAG依赖下删除原chunk，再upgrade仍不可用，不能把空库往返通过当数据保留证明。

RAG-only晚开启补齐、未知tombstone不复活、读者临时失权不全局失效等正例继续成立。本轮仅记录分析与 [修复方案](../../09-14-memory-rag-acceptance-audit/prd.md)，未改 D 生产代码或迁移；任务保持待修，原D-AC2/4/5/6/8不能维持整体通过。
