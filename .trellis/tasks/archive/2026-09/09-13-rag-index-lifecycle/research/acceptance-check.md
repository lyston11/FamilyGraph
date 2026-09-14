# D 修复独立核验（2026-09-14）

审阅目录：`/Users/lyston/PycharmProjects/fg-09-13-rag-index-lifecycle`。基线 `c253cb6` 加本轮冻结的 D 改动，迁移 head 为 `0047_rag_lifecycle_integrity`。业务代码、仓库测试和原历史探针均未修改；仓库中仅写入本文。新合成探针保存在 `/private/tmp/fg-d-review-eNCyDK/`。

**最新判定：最终复核通过，D 本轮审阅范围内没有剩余实质阻断项。** 主线程实施者已修复下面保留的 `index_superseded` 恢复回归；最终独立探针 **15 passed / 1 deselected**，相关 lint、format 和 type-check 再次通过。原 D-I01～10 的修复路径与下述实现、独立组合测试及主线程迁移 oracle 证据相符；主线程负责累计全套和最终集成门禁。

首次冻结检查点的结论是“不能给 D 整体验收通过”：当时合法来源的已知 `index_superseded` 投影被排除在恢复入口之外，正式合同选择为 **2 failed / 10 passed / 1 deselected**。这两个失败属于同一问题的直接 ensure 和真实维护入口。以下保留该检查点的发现、定位和红测事实；它们已由末尾的最终复核闭环，不代表当前仍未解决。

## Findings (fixed)

本核验者未修改业务代码。主线程实施者完成下列窄修，本核验者独立复查并验证：

- File：`backend/app/services/memory_rag.py:742`、`:921`。
- Issue：已知 `invalidated/index_superseded` 的合法 Memory 投影在完整证据仍在时也无法恢复。
- Fix：仅新鲜来源已验证的 Memory index/ensure 可允许这一状态；完整 metadata、正文摘要和块集合验证后，用条件 UPDATE 恢复 active，清空 reason/invalidated_at，保留活动版本和原片段。ingest 的默认检查仍严格，stage 仍跳过失效文档。恢复、补块、FTS 与最后有效开关检查位于同一 savepoint。

## Findings (not fixed)

无。下面的 P2 为首次冻结检查的历史发现，已修复并独立复验通过。

## 首次冻结发现（历史，现已解决）

### P2：已知 index_superseded 的合法投影失去恢复入口

- 位置：`backend/app/services/memory_rag.py:742` 的 `_check_document_metadata` 无条件拒绝非 active 文档或任何 `invalidation_reason`；`:778` 的 `_canonical_document` 在物化前调用它。`index_memory:909`、`ensure_memory_index:1641` 均没有原有的已知 supersede 恢复分支。`rag_maintenance.py:423` 的 stage 也直接跳过非 active 文档。
- 旧合同：D `design.md:19` 区分来源永久失效与算法 supersede；原 `research/implementation.md:13` 明确 `index_superseded` 在 `memory_materializable` 成立时可恢复。`0045_rag_index_lifecycle.py:8` 的说明仍称该标记可由升级流程恢复。旧 `c253cb6` 的 `index_memory:701–715` 实际实现了这条窄分支。新 `acceptance-implementation.md` 未声明取消此行为，也没有替代处置入口。
- 最小实测：经真实 `propose_candidate` / `confirm_candidate` 保存 manual Memory，保持来源 verified、active、confirmed，完整块集合、正文摘要与元数据一致；仅将投影设为 `status='invalidated', invalidation_reason='index_superseded'` 并提交。探针先断言 `memory_materializable=True` 且 `content_sha256 == hash(Memory.content)`。
- 结果：`ensure_memory_index` 返回 409 `RAG_SOURCE_NOT_ALLOWED`；`run_maintenance_batch` 返回 `materialized=0, failed=1`；stage 返回 `flipped=0, skipped=1`。未做数据破坏，但此已知状态无法恢复检索，维护将持续进入失败退避。状态由隔离探针构造，未断言生产数据库实际已有此类行。
- 证据：`test_d_independent.py:89` 的 `test_known_index_superseded_restore[ensure|batch]`，完整结果 `contract-review.log` / `contract-review.xml`。首轮另试 stage 恢复作为替代路径；旧 stage 本来也只接 active 文档，因此该断言不另算既有行为回归，正式合同回归选择中排除它，原探针与首轮日志保留。
- 建议：保留仅针对已知 `index_superseded` 的恢复路径，在实际 writer 中重取来源、配置与文档，并验证完整元数据和可信正文/块证据；任何冲突仍整体回滚。不得因此放行 `source_invalidated`、未知/空失效原因、来源 revoked/unverified、同 revision 改文或缺证据的旧不完整投影。若决定取消旧允许行为，需要明确合同与存量处置决定，不能以现有绿色套件替代该判断。

