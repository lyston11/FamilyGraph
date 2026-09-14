# Steward 证据版本与行为投影验证

## 环境与证据边界

- 用户于 2026-09-14 明确要求执行既有任务；启动基线 `0db88c2`，任务分支 `feat/09-13-steward-memory-evidence-projections`。
- 业务 worktree：`/Users/lyston/PycharmProjects/fg-09-13-steward-memory-evidence-projections`。仅共享 backend/.venv 依赖，`PYTHONPATH=.` 已验证导入本 worktree 的 app。
- pytest 使用真实 Alembic 迁移的独立临时 SQLite；模型仅 fake transport。未操作生产数据、模型服务或线上开关。
- 启动前主检出既有 52 个脏/未跟踪文件及 index 已记录哈希，原有工作不纳入本任务提交。渐进重算的共有服务/迁移在另一个 worktree 合并，独立测试可先准备，共有实现串行修改。

## MR-26 失败回归（修复前）

仅新增 `backend/tests/test_steward_behavior_rebuild.py`，真实调用 SourceFact、PFV、推荐 dismiss、推荐读取和行为重建。没有 mock 冷却生产者或通过直接改生产数据库构造结论。

工作目录为本任务 backend：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_behavior_rebuild.py --tb=short
.venv/bin/ruff check tests/test_steward_behavior_rebuild.py
.venv/bin/ruff format --check tests/test_steward_behavior_rebuild.py
```

初轮结果：4 failed / 4 passed，1.45 秒。失败均符合原缺陷：

- enabled/account 与 enabled/space：真实推荐冷却被删除，已忽略推荐重新出现。
- account/space 无匹配重放事件：其他键族被删除。
- disabled 两组合、有效事件账户/空间过滤与两次回放两组合通过。
- Ruff lint/format 通过，无夹具或迁移错误。后续补入大小写近似键反例，防止修复误用 SQLite 不区分大小写的 LIKE。

此处是修复前的失败证据，不能作为 MR-26 验收通过。

## 验收映射

| AC | 必须取得的证据 | 当前状态 |
| --- | --- | --- |
| SP-AC1 | 四组合、无事件、未知/近似键、其他账户/空间保全，所属语义幂等 | 12 项相关测试与独立复核通过 |
| SP-AC2 | 真实多作业相关证据换版，无关/无新事实和批次重试不重复 | 真实多作业、两 pending 版本、捕获 ID 与重试回归通过 |
| SP-AC3 | 当前空间授权、父母节点、撤销、原 revision、租约、unsupported 降级 | 定向反例通过；另已修复真实写锁等待后的过期写回 |
| SP-AC4 | 唯一约束/并发、来源与旧确认历史、原地无损迁移与拒绝破坏性降级 | 定向双执行者、原子回执及迁移回归通过 |
| SP-AC5 | 私人/共享驳回保留；新/旧/反向候选均无新增关系待办和推测边 | 两条真实主链及两个独立公开入口反例通过 |
| SP-AC6 | 最新迁移链、受影响服务及完整后端质量检查 | 单一 0049；全后端 1594/3 skipped，整理后 43；Ruff/format/mypy 和独立最终复核通过 |

MR-23 新证书的 `projected` 仅表示记录时点核验通过，不声明持续有效或正式亲属事实。首版没有个人证书 API，不以内部空间级范围替代查看者授权；旧 candidate 的首个 job CASCADE 仍是既有删除边界。

## 串行依赖解除与实施基线

2026-09-14 按用户要求等待渐进重算完成集成。`dee91a1` 已进入 main，相关 worktree 当时无未提交代码；本任务随后先同步 origin/main，再 fast-forward 到本地已集成的 main。业务代码实施基线为 `dee91a14e85ca69d4c39044c1da359ea46700612`，单一迁移 head 为 `0048_steward_terminology_publication`。再次确认 `PYTHONPATH=.` 导入本任务 worktree 的 app，两个任务 manifest 各 5 条均通过 validate。

既有任务文件先于 Context Curation adoption marker 创建，保留原审计位置与 manifest；新增合同按现行叶文档规则组织。SourceFact 没有 TTL，SP-AC3 的过期条件已明确为批次/交付租约过期，事实自身核验 state、原 revision 与作用域。

## MR-26 修复后验收

实施与独立检查均在任务 worktree 进行。修复限定删除谓词和重放前缀定义，原真实推荐夹具在集成后的 progressive 基线正常，无需 mock 或弱化断言。

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_behavior_rebuild.py tests/test_family_recommendations.py tests/test_steward.py::test_projection_roundtrip_and_whitelist tests/test_steward.py::test_kind_cooldown_roundtrip_and_blocks_card --tb=short
.venv/bin/ruff check app/services/steward.py tests/test_steward_behavior_rebuild.py
.venv/bin/ruff format --check app/services/steward.py tests/test_steward_behavior_rebuild.py
PYTHONPATH=. .venv/bin/python -m mypy app
git diff --check
```

结果：12 passed，1.86 秒；Ruff lint/format、mypy 204 source files、diff 检查均通过。独立检查再次确认两个删除分支及 caller transaction、真实生产/忽略/读取链、外部键 ID/值/更新时间和跨作用域保全；未发现问题，未改代码，静态检查通过。没有新代码变化或疑点，因此独立复核未重复 pytest。全套后端留待 MR-23 完成后统一验证。

## MR-23 与最终门禁（2026-09-15）

