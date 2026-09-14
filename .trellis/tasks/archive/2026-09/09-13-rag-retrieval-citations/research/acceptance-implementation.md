# B 验收修复实施记录

日期：2026-09-14。Active task：`09-13-rag-retrieval-citations`。执行位置：`/Users/lyston/PycharmProjects/fg-09-13-rag-retrieval-citations`，分支 `feat/09-13-rag-retrieval-citations`；本记录对应基线 `7f2e88c815fa1f7c19b4a9b879c4812caed852d8` 上尚未提交的累计 B 修复。该 SHA 是检查时的 HEAD，不冒充未提交修复的提交号。

实施依据是 audit 的 `design.md` 第 1～4 节、B PRD/design，以及 `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/b-integration-check.md` 所列 B-I01～B-I10。原诊断、冻结 fixture、旧测量报告保持不变。本实施者没有执行 commit、push、merge、archive 或工作流状态变更，没有嵌套委派。本记录包含首轮冻结结果与独立复核后的 R-04 补修；首轮 1194 项通过不冒充补修后的最新全套。生产代码再次交主线程冻结，独立累计检查与最终集成结论由主线程负责；此记录不能替代 D 的索引验收。

## 缺口与实际收口

| 审查编号 | 实现与出处 | 直接回归证据 |
|---|---|---|
| B-I01：请求授权后的旧 attempt 仍能执行 | 新 `backend/app/services/agent_execution.py:29` 的不可变 `ExecutionIdentity` 只取验证后的 claims；`:64` 在 SQLite writer 内重新读取 Run、Job、Session、Account、成员资格，核验双 attempt、scope、状态、取消与双租约到期。context/events/heartbeat/settle/tool/provider 六个真实入口均透传身份。 | `test_rag_acceptance_contract.py:384` 在入口授权成功后用另一真实 Session 做 reaper→lease，覆盖 context/events/heartbeat/settle/tool；`test_rag_acceptance_bindings.py:27` 覆盖 provider 授权后及最终 admission 前两处换租，旧请求发送零上游请求；`:82` 在工具 final fence 前再次验证拒绝。 |
| B-I02：引用没有原片段证据 | `memory_sources.py:72` 新 `ExactChunkRef` 保存 document/chunk/source type/id/revision/index_version/chunk_index/content_hash；`:111` 精确重读后复用 A 的当前来源授权。`ContextSource.from_hit`、build item、已认证消息贯穿同一证据。历史读取不要求旧块版本等于新活动指针。 | `test_rag_acceptance_contract.py:195` 对删除块、改文本、改版本、改 revision 四种变更拒绝认证；`test_rag_acceptance_bindings.py:234` 对四种变更使重复 context 持久失效；`:264` 验证合法保留旧版本仍可读取，不能以新块替换。 |
| B-I03：自报 citations 经 SSE 外露 | `agent_citations.py:38` 的公开基础投影剥离服务器独占字段；`:144` 统一按当前读者投影。`api/agent.py:563` 在短 Session 内完成读取、授权与纯 DTO 物化，Session 外只发送 bytes；正文/web 引用保留。 | `test_rag_acceptance_contract.py:284` 使用有效 run token 自报假引用并读取真实 SSE；`test_rag_acceptance_bindings.py:340` 验证撤权后 SSE、重连和 history；`:383` 验证保留字段剥离前已计入原请求指纹。 |
| B-I04：internal 历史回传撤权结构化引用 | `api/internal_agent.py:429` 的 context 历史只输出合同允许的 user/assistant `content_json.text`，不向 sidecar 携带消息存储中的原 citation/provenance；正常正文和 C 的历史恢复保持。 | `test_rag_acceptance_contract.py:241` 先真实回答、settle、撤权，再同会话创建新 run 检查 raw internal context。主线程真实 smoke 也验证此出口。 |
| B-I05：重复 build 与永久失效不闭合 | `context_builder.py:150` 在 writer/fence 后查旧 build，复用时只重读原 included 描述符，不重新检索。`:94` 保存不可逆 invalidation；`:320` 比较原 query、预算、部署/run policy、provider 决策和来源。context endpoint 对 `AGENT_CONTEXT_INVALIDATED` 提交安全标记后再返回错误；append 拒绝路径使用 `:102` 的安全回滚 helper，细节见 R-04 补修。 | `test_rag_acceptance_contract.py:490` 并发真实 GET 返回同一 build，无唯一键 500；`:220` 关闭 RAG 后失效；`test_rag_query_context.py:153` 证明重复 GET 不检索、不吸收当前 run 的新回复；`test_rag_acceptance_bindings.py:406` 验证 provider/部署 policy 变更及恢复后仍失效。`:436` 验证 off 空 build 提交普通回答、重读、晚开启均保持同 ID 空集；`:484`、`:580` 验证批次拒绝后的失效保留及 signed attempt 隔离。 |
| B-I06：真实 builder 仍低估中文子预算 | `rag_budget.py` 和 `agent/src/context.ts` 使用同一真实 appendix，包括 data 标记、句柄、标签、分隔符、正文和中文引用指引。版本为 `utf8-half-envelope-v1`，估计值为 `ceil(UTF8 bytes / 2)`。build item 的 included 估计合计与最终包络一致，完整块放不下则记录排除原因。 | `test_rag_acceptance_contract.py:152` 输入 6 条真实合法中文候选，检查有界 included 子集；`test_rag_query_context.py:191` 和 `agent/test/context.test.ts` 共读 `rag_context_envelope_v1.json`，完整包络估计为 209。没有把这个估算说成真实 tokenizer 或全请求预算。 |
| B-I07：授权过滤后未继续补足 | `memory_rag.py:945` 的 FTS/短词 LIKE 使用稳定分页、同一权限路径、共享 200 候选扫描上限；每页 32 条，在当前来源授权后继续补足。trace 记录扫描、拒绝和停止原因，不记录资料或查询正文。 | `test_rag_acceptance_contract.py:530` 真实根来源撤销后仍召回后续合法资料；`test_rag_query_context.py:99` 用 37/205 个失权前排候选分别证明跨页补足与总上限，覆盖纯英文、两字中文和混合查询。 |
| B-I08：唯一同会话追问没有接线 | `api/internal_agent.py:471` 只传当前 user message 之前、同 session 已授权 user/assistant 的最后 4 条文字。`rag_query.py:78` 仅在显式代词与唯一受控亲属称谓/标注人物或地点时扩展；缺失、歧义、复数、显式新实体或任一选中历史超过 500 字符时降级。查询总词项仍不超过 8。 | `test_rag_query_context.py:37` 使用相同问题“他呢？”与不同唯一前文，断言 planner 的不同 anchor 与不同实际召回；`:71` 覆盖多实体、无前文、别会话、窗口外、过长历史；`:85` 覆盖显式地点与截断前后两实体，不能把前半段误当唯一。 |
| B-I09：fallback 误匹配别会话/user 消息 | `agent_citations.py:42` 同时限定 run.session_id、assistant role 和服务器事件 key；固定 run/seq 端点及 SSE 复用该定位和当前授权 projector。 | `test_rag_acceptance_contract.py:322` 提前建立无关会话的同 key user 消息，目标 fallback 仍与目标 history 的合法引用相同。 |
| B-I10：私有 reference 与原请求指纹无端到端绑定 | `schemas/agent.py` 的严格 `ContextReferenceIn` 为 `{build_id, attempt, used_handles}`，仅 assistant_added 可提交，整数不接受 bool，最多 20 个句柄、每个最多 255 字符。`agent_events.py:76` 的 v2 指纹覆盖 run/attempt/seq/type、原 public_payload、原 reference。`:301` 校验当前执行/build/included/正文确实使用的句柄后认证。`agent/src/events.ts:245` 仅从 completed assistant `message_end(stopReason=stop)` 取实际使用的受允句柄；worker 从真实 context 响应取得 build/attempt。 | `test_rag_acceptance_bindings.py:290` 覆盖错误/变更绑定与幂等；`:326` 证明无 reference 客户端只保留普通正文/web 引用；`agent/test/events.test.ts` 区分 delta/tool/error/已完成回复；`agent/test/worker.integration.test.ts` 通过真实 Pi 与 InternalClient wire 证明两个 included 来源只提交正文实际使用的一个。主线程真实 smoke 加入错 build、错 attempt、未在正文使用、无绑定等反例。 |

