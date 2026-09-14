# B 累计后端集成核验

日期：2026-09-14。核验提交：`bd899b98871c02ad97b7791a051f126d671bfefb`，包含 B `078f2e3` 与 D `bd899b9`。工作目录：`/private/tmp/familygraph-memory-rag/09-13-agent-memory-rag-remediation`。

本次遵守主线程只读授权：仅写本报告与 `/private/tmp` 合成探针；未修改生产代码、仓库测试、迁移、分支或提交。用户最新要求转入独立规划任务 `.trellis/tasks/09-14-memory-rag-acceptance-audit`，本报告供该任务引用，不代表进入 B/D 修复。未重审 D 的索引维护生命周期。

结论：发现 **10 组未闭合合同，其中 8 组有独立复现、2 组为静态接线缺失**。Lint、format 与 mypy 通过，不能据此认定 B-AC1～8 全部完成。18 个独立探针场景的最新逐项结果为 **16 个失败、2 个通过**，分三批执行，未把旧套件或同一探针重跑累加计数。

## Findings (fixed)

无。当前角色授权为只读核验；下列问题均保留给唯一实施所有者。本次没有可称为“已修复”的业务结果。

## Findings (not fixed)

### B-I01 · P1 · signed attempt 未到达写事务/admission

- 映射：MR-14、MR-24；B-AC6/8，V-B03 的旧 attempt 竞争与身份门禁。
- 证据：`backend/app/api/internal_agent.py:208` 仅在 `_authorize_run` 入口比较 `claims["attempt"] != run.attempt`，没有核验 `job.attempt`。随后 context 在 `:470` 改传 ORM 的 `run.attempt`；events 在 `:545` 不传签名 attempt；heartbeat 在 `:342`、settle 在 `:615` 也不透传。`backend/app/services/agent_events.py:152` 在取得 writer 前读取 `next_seq`。
- 实际写点：`backend/app/services/agent_queue.py:320` 的 heartbeat writer 没刷新/比较执行身份；`:382` 的 settle writer 只刷新 `cancel_requested`。`backend/app/services/agent_tools.py:523` 的 CAS 只有 run ID/status/cancel；`backend/app/services/provider_proxy.py:127` 的 CAS 同样无 attempt，调用 `:273` 也无签名 attempt。Provider 此处为静态确认，没有执行真实或模拟上游 I/O。
- **已复现**：`test_signed_attempt_is_fenced_after_authorization` 在正常 `_authorize_run` 成功后，通过另一个真实 Session 执行 `reaper_pass → lease_next`，将 attempt 1 换成 attempt 2，再恢复原请求。使用真实 lease 签发的旧 token，没有伪造 token。context/events/heartbeat/settle/tool 五个入口全部 HTTP 200；旧回答和工具结果实际落库，旧 settle 将 attempt 2 结算为 succeeded。工具使用合成 echo，但仍产生真实 AgentToolCall 结果与审计。
- 影响：入口已获权的旧 worker 可以在换租约后继续作用于新执行。仅“请求开始时旧 token 会被拒绝”的顺序测试不足以证明 fencing。
- 最小修复：按已批准 `protocol-preflight.md` 的补点透传不可变 `claims.attempt`；在现有 writer/CAS 内刷新并比较 run/job，再读 build/seq 或写入。Provider admission 提交后才进行网络 I/O，保留已 admission 请求的原在途语义，不扩大成长事务或重写队列状态机。`agent_tokens.py:127` 的 attempt 类型检查还接受 bool/非正整数（静态）；同步改成非 bool 的正整数校验。缺 attempt 的旧 token 目前已拒绝，应保留。

### B-I02 · P1 · 引用认证与重读没有精确片段证据

- 映射：MR-14；B-AC4/6/8，V-B03，以及与 D 共享的原片段定位合同。
- 证据：`backend/app/services/context_builder.py:28` 的 `ContextSource`、`:39` 的 `from_hit` 丢失 document/chunk/index_version/hash；`:256` 的 item metadata 仅记录 scope/sensitivity/revision。重复 context 在 `:178` 只用 `source_id` 查 active document，连原 source_type/revision 也未锁定。`backend/app/services/agent_events.py:321` 认证只检查 document、从 handle 解析的 revision 与 `document_readable`；`backend/app/api/agent.py:147` 的历史/fallback 投影同样只读 document。
- **已复现**：真实 context 获取后，分别删除原 chunk、修改 chunk 文本、修改 chunk.index_version、修改 chunk.source_revision；四种合成变更均未影响原句柄认证。append 后授权 fallback 仍返回该引用，`unavailable_citation_count=0`。这些是受控存储变更探针，不声称四种变更都已有普通 UI 入口。
- 影响：当前“认证”不能证明历史句柄仍定位模型实际使用的那一段；chunk 不存在或已变更也会被标成可用。根来源授权本身可以复用 A，不能替代原块认证。
- 最小修复：将精确 document/chunk/source revision/index_version/内容 hash 从 hit 带入 build item 与消息的服务端私有证据；统一精确 resolver 后再做当前读者授权。旧引用读取原版本，不能用活动版本的新块替换；算法旧版仍存在且合法时应可读，不能简单拒绝一切旧版。无需重设计 D 维护状态机。

