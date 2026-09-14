# B 修复验收：主线程代码核验

2026-09-14。范围为 `7f2e88c` 后本轮 B 累计差异：backend 执行/上下文/引用/检索、agent 实际提交接线、frontend 投影及补取。主线程直接审查决定性源码，执行独立反例与真实 listener/Pi smoke；实施者负责主要修复，主线程只修正末轮 LIKE 字面查询问题。

两次 trellis-check 子代理因服务端 rate limit 终止，仅完成部分材料加载，没有可采信结论；不计为独立核验通过。以下结论由主线程源码核验与实际命令支撑。未改原审计探针、日志或冻结检索 fixture；D 已知问题仍由 D 接续。**B 本轮已通过核验，可以提交并交接 D；父任务累计验收仍待 D。**

## 本轮发现并修复的残留

1. **B-I05 / R-04：事件批次回滚丢失 context 失效状态。** 合法 build 后关闭 RAG；batch 的 seq 2 回答检测失效，seq 4 间隔冲突使整批回滚；重新开启后同 attempt 返回 200，数据库失效标记为 None。独立反例首次 `1 failed in 1.62s`，输出 `invalidation_persisted=False / after_restore_status=200`。现由 `context_builder.rollback_preserving_invalidation`（`backend/app/services/context_builder.py:102`）在回滚前读取 signed run/attempt/account/space/kind 唯一 build 的标量证据，回滚后条件保留；事件、消息、指纹仍全部回滚，不借用 sidecar 自报定位，不改变其他 attempt。
2. **B-I05 / B-AC8：合法 RAG-off 空构建被普通回答误判失效。** 空 build + `used_handles=[]` 的普通回答成功后，政策未变化却重取 409。首次 `1 failed in 1.56s`。现在事件认证与 ContextBuilder 统一为“原构建开启、当前关闭”才触发该项政策失效；off 空构建回答后可重放，late enable 也保留原空集。
3. **B-I07 / N08：参数化 LIKE 仍把 `_` 当通配符。** 仅有普通上海摘要时查询 `__` 实际命中一条无关内容，首次 `1 failed in 1.48s`。主线程在 `memory_rag.search_rag` 的短词条件显式设置 `ESCAPE '!'`，并转义 `!/%/_`，保留查询字面含义。新增 `test_short_word_fallback_treats_pattern_characters_as_literals`（`backend/tests/test_rag_query_context.py:205`）同时覆盖真字面来源与无关内容。

三个独立临时反例修复后 **3 passed in 1.61s**。持久用例已进入 bindings/query 测试；临时脚本不是唯一可复现入口。

## 全部 B 问题的核验映射

| 原问题 | 决定性代码与观察 |
|---|---|
| B-I01 签名执行身份 | `agent_execution.py:29,64` 捕获 claims 并在 writer 内核对 Run/Job 双 attempt 与授权；context/events/heartbeat/settle/tools 和 Provider 实际准入均传入原身份。独立 Session 的真实 reaper/lease 反例和已准入在途正对照覆盖，不靠手改 attempt 冒充竞争。 |
| B-I02 精确片段 | `memory_sources.py:72,111` 的 ExactChunkRef/read_exact_chunk 核对原文 hash、位置、版本和 revision 后复用来源权限；合法保留旧版本可读，缺失/漂移不改指向新块。 |
| B-I03 自报引用 | `agent_citations.py:38,144` 丢弃自报保留字段并从服务端消息证据投影；真实 wire 的五类伪造/错绑定/未使用反例未获认证。 |
| B-I04 内部历史 | `internal_agent.run_context` 只输出允许的 user/assistant 文字；真实第三次 lease 在解析前检查 raw JSON，撤权的结构化 provenance 不再传给 sidecar。 |
| B-I05 context 生命周期 | `ContextBuilder.build/_replay` 在同 writer 内唯一创建/重放，政策/来源失效单调保留；包括本轮整批拒绝、空构建和其他 attempt 隔离变体。 |
| B-I06 实际预算 | `rag_budget.py:20,30,34` 与 `agent/src/context.ts` 的真实包络逐字 fixture 一致；默认 2000 子预算计入指令、标签、句柄和换行。不是全请求 tokenizer 预算。 |
| B-I07 授权补足 | `search_rag.collect` 分页 32、两支共用 200 返回候选预算；二次来源拒绝后补足，LIKE 特殊字符保持字面含义。SQL 引擎内部扫描量未据此宣称有界。 |
| B-I08 唯一追问 | `rag_query.py:78,158` 使用同 session 前四条有限文字与封闭锚点语法；同问题/不同前文正例、歧义/无前文/超长/跨 session 反例可区分词面命中。 |
| B-I09 固定补取定位 | `agent_citations.py:42` 同时限定 run.session_id、assistant role 和服务端 key；同 key 的别会话/其他角色不能污染结果。 |
| B-I10 内部 wire 与幂等 | `EventEntry.fingerprint` 认证前纳入 run/attempt/seq/reference；worker 绑定 lease/context attempt；RunEventBuffer 只从完成文本提取句柄。丢响应后撤权的重试保持原 request，duplicate 不重写记录。 |

