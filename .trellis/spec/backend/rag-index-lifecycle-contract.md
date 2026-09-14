# RAG 索引生命周期实现合同

2026-09-14，记录 D 的来源身份、内容证据、维护事务与迁移行为。本文用于追溯可执行代码，不替代根目录 AGENTS.md 或任务授权，也不改变历史 spec 索引的地位。Assistant 的执行身份与引用读取见 [执行合同](memory-rag-execution-contract.md)。

## 1. 范围与触发

适用于 Memory 索引补建、授权文档幂等写入、显式算法换版、FTS 修复和 0045/0047 迁移。来源能否继续物化由 `memory_sources.memory_materializable` / `_document_chain` 判断；当前读者能否读取由 A 的访问解析器判断。某位读者退出空间不会让全局来源永久失效。

本实现不接入 embedding、文档上传或 Steward RAG。旧来源缺少可信完整原文时保留数据并报告冲突/跳过，不把维护当作重新确认来源的入口。

## 2. 接口与持久字段

```python
index_memory(db, memory, *, target_version: str | None = None) -> RAGDocument
ensure_memory_index(db, memory, *, target_version: str | None = None) -> RAGDocument
run_maintenance_batch(db, *, worker_id: str, batch_size: int = 100) -> dict
stage_index_version(db, *, target_version: str, worker_id: str, batch_size: int = 100) -> dict
repair_fts(db) -> int
maintenance_status(db) -> dict
```

`ingest_authorized_document` 保持显式来源、revision、授权 scope 与文本的入口。`rebuild_index` 现在只调用 `repair_fts`；补建由维护入口承担。调用方负责短外层事务的 commit/rollback。

| 对象 | 约束与含义 |
|---|---|
| `rag_documents` | `(source_type, source_id, revision)` 唯一；`revision == source_revision`；`index_version` 是该文档的活动算法指针 |
| `content_sha256` | 完整 `Memory.content` 或授权文档输入的 SHA-256；不是 `raw_quote` 的来源 hash；NULL 表示尚无完整输入证据 |
| `rag_chunks` | `(document_id, index_version, chunk_index)` 唯一；已有 ID/text/revision/version 不原地改写，旧版本保留供精确引用读取 |
| `rag_index_maintenance_state` | 单例；owner/attempt/expiry/policy/target，加 Memory 与 document 各自的 cursor/upper/round |
| `rag_index_maintenance_failures` | memory_id、稳定错误码、retry_count、next_retry_at；不保存正文或来源标签 |

`MaintenanceLease` 为不可变值。每次 SQL CAS 比较其所有字段及未过期条件，不能从会自动刷新的 ORM 对象重新计算“期望值”。

## 3. 状态、参数与事务合同

`index_memory` 在真实 SQLite writer 内重新加载来源/配置，使用 `INSERT ON CONFLICT DO NOTHING` 取得规范文档，并核对完整 metadata。已有文档保持活动指针；`target_version` 只决定首次物化的算法。支持真实 v1（1200 字符固定块）与 v2（句段分块）；注册名必须对应实际算法，不能只改版本字符串。

完整正文摘要与所有已有块一致后，才补齐缺块和 FTS。旧 NULL 摘要只有完整、匹配的活动集合才能补证据；仅凭剩余块或 raw_quote 无法证明缺失部分。输入上限 120000 字符、256 块；维护读取已有集合最多 257 行，以发现溢出。

`invalidated/index_superseded` 的 Memory 投影可由 index/ensure 恢复，但须同时满足新鲜来源合法、metadata 一致和完整正文/块证据。验证后条件更新为 active，并清空 reason/invalidated_at，保留活动版本和原片段。恢复及 FTS 写入属于同一个 savepoint，最终开关/来源拒绝后一起回滚。此例外不开放给授权文档 ingest，也不改变 stage 对失效文档的跳过行为。`source_invalidated`、未知原因、deleted/revoked 和未验证来源均不恢复。

生产补建默认/最大每批 100 条，2 秒软时间预算（条目之间检查，另含最后一条和最终核验），lease 120 秒。每轮固定 `cursor < id <= upper`，Memory 与 document 分别维护水位；完成有限集合后重开下一轮。失败退避为 `60 * 2**min(retry_count, 6)` 秒，全轮扫描同样遵守 next_retry_at。

整个 RAG batch 使用 savepoint，普通坏来源另用单条 savepoint。失租、最终开关或来源检查失败时，物化/FTS/失败账本/游标全部回滚。真实 `run_maintenance_tick` 先提交 core，再用独立 RAG Session；RAG 失败显式 rollback，不能把部分批次提交，也不回滚已经完成的 Steward core。