### B-I03 · P1 · sidecar 自报 citations 可以原样进入 SSE

- 映射：MR-14、MR-24；B-AC5/6/7/8，V-B03/04。
- 证据：`backend/app/schemas/agent.py:118` 的 public_payload 允许任意字典；`backend/app/services/agent_events.py:125` 仅校验类型/可序列化/字节数，未拒绝保留的 citations/内部字段；`:207` 把原 public_payload 直接入 event。`backend/app/api/agent.py:641` 的 `_wire_event` 直接序列化该值，没有引用投影。`:611` 返回关闭 Session 后的 ORM 行，当前 `_wire_event` 不查数据库，因此本次没有把它误报为 detached ORM 异常；真正缺口是读取时未授权。
- **已复现**：持有有效 run token 的合成请求没有取得 context，却提交带六字段假 citation 和 `text: "synthetic unverified excerpt"` 的 public_payload。append HTTP 200；SSE 返回假 `source_id=not-a-source`、`citation_handle=forged-handle` 和该摘录。相同事件的授权 fallback 返回空 citations。这是 **SSE 实际输出未认证字段**，不是对未来字段的推测，也不意味着普通浏览器能调用 internal endpoint。
- 影响：公开事件可以展示未认证来源，且与受控消息历史/补取不一致。当前前端 `frontend/src/stores/agent.ts:386` 只在无 citations 时补取，不能依赖 fallback 自动覆盖这种值。前端本轮只沿此调用点确认影响，未扩大为独立前端核验。
- 最小修复：内部输入保留字段由服务端独占；原请求指纹仍在认证/投影前计算，新事件公开 citations 只由认证结果生成。SSE 在短 Session 内加载当前读者并投影，只把纯 DTO/bytes 带出 Session；正文与 web_citations 保留，不引入读取长事务。

### B-I04 · P1 · internal context 的历史出口未遮蔽撤权引用

- 映射：MR-14；B-AC6/8，V-B03 的统一读取投影。
- 证据：`backend/app/api/internal_agent.py:489` 的 `ContextMessageOut` 直接赋 `content_json=m.content_json`。浏览器 `backend/app/api/agent.py:182` 已调用 `_message_citations` 并去除原始 citations；两个出口没有共用投影。
- **已复现**：合法 context → 合法引用回答 → settle → revoke Memory → 在同会话创建下一 run。浏览器 history 显示 `citations=[]`、`unavailable_citation_count=1`；下一 run 的 internal context 历史仍含原 `source_id`、scope、revision、citation_handle。
- 影响：受限元数据仍跨 internal context 出口发送给 sidecar。此发现仅针对结构化 `content_json.citations`；没有将保留的 Assistant 回答正文或正文里的旧句柄当成本任务承诺擦除的内容。
- 最小修复：internal 历史也调用统一当前权限 projector，或仅输出其合同允许的文字字段。保留已授权的正常正文恢复和 C 的压缩接线。

### B-I05 · P1 · 同 attempt context 的复用/失效并未闭合