## 原缺口修复核对

本表区分源码核验、独立实测与主线程提供的外部证据，不将实施者测试计数重复记作本核验者执行。表中的行号对应首次冻结代码；窄修后的定位与源码 hash 见最终复核。

| 问题 / 验收映射 | 实现与证据 |
|---|---|
| D-I01 / F-07 | `models/rag.py:61` 为 canonical 唯一键；两个写入口共用 `_canonical_document` 的冲突后重取与 metadata 比较；0047 原地加列/唯一索引/镜像触发器，DDL 前检查冲突。读取并核对了两个真实 Session 竞争的仓库回归形态。主线程另报强化迁移 oracle 8/8，与同 seed 旧 B 的 v2 0/8 对照；该 oracle 由主线程执行。 |
| D-I02 / F-07、F-09 | `_materialize_chunks:833` 将完整 `Memory.content` 摘要与 raw_quote 来源 hash 分开；检查全部目标块位置、文本、revision、状态与集合大小，仅补缺块。独立实测：已有完整目标块也不能替代原活动集合缺失的 legacy 证据；第三条 target 冲突会回滚此前成功的两条换版。 |
| D-I03、D-I04、D-I05 / F-08 | 冻结 `MaintenanceLease` 绑定 attempt/owner/expiry/policy/target/两组轮次游标水位；获取、续租、游标、指针均有 SQL 条件更新。writer 在 savepoint 前建立真实事务，维护 tick 的 core 与 RAG 使用独立提交边界。独立实测：第三条物化后直接 SQL 修改持久 policy，batch 与 stage 的真实栅栏均拒绝，外层调用者随后 commit 仍无任何投影/状态残留。源码与仓库测试另覆盖真实 tick 最终开关/来源/失租和单条失租。 |
| D-I06 / F-09 | 注册实际 v1/v2 算法，stage 不接受任意标签；搜索按 document 活动指针，ensure 保持已有活动版本；非 Memory 无完整原文时跳过换版并保留旧检索。独立组合实测真实 B context → D stage(v1) → FTS repair → maintenance → 原 context 重放 → 接受回答 → 公开引用详情，旧 v2 原片段及句柄不变，新搜索只读 v1。 |
| D-I07、D-I09 / F-10 | 完整性检查涵盖所有预期块和对应 FTS 行；两个固定轮次水位将新到达记录留到下一轮。独立实测低 ID 一次普通失败，在持续新记录到达时仍遵守 retry_at，期限到后有限轮次恢复并删除失败记录；保存旧 ID 的正常缺块恢复仓库回归与实现相符。 |
| D-I08 / F-11 | 0045 旧 downgrade 首先执行无改行 writer 获取，再做非破坏兼容性检查；删除历史块的旧语句已移除。0047 不重建父表、不开关 FK，拒绝丢弃已存在摘要或未来策略。FK ON/OFF、持久保存依赖、实际唯一/镜像约束和旧降级入口由主线程强化 oracle 8/8 提供独立迁移证据；本核验者未重复该矩阵。 |
| D-I10 / F-10 | `repair_fts:1606` 调用 A 的全局 `_document_chain`，保留 Memory/confirmation/document 状态。独立实测同一旧精确引用与已保存 Memory 经换版、repair、会员资格移除/恢复后按当前读者授权变化；后台 repair 不把单个读者失权写成全局 tombstone。 |

