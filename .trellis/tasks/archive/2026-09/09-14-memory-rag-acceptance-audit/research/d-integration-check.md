# D 索引生命周期集成独立核验

日期：2026-09-14。审阅代码：`bd899b98871c02ad97b7791a051f126d671bfefb`，目录 `/private/tmp/familygraph-memory-rag/09-13-agent-memory-rag-remediation`。业务文件在核验期间未修改；只新增本文和 `/private/tmp/familygraph-d-integration/` 中的合成探针。未 commit、merge 或执行 task 生命周期命令。

结论：D 尚不满足验收。20 个独立合同探针中 **17 个失败、3 个通过**，归并为下面 10 项问题。失败断言来自已批准的 D design/PRD；不是把已有测试通过结果改称失败，也不代表线上已发生事故。D-AC2/4/5/6/8 不能维持整体通过结论。真实 RAG-only 晚开启、未知 tombstone 阻断、读者失权/来源撤销区分已有正面证据。

后续分析规划由主线程新建的 `.trellis/tasks/09-14-memory-rag-acceptance-audit` 承接，保持 planning；本文是原已授权核验的证据交接，不启动 B/D 业务修复。

| 本报告问题 | 审计发现映射 | D 验收项 | 父验收项 |
|---|---|---|---|
| D-I01 document 唯一性/迁移预检 | MR-25、MR-24 | D-AC2、D-AC8 | AC-06、AC-10 |
| D-I02 chunk 不可变性/目标版本冲突 | MR-25 | D-AC2、D-AC6 | AC-06 |
| D-I03 持久 lease/round/policy 栅栏 | MR-13、MR-25 | D-AC4、D-AC5 | AC-06 |
| D-I04 失租异常后仍提交 | MR-13、MR-25、MR-24 | D-AC4、D-AC5 | AC-06、AC-10 |
| D-I05 换版与批次提交前重验 | MR-13、MR-15、MR-25 | D-AC4、D-AC5、D-AC7 | AC-03、AC-06、AC-08 |
| D-I06 换版/检索/维护不一致 | MR-13、MR-25 | D-AC2、D-AC5、D-AC6 | AC-06 |
| D-I07 缺失投影无法恢复 | MR-13 | D-AC1、D-AC5、D-AC6 | AC-06 |
| D-I08 降级删除持久来源依赖 | MR-17、MR-25 | D-AC6、D-AC8 | AC-03、AC-06 |
| D-I09 有限全轮/低 ID 饥饿 | MR-13 | D-AC1、D-AC5 | AC-06 |
| D-I10 FTS repair 来源合法性 | MR-13、MR-25 | D-AC3、D-AC6 | AC-03、AC-06 |

## Findings (fixed)

无业务修复。派发限定只读业务代码；下面均涉及持久身份、事务、迁移或版本合同，交由主线程选择并串行修复。

## Findings (not fixed)

### D-I01 / P1：document 身份仍非唯一；迁移不阻断旧重复和 revision 镜像冲突

- 合同：D `design.md:5,23,68` 要求 `(source_type, source_id, revision)` 唯一、危险存量组先报告并阻断、检查 `revision/source_revision` 镜像。
- 代码：`backend/app/models/rag.py:60` 的 `ix_rag_documents_source` 没有 `unique=True`；`backend/migrations/versions/0014_memory_rag.py:206` 创建普通索引；`0045_rag_index_lifecycle.py:33` 的 upgrade 只增加 reason、chunk 版本键及维护表。检索了整个 `backend/migrations/`，没有补 document 唯一键或镜像预检的其他迁移。
- `memory_rag.index_memory`（`backend/app/services/memory_rag.py:671`）先 SELECT，再在 `:698` INSERT，没有数据库唯一约束裁决这段竞争。
- 最小复现：RAG off 时确认一条 Memory，再开启；两个真实 `SessionLocal` 在线程同步点都完成“没有 document”的 SELECT 后继续 `index_memory` 并 commit。结果两个调用返回 IDs `[2, 1]`，相同 source/revision 保存 **2 行 document**。
- 迁移反例：在真实两个 0044 head 上插入两个相同来源/revision document，再插入 `revision=2/source_revision=9` 的第三行，执行 `alembic upgrade head`。结果 **exit 0**，三行原样保留，`PRAGMA index_list('rag_documents')` 的两个索引均 `unique=0`；没有阻断或报告。
- 探针：`test_two_real_sessions_cannot_create_duplicate_documents`（探针文件 `:82`）、`test_migration_blocks_duplicate_and_revision_conflict`（`:367`）。
- 建议：先实现不含正文的预检与非破坏阻断，再建立模型/迁移一致的唯一约束及镜像校验；将并发冲突转换为读取同一投影或明确内容冲突。不能对已存在重复组静默删重。

