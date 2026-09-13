# Design：有界词法检索与可信引用

## 1. 生产入口与查询规划

保持 browser /rag/search 和 Assistant ContextBuilder 复用 memory_rag 检索，不接另一个未使用的 build_context 实现。ContextBuilder 是后端数据库边界；Pi context hook 只消费已准备的数据。

QueryPlan 是内部值对象，包含规范化问题、有限词项/别名、明确的前文锚点、plan_version、降级原因。建议起始上限：当前 query 500 个字符、最多 8 个有效词项、同 session 最近 4 条允许的文字消息；这些上限在实现时通过现有接口限制与合成性能用例校准并固定，不自动取整个历史。查询计划日志只存版本/计数/耗时，原文和人物名不写日志。

规范化采用 Unicode 规范化、空白/标点处理与受控分词；FTS 运算符始终转义或作为普通文本。精确短语、拆分词项和短词后备各自产生有限候选，再合并去重。问句词如“哪里/什么时候”不应成为唯一命中依据。实体别名/领域词来自有出处、可版本化的有限词表；“过年→春节”不能被描述为开放语义理解。

追问解析只接受唯一、明确的同会话锚点。“那里有什么安排”只有前文已唯一指出地点时才扩展；两个人或两地点并列时不猜。检索请求仍受本次账户/空间/Provider 的政策约束，前文不能扩大权限。

## 2. 召回、范围与排序

所有支路先用相同 eligibility/source resolver 限制状态、空间、作者和 A 的持续来源依赖；不能让短词查询绕过 FTS 路径上的保护。FTS、参数化短词后备只读已获权的有限候选；短词搜索不能变成未过滤的全库 LIKE。

第一版保留 BM25 作为词法分数的一部分；不同召回支路不直接比较不兼容原始分值，采用显式归一化/秩融合与稳定 source/chunk 平局规则。不能依赖 ORM/数据库默认顺序。

作者等二次过滤会导致 LIMIT 后不足，因此在有界扫描预算内补足；候选扫描量、返回量和停止原因可测。高频单字、空串、超长输入和仅标点返回清楚的不足/缩小查询提示，不扩成无限扫描。下游仍执行当前敏感内容本地 Provider 门禁，检索权限和模型外发权限分别成立。

## 3. 分块、索引版本与预算

分块按段落/句子边界优先，遇到过长句再有界切分；保留有限重叠，设最大块长/最小有效内容。检索基线继续索引 Memory.content（通常是确认的 summary），raw_quote 不自动加入。授权文档导入尚未接线，不能以现有 source_type 枚举假装已有正文。

source revision 表达来源内容版本；index_version 表达切分/检索算法版本。一个规范 document 对应 source_type/source_id/revision；chunk 身份绑定 document、index_version 和片段位置/内容摘要，document 记录当前有效 index_version。内容和切分版本未变时复用 ID；切分升级先物化新版本 chunks，再原子切换有效版本，旧引用仍定位原片段或显示不可用，绝不悄悄指向另一段文字。D 负责批量迁移和防复活，B 交付确定性切分与版本合同。

RAG 包装标记、句柄及正文一起参与预算。能获取与本轮模型匹配且验证过的 tokenizer 时使用；否则采用明确记录的保守 Unicode/UTF-8 估算，不能统一除以四。优先选择完整有意义块；若设计片段截选，必须记录范围并保持引用定位，不任意从字节中间截断。排除原因至少包括预算、无效来源、无效 trust；不写正文日志。

本子预算只控制 RAG。system、历史、工具定义、当前 user、tool results 和输出预留的总和仍由 E 设计；C 修复历史同步不改变这个限制。

## 4. 引用跨层合同

当前 BuiltContext.as_data_blocks 输出 citation/content，前端 MemoryCitation 使用 citation_handle/可选 text；必须显式映射，不能简单透传。source_id 保持现有 string 合同。

目标 AgentMessage.content_json 的最小结构（不是整个公开事件；message.assistant_added 的事件仍保留 role: assistant）：

```json
{"text":"回答正文 [来源句柄]","citations":[{"source_type":"memory","source_id":"42","scope":"private","sensitivity":"normal","revision":1,"citation_handle":"本轮有效句柄"}],"web_citations":[]}
```

示例句柄只说明位置，实际值由服务端生成；sensitivity 使用现有枚举。可选 document_id/chunk_id/index_version 仅在服务端已有可信定位时附加；默认省略 text，避免持久事件复制私有摘录。build_id、候选句柄及原请求指纹属于内部提交合同，不直接成为公开引用字段。

实施前沿以下链统一字段：
1. 后端 ContextBuild/Items 记录 Run、attempt、included 状态、source/revision、片段及允许句柄；一个 attempt 只有一个有效 build。
2. internal context response 显式携带 context_build_id；agent client parser/type/schema 保留该字段，当前 parser 丢弃它的行为一起修复。
3. worker/prompt 标记材料是非可信数据，要求引用真实使用的句柄；本轮 context 仍只加入一次。
4. events 从最终 Assistant 文本/结构中提取使用的句柄，形成候选引用；提取不能把材料列表本身算作回答使用，也不能把不完整流片段先认证。事件条目增加有界内部 context_reference（build_id/attempt/used_handles），与 public_payload 分开；内部字段参与指纹和 schema 校验，不复制到公开事件。
5. 后端对尚未提交的新事件，以有效 Run/attempt 身份定位 build，核对 included item、handle 和 A resolver 的当前来源权限，产生可信 citations 后原子写 AgentMessage/公开事件；已提交事件的重放另按下节处理。
6. 前端复用现有 parser/CitationList；后端 api/agent.py 的 _message_out 与 _wire_event 分别在历史读取和 SSE/重放时投影当前可读元数据，详情再走授权端点。

