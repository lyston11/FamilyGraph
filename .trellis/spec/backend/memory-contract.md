# Memory 来源、响应和前端缓存合同

本文件记录 2026-09-13 A 任务的实现合同，用于维护真实接口；不将其他历史 spec 重新提升为开发门禁。授权与任务边界仍以 AGENTS.md 和当前任务为准。验证入口：[A 任务](../../tasks/archive/2026-09/09-13-memory-contract-repair/prd.md)。

## 1. Scope / Trigger

修改 Memory 候选、确认、管理列表、RAG 保存、旧来源验证或前端投影缓存时适用。HTTP 字段、数据库来源证据和各读取面的授权必须一起验证，前端 mock 成功不足以证明真实创建成功。

## 2. Signatures

- `POST /api/memory-candidates`：创建待确认候选，201。
- `GET /api/memory-candidates?include_decided=...`：本人候选。
- `POST /api/memory-candidates/{id}/confirm`：`{scope, retention_days?}`，返回 Memory。
- `POST /api/memory-candidates/{id}/dismiss`、`POST /api/memories/{id}/revoke`、`DELETE /api/memories/{id}`：管理自身记录。
- `GET /api/memories?space_id=...`、`GET /api/rag/search?space_id=...&q=...`：当前获权投影。
- `memory_sources.source_lifecycle / memory_materializable`：不含读者的来源有效性；`document_readable / source_access / memory_access`：当前读者/空间授权。
- `verify_legacy_source(db, row, account=...)`：只恢复可验证来源，检查既存 Memory 范围；不确认、不扩大范围、不索引。
- DB：candidate 的 `(author_account_id, idempotency_key)` 唯一，Memory 的 `source_candidate_id` 唯一；来源证据同时存入 Memory，不能只依赖 candidate/message FK。

## 3. Contracts

新客户端创建请求必须显式来源，并为一个用户操作保持同一 `idempotency_key`（8～128 字符）：

```json
{"source":{"kind":"manual"},"raw_quote":"本人此次输入","summary":"整理后的摘要","purpose":"用途","suggested_scope":"private","sensitivity":"normal","idempotency_key":"operation-unique-key"}
```

`source` 为以下三种之一，额外字段拒绝：

- `manual`：原文必填；HTTP raw_quote 最多 12,000 字符。
- `agent_message`：附正整数 `message_id`，仅本人当前获权会话中的原始 user 全文；不接受 assistant/tool/派生消息作独立快照。
- `rag_chunk`：附 `document_id/chunk_id/revision/index_version/space_id`；space_id 是当前搜索空间，服务端精确回读原文。客户端原文可省略，提供时必须完全相同。

summary 最多 20,000 字符，purpose 最多 120 字符。非 manual 的原文在 UI 只读，RAG 保存保持源敏感度；候选不是可检索 Memory。确认 scope 使用 `private` 或 `household:N/lineage:N`，选项为服务端 `allowed_scopes` 与当前空间的交集。

旧无来源请求不能推断 manual；旧 source_message_id 只适配合法原始 user。任意 source_document_ref 不是授权；非空客户端 source_span 拒绝，来源证据由服务端生成。

输出保留 HTTP `raw_quote`（候选 ORM 字段仍是 `source_quote`），新增 source_kind、source_status、allowed_scopes。`unavailable/unverified` 时原文/摘要/用途和源消息 ID/ref/span 均隐藏；本人可管理元数据，其他失权读者不返回记录。已确认本人 user 快照在消息删除后为 `deleted_snapshot`；RAG 副本仍依赖根来源，不能扩大原空间或敏感度范围。

写入顺序固定为 mutation → flush → 构造 DTO / model_dump_json → commit → 返回已验证 DTO。创建请求 fingerprint 与归一化输入绑定；确认 fingerprint 保留原 retention_days，不用重算后的到期时间比较重试。取得写锁后重验来源、目标 scope 与有效 Memory 开关。

前端所有写同一投影的读请求共享请求顺序，写回需匹配账户 generation、分区对象和请求序号。clear/reset 不仅禁止旧回写，还禁止旧 mutation 继续发起新身份的刷新。能力刷新清除旧 RAG 结果；读取失败清除相应旧正文并显示错误，不伪装操作成功。

## 4. Validation & Error Matrix