### D-I02 / P1：同一 chunk ID/版本可被覆盖；冲突的 staging 内容也能直接成为活动版本

- 合同：D `design.md:5,23,25` 要求同版本内容不变、冲突稳定报错、新版完整验证后才能切换。
- `memory_rag._materialize_chunks`（`backend/app/services/memory_rag.py:765`）直接执行 `row.text = chunk_text_value`，在 `:768` 把版本写为模块常量，并可重新激活该行；没有不可变文本/hash 检查。
- 最小复现：确认并索引一条记忆，保留 revision=1、原 chunk ID=1，仅给同 revision 提供不同摘要后再次调用 `index_memory`。结果无 409，chunk 仍为 ID=1、revision=1、`fts5-trigram-v2`，文本从 `orchidgrove synthetic original` 改为 `orchidgrove conflicting replacement without a new revision`。
- `rag_maintenance.stage_index_version`（`backend/app/services/rag_maintenance.py:303`）只收集已有目标版本的 `chunk_index`，在 `:313` 直接跳过；不核验 text/hash、source_revision、status、缺块/多块。
- 最小复现：预放一个目标 v3 的 index=0 冲突文本，再 stage v3。结果无错误，document 切到 v3，活动 chunk 文本仍是预放的冲突内容。
- 探针：`test_same_revision_conflicting_text_preserves_old_chunk`（`:119`）、`test_stage_rejects_conflicting_preexisting_target_chunks`（`:248`）。
- 建议：同 source/revision/index_version 的已有内容只允许一致重放；冲突时保留原片段并失败。换版先核验完整目标集合，失败保留旧活动指针。

### D-I03 / P1：lease、round、policy 没有数据库事务栅栏；旧会话能抢走新 lease 并回退游标

- 合同：D `design.md:39,40` 要求持久 attempt/round/policy 条件更新，不得由旧 worker 回退游标/活动版本。
- `_acquire_lease`（`backend/app/services/rag_maintenance.py:93`）读取 ORM 对象属性后普通 flush；不是带当前 owner/expiry/attempt 的条件 UPDATE。
- `_renew_lease`（`:108`）的 `db.get` 可以返回同一 identity-map 对象，只比较该对象的 attempt，不校验数据库当前 owner、expiry、round、policy。后续 `:225`/`:226` 又直接赋 round/cursor。
- 最小复现：旧 Session 先加载空 lease/cursor=0；另一个真实 Session 跑 batch_size=3 并 commit，数据库变成 `(cursor=3, attempt=1, owner=current)`；旧 Session 恢复运行 batch_size=1 并 commit。结果没有 `MaintenanceLeaseLost`，数据库变成 **`(cursor=1, attempt=1, owner=stale)`**。
- policy 反例：已有 state 为 `future-policy-v99/round=99`，当前代码为 `rag-index-maint-v1`。调用真实 batch 后仍物化一条、round 变成 100，未拒绝旧策略执行。
- 探针：`test_stale_session_cannot_overwrite_new_lease_cursor`（`:137`）、`test_old_policy_cannot_write_future_policy_state`（`:477`）。
- 建议：数据库条件写同时绑定 attempt、owner、有效 lease、round、policy 和目标版本；检查 rowcount，失配必须回滚该批次的全部物化/失败登记/游标。SQLAlchemy 对象缓存不构成栅栏。

### D-I04 / P1：真实 maintenance tick 吞掉最终栅栏异常后仍 commit，批次原子性不成立