后端不能信任 sidecar 自报的来源字段或 build_id；客户端传的 ID 只能定位。当前 handle 是来源片段标识，可合法出现在多个 Run；拒绝的是 build 不绑定当前执行或 handle 不在该 build 的 included items，不能仅因相同 handle 曾出现过就拒绝。无效引用不获认证；正文作为未核验文本呈现，不把模型文字改成事实确认。此校验证明来源绑定，不证明每句话都被材料支持；语义忠实度由评估单列。

复用现有 AgentRun.attempt/AgentJob.attempt（lease 时同步递增），在 ContextBuild 增加绑定/唯一性，并为新 run-token 签入 attempt；_authorize_run 必须与当前 Run/Job 比较。当前 token 没有此约束，不能把它描述为已实现的租约隔离。lease_expires_at 会续租变化，不能用它充当不可变执行身份。旧 token 缺 attempt 时不自动补成当前值；升级先排空旧 lease 或让其重新领取，兼容与回归须覆盖此过程。

同 attempt 重复 GET context 返回同一 build，且每次重新授权；不重新生成一个可竞争的有效 build。若来源/政策变化使旧 build 不再合法，返回明确 context_invalidated，须由新的 attempt 重建；当前已拿到材料的模型事件仍须写入时再次核验。新 attempt 的 build 不能被旧 token 借用。原始请求指纹也包含 attempt/build 绑定。

历史引用在来源失效后不返回受限的 source_id/句柄/摘录。采用兼容的外层 unavailable_citation_count 表示不可用数量，citations 数组只包含当前可读且满足现有六字段合同的项目；前端为该可选计数显示普通“部分来源已不可用”提示。不要把缺字段对象塞入现有 parser 后悄悄丢弃。计数只在用户已获权读取该消息/事件时给出，不对外枚举来源。旧消息无计数按 0 处理，历史/SSE/重放规则相同。已发送回答中的既有文字不承诺由本任务物理擦除，见 E。

## 5. 16 KiB、重放与兼容

事件幂等以规范化原始请求指纹为依据，不能比较 sidecar 候选 payload 与服务端认证后 payload 是否相同。保留现有身份/权限门禁后，先查 (run_id, seq)：同类型/同原请求指纹确认原提交且不重新生成持久 payload；异参冲突。只对尚未提交事件做当前来源认证、DTO/字节校验和原子写入。请求指纹与已认证结果在同一事务保存；旧事件保留既有原 payload 比较兼容路径，不把未知指纹当相同。网络丢响应后来源撤销的重复提交不改写原记录；读取面遮罩独立执行，不因幂等而返显受限资料。

按 UTF-8 编码后的整个 public_payload 测量，role、JSON 转义和 web_citations 都计入。先保留当前正文可持久化合同，再按稳定顺序纳入最小 citation 元数据；设引用数量/字段长度上限。额外引用过多时提供可解释的省略计数或已验证来源入口，不能无提示把未展示来源算作全部已展示。

采用独立授权读取作为固定后备：完整已认证引用以有限元数据保存在 AgentMessage.content_json，与事件/原请求指纹同事务；公开事件只附能在 16 KiB 内容纳的引用。新增 GET /api/agent/runs/{run_id}/events/{seq}/citations，按已授权会话和服务端生成的消息幂等键定位对应 assistant 消息，重验来源后返回完整 citations/unavailable_citation_count，不返回摘录。条目数受 included 来源数上限约束，不提供任意历史来源枚举。

前端在 assistant_added 没有完整引用元数据时按已有 run_id/seq 补取；正文已占满 payload 时不强加连一个计数字段，也能由这个固定读取动作补齐。历史直接使用同一消息引用投影，最终展示与 SSE 加补取一致。新增接口失败显示可重试的来源加载状态，不吞掉正文、不把未加载来源当作已核验。旧消息无引用返回空集合；无需增加新的公开事件种类或提高 MAX_PAYLOAD_BYTES。

允许旧 sidecar/旧消息没有 citations；新前端不因缺字段失败。实施采用兼容的后端校验/schema先行，再部署 sidecar；不支持的新增字段在测试环境验证返回语义。现有 web 引用、运行终态、取消与 (run_id,seq) 规则保持。

## 6. 文件所有权与风险

backend：services/memory_rag.py、context_builder.py、agent_events.py、agent_tokens.py、api/internal_agent.py、api/agent.py、相关 schemas/models 和检索/事件/合同测试；token attempt 与事件原请求指纹是新增的有界协议工作，不能假定现有代码已经满足。
agent：client/parser/schema、worker.ts、events.ts、prompt 及集成测试；保留 C 的 session 回归。
frontend：既有 memory citations 类型、agent store、CitationList/MessageList 和 SSE/history 测试；按需要修复，不重造引用 UI。

A/B/D 均触及 memory_rag，默认串行；新增迁移必须在当前 head 上排号。重点反例：短词越权、指代猜测、legacy 返显、同源换块引用漂移、伪造引用、长中文 payload。对照 B-AC1～8 验收。