| 情况 | 结果 |
|---|---|
| 缺失/冲突/伪造来源、原文不匹配 | 422，不落候选 |
| 无权、跨空间、来源撤销或不可验证 | 403 / 隐藏投影，不能检索 |
| 同 key、同输入 | 返回同候选；仍重验当前授权 |
| 同 key、异输入；同候选异确认参数 | 409 MEMORY_STATE_CONFLICT |
| 重复确认、同参数 | 同一 Memory / 同一 retention_until |
| 存量 scope 超过可验证来源 | 拒绝恢复，原行保持 unverified |
| Memory 关闭 | 管理 API 503 MEMORY_DISABLED；独立 RAG 读取仍按其开关 |
| 功能状态或写后刷新失败 | 前端解释错误；保留重试键，不插入假成功结果 |

## 5. Good / Base / Bad Cases

- Good：显式手工创建 → 列表序列化 → 确认 → 真实搜索命中；相同请求重试不重复。
- Base：旧合法本人 user 来源继续适配；旧无引用的 Assistant 消息展示合同不变。
- Bad：将共享片段保存为 private 后退出原空间，副本仍读出正文；恢复旧来源时把 A 空间原始 user 认证到 B 空间。两者都必须拒绝/隐藏。

## 6. Tests Required

- 真实迁移临时库中的 API 创建/列表/确认/检索/撤销；提交前 DTO 检验失败时无提交，重试与并发确认只有一条 Memory。
- 三种合法来源和非法角色/跨空间/撤权/过期/错误 revision/sensitivity、删除聊天 FK、legacy 迁移与恢复反例。
- 真实 Axios serializer 的来源 payload、只读字段、同内容重试键、异内容新键、四开关组合及失败提示。
- 受控 Promise 顺序的旧响应/退出/空间清理/能力切换回归，断言受限正文不被恢复且无旧链额外 GET。
- linked worktree 共享依赖时，Python 验证显式 `PYTHONPATH=.` 并运行 `.venv/bin/python -m pytest`；先确认 app.__file__ 来自当前 worktree。真实 listener smoke 为三个端口分别分配独立动态地址。

## 7. Wrong vs Correct

错误：收到无 source 的请求自动视为 manual；提交后才发现 DTO 缺 raw_quote；仅用搜索结果或旧快照认定当前授权；按整段文本去重用户操作。

正确：显式/可验证来源、服务端原文回读、提交前响应校验、每个读写面持续检查来源；按用户操作 key 与请求 fingerprint 处理重试。新切分与批量索引生命周期由 B/D 继续实现，不能用 A 通过宣称它们已经完成。

## 8. 自动候选提取（2026-09-15 补充）

普通聊天此前永不产生候选（默认 detector 返回空），候选/记忆/RAG 索引长期为空。现由确定性规则提取器补齐输入侧；本段是维护该路径的合同。

### Scope / Trigger

修改 `backend/app/services/memory_extractor.py`、`MemoryCandidateExtractor` 的默认 detector 或幂等键构造、或在 agent 结算路径增删提取钩子时适用。

### 签名与接入点

- `memory_extractor.rule_detector(text) -> list[MemoryCandidateInput]`：纯函数，无 DB/模型调用，相同输入逐字节相同输出。
- `memory_extractor.rule_detector_with_stats(text) -> (inputs, dropped)`：`dropped` 为超上限被丢弃数，供日志观测。
- `memory_extractor.extract_after_settle(db, run) -> int`：容错钩子，**永不抛错**。
- 接入点唯一：`agent_queue._settle` 在 `effective == "succeeded"` 且 `run.message_id` 非空时调用，与终态写入同事务。
- `MemoryCandidateExtractor.extract(...)` 为每条候选生成 `idempotency_key=f"extract:{resolved_message_id}:{label}:{index}"`，其中 `resolved_message_id = item.source_message_id or source_message_id`（不得只用参数，否则自带 message_id 的 detector 条目会跨消息碰撞）。

### Contracts

- 提取产物只是 review card；确认、索引仍由用户经既有 `POST /api/memory-candidates/{id}/confirm` 完成。
- `source_quote` 必须是完整 user 消息原文（`resolve_source` 对 `agent_message` 做全等校验）；`summary` 承载结构化概括。
- 类别集合与顺序固定：birthday > anniversary > dietary > occupation > school > residence > preference，单消息上限 3 条。
- `suggested_scope` 固定 `private`；sensitivity 仅 `normal`（dietary 为 `sensitive`），不产 `high`。
- 否定/习语守卫：命中关键词前 2 字内出现 不/没/别/无/哪 时不产卡；`不吃` 后接 亏/消/准/着/过/喜爱想要会敢能得吃 时不命中。
- 消息长度超过 `config.AGENT_MESSAGE_MAX_LENGTH` 时整条跳过（不截断），避免截断导致原文失配的静默 no-op。
- 提取整体运行在 `db.begin_nested()` savepoint 内：任何失败（含延迟到外层 flush 的约束错误）只回滚候选，终态照常提交。
- `MEMORY_ENABLED` 关闭时前置短路；`memory.*` 领域事件不触发 Steward 作业与 PersonalFamilyView 失效（`_schedule_steward_job` 白名单）。