上述后端文件简写均相对 `backend/app/services/`，API/schema/model 路径已另标；测试简写均相对 `backend/tests/`。所有行号对应本次冻结内容。

## 原子准入与传输兼容

工具准入与去重占位一起提交后才进入 `_dispatch`，不跨网络持有 SQLite writer。新 `ToolRunScope` 固定已准入的原 attempt，避免后续 ORM expire/reload 把在途结果或审计归属到 replacement attempt。真实 `reaper_pass → lease_next` 后原在途 scope/audit 仍为 attempt 1，数据库当前 run 已为 attempt 2；旧 token 发起另一调用继续被拒绝。这沿用已准入在途调用可以完成的合同，不承诺撤回已经发送的请求。

`test_rag_acceptance_bindings.py:162` 到达真实 `controlled_web.search_web` 的 `_provider_search` 网络边界，在另一个 SQLite 连接上用 100 ms busy timeout 成功 UPDATE/commit，并检查已提交空占位。成功/明确拒绝两条路径均通过；明确的 `ToolProtocolError` 回滚效果后只移除本次尚为空的占位，未知中断保持 in-progress，不能自动重复副作用。没有改 `controlled_web.py`。

该真实成功路径同时暴露工具 result_json 直接存 datetime 的既有 500。修复限制在 `agent_tools.py`：结果先用 FastAPI `jsonable_encoder` 转成同一 JSON 值，再保存并经过既有结果策略；首次响应和幂等重放一致，第二次请求不再调用 Provider。未增加新的工具策略或扩大允许发送的数据。