- 映射：MR-14、MR-24；B-AC6/8，V-B03 同 attempt 重读。
- 证据：`backend/app/services/context_builder.py:109` 先重新搜索，再在 `:163` 检查旧 build；`:177` 直接回放 blocks。复用分支不比较 query_hash、budget、policy_version、当前 RAG 状态；`backend/app/models/context.py:35` 只有 attempt/blocks_json，没有持久 invalidated 状态。首次不存在记录时在 `context_builder.py:236` flush，缺少锁内“查旧/创建”或竞争后的授权重读。
- **已复现 1**：首次获取有来源的 context 后，将 RAG DB override 关闭；同 attempt 重取仍 HTTP 200，返回 1 个原 block，而不是明确 invalidated。
- **已复现 2**：两个真实 GET 在“查得无现有 build”处用 barrier 同步；返回 `[200,500]`，第二请求触发 `UNIQUE constraint failed: context_builds.run_id, context_builds.attempt`。数据库最终保留 1 个 build，唯一约束生效，但 API 幂等未成立。
- 影响：配置变化后仍下发既有材料；并发重试会产生非合同 500。其他 policy/query 变化和持久失效后恢复的行为仅静态确认缺少校验，本轮未继续扩展探针。
- 最小修复：先取得现有短 writer 并复验执行身份，再决定复用/创建；重复 GET 只精确重读原 included 集。当前政策不再允许时持久标记 invalidated 并明确返回，不能同 attempt 偷建新 build，也不能仅依赖 unique 索引捕错。

### B-I06 · P2 · 真实 ContextBuilder 仍绕过新的 RAG 预算估算

- 映射：MR-10；B-AC4，V-B02。只针对 RAG 子预算，不扩为 E 的全请求窗口。
- 证据：`backend/app/services/context_builder.py:151`、`:240`、`:267` 仍使用 `max(1, len(source.text)//4)`，未计包装。新 UTF-8 estimator 在 `backend/app/services/memory_rag.py:602`，未接入该真实 API builder。另一个 `memory_rag.build_context` 的改动不能证明生产入口已改。
- **已复现**：6 条合成中文 Memory 经真实 internal context 返回 6 个 blocks；持久 build.token_budget=2000，而使用本实现新 estimator 计算序列化 blocks 得到 6270。这里报告的是已声明算法的估算值，不将它冒充真实模型 tokenizer 的 token 数。
- 最小修复：在实际 ContextBuilder、审计 token_estimate 和下发包装之间复用同一预算算法；计入真实 citation/标记/正文包装，记录 excluded 原因。保持完整块定位，不截正文或改成全文请求“已安全”的宣称。

### B-I07 · P2 · FTS 二次授权后没有有界补足

- 映射：MR-03；B-AC3，V-B02。
- 证据：`backend/app/services/memory_rag.py:1025` 只取 `limit` 个 FTS 结果；`:1028` 再用 `_rows_to_hits` 丢弃根来源失效项，`_denied` 不用于继续扫描。`:1040` 仅在有 fallback_terms 时走短词后备，纯英文长词没有补足分支。
- **已复现**：通过真实确认服务创建 RAG 派生 Memory，再创建相同词面的合法独立 Memory，随后撤销派生来源的根 Memory。查询 `needlexyz`，`limit=1` 返回 `[]`，`limit=20` 返回合法来源 `3`；被拒绝的前排副本为 `2`，合法目标就在下一项，并未超出 200 的既定扫描预算。
- 影响：没有越权返回，但合法资料被前排失效依赖挤掉。提高固定 limit 不能替代权限过滤后的补足合同。
- 最小修复：在稳定顺序与总扫描上限内继续读取候选，直至收集足够合法结果或用尽预算；统计扫描/拒绝/停止原因。保持各分支共同授权规则，不为召回放宽权限。

### B-I08 · P2 · 唯一同会话追问没有生产接线（静态）

- 映射：MR-04；B-AC2，V-B01/02。
- 证据：`backend/app/api/internal_agent.py:451` 从 recent 仅提取最新 user text，`:466` 只传该 query；`backend/app/services/context_builder.py:118` 只将 query 交给 search；`backend/app/services/memory_rag.py:965` 调 `plan_query(query)`；`backend/app/services/rag_query.py:118` 的 planner 只有 raw 参数，QueryPlan 也没有历史 anchor/歧义降级字段。
- 检索范围：上述整条真实调用链、`backend/app` 内 plan_query/log_summary/anchor/recent_messages 使用点，以及 `agent/src`/`shared` 的协议使用点。未发现生产历史扩展入口；没有运行新的追问质量数据集。
- 影响：Q16 即便仅凭“体检”二字命中，也不能证明已按唯一人物消解；更不能证明同会话歧义时有明确降级。实施记录没有该项完成证据，不能勾选 B-AC2。
- 最小修复：只从已授权同 session 的有限文字历史传入可判定 anchor，唯一时扩展、歧义/不足时记录降级；不取别的 session、不给历史新增长期记忆地位。新增正反例应能区分“关键词碰巧命中”和“确实用了唯一 anchor”。

### B-I09 · P2 · fallback 消息定位缺少 session 与 role 条件