### Validation & Error Matrix

| 情况 | 行为 |
|---|---|
| run 终态非 succeeded、`message_id` 为空、消息非 user、文本为空 | 返回 0，不落候选 |
| MEMORY_ENABLED 关闭 | 返回 0，settle 不受影响 |
| 消息超长 | 返回 0 并记 info 日志 |
| propose 或来源校验抛错 | savepoint 回滚，warning 日志，settle 仍成功 |
| 延迟到外层 flush 的失败 | savepoint 回滚，run 保持 succeeded，无残留写入 |
| 同消息重复提取 | 幂等键命中同候选，行数不变 |
| 平台操作员账号（来源 403） | 被容错层吞掉，settle 照常 |

### Tests Required

- `backend/tests/test_memory_extractor.py`：类别正/反例、否定守卫、上限与丢弃计数、确定性、超长跳过、settle 失败无候选、MEMORY 关闭时 settle 成功、重复提取幂等、延迟 flush 失败不击穿 settle、公共 seam 幂等键不跨消息碰撞。
- 隔离库验证必须设 `DATA_DIR` 指向隔离目录（`config.DATABASE_URL` 由 `DATA_DIR` 计算，设 `DATABASE_URL` 环境变量无效），并用 `sqlite3 .backup` 复制主库。
- 隔离库验证还必须加载部署环境变量文件（远端 `/home/ubuntu/.config/familygraph/familygraph.env`，含 `MEMORY_ENABLED=1`）：`platform_feature_configs` 无行时 `platform_features` 回落到 environment 源，未加载 env 会得到 `memory_enabled=False`，`extract_after_settle` 直接返回 0 —— 这是**假阴性**，不是链路缺陷。验证提取器前先断言 `config.MEMORY_ENABLED` 与 `get_platform_feature_state(db).memory_source`。

### Wrong vs Correct

错误：把提取钩子的异常向外抛（会让终态丢失、run 停在 leased）；截断超长原文（原文失配后 422 被吞成静默 no-op）；用调用方参数而非 `item.source_message_id` 构造幂等键；在无否定守卫的情况下把「不喜欢」输出为偏好候选。

正确：savepoint 隔离 + 全量容错；超长直接跳过并记日志；幂等键绑定实际来源消息；产出前做否定守卫。

## 9. 记忆取代与时间有效区间（2026-10-09 补充）

在此之前记忆只有 `retention_until`（到期），没有「被取代」。同一个人的职业、住址、
称呼变化后，新旧两条都是 `status='active'`，检索会同时命中，模型看到**互相矛盾的
事实**且无法判断哪个有效。本节是维护该语义的合同。

### Scope / Trigger

修改 `backend/app/models/memory.py`、`memory_rag.supersede_memory/restore_memory`、
`_ELIGIBILITY_SQL` 的取代/有效区间条件、`confirm_candidate` 的 `supersedes` 参数，
或前端 `MemoryCardItem` 的取代状态展示时适用。

### 数据模型与语义

```text
memories
  valid_from        -- 事实开始有效（可空 = 未知，不猜）
  valid_to          -- 事实失效（空 = 仍有效）
  superseded_by_id  -- 取代它的 Memory id（空 = 未被取代）
  supersede_reason  -- 'user_replaced' | 'source_revision' | 'expired'
  superseded_at     -- 取代发生时间（审计）
  restored_at       -- 撤销取代的时间（审计）
```

被取代的行**不删除**：`status` 保持 `active`，历史可审计、可回溯，取代**可撤销**。
迁移 `0059_memory_supersede` 只加列，六列全部可空，因此迁移本身**不改变任何检索
结果**；降级在存在任何 `superseded_by_id IS NOT NULL` 的行时**拒绝**（丢指针等于让
旧事实静默重新进入检索，那是事实回退而非 schema 回退）。

### 承重的可见性三层（每层都必须独立挡住）

| 层 | 位置 | 作用 |
|---|---|---|
| SQL 取代/有效区间 | `_ELIGIBILITY_SQL` 的 `m.superseded_by_id IS NULL AND (m.valid_to IS NULL OR m.valid_to > :now)` | 检索前过滤 |
| 文档状态 | 取代时写 `invalidated` + `index_superseded`，eligibility 要求 `d.status='active'` | 纵深防御，且不依赖重建索引 |
| 投影复核 | `_rows_to_hits` 的 `_memory_is_current` | 挡住「SQL 执行后、行读取前被取代」的并发窗口 |

