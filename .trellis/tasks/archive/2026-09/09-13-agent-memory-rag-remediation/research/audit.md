# 双 Agent RAG / 记忆审计台账

## 证据口径

审计日期：2026-09-13。基线：主检出 HEAD `b7bd368` 及该时刻工作区文件；存在其他任务的未提交配置/管理端改动，未将其纳入本任务写入范围。业务代码未修改。

- **已复现**：调用真实应用代码、真实迁移临时库或已安装 Pi 0.84.3；模型流为离线假数据。
- **静态确认**：实际生产调用链和关键原文已检查，不等于线上已经触发。
- **能力缺口/设计取舍**：功能未接通或范围有意受限，不能全部称为缺陷。
- **待验证风险**：有明确可达代码路径，需要后续复现；不得写成已发生的数据泄露或线上事故。
- P1/P2/P3 表示本规划的处理优先级，不表示漏洞评级。

否定结论检索范围：`backend/app`、`agent/src`、`frontend/src`、`system-admin-frontend/src`、`shared`、`scripts`；测试证据另查 `backend/tests`、`agent/test` 和前端相应测试。Pi 只检查锁定版本本地依赖的 session/settings/compaction/API 实现。未搜索互联网，未调用线上模型，未读取线上私有记忆。

所有复现数值见 [evidence.json](evidence.json)。A/B/C/D/E 对应父 PRD 的五个子任务。

## 实际架构

Assistant：
`AgentMessage → AgentRun/Job → sidecar context → 同 session 文字重放 + 一次 RAG → Pi → 公开事件 → AgentMessage`。
来源：`backend/app/api/agent.py:323`、`backend/app/api/internal_agent.py:418`、`agent/src/worker.ts:159`、`backend/app/services/agent_events.py:177`。

Memory/RAG：
`候选 → 明确确认 → Memory → RAGDocument/RAGChunk → SQLite FTS5 → ContextBuilder`。
核心实现为 `backend/app/services/memory_rag.py`；`memory.py`/`rag.py` 是兼容门面。Assistant 实际走 `context_builder.ContextBuilder`，不能因另一同名 build 函数存在就推断已接线。

Steward：
`maintenance → 确定性作业 → DerivedFact/视图/建议/卡片 → 可选辅助批次 → 结构化验证产物`。
每次模型调用只有 system + user，没有聊天历史；`steward_guard.py:101,128,136` 白名单生成输入，`steward_assist.py:413` 生成请求。

## MR-01

**P1 / 已复现 / A：记忆新增和 RAG 保存缺少来源。**

`frontend/src/components/memory/MemoryEditorDialog.vue:93` 只传原文、摘要、用途、scope、sensitivity；`MemoryRagPanel.vue:45` 也丢掉原始引用。store 原样转交，API 只补 source_span。后端 `memory_rag.py:202` 要求 message/doc 来源，真实 UI payload 返回 422 MEMORY_STATE_CONFLICT。

建议：新增明确 manual/agent_message/rag_chunk 来源合同。手工文本的原始来源是本人此次输入，不生成假的授权文档；RAG 来源按 ID/版本从后端重新授权。不能只给前端补一个随意字符串。

## MR-02

**P1 / 已复现 / A：候选 DTO 字段错误，提交后返回 500。**

`backend/app/models/memory.py:52` 为 source_quote，`backend/app/schemas/memory.py:29` 要 raw_quote；`api/memory.py:30` 直接 model_validate。带来源请求已提交候选后返回 500，随后 pending 列表也返回 500。创建、dismiss 都在 commit 后转换 DTO（同文件 :51、:96）。

建议：保持 HTTP raw_quote，通过明确映射修复；flush 后先构建并验证响应再 commit；引入账户内操作幂等和确认唯一性，覆盖丢响应重试与并发确认，避免只改字段后留下重复写入风险。

## MR-03

**P1 / 已复现 / B：两字中文和正常问句召回失败。**

`backend/migrations/versions/0014_memory_rag.py:245` 使用 tokenize='trigram'；`memory_rag.py:545` 把整句包成引号短语；:673 使用 BM25，:693 排序，默认 limit=20。样本“今年春节在上海聚餐，外婆喜欢清淡饮食。”，查询“春节”“上海”“今年春节在哪里聚餐？”均 0，“在上海”及“春节在上海”各 1。

