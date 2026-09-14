# D 修复实施记录（2026-09-14）

工作区：`/Users/lyston/PycharmProjects/fg-09-13-rag-index-lifecycle`，分支 `feat/09-13-rag-index-lifecycle`，实施起点 `c253cb6`。开始时业务文件干净，真实唯一 Alembic head 为 `0046_context_execution_contract`。本记录是本轮新增证据；不覆盖初版 `research/implementation.md`、独立核验的 17 failed / 3 passed 或冻结探针。

## 实现与接口

| 缺口 | 本轮实现及主要位置 |
|---|---|
| D-I01 | `models/rag.py` 将 `(source_type, source_id, revision)` 设为数据库唯一键；`memory_rag._canonical_document`（:752）对两个入口共用 `INSERT ON CONFLICT DO NOTHING`，随后核对完整 metadata。一致重放返回原 document/chunk；不同正文、scope/sensitivity/作者/visibility key 等冲突，不覆盖。 |
| D-I02 | `RAGDocument.content_sha256` 记录完整索引输入 `Memory.content` 或授权文档输入的 SHA-256；与 `raw_quote` 的来源 hash 分离。`_materialize_chunks`（:833）验证所有位置、正文、revision、version、status 和集合大小，只新增缺失块；从不改写已有块字段。旧 NULL 摘要必须有完整一致的活动块集合才能建立证据，缺块时拒绝补签。 |
| D-I03/I05 | `rag_maintenance.MaintenanceLease`（:41）为不可变值，绑定 attempt、owner、expiry、policy、target、两个游标/水位及轮次。`_acquire_lease`（:94）、`_cas_state`（:134）、`_renew_lease`（:154）都执行 SQL 条件 UPDATE 并检查 rowcount。未来 policy/未知算法 target 被拒绝；授权不依赖 ORM 缓存。 |
| D-I04 | `run_maintenance_batch`（:272）先取得实际 SQLite writer，再以 savepoint 包住整个 RAG 批次，单条坏来源另有内部 savepoint。`MaintenanceLeaseLost` 不进入失败账本；最终续租、开关、所有成功来源/活动指针/摘要重验失败时整体回滚。`maintenance.py:106` 在 core 提交后用独立 RAG Session，RAG 异常显式 rollback，计数仅在成功提交后发布。 |
| D-I06 | `INDEX_CHUNKERS`（`memory_rag.py:674`）只注册真实 v1（1200 字符固定切块）和 B 当前 v2。`stage_index_version`（`rag_maintenance.py:372`）允许当前部署算法及显式回滚，使用相同 lease/policy/target CAS；全部目标块验证后条件切换指针。普通 index/ensure 保持已有活动指针；FTS/短词搜索按 document 活动指针读取，不要求等于模块常量。没有完整原文的非 Memory 来源跳过换版，旧合法活动版本仍可检索。 |
| D-I07/I10 | `_memory_projection_complete`（:887）检查完整预期集合及每个 FTS 行；可证实正文时恢复缺块/FTS，保留原块 ID/内容。`repair_fts`（:1606）复用 A 的全局 `_document_chain`，排除 unverified、到期及失效根依赖，按实际活动版本重建；不改变 Memory/confirmation/document/tombstone，读者会员资格不参与全局失效。 |
| D-I08 | 0045 的旧 downgrade 在任何 DROP/DELETE 前检查历史版本块、旧键冲突和不可丢弃的失效原因，报告保存依赖计数并拒绝不能无损退回的库；原删除历史块语句已移除。0047 有同样的 writer 内预检，不允许丢弃已建立的正文证据或未来 target/policy。 |
| D-I09 | Memory 补建与 document 换版分别持久化固定水位和游标。每轮只扫描 `cursor < id <= upper`，有界集合完成后重置，之后到达的数据进入下一轮。失败仍遵守 `next_retry_at`，无忙重试。 |