D-AC2/4/5/6/8 原有关键缺口有上述修复证据；首次冻结检查中新增的恢复行为回归影响 D-R2/D-R5 及合法旧投影的可恢复性，当时不能仅据表格将 D 全部勾选为完成。最终窄修已解除这一阻断。

## Verification（首次冻结检查，历史）

- Lint：**pass（本核验者执行）**。D 的模型、3 个服务、0045/0047 迁移和 3 个 lifecycle 测试文件，`ruff check --no-cache` 通过；同 9 个文件 `ruff format --check --no-cache` 通过。
- TypeCheck：**pass（本核验者执行）**。`PYTHONPATH=tests:. .venv/bin/mypy --cache-dir /private/tmp/fg-d-review-eNCyDK/mypy-cache app/models/rag.py app/services/memory_rag.py app/services/rag_maintenance.py app/services/maintenance.py`，4 个直接目标文件通过。
- Tests：**fail**。正式合同选择为 2 failed / 10 passed / 1 deselected，1.77s。失败项仅上面的同一恢复回归。10 个通过项包括 3 种禁止 tombstone 恢复、延后 target 冲突整批回滚、batch/stage SQL 最终策略栅栏、退避与低 ID 进度、历史精确依赖与读者权限、legacy 缺证据阻断及真实 B/D 引用链组合。
- 首轮探索：3 failed / 9 passed，1.48s；额外真实 B/D 组合：1 passed，1.50s。首轮 stage 恢复断言的范围校正在上文说明，未改写原输出或将排除项冒充通过。
- 所有测试由该 worktree `tests/conftest.py` 为进程创建新临时 DATA_DIR，以真实 Alembic 建空库后造合成数据。每个进程明确打印 `app.__file__=/Users/lyston/PycharmProjects/fg-09-13-rag-index-lifecycle/backend/app/__init__.py`。未接触现有数据库、模型网络或开发端口。
- 未重复完整 backend 套件、完整迁移 oracle、前端/sidecar build、listener smoke 或生产规模测试；主线程负责累计质量门禁，本核验聚焦 D 的独立语义证据。实现记录的 239/119 测试计数不是本核验者执行结果。

复现正式合同选择（从目标 worktree 的 backend 执行）：

```bash
PYTHONPATH=tests:. .venv/bin/python - <<'PY'
import sys
sys.path.insert(0, '/Users/lyston/PycharmProjects/fg-09-13-rag-index-lifecycle/backend/tests')
import conftest, app, pytest
print('APP_IMPORT', app.__file__, flush=True)
raise SystemExit(pytest.main([
    '-p', 'conftest', '-p', 'no:cacheprovider', '-q', '--tb=short',
    '/private/tmp/fg-d-review-eNCyDK/test_d_independent.py',
    '/private/tmp/fg-d-review-eNCyDK/test_d_composed.py',
    '-k', 'not (known_index_superseded_restore and stage)',
]))
PY
```

探针 SHA-256：

- `test_d_independent.py`：`3a6e7431f017c17b05b5b71128c26800981d6eec8dc1460a319e030909a1f6ad`
- `test_d_composed.py`：`d8383eae4eacc4bddeee1d641029db9d34db618cf8b7a5e20f2e87411f71cadd`

本轮核验时核心源码 SHA-256：

- `memory_rag.py`：`a4965c86b9db1c901d863a09e140db18b7bd0b212be1a33958dc6aeb1592b116`
- `rag_maintenance.py`：`21ea2fa15d8eace3f390854f5ec0b0f983aa53e765fa01d688dda797bc80ac3a`
- `0047_rag_lifecycle_integrity.py`：`f7f399ec1da7a4fcb064c5ba49b23490c72900d7734aafd354de2a291ee68b81`

首次检查未改动 `.trellis/spec/` 历史资料。主线程随后新增的可执行实现记录在下面另行核对；它不替代任务 PRD/design 或原授权。

## 最终窄修复核