- 映射：MR-14；B-AC7/8，V-B03/04。
- 证据：`backend/app/api/agent.py:559` 的查询只有 `AgentMessage.idempotency_key == run:{id}:event:{seq}`。`backend/app/models/agent.py:90` 的消息 key 唯一性为 `(session_id, idempotency_key)`，并非全局；用户消息也可使用 key。
- **已复现**：在更早的无关会话建立合法 user 消息形状、设置相同 key；随后真实目标 run 回答产生 1 条合法 citation。目标 history 返回 1 条，fallback 却选中无关 user 消息并返回 `[]/0`。该 probe 用合成 DB 夹具建立用户消息，未冒称执行了另一用户完整浏览器提交链。
- 影响：合法引用被遮掉，history 与 fallback 不一致。本次未证明该路径泄漏他人来源；当前 `_message_citations` 对 user role 返回空集合。
- 最小修复：定位时同时匹配已授权 `run.session_id`、`role='assistant'` 和服务端 key；事件 type 的现有校验保留。

### B-I10 · P2 · 内部 context_reference 与其指纹绑定未实现（静态）

- 映射：MR-14、MR-24；B-AC5/6/8，V-B03。
- 证据：`backend/app/schemas/agent.py:115` 的 EventIn 只有 seq/type/public_payload；`backend/app/services/agent_events.py:70` 的 EventEntry 同样如此。`:75` 的指纹仅哈希 type/public_payload；`:283` 从数据库当前 run.attempt 猜选 build，`:293` 正则解析文本，最后在 `:353` 生成内部记录。没有来自当前 sidecar 执行的 `build_id/attempt/used_handles` 输入。
- 检索范围：`backend/app`、`backend/tests`、`agent/src`、`agent/test`、`shared` 中 context_reference/expected_attempt 使用点。context_reference 命中仅存储/服务端输出记录及其断言，没有 wire 输入。`agent/src/worker.ts:254` 只追加引用文字指引；本轮未对 sidecar 执行新的集成测试。
- 影响：不能验证“提交声明的 build 确实属于当前 signed attempt 且包含所用句柄”这一明确合同，也不能把 build/attempt/reference 纳入原请求重放比较。当前 DB 自动挑 build 与已批准协议不同，应记录为未接线，不伪称现有字段已经端到端传输。
- 最小修复：同步 internal schema、client/event adapter 与后端 EventEntry，加入有界内部 reference，按 current signed attempt/build/included 校验；原始 reference 在认证/裁剪前参与 canonical fingerprint，私有字段不复制到 public_payload。旧无 reference 客户端按批准兼容路径提交普通正文/web 引用。与 B-I01/B-I02 一起收口，不另造一套来源授权。

## 已通过、静态正确及实际限制

- **原请求重放实测通过**：`test_source_revocation_retry_keeps_original_committed_event` 先提交真实引用回答，再撤销来源，再提交相同原 payload。返回 `duplicates=[2]`，event ID/public_payload/request_fingerprint/context_reference 均未改写；授权 fallback 为 `[]/1`。这里是“原请求是否同一次提交”的判定，与“当前读者能否看到来源”的投影分离；不要为修读取面而重新生成已提交 event。
- **整个 public_payload 的 16 KiB 边界实测通过**：包含中文、emoji、引号/换行 JSON 转义、role 和 web_citations，将 UTF-8 大小精确填至 16384；提交成功，正文/web 原值保留，完整认证 citation 经 fallback 取得；再加 1 字节返回 422。此结果不表示 SSE 已正确投影，也不表示现有前端补取失败体验已通过。
- **浏览器 history 当前基本撤销遮蔽已实测通过**：正常来源撤销后 `citations=[]`、`unavailable_citation_count=1`，原 stored citations 不经 content_json 回显。普通 fallback 在没有消息 key 碰撞时相同。精确 chunk 变更仍存在 B-I02，internal 出口为 B-I04，SSE 自报字段为 B-I03；三者不可混称“一切 history 均泄漏”。
- **0044 迁移**：本次三批 probe 均由现有 session fixture 在独立 DATA_DIR 执行真实 `alembic downgrade base → upgrade head`，通过了包含 0044 的现行迁移链。静态读取 `0044_rag_citation_contract.py:32`：新增列 nullable，未猜填旧 attempt 或 fingerprint，唯一索引允许历史 NULL。未另造旧 schema 存量数据迁移；不把空库升级称为历史数据回归通过。
- **新检索活动版本（静态）**：`memory_rag.py:851` 检查 chunk.index_version == document.index_version，`:930` 将实际版本写入 RAGHit。D 的维护、旧版保留/切换、tombstone 防复活、失败退避由另一 reviewer 负责，本次未重复判断。
- **测试覆盖核对**：现有 B `test_same_attempt_context_is_idempotent_and_reauthorized`（`:312`）使用新账户，未断言真实来源或撤权；`test_event_fingerprint_differs_from_authenticated_payload`（`:566`）仅比较属性自身/不同空 payload；`:385` 的“每个读取面”实际覆盖浏览器 history 与 fallback，没有 SSE/internal history；`:504` 的大 payload 例子没有真实引用。保留这些可用回归，同时补上会失败的实际路径，不以通过数代替验收。
- **规范同步**：已读取当前根 AGENTS、父 check manifest 及合同、B PRD/design/implement、实施记录/preflight。`.trellis/spec/backend/index.md` 自标历史不可执行，本次未把历史门禁当当前要求。修复后应回填 B 实施记录与父验证证据；当前全完成复选框不能代表以上未闭合合同已完成。没有修改其他任务/规范文件。