保留接口：`index_memory` / `ensure_memory_index` 返回 `RAGDocument`；新增可选 `target_version` 仅决定首次创建目标，已有活动指针不变。`stage_index_version` 新增可选 `batch_size`，返回安全扫描/切换/跳过计数。状态查询增加 target 与两组水位/轮次，不输出正文、人物名或 source_id 标签。旧 `active_index_version` 状态字段仍表示部署算法，不能据此推断所有 document 已完成换版；实际每条活动版本以 document 指针为准。

## 迁移

新增 `0047_rag_lifecycle_integrity`，down revision 为 0046。字段：document `content_sha256`；维护状态 `upper_memory_id`、`cursor_document_id`、`upper_document_id`、`stage_round`、`target_index_version`。已知 v1 维护策略前进到 v2，并重置旧无水位的游标/租约；未知未来策略不覆盖。

预检只报告 document IDs、来源类型、revision、重复/镜像冲突数量、未知 tombstone 和保存依赖计数；不打印可含人类标签的 `source_id`、正文、摘要或密钥。存在重复或 revision/source_revision 冲突时，在首项 DDL 前拒绝。没有自动删重、解除来源隔离或为旧数据补摘要。

原地加列/唯一索引；SQLite revision 镜像通过 INSERT/UPDATE 触发器实现，语义与 ORM 的 CHECK 一致。没有 parent table rebuild、chunk 复制或 FK 开关修改；`migrations/env.py` 未变更。

## 持久回归与参数

新增 `test_rag_lifecycle_acceptance.py` 和 `test_rag_lifecycle_migrations.py`。保留并更新 `test_rag_index_lifecycle.py` 中测试算法的显式注册/安全状态字段；将原同 Session 顺序重放测试改为准确命名，真实竞争另由新增测试覆盖。主线程另行授权 `test_steward_suggestion_quality.py` 的单行 import 分组调整，未改测试行为。

- 两个真实 Sessions 在线程同步点共同尝试 Memory/授权文档创建，返回同一 document 与 chunk IDs；直接 SQL 也不能破坏唯一性/镜像。
- 真实旧 Session 缓存遇到另一 Session 新 lease/cursor；旧 token 对 round、两个水位/游标、owner、policy、target 的 CAS 拒绝；真实过期换租后旧 worker 不能续租。
- 在旧 Memory 已读取而 writer 尚未取得的同步点，另一真实 Session 撤销并提交；index/batch/stage 均重读来源，不能新增或切换已撤销投影。
- 真实 `run_maintenance_tick` 覆盖 final lease、final flag、final source、单条 lease 四种拒绝：doc/chunk/FTS/失败账本/游标全部无残留，真实 Steward job 仍成功。final flag/source 是同事务受控最终检查点，未声称 SQLite writer 中另一个管理请求可同时提交。
- 原 `[A,B]` 缺 B 后同 revision 正文变 `[A,C]` 被旧摘要拒绝；raw_quote 与 content 故意不同，防止错用来源 hash。无摘要且缺块的旧投影不补签；可信摘要下缺块和 FTS 可恢复。
- 真实 v1→当前 v2→显式 v1 回滚，以及测试注入的不同 v3 切分，均检查检索、完整集合、旧保存依赖精确可读和下一批不降级。冲突/额外/错误状态/错误 revision 的 staging 全批拒绝，合法部分 staging 可补齐。
- 连续新高 ID 到达时，两类固定水位都能完成并重访低 ID；真实 RAG-only 后台循环能在平台晚开启的下一 tick 补建。
- 迁移矩阵 FK OFF/ON 均通过全局 Engine connect listener 设置在 `env.py` 实際使用的 Alembic 连接，并在其 RAG SQL 执行时再次断言 PRAGMA。比较全部 chunk IDs/text/version、FTS、Memory/candidate 原行和文档旧字段；验证真 `propose_candidate` / `confirm_candidate` 保存的 Memory 来源依赖。拒绝路径断言 DDL 计数为 0 和 schema/version/原行不变。