`ToolRunScope` 在准入/占位同一短事务提交前固定原 attempt；真实 WebGateway I/O 点另一 SQLite 连接能写。首次与重放的日期值先规范为相同 JSON，再走现有结果策略。没有将网络阶段放入 writer。

## 检查证据

- 实施者首次完整检查：backend **1194 passed / 3 skipped**，ruff/format/mypy 通过，原始日志 [acceptance-implementation-backend.log](acceptance-implementation-backend.log)。该结果发生于上述末轮复查修复前，不能替代后续验证。
- 末轮 R-04 修复：events/context/policy/memory 相关九文件 **127 passed**；类型检查 193 sources、ruff/format 通过。
- 主线程 LIKE 与检索回归：`test_rag_query_context.py test_rag_retrieval_citations.py` **49 passed in 5.68s**。
- agent **118 passed**、frontend **625 passed**，两包 lint/type-check/build 通过；末轮三项修复未改这两包。C 的 Pi 恢复/压缩/终态回归保留。
- 冻结检索：[acceptance-retrieval.json](acceptance-retrieval.json)，中文核心 **16/16**、英文 **2/2**，扩展 **7/10，MRR 0.65**；不以补字典提高单句结果。
- 首次真实 smoke：[acceptance-smoke.json](acceptance-smoke.json)，**95/95**，三 listener/真实 SDK/HTTP，模型流合成。
- 主线程末轮累计 backend：**1201 passed / 3 skipped / 4 既有 datetime 弃用 warning，66.15s**，见 [acceptance-review-backend.log](acceptance-review-backend.log)，SHA-256 `fbc72e8108135ace97054de12b83887be16426f0c74fb50685dd155994d1a0f2`。全量 ruff、format（351 files）、mypy（193 source files）通过。
- 主线程末轮真实 smoke：再次 **95/95，退出码 0**；报告与 [acceptance-smoke.json](acceptance-smoke.json) 字节相同，SHA-256 `f3420b7bc17c8adf4275b146a9b5cc63244a5c23b9f10ce9091132a4df7f4171`。这次运行在三个末轮修复之后。
- 主线程末轮冻结检索：**1 passed，1.32s**，见 [acceptance-review-retrieval.json](acceptance-review-retrieval.json)，核心/英文/扩展及 MRR 保持上述数值；fixture SHA-256 仍为 `92f0bed8c438a0bfde6672d71e6bb47db0f039c4cd1ac07fc490a7b3ac6c675a`。报告另有实际源码 hash，`code_commit=7f2e88c` 是未提交修复前的 HEAD，不冒充检查了该旧代码。

R-04 的中间原始结果另保留于 [acceptance-r04-followup.log](acceptance-r04-followup.log) 与 [acceptance-r04-probes.log](acceptance-r04-probes.log)。前者包含当时尚未完成 LIKE 修复的混合红测；不抹去或改称全绿。完整执行命令及三端首次验证见 [实施记录](acceptance-implementation.md)。

## 边界与交接

0046 新增持久政策/失效证据，历史无证据记录不猜填、不删除；有新证据的降级在第一项 DROP 前拒绝。A 来源迁移测试修正了写死 head 和多分支降级 scalar 任取版本的断言，原来源行/列保留断言继续执行。

D 仍须完成规范 document、不可变内容、活动索引接管、真实维护 lease/事务、固定水位、完整投影和无损降级；本报告不把它们算成 B 已修复。最终累计分支还须覆盖 main 最新集成与 D 迁移。真实模型语义忠实度、生产延迟、全请求预算及 E 延期能力没有由这些合成测试证明。