新事件先对原始请求做指纹再剥离服务器独占字段。来源撤销后同一原请求重试仍返回 duplicate，原 event/fingerprint/私有记录不改写；读取时才按当前权限遮蔽。旧无 reference 客户端继续提交正文/web 字段，但不能根据正文中的句柄猜出受认证 RAG 引用。无精确服务端证据的旧引用按不可用处理，不推断可信来源。

整份 public_payload 的 UTF-8 上限保持 16384 字节。真实中文、emoji、转义、role 和 web_citations 填满 16384 时保留原值，增加 1 字节拒绝。服务器按稳定顺序容纳引用，空间不足时输出部分列表/`citations_complete=false`，完全没有空间时保留原正文并使用固定 run/seq 补取。`frontend/src/stores/agent.ts:340` 在未收到完整标记时补取，即使已有部分引用；显示加载/失败/重试，并丢弃已切换空间或已替换请求的迟到响应。`MessageList.vue` 同时显示仍可用引用与不可用数量。前端新增 partial、complete、失败重试、reset 后迟到响应与组件按钮回归。

## 迁移与保留数据

新增 `backend/migrations/versions/0046_context_execution_contract.py`，前驱 `0045_rag_index_lifecycle`，当前链单 head。仅为 context_builds 增加 nullable `policy_json`、`invalidated_at`、`invalidation_reason`。新 build 保存精确描述符，`blocks_json=None`；历史快照仍保留，不借此删除旧数据，也不猜填 policy/attempt/provenance。

0046 downgrade 在任何 DDL 前检查新 policy/invalidation 证据；存在时拒绝并要求保留数据后前滚。`test_context_execution_migration.py` 用独立 SQLite 与真实 MigrationContext/Operations 检查 legacy 快照保留、空/legacy schema 往返、带新证据的拒绝及前后行内容不变。测试套件及真实 smoke 都执行独立数据库的 Alembic upgrade head。

`test_memory_source_migration.py` 仅同步验收断言：升级后从 ScriptDirectory 获取实际单 head；受保护的 0042 downgrade 失败后检查 source 分支仍在版本集合中。merge downgrade 部分执行时允许兄弟分支仍存在，不能用任意 scalar 行误判。原始 Memory 行、来源验证值和内容保留断言继续保留；A/D 的生产迁移未在此修改。

## 独立复核后的 R-04 补修

主线程用真实 internal API 补充发现两个 B-I05 反例：一是合法引用先观察到 RAG 关闭并标记失效，后续 seq 间隔使整个事件批次被拒绝，原实现把失效标记一同回滚，RAG 恢复后旧 build 得以重放；二是原本在 RAG 关闭时建立的合法空 build，经新 sidecar 的空 `used_handles` 普通回答后被错误标为失效。这两项原先未被首轮全套覆盖，原红测证据不改写。

`context_builder.rollback_preserving_invalidation` 在已知 HTTP 拒绝路径回滚前，按**不可变签名执行身份**的 run、attempt、account、space、kind 查询唯一 build，只复制数据库中服务端已经写入的 id、失效时间与原因。随后回滚全部事件、消息、指纹和其他半批写入，再以同一 scope、build ID、`invalidated_at IS NULL` 条件恢复失效标记，与拒绝审计一起提交。既有并发失效不覆盖，已删除 build 不重建，不改其他 attempt；不使用 sidecar 提交的 build ID 或理由作为失效指令，不跨网络持有事务。