检查 `memory_rag._check_document_metadata` 和 `index_memory` 的新差异：默认检查没有放宽；恢复例外同时要求 source_type=memory、status=invalidated、reason=index_superseded。调用前后仍在真实 writer 中刷新来源和配置；先通过原 `_materialize_chunks` 的摘要/集合证据，再按旧状态、两份 revision、活动版本、摘要执行条件 UPDATE 并检查 rowcount。不是按原因字符串直接恢复，也没有改写原块或转移活动版本。

原正式合同选择的 12 项全部通过，另外新增 3 项独立边界检查，合计 **15 passed / 1 deselected，2.05s**：

- 原先失败的 direct ensure 与真实 batch 均恢复合法投影，并保留旧块 ID/文本/版本。
- stage 对已知 superseded 仍返回 `scanned=1, skipped=1, flipped=0`，不改 Memory/document/chunk/FTS。旧首轮 `stage` 恢复探索断言仍排除；没有把 stage 改成新恢复入口或伪称该断言通过。
- 恢复一个被已保存 Memory 依赖的根投影后，同一维护批次修复两份 FTS；保存的原 Memory/candidate 行及精确根片段保持不变，无需重新确认。
- 根 Memory 已通过真实 `revoke_memory` 撤销时，即使派生 Memory 自身仍 active/verified 且其投影标为 index_superseded，恢复仍返回 409，外层 commit 后无任何残留。
- 原 10 项通过控制和真实 B/D context/citation 组合在新代码上再次通过。

最终 lint/format：仅对本次追加变化的 `models/rag.py`、`memory_rag.py`、0045 注释和 acceptance 测试共 4 文件重跑，均通过；其余 D 文件沿用首次已通过结果。最终 type-check：原 4 个 D 直接目标再次通过。没有为了补一个局部修复而重复完整后端、迁移 oracle 或 frontend 套件。

主线程新增 `.trellis/spec/backend/rag-index-lifecycle-contract.md` 及 backend 索引条目；已逐节抽查。它准确记录 canonical 唯一性、完整输入证据、不可变 lease 与固定水位、三类索引职责、有限恢复例外、迁移首写前拒绝和 B 精确引用；明确 stage 仍跳过失效文档、active_index_version 兼容字段代表部署算法、真实延迟未由合成测试证明。没有发现需要再次修改的合同漂移。

最终复现命令沿用上面的入口，额外加入 `/private/tmp/fg-d-review-eNCyDK/test_d_recovery_boundary.py`，保持同一 `-k 'not (known_index_superseded_restore and stage)'` 选择。

| 制品精确路径 | SHA-256 |
|---|---|
| `/private/tmp/fg-d-review-eNCyDK/test_d_independent.py` | `3a6e7431f017c17b05b5b71128c26800981d6eec8dc1460a319e030909a1f6ad` |
| `/private/tmp/fg-d-review-eNCyDK/test_d_composed.py` | `d8383eae4eacc4bddeee1d641029db9d34db618cf8b7a5e20f2e87411f71cadd` |
| `/private/tmp/fg-d-review-eNCyDK/test_d_recovery_boundary.py` | `2e61da51c2c71f4c5d56c275ed8c98cda2d44d93c9a9f8d87070e345270052b1` |
| `/private/tmp/fg-d-review-eNCyDK/contract-review.log`（首次正式红测） | `1002636da9ec4d057bbe56174c4982d105153bb4a460d4df57715f3d3d87069d` |
| `/private/tmp/fg-d-review-eNCyDK/final-review.log`（最终绿测） | `fbffc814dbde202e8ec183804f7836ee85575b5167ba385100ba48acb3ab7443` |

完整 11 个原始探针、日志和 JUnit XML 的校验清单位于 `/private/tmp/fg-d-review-eNCyDK/review-artifacts.sha256`，包括未改写的首轮探索 `first-pass.log/xml`、单独 B/D 组合 `composed.log/xml` 和正式红/绿两次结果。

最终 `backend/app/services/memory_rag.py` SHA-256 为 `62691b270b0c065c5df6ae283178dc825d2eee97f4c010835c507c1147c6a8e4`；`rag_maintenance.py` 与 0047 源码 hash 与首次冻结记录相同。0045 和 model 后续只有恢复语义的注释同步。最终累计迁移 oracle 和集成检查由主线程基于其最终制品继续记录。