三个新增文件为 `test_steward_candidate_evidence.py`（22 项）、`test_steward_candidate_evidence_integration.py`（19 项）及 `test_steward_candidate_evidence_migration.py`（10 项）。连同 MR-26 的 8 项新增回归，本包新增 59 项；相对渐进重算最终基线，完整通过数由 1535 增至 1594。

| AC | 具体断言 |
| --- | --- |
| SP-AC2 | `test_real_jobs_version_only_related_support_and_never_create_public_work` 验证真实 core/assist/后续交付、完整相关配对换版、无关/无新事实与已应用批次不重发；`test_two_pending_versions_prepare_distinct_intents_and_both_finish_once` 和 captured-version 用例分别验证多 pending 与准备后新版本 |
| SP-AC3 | unit 中原支撑的 revision/state/结构/空间、父母与端点范围反例；integration 中真实响应后撤销/撤权/改版、失效 owner/attempt/lease/input；实际 SQLite 写锁等待跨过 lease 的失败回归修复 |
| SP-AC4 | 两个真实 `_apply_batch` 执行者，以及两个独立版本写入者；首次来源和旧快照不变；核验与 done 回执一起回滚再重试；数据库不可变 trigger/nullable FK；FK ON/OFF 原地升级保留全部旧行、无损 -1 往返和零 DDL 拒绝 |
| SP-AC5 | `test_existing_private_and_shared_dismissals_survive_real_evidence_adoption` 比较旧建议/recipient/通知/推测边完整行；初始完整证书主链始终无公开记录；旧已准备意图、直接调用两个公开入口、失去支撑后反向输出均不能绕过隔离 |
| SP-AC6 | 新迁移与原 RAG/Memory/0048 深降级回归、完整后端测试、全包静态检查、独立完整代码审查；原确认 SourceFact 及确认结果保留 |

实施者先在隔离库执行 upgrade head 到 0049，`foreign_key_check` 为空，再执行：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_steward_candidate_evidence.py \
  tests/test_steward_candidate_evidence_integration.py \
  tests/test_steward_candidate_evidence_migration.py \
  tests/test_steward_assist.py tests/test_steward_suggestions.py \
  tests/test_steward_inferred.py tests/test_steward_terminology_delivery_integration.py \
  tests/test_steward_terminology_publication_migration.py \
  tests/test_rag_lifecycle_migrations.py tests/test_memory_source_migration.py \
  tests/test_steward_delivery_recovery.py tests/test_steward_staged_pipeline.py --tb=short
PYTHONPATH=. .venv/bin/python -m mypy app
```

结果：188 passed，84.19 秒；mypy 205 个源文件通过。三个新测试中的旧数据使用真实迁移库；模型只替换 transport，主链没有手工伪造建议/推测边生产。

主线程随后在同一 worktree 的 backend 执行完整门禁：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q --tb=short --junitxml=<isolated-output>/backend.xml
.venv/bin/ruff check .
.venv/bin/ruff format --check .
PYTHONPATH=. .venv/bin/python -m alembic heads
```

完整测试 **1594 passed / 3 skipped / 4 warnings，156.36 秒，exit 0**。单一 head 为 `0049_steward_candidate_evidence`；format 394 文件通过。三项 skip 均是原 PRD 延期的系统管理员 break-glass 家庭数据能力；四条 warning 是既有 Python 3.12 SQLite datetime adapter 弃用提示。

首次全包 Ruff 发现六个既有 Steward 测试的 I001；待完整 pytest 结束后，只整理以下文件的 import 顺序，未改测试行为。之后全包 Ruff/format 全绿，并执行：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_personal_family_view_progressive.py tests/test_steward_generations.py \
  tests/test_steward_input_versions.py tests/test_steward_runtime_recovery.py \
  tests/test_steward_short_tx.py tests/test_steward_snapshot_fences.py --tb=short
```

结果：43 passed，7.42 秒。完整测试记录的 16 个任务后端文件（全部生产改动及原新增测试）逐字节未变；最终 22 个改动文件的 hash 见 [源码清单](evidence/backend-source-manifest.json)。此后没有新运行时变化或疑点，未重复全部 pytest。

独立最终 `trellis-check` 阅读全部 MR-26/MR-23 实现、迁移和三套新增测试，没有未修复的可操作缺陷；复核 mypy 205 文件及 diff 检查通过。两处历史风险的防回归合同分别写入 `steward-behavior-rebuild.md`、`steward-candidate-evidence.md`；原 Steward 规范的候选公开投影条款已指向新内部版本例外。

可核对的原件：[完整日志](evidence/backend-final.log)、[JUnit](evidence/backend-final.xml)、[导入整理后回归](evidence/import-regressions.log)、[结果及制品 hash](evidence/final-validation.json)。真实写锁红测及原因单独保留在 [租约调查](evidence/lease-writeback-investigation.md)。

## 交付限制

- 证书首版只支持 complete common biological_parent → direct_sibling；其他类型仍明确 unsupported，不伪造模型证明。
- 内部 projected 只代表历史核验，未提供个人证书 API；原 candidate→首次 job CASCADE 未改，也没有新 job GC。
- 未调用真实模型、操作生产数据/迁移、部署或更改环境/平台/空间开关。
- 本包无前端或公共 API 变更，未重跑两端构建、浏览器、生产 smoke 和 30/50/200 人容量实测。渐进重算的原冻结性能验收继续由其归档制品说明，不能冒充本包的新生产规模验收。