引用认证的 policy 条件也与 `ContextBuilder._replay` 对齐：只有原 build 的 `rag_enabled` 为真而当前关闭，才因该开关变化失效。原本 off 的空 build 在普通回答后仍可同 attempt 重读；晚开启仍保留原空集合，不重新检索。

持久回归包含：RAG 开关变更、部署 policy 变更、精确 source 文本漂移、完全未变化四种批次对照；拒绝审计持久化、原事件/指纹未改写、全部新 assistant 消息回滚；来源/策略恢复后保持失效，而未变化的 build 仍可正常重试。另用真实 reaper 与 HTTP lease 取得 attempt 2：错误引用 attempt 1 的旧 build 不使任一 build 失效，合法 attempt 2 观察失效后仅保留自己的标记，旧 token 仍被拒绝。

补修只改 `backend/app/api/internal_agent.py`、`backend/app/services/context_builder.py`、`backend/app/services/agent_events.py`、`backend/tests/test_rag_acceptance_bindings.py` 与本记录。验证：

- 全量 backend ruff、format（351 files）、mypy（193 sources）通过。
- events/context/policy/memory 相关 9 个测试文件：**127 passed，6.61 s**。包含新增 5 个场景及扩展的 off 空 build 正对照。
- 主线程原两条 R-04 独立反例，限定名称重跑：**2 passed, 1 deselected，1.45 s**；剩下一个是主线程独立处理的 LIKE 通配反例。
- 不重复 agent/frontend 全套；这些包没有 R-04 改动。最新累计 backend 全套由主线程在后续 D 集成阶段执行；不把 127 的局部结果写成新的全量通过数。

补修检查原日志：`/private/tmp/familygraph-b-r04-followup-20260914.log`，SHA-256 `15717afc173d19b27ce366859ad2fe5c2134d207e4f2a2dba18225700d3cb873`。该日志保留真实顺序：lint/type 与 127 项通过，随后临时 probe 运行时主线程新增的 underscore 场景为 1 failed / 2 passed；失败属于下面的 N08 修复前版本，没有抹除或改称全绿。

限定两条 R-04 probe 的成功日志：`/private/tmp/familygraph-b-r04-independent-probes-20260914.log`，SHA-256 `f589a5c87669687245848a92c3f98f6a96c8ae76681925ca6be1b58f9e113dfa`。主线程将在当前冻结代码上复跑全部三条独立反例与真实 smoke，结果以主线程 check 记录为准。

主线程另负责 N08/B-I07 的 LIKE 字面语义修复：`memory_rag.py` 的短词条件使用 `ESCAPE '!'` 并转义 `!/%/_`，在 `test_rag_query_context.py` 新增 `__`、`a_` 的真实字面来源与无关内容对照，不改词典或冻结 fixture。**主线程报告**这两文件 lint/format 通过，query_context 与原 RAG citation 组 **49 passed，5.68 s**；这部分不是本实施者的编辑或执行结果。

## 首轮验证（R-04 补修前）

| 检查 | 首轮结果 | 证据 |
|---|---|---|
| backend `ruff check .`、`ruff format --check .` | 通过；351 files formatted | [持久完整日志](acceptance-implementation-backend.log)，原路径 `/private/tmp/familygraph-b-acceptance-backend-final-20260914.log` |
| backend `mypy app` | 通过；193 source files | 同上 |
| backend 全套 pytest | **1194 passed, 3 skipped, 4 warnings，70.70 s** | 同上；三个跳过是 `test_m4b_admin.py` 既有 break-glass 占位，四个 warning 是现有 SQLite datetime adapter 弃用提示 |
| Memory source 迁移专项 | **5 passed，5.63 s** | 全套前执行 `pytest -q --tb=short tests/test_memory_source_migration.py`，全套再次覆盖 |
| agent lint/type-check/test/build | 全部通过；**118 tests / 14 files** | 本实施会话执行 `npm run lint && npm run type-check && npm test && npm run build` 的完整成功输出；未另外生成持久日志 |
| frontend lint/type-check/test/build | 全部通过；**625 tests / 67 files** | 同上命令在 frontend 执行；既有 ProfileDrawer kinship 测试输出 mock XHR stderr，未产生失败；未另外生成持久日志 |
| 冻结检索原 probe | **1 passed，1.31 s** | [持久原始报告](acceptance-retrieval.json)，原路径 `/private/tmp/familygraph-b-acceptance-retrieval-20260914.json`；原 fixture 与 probe 未修改 |
| 真实传输 smoke | **95/95，0 failed** | **主线程执行并提供** [acceptance-smoke.json](acceptance-smoke.json)，本实施者核对 counts 与 SHA；不冒称为本实施者执行 |
| `git diff --check` | 通过 | 冻结交接前和记录完成后检查 |