- 合同：D `design.md:39,40`；物化/失败登记/游标必须一起提交，失租执行不能写回。
- `backend/app/services/maintenance.py:109` 调用 batch，`:116` 捕获异常仅记日志，随后无 rollback/savepoint 回滚便在 `:121` 执行 `db.commit()`。
- 最小复现：RAG off 保存后开启，通过真实 `run_maintenance_tick` 执行；仅把最后 `_renew_lease` 同步点替换成 `MaintenanceLeaseLost`。结果日志记录了失租，但新 document 已持久化，cursor 仍为 0，返回的全部 RAG counters 也是 0。
- 该测试替换的仅是最终栅栏拒绝结果，建库、物化、真实 tick 和 commit 均使用生产代码。它验证异常路径，未声称已在线上制造 lease 竞争。
- 探针：`test_real_tick_rolls_back_batch_when_final_fence_rejects`（`:166`）。
- 建议：将 RAG 批次放在独立事务/适当 savepoint 中；捕获错误之前回滚其全部写入，同时保持 Steward/core 的事务职责清楚。只增加 finally 中的 lease 检查不能修复此提交路径。

### D-I05 / P1：换版没有开关/worker/来源提交前校验；普通 batch 的最终开关复核也缺失

- 合同：D `design.md:25,40,42,48,52`。
- `stage_index_version`（`backend/app/services/rag_maintenance.py:259`）不调用 effective RAG 或 lease 校验，`:340` 明确 `del worker_id`；`:331` 的 UPDATE 只约束 document ID/旧 index_version，没有 status/revision/策略/lease 条件。
- 最小复现：已有合法索引，持有者 `legitimate` 拿到未过期 lease，平台 RAG 设为 false；`intruder` 调 stage。结果 **`staged=1/flipped=1`**，活动指针已切 v3，未被拒绝。
- 真实撤销竞争：stage 完成 `memory_materializable` 校验后，在同步点用另一个真实 Session 调 `revoke_memory` 并 commit，再让 stage 继续。结果 document 最终虽保持 `invalidated`，但版本仍从 v2 改为 v3，新增 **active v3 chunk ID=2**，返回 flipped=1。检索的 document status 门禁仍拦住该内容；这里证明的是失效后写入/换版没有被阻断，不是声称对外泄露。
- 普通 `run_maintenance_batch` 仅 `:164` 检查有效开关；`:219` 到返回没有再次检查。受控最终检查点把平台开关设为 false 后，batch 仍 commit `materialized=1`。此开关探针是同事务同步点注入，未冒充两个 admin 请求在 SQLite 写锁内同时提交。
- 探针：`test_stage_respects_effective_off_and_another_lease`（`:187`）、`test_batch_rechecks_effective_flag_at_final_checkpoint`（`:453`）、`test_stage_rechecks_source_after_concurrent_revocation`。
- 建议：换版复用同一持久 lease/policy 栅栏，切换与新块写入前在事务中重验当前来源版本/状态及 effective flag。失败需与 D-I04 一起保证回滚。

### D-I06 / P1：换版入口与实际读取/维护不兼容，新版无法正常接管且被下一批次改回旧版

- 合同：D `design.md:25,27,40` 要求可恢复的版本切换、新搜索读活动版本、旧 worker 不得回退版本。
- `stage_index_version`（`backend/app/services/rag_maintenance.py:272`）拒绝 `target_version == memory_rag.RAG_INDEX_VERSION`，恰好拒绝部署算法常量升级后的目标版本。
- 允许切到其他目标时，搜索 SQL `backend/app/services/memory_rag.py:852,1000` 仍额外要求 document 版本等于进程常量。
- 随后 `ensure_memory_index`（`:1452`）遇到不同版本调用 `index_memory`，后者在 `:716` 无条件把 active document 指针写回自身的 `RAG_INDEX_VERSION`。
- 最小复现：一条 v2 索引原本命中 1 次；stage v3 返回 flipped=1，v2/v3 chunks 均确实存在且版本各自正确，但新搜索变成 **0 命中**；下一维护 batch 把活动版本 **v3→v2**。另将进程常量模拟升级到 v3 再调用 stage(v3)，直接收到 **422**。
- 探针：`test_stage_switch_is_searchable_and_not_undone_by_next_batch`（`:212`）、`test_current_algorithm_version_can_be_upgrade_target`（`:234`）。
- 建议：明确受策略栅栏保护的目标版本合同，统一换版、搜索、维护三处版本语义；普通 ensure 不得自行改变已有合法活动指针。保留旧块并不能单独保证新版本可用。