默认与最大批量均为 100；批次扫描使用 2 秒软预算（每条间检查，另含最后一条及最终核验），lease 120 秒。每份输入最多 120000 字符、256 块，维护读取已有集合最多 257 行用于识别溢出。显式全量 FTS repair 独立于生产有界补建。失败按 60 秒基数指数退避，指数封顶为 6；各全轮扫描仍尊重该时间。未测线上规模或真实延迟。

## 已执行检查

所有 Python 命令从该 worktree 的 `backend` 执行；已实测 `PYTHONPATH=. .venv/bin/python` 的 `app.__file__` 指向本 worktree，避免共享 editable 环境误导。

1. 新建独立 TemporaryDirectory/DATA_DIR，执行 `.venv/bin/python -m alembic upgrade head`：通过，head 为 0047，预检空库报告已输出；未接触主库。
2. 首轮既有 lifecycle/service/maintenance：37 passed，2 failed。两处是旧测试未注册合成算法和状态字段断言，已按真实合同更新。
3. 首轮新增 acceptance + 既有 lifecycle：56 passed，5 failed。四处新增 fixture 未在真实 Steward enqueue 前 commit，一处使用了不存在的 invalidate_document 名称；修正为真实入口及事务准备。
4. 新增 lifecycle acceptance + migration：59 passed，17.64s。
5. A/B/D 相关 14 文件：239 passed，30.27s。命令：

```bash
PYTHONPATH=tests:. .venv/bin/python -m pytest -q \
  tests/test_memory_rag_models.py tests/test_memory_rag_service.py \
  tests/test_memory_api_contract.py tests/test_memory_source_migration.py \
  tests/test_rag_retrieval_citations.py tests/test_rag_query_context.py \
  tests/test_rag_acceptance_contract.py tests/test_rag_acceptance_bindings.py \
  tests/test_platform_features.py tests/test_maintenance.py \
  tests/test_context_execution_migration.py tests/test_rag_index_lifecycle.py \
  tests/test_rag_lifecycle_acceptance.py tests/test_rag_lifecycle_migrations.py --tb=short
```

6. 随后补充已有损坏块集合读取上限和 downgrade 预检 writer，回跑 lifecycle/acceptance/migrations/source migration/获准机械排序的 Steward 测试：119 passed，24.94s。
7. `.venv/bin/ruff check .`：通过（初次发现的既有 Steward I001 已按主线程明确授权机械修复）。`.venv/bin/ruff format --check .`：357 files 通过。`PYTHONPATH=tests:. .venv/bin/mypy --cache-dir /private/tmp/familygraph-d-acceptance-mypy app`：193 files 通过。`git diff --check`：通过。

## 交接与限制

实现者没有 commit/merge/push、task 生命周期操作或主检出写入；主线程维护的其他 research/验收材料未回退。当前实现/迁移冻结，交主线程独立审查、使用旧 B seed 的迁移 oracle 和全 backend 集成检查。

未重复原冻结探针、未修改其源码/历史日志；其任意合成目标应由新算法注册合同解释，不能当作生产 v3 能力。未运行前端/sidecar 构建或 listener smoke，因本次写入仅在后端，父线程承担累计 A/B/C 的集成验收。没有主库/线上开关/真实 Provider/物理清理操作。合法但无法证实完整原文的旧投影保留并保守报告失败/跳过，所需人工来源处理仍须独立审阅。

## 独立审查后补修：已知 index_superseded 恢复（2026-09-14）

独立探针发现本轮 canonical metadata 核对过严：来源 Memory 仍全局有效、正文和投影可验证时，原先受支持的 `invalidated/index_superseded` document 被 ensure/后台补建当作冲突拒绝。修复前新增的 6 个正例全部失败，确认这是恢复合同的回归。