建议：短查询后备路径、自然问句的有界检索词规划及排名评估；继续参数化、先限定可访问范围。BM25 不是 embedding。当前 :694 的 SQL LIMIT 早于 :712 的作者可见性二次过滤，可能筛掉前排结果后不补足；这是静态召回风险，纳入测试而不声称已实测泄露。

## MR-04

**P2 / 静态确认 / B：查询只取最新用户原文，不消解追问。**

`backend/app/api/internal_agent.py:447` 查最后一条 user，:462 传 query=latest_text；没有历史查询规划。“他呢？”的模型推理与 RAG 检索上下文是两件事。

建议：仅在可明确解析的追问中使用同 session、同 scope 的有限前文；无依据不猜实体。新会话不继承其他会话全文。正常同 session 并发已有 guard 和唯一约束，不把“较新消息抢走当前问题”登记为现有故障（`agent_queue.py:242`、`models/agent.py:120`）。

## MR-05

**P3 / 能力缺口 / E：一次预取，没有模型主动检索循环。**

`agent/src/worker.ts:162` 每 Run 取 context 一次，:243 拼接；工具环不重新调用 ContextBuilder。完整工具表 `agent/src/tools.ts:31` 不含 search_memory/search_rag/remember。`search_space` 是家谱名字/关系文本查询（`agent_query.py:522`）。

建议：先测一次检索改进的收益，再评估有 scope、次数、token、幂等/取消边界的主动检索工具；不在 Pi context hook 中查库。不是本期必须扩大工具权限的理由。

## MR-06

**P3 / 静态确认的未接入能力 / E：聊天不产生长期记忆。**

`memory_rag.py:138` 默认 detector “Return no implicit candidates by default”，:145 return []；生产目录无实例化/聊天调用。Assistant 只显示引用，没有“保存此消息”动作，`MessageList.vue:134`。离线执行默认 extractor 返回 0 条。

建议分开评估显式“保存这条消息”和默认关闭的候选提取。无论哪种方案都只生成 pending 候选，必须由用户确认 scope/保留期；不将模型承诺“记住了”作为持久化成功。

## MR-07

**P3 / 静态确认的未接入能力 / E：授权文档导入只有 service。**

`memory_rag.py:474` 有 ingest_authorized_document，`rag.py:18` 是别名导出，生产无调用；实际调用在测试。现有 memory API 没有上传→解析→授权→索引流水线。

建议规划来源许可、文档版本、格式/大小限制、受控解析、撤销依赖和重建。家庭故事、profile、public_kinship 在允许类型中，不代表已存在生产数据入库者。

## MR-08

**P3 / 预留能力 / E：embedding 字段不等于语义检索。**

`backend/app/models/rag.py:118` 默认 embedding_status='not_configured'；在上述生产与依赖声明范围无 embedding 模型调用、向量数据库、混合召回或 reranker 链路。

建议以固定问答集比较词法基线、混合检索、重排的质量/延迟/费用与隐私。没有净增益不引入额外基础设施；敏感资料不得为向量化绕过本地要求。

## MR-09

**P2 / 静态实现限制 / B：固定分块没有语义边界和重叠。**

`memory_rag.py:415` 按 1200 字符步长切片；:459 仅索引 Memory.content，默认来源是确认时的 summary（:358），并非 raw_quote 全文。

建议句/段边界优先、有界重叠、稳定 chunk 定位和 index_version。测试跨块姓名/事实、短文本、长中文、中英混合，明确摘要被编辑后可检索的内容范围。

## MR-10

**P2 / 静态实现限制 / B 的局部修复 + E 的全请求方案：预算不完整。**

`context_builder.py:91` 默认 2000；:149 用 len(text)//4，超预算整块排除。预算不含聊天、system、工具定义、工具结果和输出预留。后端会话全文不截断（`internal_agent.py:432`）。Pi pre-prompt compaction 在新 user/RAG 加入前执行（依赖 `agent-session.js:864`）。

建议 B 改善 RAG 估算及排除原因；E 单独设计 Provider 请求总预算、模型窗口快照、压缩后复核和明确超限失败。中文除以四不是真实 token 数；不要把修复 Pi 历史同步写成全请求预算已经完成。

## MR-11

