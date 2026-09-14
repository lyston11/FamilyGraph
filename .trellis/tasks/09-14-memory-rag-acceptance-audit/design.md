# 设计：验收缺口的最小修复路径

## 状态与依据

本文件是当前授权执行的修复方案。基线 `bd899b9` 的实际行为以 [独立报告](research/findings.md) 为准；2026-09-14 用户授权审查、验收及通过后的提交、合并、归档。约束来自当前 AGENTS、父任务和 B/D 已审定 PRD/design；历史 `.trellis/spec` 索引已标不可执行，不作为新增门禁。

原 A 的来源 resolver、C 的同一 SessionManager、B 的词法改善/原请求指纹/固定补取、D 的 tombstone 原因区分均继续复用。不重写队列状态机，也不新增第二套授权系统。

## 1. 执行身份必须进入实际读写边界

将签名 token 中的不可变身份显式传入 context、events、heartbeat、settle、tools 和 provider admission：至少含 run_id、job_id、account/space、kind、**expected_attempt**。expected_attempt 只能来自验证后的 claims，不能从随时会变化的 ORM run.attempt 回填；类型为非 bool 的正整数。

沿现有 SQLite writer/CAS 边界重新加载并比较 Run/Job 的双 attempt、绑定、状态、取消与本操作必要的 lease 条件，然后读取 seq/build 或写入。比较后的相关写入必须在同一个短事务内；仅 refresh、SELECT 或进程锁不能封住“检查后换租约”。明确 rowcount=0 的冲突错误与有界重试，不能漏成 500。

Provider 在短事务准入成功后再进行网络 I/O。已经合法准入的在途请求按现有合同处理，不为本次修复持有网络长事务或承诺撤回已发送内容。工具也在既有准入/提交点消费签名身份，沿用取消和终态优先级。

对应 B-I01；`research/protocol-preflight.md` 提供现有可复用写点。复查已证实 context/events/heartbeat/settle/tool 五条 TOCTOU 反例；provider 目前仅静态，应补真实准入层配假 upstream 的反例。

## 2. ContextBuild 与精确片段证据

一个 (run_id, attempt) 只有一个有效构建。先在适当短事务取得执行身份和既有构建，再决定复用/创建，避免先搜索后插入发生唯一键 500。

构建记录持有 query/plan version、budget estimator version、policy/provider 决策依据与不可逆的失效状态。复用只重读原 included 集；来源或政策不再允许时持久标记失效并返回 `AGENT_CONTEXT_INVALIDATED`，之后即使权限恢复也不能在该 attempt 偷换内容。

服务端每项保存的定位证据至少包括：

```text
source_type, source_id, source_revision
document_id, chunk_id, index_version, chunk_index / span
content_hash, citation_handle, included
```

这些是服务端证据，不允许 sidecar 自报内容 hash 后直接认证。`RAGHit → ContextSource → ContextBuildItem → 已认证消息记录` 全程保留；原片段解析与 A 的当前来源授权分开执行。document 的活动版本只控制新检索，历史精确读取不强制等于活动版本。

当前 blocks_json 额外保存正文，却不含完整定位，复用还仅按 source_id 选择 document。建议改为上述不可变描述符加重新授权的精确读；若确有快照需求，单独明确留存/访问/失效规则。现存快照不能在本分析轮物理删除；新 schema 通过前滚迁移兼容，旧无证据引用采用明确的未认证/不可用路径，不能猜填可信定位。

对应 B-I02/B-I05，依赖 D-I01/I02 的身份和不可变性修复。

## 3. 内部引用提交、幂等与公开投影

新增有界内部事件字段 `context_reference = {build_id, attempt, used_handles}`，与 public_payload 分离。sidecar 从实际完成的 assistant 消息提取所用句柄，携带自己取得的 build；后端把它视作定位请求，核验 signed attempt、build 绑定、included 和精确来源证据。

指纹在认证/裁剪前计算，包含 type、原 public_payload 及原 context_reference 的规范化值。已存在 (run, seq) 的同请求确认原提交，不重算/改写持久 payload；异参冲突。新提交才进行当前权限认证、DTO/字节检查和消息/事件/指纹原子写入。旧缺 reference 的 sidecar 仅走批准的普通正文/web 兼容行为，不自动生成受认证 RAG 引用。

公开 citations、unavailable 数量和内部 provenance 字段由服务端独占，拒绝或剥离未经认证的保留字段。为避免迁移期“先剥离后指纹改变”，先规范化原请求再处理公开输出。

建立单一读取 projector，复用于：

| 出口 | 处理 |
|---|---|
| 浏览器历史 | 已授权会话 + 精确证据 + 当前读者；去掉原 stored citations |
| SSE / reconnect | 在短 Session 内取得同样投影，Session 外只发送 DTO/bytes；不能原样发送 public_payload 中的保留字段 |
| 固定 run/seq 补取 | 限定 run.session_id、assistant role、服务端消息 key 及事件类型 |
| internal context 历史 | 仅输出允许的正文，或复用同一 projector；不把撤权结构化引用回传 sidecar |

完整 UTF-8 public_payload 包含 role、转义和 web_citations。保留 16384 字节上限，先保持现有正文合同，引用按稳定顺序加入；不足时通过现有固定授权补取补全。联调验证“事件投影 + 固定补取”的有效读取结果，不错误要求每条 SSE 必须原生携带所有引用。自报假引用不能借此逃过覆盖。