本次有限写入仅涉及 `backend/app/services/memory_rag.py`、`backend/tests/test_rag_lifecycle_acceptance.py` 及本实施记录。`_check_document_metadata` / `_canonical_document` 默认继续严格拒绝失效状态；只有 Memory 索引入口在获得真实 SQLite writer、重新读取并验证来源后，才能显式允许精确的 `invalidated/index_superseded` 状态。恢复必须先验证完整输入摘要、canonical metadata、revision 镜像及完整块集合，再重新核对来源和 document；最后用带状态、原因、Memory 类型、revision、原活动指针和摘要条件的 SQL UPDATE 激活，并清空 `invalidation_reason` / `invalidated_at`。保留原活动版本及已有 document/chunk IDs，不覆盖已有块内容。末尾开关核验仍处在同一 savepoint 内，失败时状态恢复、补块和 FTS 一起回滚。

可信完整输入摘要允许恢复缺块；旧 NULL 摘要只在已有完整且一致的块集合可证明原文时恢复。未知/来源失效原因、deleted/revoked 状态、无效 Memory、同 revision 正文漂移、NULL 摘要缺块、冲突块或 metadata 不匹配仍被拒绝。授权非 Memory ingest 不获得该例外。父线程明确不要求 stage 恢复；stage 路径与 lease、迁移行为均未改动。

新增回归覆盖 ensure/后台 batch 的三种合法恢复（完整可信摘要、完整 NULL 摘要旧投影、可信摘要缺块），实际使用部署 v2 下保留的 v1 活动指针，检查旧精确引用、FTS、重复调用稳定性和 Memory/candidate 原行。负例包括 final flag 拒绝后的整体回滚，以及另一真实 Session 在 writer 获得前撤销来源，确保旧 ORM 缓存不能恢复该投影。

补修验证结果：

1. 修复前 6 个新增正例：6 failed。
2. `PYTHONPATH=tests:. .venv/bin/python -m pytest -q tests/test_rag_lifecycle_acceptance.py -k index_superseded --tb=short`：24 passed、48 deselected，1.56s。
3. 六个相关完整文件：174 passed，6.73s。

```bash
PYTHONPATH=tests:. .venv/bin/python -m pytest -q \
  tests/test_rag_lifecycle_acceptance.py tests/test_rag_index_lifecycle.py \
  tests/test_memory_rag_service.py tests/test_rag_retrieval_citations.py \
  tests/test_rag_acceptance_contract.py tests/test_rag_acceptance_bindings.py --tb=short
```

4. 独立审查探针原文件 `/private/tmp/fg-d-review-eNCyDK/test_d_independent.py`：11 passed、1 deselected，2.34s。唯一排除项为父线程明确不要求的 stage 恢复正例，选择表达式为 `not (known_index_superseded_restore and stage)`；其余 stage 安全性探针仍执行。未修改探针及既有 `first-pass.log` / `first-pass.xml`。首次外部探针收集因 backend 根目录的 conftest shim 遮蔽真实 fixtures 而失败；改用 runner 将 `backend/tests` 前置并显式加载 fixtures，运行时打印并确认 `app` 与 `conftest` 均来自本 worktree 后通过。两条 pytest warning 为预加载 `anyio` / `conftest` 的 assert-rewrite 提示。
5. 全 backend `.venv/bin/ruff check .` 通过；`.venv/bin/ruff format --check .`：357 files 通过；`PYTHONPATH=tests:. .venv/bin/mypy --cache-dir /private/tmp/familygraph-d-acceptance-mypy app`：193 source files 通过。

所有验证仍使用隔离合成数据；未访问主库或真实 Provider。迁移行为未变，按父线程要求本次不重复迁移矩阵。全 backend 最终检查、独立迁移 oracle、listener smoke 与串行集成交由父线程统一执行；本实现者没有进行 Git 或 task 生命周期操作。