**P1 / 已离线复现 / C：历史没进入 Pi 压缩源。**

`agent/src/session.ts:377` 创建内存 manager，:420 仅覆盖 agent.state.messages。Pi 默认 compaction enabled（依赖 `settings-manager.js:548`），手动/自动路径从 manager.getBranch 取源，再用 buildSessionContext 替换 agent state（`agent-session.js:1410,1465,1674,1752`）。

实测：恢复后 state=2 条、manager=0；本轮后 state=4、manager=2；强制成功压缩后旧历史不在上下文，摘要请求也看不到旧历史。数据库未被删除。生产自动触发条件与发生频率尚未覆盖。

建议通过同一个 SessionManager.appendMessage 预填历史，再交 createAgentSession 恢复；SDK 公开支持（`session-manager.d.ts:217`、`sdk.js:81,239,253`）。禁止恢复历史时触发模型 turn。

## MR-12

**P3 / 有意边界及后续能力 / C/E：恢复的是文字，不是完整执行状态。**

`session.ts:397` 仅转换 user/assistant text；`events.ts:173` 丢弃 thinking、usage、raw tool call；工具事件不保存原始结果；重启后重建 Run。旧 RAG blocks、tool results、Pi checkpoint/summary 不跨 Run 重放。

建议保留当前授权边界。若评估跨 Run 摘要，必须有 account/space/session、覆盖消息 ID、版本、失效、尾部去重和权限合同，不保存整个 Pi session，不把摘要中旧检索材料变成隐藏永久记忆。

## MR-13

**P1 / 已复现 / D：打开 RAG 不补建旧记忆。**

`memory_rag.py:379` 只在确认当时开启 RAG 才 index；`platform_features.py:60` 仅更新配置；rebuild_index(:926) 只有定义/导出，没有生产入口。

实测：关闭时保存，开启后命中 0；显式 rebuild 后命中 1。建议独立于 Steward 的有界维护补建，覆盖启动、开启、重试；不在 admin PUT 中做全库长事务。接线前必须先修 MR-25。

## MR-14

**P2 / 静态确认 / B：RAG 引用未贯通回答事件与历史。**

`context_builder.py:58` 有 source/citation 元数据；`worker.ts:243` 仅把 citation+content 拼成用户文本。sidecar `events.ts:173,235` 只回 text/web_citations，后端 `agent_events.py:193` 也只存这两类。

前端已有 `stores/agent.ts:171` 的 payload.citations 解析，历史和 SSE 共用；`MessageList.vue:147` 已渲染 CitationList。缺的是可信回传与校验，不应再造一套 UI。建议根据实际回答使用的句柄和本 Run included ContextBuildItem 校验；支持刷新、伪造/失效反例及 16 KiB 事件上限（`agent_events.py:60`）。

## MR-15

**P2 / 状态与文档治理 / D/E：有效开关不能仅看界面或默认值。**

`config.py:216` 默认关闭；`platform_features.py:38` 无 DB 行用环境值，有行是 DB AND 部署值。当前代码和测试是审计事实。归档 `09-13-memory-enable-entry/design.md` 的优先级段落互相矛盾，本任务不据历史文案改默认。

Steward 三类辅助另有平台×空间开关和模型/云同意门禁；平台治理归既有 `09-13-steward-assist-platform-switch-admin`。该任务历史日志同时记录开关处置前 0 调用及处置后 candidate/explanation 成功，不能只引用前半段声称线上仍未运行。本轮未重新验证线上状态。

## MR-16

**P2 / 静态确认 / A：Memory 关、RAG 开时仍可点保存。**

`MemoryManager.vue:407` 可以挂 RAG 面板；`MemoryRagPanel.vue:96` 保存按钮未按 Memory 功能禁用，后端写请求会被拒绝。归档 memory-enable-entry 设计明确要求此组合隐藏保存。

建议前端根据实际功能状态显示/禁用写入口，服务端最终门禁保留；四种组合及状态读取失败均测试，不把 404/网络失败当作功能关闭。

## MR-17

**P3 / 产品语义与生命周期边界 / A/D/E：删除不是物理擦除。**

`memory_rag.py:801` 将 Memory 标 deleted，:775 标文档/chunk invalidated；FTS 触发器针对 text 更新/物理删除（迁移 :249），状态失效不会清空旧文本。expire 在读取边界触发，聊天历史不跟随删除。