首轮完整后端日志 SHA-256：`0c1a7365361c64a81052a94fb3e9592d1b21984ebc137c0a9186fbf7dfaa0b20`。日志逐项保存命令、输出和退出码，实际命令在 backend 目录执行：

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. .venv/bin/mypy app
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. .venv/bin/python -m pytest -q --tb=short
```

首轮真实 smoke 报告 SHA-256：`f3420b7bc17c8adf4275b146a9b5cc63244a5c23b9f10ce9091132a4df7f4171`。后续主线程若更新同名报告，最新内容与哈希以主线程 check 为准。其模型流为合成流，使用真实 listener、HTTP、InternalClient、SidecarWorker 与 Pi；具体覆盖和执行边界见 [smoke-harness-update.md](smoke-harness-update.md)。

## 冻结检索测量

| 分组 | 命中 | Mean Recall@5 | MRR |
|---|---|---|---|
| 中文核心 | **16/16** | 1.0 | 1.0 |
| 英文 | **2/2** | 1.0 | 1.0 |
| 独立扩展诊断 | **7/10** | 0.70 | 0.65 |

Fixture SHA-256 保持 `92f0bed8c438a0bfde6672d71e6bb47db0f039c4cd1ac07fc490a7b3ac6c675a`。本轮报告 SHA-256 为 `4adc823e4f13e8d8efea3749174f16712b7dfeddde95cffb00f7960d970f5afb`，含真实导入的 ContextBuilder/memory_rag 路径与源码哈希。原脚本的 Q16 没有向 builder 传新 recent_messages 参数，因此这里的 16/16 **不作为追问 anchor 接线证据**；接线由前述真实 internal endpoint 的同题异前文正反例单独证明。

复现命令（backend 目录）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. \
  FG_RAG_PROBE_REPORT=/private/tmp/familygraph-b-acceptance-retrieval-20260914.json \
  .venv/bin/python -P -m pytest -p conftest -p no:cacheprovider -q -s --tb=short \
  /Users/lyston/PycharmProjects/fg-09-13-rag-retrieval-citations/.trellis/tasks/09-13-rag-retrieval-citations/research/test_retrieval_probe.py
```

扩展集仅作为诊断，没有为其未命中题修改别名表/fixture/期望值。数据均为合成资料；未执行真实 Provider 答案忠实度、语义泛化、线上延迟或全请求 token 窗口测量。

## 改动文件与后续边界

- 后端新模块：`services/agent_execution.py`、`services/agent_citations.py`、`services/rag_budget.py`；更新 internal/public Agent API、schema、context model、events、queue、tokens、tools、provider_proxy、context_builder、memory_sources、rag_query，以及 memory_rag 的 hit/search/预算估算部分。
- 后端迁移与回归：新增 0046、`test_rag_acceptance_contract.py`、`test_rag_acceptance_bindings.py`、`test_rag_query_context.py`、`test_context_execution_migration.py`、共享包络 fixture；适配 tokens、原 B citation 测试与 Memory source 迁移测试。
- Sidecar：新增 `src/context.ts` 与共享包络测试，更新 events/worker 和其实际 wire 回归；保留已集成的 C 历史与溢出恢复行为。
- 前端：Agent store、MessageList、store 与 AgentPrimitives 回归。`scripts/smoke/*` 和其研究记录由主线程派发的独立 harness 任务维护，不列作本实施者的编辑。

B 的十项缺口已完成实施与上述自验。D 的规范 document 唯一约束、不可变块物化、维护租约/游标、版本切换、完整性修复和旧迁移降级防破坏仍由 D 串行处理；本轮没有修改这些算法、D models 或 D migrations。后续主线程应以独立累计 B check 为合入依据，并在 D 与主线更新集成后执行所需累计检查、归档和清理。本记录不自行勾选父任务 AC、不宣称整项治理已可发布。
