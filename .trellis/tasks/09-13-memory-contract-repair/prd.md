# PRD：记忆候选与管理端到端契约修复

## Goal

恢复已有“新增记忆”和“检索结果保存”的完整使用链，确保创建成功、候选可读、确认可重试且来源权限不被复制绕过。

父任务：[双 Agent RAG 与记忆治理](../09-13-agent-memory-rag-remediation/prd.md)。用户已在最终规划后回复“执行”，本子任务进入实施。所有者 A；负责 MR-01、MR-02、MR-16，并落实 MR-17 的用户可见语义。完整证据见 [台账](../09-13-agent-memory-rag-remediation/research/audit.md)。

## Confirmed background

- P1/MR-01：`MemoryEditorDialog.vue:93` 的真实请求缺 source，后端 `memory_rag.py:202` 返回 422。
- P1/MR-02：`models/memory.py:52` 的 source_quote 与 `schemas/memory.py:29` 的 raw_quote 不匹配；创建提交后 500，pending 列表 500。
- P2/MR-16：Memory 关闭、RAG 开启时，`MemoryRagPanel.vue:96` 仍显示可提交的保存入口。
- 当前模型有消息 FK SET NULL 和“必须有 message/doc”的 CHECK；增加手工来源时必须一起处理删除来源的约束，不能只删校验。
- 普通聊天不自动创建候选；本子任务不改变这个约定。

## Requirements

### A-R1 明确的来源

支持显式手工输入、本人原始 user 消息及当前获权的 RAG 片段。手工内容的来源就是本人此次提交，不伪装为授权文档。agent_message 独立快照不接受 Assistant、工具或 RAG 派生消息，未来支持时另保留引用来源依赖。RAG 来源必须由服务端回读，原文只读；摘要与用途是用户整理内容，不能冒充源文档原话。保留作者、原始来源、版本、时间与确认记录。

### A-R2 来源权限贯穿生命周期

复制 RAG 片段不得扩大 scope、降低敏感度、带到无权空间或摆脱原来源撤销。候选创建/列表/确认、Memory 列表和检索均重验适用授权。撤销导致的不可用正文不从旧候选响应重新泄露。引用字符串不是授权凭证。

本人原始 user 消息已确认的快照与聊天删除分别管理；删除会话不应被 Memory FK/CHECK 阻塞，来源缺失仍保留追溯标记，不自动改成 manual。该独立快照规则不适用于 Assistant、工具或 RAG 派生文本。用户主动删除 Memory 仍按既有生命周期失效，不承诺物理擦除。

### A-R3 响应与存储一致

保持 HTTP raw_quote 合同。列表、创建、确认、忽略均能序列化；事务提交前已验证响应形状。新 UI 的创建与确认可安全重试，同键异参可解释拒绝，并发不能确认出两条 Memory。禁止“已经提交但返回字段异常”这一已复现路径。

### A-R4 兼容与开关

新客户端必须显式提交 source.kind。旧无来源手工请求与旧 RAG 保存请求形状相同，无法安全适配，因此继续拒绝并提示刷新客户端；这类请求在旧版本也未成功。旧消息引用仅在本人原始 user 消息规则下兼容，HTTP raw_quote 合同继续保留。

任意 source_document_ref 不构成授权证据。存量无法验证的来源或 Assistant 派生来源保留 legacy/unverified 标记、原文及当前 scope/确认历史；来源验证前不新增索引、不从旧索引返回、不进入 AgentContext。本人管理入口仅返回获权元数据/待验证状态，不展示原文和摘要。只有可验证适配器补齐证据后才恢复相应访问，不自动改 manual、扩大权限或物理删除。Memory/RAG 四种组合分别可测，前端不能用假成功覆盖服务端状态。

## Acceptance Criteria

| ID | 可观察结果 |
|---|---|
| A-AC1 | 修复后 UI 的显式 source payload 经 FastAPI 和迁移库创建 201、pending 列表 200；原话/摘要/来源对应；旧缺来源 payload 返回可解释拒绝 |
| A-AC2 | 确认后只生成一条 Memory；候选本身不可检索，确认后可由匹配关键词检索；dismiss/list 无 500 |
| A-AC3 | 同幂等键同输入重放同结果，异输入冲突；模拟提交后丢响应及并发确认不重复写入 |
| A-AC4 | 他人消息、Assistant/工具/RAG 派生消息快照、跨空间/过期/已撤销片段、错误 revision、伪造引用、敏感度降级和共享扩大被拒；拒绝无伪成功 |
| A-AC5 | RAG 复制后原来源/成员资格失效，候选和记忆各读取面均不返回受限原文，补索引也不能绕过 |
| A-AC6 | 删除消息/会话不产生 FK/CHECK 500；原始来源种类和审计仍可辨认；已确认本人原始 user 快照不冒充仍存在的聊天 |
| A-AC7 | Memory off/RAG on 能检索但没有有效保存动作；其他三种组合和状态读取失败提示正确 |
| A-AC8 | 存量来源与新来源迁移回归通过；legacy/unverified 不检索/外发正文，确认历史未丢失，可验证适配后恢复；响应契约和相关包检查覆盖之前 mock 漏洞 |

## Out of scope

不接聊天自动提取，不增加聊天保存按钮，不引入外部文档导入或 embedding；不做已确认记忆内容编辑和主库物理清理。中文检索/引用由 B、补建由 D，A 只提供共同来源与授权合同。

## Dependencies and review

无一期前置依赖。A 的来源模型、revision/依赖与迁移结论必须交给 B/D，禁止后续自行解释 source 字段。实施已获授权，只在主检出 task.json 记录的本任务 worktree 修改业务代码。