建议准确说明撤销/删除的未来召回语义；来源依赖必须失效。物理清理涉及备份、保留期、审计和不可逆处理，另立批准后的设计，不自动删除主库/备份，也不承诺撤回已发送给模型的内容。

## MR-18

**P3 / 功能缺口 / E：没有编辑已确认记忆或重设 scope 的入口。**

`api/memory.py` 提供候选确认、dismiss、list、revoke、delete，没有 PATCH/PUT；MemoryEditorDialog 实为创建候选。建议需要时采用可追溯的新 revision/确认流程，明示旧索引失效和引用版本；不直接改正文而留下旧索引。

## MR-19

**P3 / 明确延期能力 / E + 既有任务：Steward 尚未接入共享 RAG。**

`steward.py:1173` 排除 memory/rag 事件；所有实际 prompt 来自 `steward_guard.py:101,128,136`。生产 Steward 无 ContextBuilder/search_rag 调用。底层 steward consumer 只能证明权限谓词存在，单测不能证明生产接入。

`09-11-steward-capability-followups/prd.md:4,20` 已登记延期研究。建议本任务给出共享检索接口/失效/引用合同与收益评估，实际接入仍由既有任务负责，不读 private memory/session，不改确定性事实结论。

## MR-20

**P3 / 当前能力边界 / E：状态积累不是模型经验学习。**

`steward.py:875` checkpoint 只有 cursor、policy、finding signatures、stats；:216/242 的 ActionCard 冷却有生产读写路径，但 :228/245 在 BEHAVIOR_PROJECTION_ENABLED 关闭时分别不写入/返回 false，不能据此推断今天线上有效；候选去重在模型返回后发生。`steward_guard.py:128` ranking 只有 card_id/kind；`steward_assist.py:413` 没有历史消息/反馈摘要。

建议准确命名业务进度、反馈状态与模型学习；评估排序相对确定性基线的增益后再扩充有界结构化反馈。模型权重没有在应用中训练的证据。

## MR-21

**P3 / 未接入能力 / E：偏好投影无消费者。**

`steward.py:266,349,363` 可重建 correction_preference/term_usage，但上述生产范围无 rebuild 调用及这些键的读取者。真正称谓偏好在 `terms.py:268`，使用/晋升在 :583；Assistant record_term_usage 要显式同意（`agent_tools.py:648`）。

建议不再复制一套称谓真源；只有明确消费者才启用投影。记录键/来源/时效/可见性和幂等合同；必须遵守 MR-26 的键族保留要求。

## MR-22

**P3 / 已知范围限制，未做时间推进复现 / E：Suggestion 冷却只写不消费。**

`steward_suggestions.py:634` 写 cooldown_until/evidence_hash，:554 列表只看 dismissed_at。生产范围未找到到期判断。旧 `09-11-steward-candidate-review/notes.md:67` 已记录 UX 语义未接入 job 强制过滤。

不能因此宣称“7 天自动恢复发生回归”；ActionCard 是另一机制，已有生产读写路径且在行为投影开关开启时生效，本轮未查线上开关。建议先保持同证据驳回状态，后续若批准到期恢复，统一列表、通知、投影规则并冻结时间测试。

## MR-23

**P2 / 静态生产通路缺口，待多作业复现 / E：结构去重挡住新证据更新。**

`steward_assist.py:1174` 仅按 kind/端点摘要；:1175 查到同结构候选便跳过。`steward_suggestions.py:301` 永久排除已被建议引用的候选。已投影候选在新证据下再次生成时难以进入 upsert 新证据路径，与旧候选审核 PRD :44 的证据换版 supersede 目标冲突。

现有 `test_steward_suggestions.py:153` 直接测试 upsert，fixture :101 使用自造 digest，绕过上述生产链。建议先以 fake transport 多 job 复现，再设计“稳定结构身份 + 相关证据版本”；避免把无关空间事实变化混入去重键而重新骚扰用户。

另一个应分开的取舍：辅助在 core 提交后落候选，下一次 core 才投影（`steward.py:1035`、`steward_suggestions.py:296`）。这是异步最终一致性；先测可见延迟，确有需求再加幂等后续投影，不能制造再次调用模型的循环。

## MR-24