### D-I07 / P2：完整投影被误判为“至少一个块”，验证恢复后缺失 FTS/块不会补齐

- 合同：D `design.md:29` 明确来源验证恢复后下一全轮检查 FTS/活动投影完整性。
- `ensure_memory_index`（`backend/app/services/memory_rag.py:1453`）只 SELECT 一个 active chunk ID，`:1462` 就返回；没有检查预期完整块集合或对应 FTS 行。
- 最小复现 1：已索引来源设为 unverified 并移除该 FTS 行，维护正常跳过；随后恢复 verified 再跑完整一轮。返回 `already_current=1`，FTS 未补回，搜索依然 **0 命中**。
- 最小复现 2：合法三块来源缺一块，维护后依然只有 **2/3 块**，返回 `already_current=1`。
- 探针：`test_maintenance_restores_missing_fts_after_source_verification`（`:269`）、`test_maintenance_restores_incomplete_active_chunk_set`（`:288`）。
- 建议：检测目标投影的完整性并按明确修复职责恢复缺失项；已有块 ID/text/hash 保持不可变，无法安全恢复的项应报告失败而非记为 current。

### D-I08 / P1：0045 downgrade 会无检查删除仍被已保存 Memory 引用的旧块

- 合同：D `design.md:27,68,70`、父 `implement.md` 的回滚段要求保留旧来源/引用/块；不可逆降级另立审阅计划。
- `backend/migrations/versions/0045_rag_index_lifecycle.py:83` 先删除维护表，`:88` 无任何引用检查执行 `DELETE FROM rag_chunks WHERE index_version != (document current version)`，然后恢复旧唯一键。
- 最小复现：通过真实 Memory service 保存一条 rag_chunk 副本；stage v3 后其原 v2 chunk 仍可精确授权读取。执行隔离 `alembic downgrade 0044_rag_citation_contract`，再 upgrade head。
- 结果：命令成功；原 chunk ID=1 **消失**，saved Memory 的引用 JSON 仍存在；其 `memory_access` 从可读变为 **unavailable**。重新升级不能恢复被删除的文本/ID。
- 探针：`test_downgrade_preserves_saved_rag_dependency`（`:392`）。
- 建议：在任何 DROP/DELETE 前非破坏检查并阻断不能保持旧键/引用的数据状态；需要清理时由独立获准计划处理。不能把“空库 downgrade 往返成功”当保存引用安全证明。

### D-I09 / P2：没有每轮固定扫描上界，持续新增会饿死后来恢复的低 ID

- 合同：D `design.md:36,37` 要求每轮固定水位并重新覆盖低 ID。
- `RAGIndexMaintenanceState`（`backend/app/models/rag.py:138`）没有扫描上界字段；batch 查询（`backend/app/services/rag_maintenance.py:169`）只有 `id > cursor`，直到 `:215` 扫描量小于 batch_size 才结束一轮。
- 最小复现：初始只有两个 Memory，先以 batch_size=1 跳过 unverified 的低 ID=1，再恢复它；之后每个 tick 之前新增一条 Memory。连续五个 tick 的 round 都为 **0**，低 ID 仍没有 document。持续到达速率不低于扫描速率时，该轮永远不结束。
- 探针：`test_full_round_does_not_starve_low_ids_under_new_arrivals`（`:430`）。
- 建议：持久化每轮开始时的扫描上界，原子推进/结束该有限集合；新到达的高 ID 放下一轮，同时保留失败 next_retry_at。

### D-I10 / P2：FTS repair 的“合法来源”过滤没有调用来源生命周期规则

- 合同：D `design.md:56` 要求只从当前合法投影修复 FTS，不改变来源状态。
- `repair_fts`（`backend/app/services/memory_rag.py:1414`）仅检查 chunk/document status 和版本相等，不检查 Memory 的 verification、retention 或根来源依赖。
- 最小复现：把已索引 Memory 设为 unverified，运行 repair。结果报告 rebuilt=1，FTS 中仍有 **1 行**该来源；真实 search 由于另有授权过滤仍为 **0 命中**。
- 探针：`test_fts_repair_uses_source_legality`（`:499`）。
- 建议：repair 复用不依赖某位读者的来源合法性，不写 Memory/document 状态。本项是索引合同与计数问题，未观察到检索越权。