`test_memory_supersede.py::test_supersede_visibility_is_load_bearing_at_every_layer`
逐层拆除这四道条件（含有效区间），任何一层被删都会让断言失败。

### 关键实现约束

- `:now` 必须在语句上声明 `DateTime`（`_typed_eligibility`）：SQLite 上 datetime 列以
  字符串存储，未声明类型的参数会把 ISO 串按字符串比较，**静默丢行**。不能把
  `bindparam(...)` 放进 params 字典——那会把它当值传给 sqlite3。
- 取代时**只动文档，不动 chunk**：chunk 的 `status` 是投影完整性证据
  （`_chunks_match` 要求 active），标成 invalidated 会让撤销取代时的 `index_memory`
  判定「缺少完整正文证据」而拒绝恢复（实测 409 `RAG_SOURCE_NOT_ALLOWED`）。
- `supersede_memory` 用 `index_superseded` 而不是 `invalidate_source`：后者写
  `source_invalidated`（永不复活），取代必须可恢复。
- `index_memory` 对「已被取代」的记忆默认**拒绝**建索引；`allow_superseded=True`
  只给索引换版回填用（否则游标会停在那一行反复重试）。回填与常规维护在
  `_memory_is_current` 为假时**跳过**该行且**不记失败**，由 `restore_memory` 显式重建。
- 取代方向判据只有一条：`new.id > old.id`。刻意**不**要求同一 `source_id`
  （manual 记忆没有来源身份，而「住上海」与「住苏州」来自不同消息却确实互相取代），
  也不做相似度自动合并（「住上海」与「住上海浦东」相似但语义不同，自动合并会静默丢事实）。
- 取代目标参与确认请求的 fingerprint：同一候选配不同取代集是不同请求，否则重试会
  拿回「部分取代」的结果而不报错。

### Signatures

- `POST /api/memory-candidates/{id}/confirm`：body 增加 `supersedes: int[]`（≤20，默认空）。
- `POST /api/memories/{id}/supersede`：`{by_memory_id, reason?}`，返回 Memory。
- `POST /api/memories/{id}/restore`：无 body，返回 Memory。
- `MemoryOut` 新增 `valid_from/valid_to/superseded_by_id/supersede_reason/superseded_at/restored_at`。
- 服务层：`memory_rag.supersede_memory(db, *, memory_id, account_id, by_memory_id, reason)`、
  `memory_rag.restore_memory(db, *, memory_id, account_id)`、`confirm_candidate(..., supersedes=())`。

### Validation & Error Matrix

| 情况 | 行为 |
|---|---|
| 取代原因不在枚举内 | 422 `MEMORY_STATE_CONFLICT`，无写入 |
| 自我取代 | 422「记忆不能取代自己」 |
| `new.id <= old.id`（反向取代） | 422「取代方必须是更晚创建的记忆」 |
| 目标不存在或不属于本人 | 404，**不区分**两者（避免存在性枚举） |
| 目标非 active / 未确认 | 409 |
| 旧行已被**其它**版本取代 | 409「记忆已被其它版本取代」（不静默改写） |
| 同一取代重复调用 | 返回同一行，不改写 `superseded_at` |
| 恢复时未被取代 | 409「记忆当前未被取代」 |
| 恢复时来源已失效 | 403 `MEMORY_SCOPE_FORBIDDEN` |
| 确认请求里的取代目标非法 | 整笔拒绝：候选仍 pending、无 Memory、不触碰他人行 |

### Tests Required

- `backend/tests/test_memory_supersede.py`：取代后离开检索但行与投影可审计、撤销取代
  后重新可检索且投影原地激活、非法 reason / 反向取代 / 自我取代 / 跨账户 / 重复取代
  冲突、确认时显式取代单事务生效、确认失败无部分写入、有效区间过期排除、
  ContextBuilder 端到端不再纳入旧事实、四层 mutation。
- `backend/tests/test_rag_lifecycle_migrations.py`：0059 列存在时 `upgrade head` 保持
  全部版本、FTS 与真实 saved 依赖不变（该套件从 0054 起建库，需 `_add_supersede_columns`）。

### Wrong vs Correct

错误：用文本相似度自动合并「应该被取代」的记忆；取代时删除旧行或删除其 chunk；
把 `valid_to` 的 `:now` 作为未声明类型的字符串参数比较；用 `invalidate_source`
（`source_invalidated`）表达可撤销的取代；让索引换版回填因被取代而反复失败。

正确：取代只写指针与失效区间，行与正文证据全部保留；`index_superseded` 表达可恢复
失效；`_typed_eligibility` 声明 `DateTime`；回填跳过被取代的行且不记失败，恢复时显式重建。