RAG 部署 hard-off 与平台开关继续共同约束。部署允许 RAG 时维护循环可启动，平台晚开启后下一 tick 才补建；管理员开关 PUT 不扫描资料，也不调用模型。`maintenance_status.active_index_version` 是兼容字段，仍指部署算法；每份文档实际活动版本须读自身指针。状态只提供治理元数据，不构成家庭读者枚举全局来源的权限。

## 4. 校验与错误矩阵

| 条件 | 行为 |
|---|---|
| 同源同 revision 正文或 metadata 冲突 | 409 `RAG_SOURCE_NOT_ALLOWED`，原片段不变 |
| NULL 摘要且活动集合缺块、旧块改文/错 revision/状态 | 409，不能补签；batch 记录受退避约束的失败 |
| 已知 `index_superseded` + 合法 Memory + 完整可信证据 | 保留片段与活动版本，恢复 active；不重建引用定位 |
| 来源撤销/到期/unverified、source tombstone、未知原因 | 拒绝/跳过；不得通过 FTS repair 改业务状态 |
| `stage_index_version` 未注册目标算法 | 422 `RAG_SOURCE_NOT_ALLOWED`，未发生换版 |
| 旧 owner/attempt/expiry/policy/target/cursor/watermark | `MaintenanceLeaseLost`，整批回滚；不当作单条失败继续 |
| batch 开始时 RAG 关闭 | `{"skipped": "rag_disabled"}` |
| 最终 RAG 开关关闭 | `RAG_DISABLED`，整批回滚 |
| 非 Memory 缺完整原文 | stage 跳过，原合法活动投影保持可读 |
| 0047 重复规范来源或 revision 镜像冲突 | 首项 DDL 前报告元数据并拒绝，保留全部原行 |
| 0045 downgrade 存在多版本片段/旧键冲突/不可丢弃原因 | 首项破坏动作前拒绝，不删除旧块以强行降级 |
| 0047 downgrade 有正文证据或不兼容 target/policy | 拒绝丢弃证据，保留数据并前滚修复 |

0047 原地加列、建唯一索引与 revision 镜像触发器，不重建 `rag_documents`、不切换 FK。预检用精确零行 `UPDATE rag_documents SET id = id WHERE 0` 取得 writer；这不是数据修复。报告只含数量、document IDs、类型和版本，不输出正文、摘要或可能含人名的 source_id。

## 5. 正常、兼容与反例

- 正常：关闭 RAG 时确认的合法 Memory，在平台开启后由真实维护入口有限批次补齐；并发两个 Session 返回同一文档与片段。
- 兼容：明确 stage v1→v2 或回滚 v1 后，新搜索只读活动版本；已有 ExactChunkRef/保存依赖继续精确读取合法旧块，下一次普通补建不降级指针。
- 反例：原输入 `[A, B]` 丢失 B 后，同 revision 改成 `[A, C]`，不能仅因剩下的 A 匹配就补出 C。旧摘要冲突或缺少完整证明时保留原数据并拒绝。
- 反例：第三份文档 staging 冲突后调用方仍 commit，也不能留下前两份已切换指针、半成品块或新的 cursor。

## 6. 回归入口与断言

- `backend/tests/test_rag_lifecycle_acceptance.py`：真实独立 Session/同步点竞争、来源撤销、正文证据、完整性、所有 lease 字段、固定水位、恢复例外及最终拒绝零残留。
- `backend/tests/test_rag_index_lifecycle.py`：RAG-only、晚开启、换版/检索、保存依赖与 FTS 状态；测试合成算法必须显式注册。
- `backend/tests/test_rag_lifecycle_migrations.py`：实际 Alembic 连接 FK OFF/ON，真实 propose/confirm 保存依赖；比较 chunk ID/text/version、Memory/candidate、schema/version 和 FTS。拒绝必须发生在破坏操作前。
- `backend/tests/test_rag_acceptance_contract.py`、`test_rag_acceptance_bindings.py`：B 的精确引用与持续失效；生命周期改动须保留这些读取合同。
- `scripts/smoke/run_agent_memory_smoke.py`：隔离 listener、实际 Pi/HTTP、真实维护循环；仅模型流合成，不代表线上性能或真实 Provider 忠实度。

## 7. 错误做法与对应实现

错误：`active document` 查不到就把旧行设回 active；重建时删旧块；对 v2 切块仅标上 v3；用动态 ORM attempt 自比；捕获失租后继续提交整个 tick。

正确：以规范唯一键取得原文档并检查来源/内容证据，只允许已知的 Memory 索引替换状态恢复；已有块不可变；目标名对应真实算法；不可变 lease 进入条件 UPDATE；RAG 自有完整事务边界，最终失败零残留。