## Verification

- Lint：**pass**。目标 backend `.venv/bin/ruff check --no-cache .`；`.venv/bin/ruff format --check --no-cache .`（343 files）。
- TypeCheck：**pass**。`PYTHONPATH=.:tests .venv/bin/mypy --cache-dir /private/tmp/familygraph-d-integration-mypy app`（190 source files）。
- Tests：**fail**。独立合同探针分三次执行，共 20 条、17 failed / 3 passed；3.57s + 1.21s + 1.18s。每次测试进程由当前 checkout 的 `tests/conftest.py` 创建全新临时 DATA_DIR，并通过真实 Alembic 建库。无主库、真实模型、线上服务或现有开发端口参与。
- `app.__file__` 已明确核对为 `/private/tmp/familygraph-memory-rag/09-13-agent-memory-rag-remediation/backend/app/__init__.py`。共享 editable install 未覆盖本次应用导入。
- 未重复已有 1138 全套、前端/sidecar build 或真实 listener smoke；本检查范围是 D，父线程承担其余集成验证。探针中的 stage 版本均为合成目标，不声称已有生产 v3 算法。

探针与完整输出：

- `/private/tmp/familygraph-d-integration/test_d_contract_probes.py`
- `/private/tmp/familygraph-d-integration/probe-results.txt`（首轮 14 条）
- `/private/tmp/familygraph-d-integration/additional-probe-results.txt`（新增 5 条）
- `/private/tmp/familygraph-d-integration/revocation-probe-results.txt`（真实来源撤销竞争 1 条）

重跑入口（从目标 worktree 的 backend 执行；显式加载 tests 插件，避开根目录 conftest shim）：

```bash
PYTHONPATH=.:tests .venv/bin/python - <<'PY'
import sys
sys.path.insert(0, '/private/tmp/familygraph-memory-rag/09-13-agent-memory-rag-remediation/backend/tests')
import pytest
raise SystemExit(pytest.main([
    '-p', 'conftest', '-p', 'no:cacheprovider', '-q', '-s', '--tb=short',
    '/private/tmp/familygraph-d-integration/test_d_contract_probes.py',
]))
PY
```

## 已通过的边界与已有测试缺口

- 真正启动 `start_maintenance_loop`，关闭 Agent/Steward、保留部署 RAG 能力，第一 tick 平台关闭时不索引；平台开启后的下一 tick 自动物化 1 条。该正例覆盖“循环真实可调度”，没有用直接 rebuild 代替。
- 来源 Memory 仍合法但 document 为未知原因 invalidated 时，直接 index/ensure 被拒绝；后续维护保持 invalidated，不自动标记 index_superseded。
- 从共享来源保存 private 副本后，移除该读者会员资格只阻止该读者读取，不会使来源全局失效；恢复会员资格后可再次读取。unverified 返回单独状态；根来源永久 invalidate 后副本不可读，原 quote 保留。
- 已保存旧版本引用在 stage 后、downgrade 前仍可读，说明 A resolver 没有强制旧块等于活动算法版本；损坏来自 D 的降级删除。
- `backend/tests/test_rag_index_lifecycle.py:136` 所谓 concurrent materialization 实际是同一个 Session 的两次顺序 ensure；`:354` 的 stale executor 测试仅人为传入 `state.attempt + 5`，没有另一 Session 更新 lease 后旧对象恢复的同步点；`:416` 换版测试只检查指针/旧块存在，没有验证换版后检索及下一 tick。这解释了为何已有绿色结果未发现上述缺陷。

## 文档与交接

`09-13-rag-index-lifecycle/research/implementation.md:10,17,19,20,25` 把“水位、完整投影、提交前开关重估、条件 lease 更新、并发物化”写成已实现/验证，其证据与本轮实测不一致。`:29` 的 downgrade 往返通过仅说明命令执行成功，不能证明持久引用安全。修复前应撤回相关整体验收勾选，保留上轮检查结果作为当时已执行命令记录。

当前 `.trellis/spec/guides/index.md` 和 `.trellis/spec/backend/index.md` 均声明为历史资料；本轮采用父 contracts 与 D design/PRD 为约束，未修改这些文档。修复收敛后应同步当前任务实现记录和相关现行合同，而不是用历史规范覆盖本轮授权。