**P1 / 验证缺口 / 全部子任务：绿色单测没有覆盖真实链。**

已运行：后端 147、sidecar 45、前端 19，共 211 项通过。记忆前端测试 mock create 成功，服务测试直接提供来源；缺少真实 POST candidate 成功链。Pi worker fixture 主要为单条 user；无历史压缩回归。召回测试多为原文英文短语，不能代表中文聊天质量。

建议保留已通过边界测试，补真实 API、实际 Pi 假流、固定中英文与权限数据集、来源失效、重启/重试、SSE/历史引用测试。当前没有线上启用状态、真实模型质量或延迟结论；完整 build/lint/线上 smoke 未在只读审计轮运行。

## MR-25

**P1 / 静态生命周期缺陷，规划复核新增 / D：直接接 rebuild 可能复活 tombstone。**

`memory_rag.py:929` 只找 active document，找不到即 index_memory；后者 :453 把同 revision 旧文档重新设 active，并在 :456 删除重建 chunks。当前 source/revision 只有普通索引（`models/rag.py:60`）。

建议修复后才接生产补建：区分从未建索引与已失效；当前来源、retention、权限依赖和 revision 都必须有效；现存同 revision 的 tombstone 不自动复活。重复有效索引应保持 chunk ID/引用，唯一性与迁移前重复数据报告保证并发幂等。此静态路径不等于已证明线上泄露。

## MR-26

**P2 / 条件性风险，规划复核新增 / E：直接接行为重建会清除其他键族。**

`steward.py:297,304` 删除整个空间/账户 BehaviorProjection，:287,315 只重放三类 card/term 事件；亲属推荐冷却由 `family_recommendations.py:230` 写独立键族，未包含在重放来源。

目前 rebuild 没有生产调用，不能称为正在发生的数据丢失。建议未来先限定删除键族、完整事件来源和幂等回放；测试其他冷却完整保留后才能接入 maintenance。不要为补“学习能力”直接打开未接通 helper。

## 已排除的误读

- “Pi 完全没有自动压缩”不成立；真正问题是恢复状态不同步。
- “两个 Agent 都在用 RAG”不成立；共享服务支持 consumer 不等于生产调用。
- “个人称谓完全不记忆”不成立；TermRegistry 有真实持久化和明确使用积累。
- “所有冷却无效”不成立；ActionCard 的 until 在行为投影开关开启时有实际消费，Suggestion 需单独讨论。
- “线上辅助至今为零”未经本轮验证，且既有任务记录过处置后的成功调用。
- 同 session 活跃 Run 有事务与数据库并发保护，本次未发现正常消息路径串轮问题。

## 执行阶段增量证据（2026-09-13）

以上条目保留审计/规划时的判断。以下是在用户批准实施之后新增的结果，不覆盖原证据，也不代表相关业务补丁已经完成。

- **MR-23：隔离的完整生产链已复现。** E 使用真实迁移库、core runner、候选批处理、后续 core 投影与 dismiss 服务；仅模型 transport 为假。相关支撑事实从 2 条增加到 4 条后，5 次合成调用均被应用，但仍只有同一候选和同一 Suggestion，旧 evidence hash 与 dismissed 状态保持，新相关证据未进入建议。无关事实、无新事实及已应用批次重放作为对照。共 15 次假调用、0 次真实 Provider 调用。
- **MR-26：条件性破坏已复现。** 由真实 PFV rebuild + dismiss_recommendation 创建非所属键族冷却后，行为开关开启时，账户级和空间级 rebuild 均删除该冷却；关闭时完整保留。两次重放的所属键族结果一致。该结果不改变“尚无生产 rebuild 调用”的原范围判断。
- 主线程抽查了 harness 的实际调用链、预先写入的支撑事实标注、JSON 结果和源码 SHA-256；10 个生产源码哈希与 harness 哈希均吻合。报告记录真实 Alembic head `0041_term_pack_expansion`、零网络连接尝试。没有读取线上有效开关，也没有验证真实模型质量或线上发生频率。

完整机器记录：[E 合成复现结果](../../09-13-agent-memory-capability-plan/research/steward-capability-results.json)；脚本：[steward_capability_probe.py](../../09-13-agent-memory-capability-plan/research/steward_capability_probe.py)。后续修复边界与采用/延期决策归 E 的评估交付。