对应 B-I03/I04/I09/I10；精确 chunk 认证由第 2 节复用。

## 4. 查询、补足与实际子预算

继续使用 lex-v1 的 NFKC、受控别名、停用词、500 字符/8 词项上限和 FTS5/参数化短词后备。扩展查询时仅输入已授权同 session 的有限 user/assistant 文字及本次问题；显式记录唯一 anchor 或降级原因。测试须有相同当前问题、不同唯一前文的对照，以及多实体/无前文/别会话反例，不能只凭当前词面命中判追问完成。

所有支路共享状态、来源依赖和 Provider 外发规则。稳定候选顺序下逐页补足，直到凑够合法结果或用尽总扫描预算；记录扫描、拒绝、返回及停止原因。控制总扫描而非把 SQL limit 无界放大。

实际 `ContextBuilder`、持久 token_estimate 和下发包装使用同一个估算器。以 `BuiltContext.as_data_blocks` 到 sidecar 最终材料字符串的真实包络为对象，计入 citation、标记、换行与正文；不能仅估算另一条未接入的 build_context。默认采用已声明的保守 UTF-8 算法并记录版本；不是实际 tokenizer 精确计数。整个块放不下则解释性排除，不能静默截断改变引用定位。

对应 B-I06/I07/I08。真实 system/history/tools/current user/output 的全请求预算继续归 E-O6。

## 5. 规范 document 与不可变 chunk

对 `(source_type, source_id, revision)` 建立真实数据库唯一约束，检查 revision/source_revision 镜像一致。新增约束前，使用元数据预检列出重复、冲突、未知 tombstone 和引用依赖；无法安全归并则拒绝迁移/新维护启用，保留所有现存记录。

创建采用约束裁决的幂等语义：并发胜者形成规范投影，另一请求重新读取同一结果；内容冲突明确失败。相同 source revision/index_version 的既有块必须验证位置、文本/hash、状态和完整集合，只允许一致重放。禁止为保住 ID 原地换文字，也禁止 staging 仅凭 chunk_index 存在就跳过校验。

该约束同时服务 B 的精确引用，不能独立重造来源权限。迁移编号在实施日读取实际 Alembic heads 后分配，不在规划中预占。

对应 D-I01/I02。

## 6. 有界维护、事务与版本换代

持久状态增加每轮固定扫描上界，持有 attempt、唯一 owner、expiry、round、policy_version 和目标 index_version。每轮捕获上界后只处理 `cursor < id <= upper_bound`；完成后才开始下一轮，新数据不会无限延长旧轮。失败项依 next_retry_at 重试，全轮扫描不能绕过退避。

取得/续租/提交均使用数据库条件更新，条件绑定所持的 attempt、owner、有效时间、round、policy、目标版本；检查 rowcount。旧 ORM identity map 的值不能授权操作。旧策略看到未来策略行应明确退出，不能递增其 round 或改回旧目标。

RAG 批次独立事务或完整可回滚 savepoint，物化/失败账本/进度一起提交。真实 maintenance tick 捕获失租前先回滚该批次；不要影响已有 Steward/core 职责，也不要日志报失败后执行外层 commit 留下部分结果。

换版使用相同 lease/policy 栅栏。计算目标块后，在最终短事务校验 effective RAG、来源状态/revision、预期旧活动指针、目标集合完整性与执行身份；全部新块写入和指针切换原子完成。撤销或失配必须回滚新块，不能只靠检索时挡住 invalidated document。

目标版本与可执行的切块算法显式关联。合法部署目标可以是当前算法版本；未知目标不能只接受任意字符串。新检索按活动指针与相应可读块工作，普通 ensure 仅恢复该策略要求的合法投影，不自动把更高/不同有效版本写回进程常量。测试的合成 v3 用于验证换代合同，不代表生产已有 v3 算法。

对应 D-I03/I04/I05/I06/I09。

## 7. 完整性、FTS 与降级

完整性检查比较预期块集合/版本/hash 和搜索投影，不能把“至少一块”当完整。缺失项按确定性来源恢复，既有片段保持不可变；无法确定恢复内容时进入失败账本，不能计 already_current。

FTS repair 只重建当前合法投影，复用与具体读者无关的来源合法性（verification/retention/根依赖），不改变 Memory/document 状态或 confirmation。暂时读者失权不写全局 tombstone。

0045 的现有 downgrade 在恢复旧唯一键前直接删除非活动版本，已复现破坏持久保存依赖。修复必须在任何 DROP/DELETE 前做兼容性与引用依赖预检；无法无损降级则明确拒绝。仅新增更高版本迁移不能保证随后执行旧 0045 降级安全，需一起审阅该降级入口的防破坏前置检查。正常回滚优先关闭受影响入口、保留数据并前滚修复，任何数据清理另行授权。

对应 D-I07/I08/I10。

## 8. 所有权与验收边界

B 负责执行身份、context/query/预算、引用协议与所有读取出口；D 负责持久索引身份、不可变块、维护/换版、FTS 和迁移。两者共享 memory_rag、模型和迁移序号，全部串行。C 的 `8e91c42` 在后续累计验证时单独纳入，不能宣称 bd899b9 已包含它。

最终以 [验收矩阵](research/acceptance-matrix.md) 和 [实施计划](implement.md) 收口。原 A 回归、16 KiB/原请求幂等、RAG-only 和读者权限区分作为正对照保留。原 26 项及 E 的建议继续由已有唯一所有者处理。