## Verification

- Lint：**pass**。对下述 16 个 B 后端文件执行 `.venv/bin/ruff check --no-cache`，输出 `All checks passed!`。
- Format：**pass**。相同文件执行 `.venv/bin/ruff format --check --no-cache`，输出 `16 files already formatted`。
- TypeCheck：**pass**。`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/mypy --cache-dir /private/tmp/familygraph-bcheck-mypy app`，输出 `Success: no issues found in 190 source files`。
- Tests：**fail**。独立合成探针分批结果如下。没有重跑完整 backend/agent/frontend 套件；主线程负责真实 listener + sidecar smoke 与冻结检索基线，本报告不将那些尚未收到的结果算通过。

Lint/format 文件范围：services 的 context_builder、agent_events、agent_tokens、agent_queue、agent_tools、provider_proxy、memory_rag、memory_sources、rag_query；api 的 agent、internal_agent；models 的 agent、context；schemas/agent.py；migrations/versions/0044_rag_citation_contract.py；tests/test_rag_retrieval_citations.py。

| 批次 | 命令附加筛选 | 实际输出 | 日志 |
|---|---|---|---|
| 首轮 | 无（当时 15 个场景） | `13 failed, 2 passed in 1.79s` | `/private/tmp/familygraph-b-integration-probe.log` |
| 补查 | `-k 'fallback_is_scoped or concurrent_same_attempt or fts_refills'` | `3 failed, 14 deselected in 1.51s` | `/private/tmp/familygraph-b-integration-additional.log` |
| 字节正对照 | `-k exact_16k_public` | `1 passed, 17 deselected in 1.43s` | `/private/tmp/familygraph-b-integration-byte-control.log` |

首轮 fallback 场景因无关会话创建较晚而未触发；补查将无关会话提前，复现全局 key 查询选错行。该场景最新结果计为失败，不将两次执行算两份覆盖。最新脚本共 18 个参数化场景：16 个失败场景、2 个通过对照；未声称三批是一次完整套件运行。

探针文件：`/private/tmp/familygraph-b-integration-probe.py`。执行目录与共同命令：

```bash
cd /private/tmp/familygraph-memory-rag/09-13-agent-memory-rag-remediation/backend
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. .venv/bin/python -P -m pytest \
  -p conftest -p no:cacheprovider -q -s --tb=short \
  /private/tmp/familygraph-b-integration-probe.py
```

可加 `-k` 选择对应函数：`signed_attempt`、`requires_exact_original_chunk`、`sse_does_not_trust`、`internal_history`、`repeated_context`、`concurrent_same_attempt`、`real_context_obeys`、`fts_refills`、`fallback_is_scoped`、`source_revocation_retry`、`exact_16k_public`。B-I08/B-I10 为静态结论，没有伪造测试命令或成功指标。

使用 `-P` 与 `PYTHONPATH=tests:.` 是为让外部 probe 的 `conftest` plugin 加载 `backend/tests/conftest.py`；当前新增的 `backend/conftest.py` 与其同名。业务 app 已先确认实际来自本集成 worktree，避免共享 editable venv 误读主检出。fixture 每批新建独立临时 DATA_DIR，使用真实 API/service/Alembic；无生产 SQLite 访问、无模型调用、无外部发送。

本报告不评价真实 Provider 答案质量、生产延迟、自动压缩频率或尚未收到的完整集成结果。已复现项仅限所述合成环境与受控同步点；static 与未执行项分别保留标签。
