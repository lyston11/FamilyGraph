# Design：记忆来源、响应与幂等

## 1. 请求与来源模型

建议在 MemoryCandidateCreate 增加可判别 source，同时保留过渡适配：

```json
{"source":{"kind":"manual"},"raw_quote":"本人明确输入","summary":"本人整理摘要","purpose":"用途"}
```

```json
{"source":{"kind":"rag_chunk","document_id":12,"chunk_id":34,"revision":2,"space_id":5},"summary":"本人整理摘要","purpose":"用途"}
```

第三支是 agent_message + message_id/可选范围，一期只允许本人会话中本人提交的原始 user 消息，拒绝 Assistant、工具和 RAG 派生消息走独立快照。IDs 是定位，scope/account/author 从认证和持久来源推导，不信任客户端。raw_quote 对 manual 必填，对其他来源由源快照取得或验证匹配；summary/purpose 有独立长度限制。

归一化规则：
- source 缺失且旧来源字段为空：继续拒绝，提示刷新客户端。旧手工新增与旧 RAG 保存形状完全相同，不能推断 manual；新 UI 必须显式 source.kind。
- 旧 source_message_id：仅能适配符合归属与原始 user 角色要求的 agent_message；派生消息拒绝此入口。
- 旧 source_document_ref：只有可解析、当前获权的真实来源可作为新引用；无法解析的新请求返回稳定可解释错误，不能以任意字符串证明授权。
- 新旧字段冲突、多来源互斥违规、无效范围：422，不落库。
- 现有 confirmed 记录的旧字符串作为 legacy 描述保留；无法验证的来源和仅能定位到 Assistant 派生消息的存量标记 unverified。保留原文、scope/作者边界和确认历史，但禁止进入检索/AgentContext；本人管理面仅显示获权元数据和待验证提示，暂不显示原文/摘要。可验证适配器补齐来源依赖后才能恢复相应访问，不能由迁移或再次点确认伪造授权。不可安全处理的依赖列入迁移报告，不自动改 manual 或删除。

增加 source_kind 和有界规范化来源快照；稳定原始 source_type/id/revision 及必要来源空间有可索引字段。document/chunk 定位与 hash 可放 source_span_json，但字段结构只由服务端写入。Memory 在确认时复制依赖，不只依赖可 SET NULL 的 source_candidate_id。

CHECK 应验证来源种类/快照，而不是要求来源 FK 永远非空。source_message_id 被删除置空后仍保留原始 ID 的审计定位和聊天源已删除标记；不得降级成 manual。对本人原始 user 消息已明确确认的原话保留独立快照，对来自共享 RAG 的复制持续校验原授权，两者不能混用。Assistant/工具/RAG 派生消息未来只有保留依赖后才可保存，不因属于本人会话免检。

## 2. 授权与复制依赖

提取 shared source resolver，供候选和检索复用。它按 ID 回读，不调用全文搜索来判断权限；搜索的 query/排名/limit 不是授权机制。

校验 document/chunk 归属、revision、来源 active/confirmed/authorized、作者可见性、原空间 membership、sensitivity、源链是否有效。来源生命周期有效与“是否当前切分算法版本”分开：已保存依赖按原 chunk ID/版本/hash 精确读取，算法换版不自动撤销合法原片段，也不能把它重定向到新片段；新搜索只读 B/D 的活动版本。RAG 创建接口不因 provider_kind 缺失把可人工阅读的来源误当模型外发；来源读取权限和后续模型外发本地要求分别执行。

可选确认范围由后端算出：private 来源仅本人；shared 来源不能扩到别的空间；private 副本也不得跨越原共享来源边界。敏感度以下游最严格约束为准。复制同一 RAG 来源时保留可追溯根依赖，设深度/环检测；不让多层复制洗掉来源。

手工敏感度仍是用户声明，不能称为已自动分类；现有策略检查保持。来源撤销/过期/删除与动态 membership 变化采用适当区分：来源整体失效可 tombstone 依赖投影；某一读者失权不能把所有人来源全局删除。

候选、Memory API 的列表同样调用来源有效性投影，只修 search_rag 不足。软删除保留审计，不在本任务新增物理擦除行为。

## 3. DTO 与事务

修复 MemoryCandidateOut 的读取别名/显式 mapper，HTTP 仍用 raw_quote，数据库仍用 source_quote。禁止靠数据库重命名消除一个 DTO 映射错误。

所有写端点：service mutation → flush → 构造及 JSON 序列化检查 DTO → commit → 返回已构造 DTO。异常回滚由现有事务管理承担；不得先 commit 再临时访问无法序列化字段。

候选创建增加账户内唯一 idempotency key 与规范化请求指纹；相同 key 同请求返回同 ID，不同请求 409。原文/摘要等影响结果的字段都参与指纹，指纹不在公开日志中反显文本。新 UI 每个用户操作生成一次 key，网络重试复用；旧无 key 客户端明确只保留原有保证。

确认采用单事务状态比较/条件更新与来源候选唯一约束；保存 scope/retention 等确认参数用于相同请求重放，异参返回冲突。幂等重放也必须重验读取权限。新增唯一约束之前检查存量重复，不任意删除冲突 Memory。

## 4. 前端改动

复用 MemoryEditorDialog。manual 模式保留原话输入，RAG 模式接收完整来源定位且原文只读；显示摘要属于用户整理。保留来源的初始值、store 类型和 API 参数，不能只传 raw_quote。

Memory 关闭时移除/禁用新增、确认、忽略、撤销、删除以及 RAG 保存动作；RAG 独立开启仍可搜索。服务端状态是最终真源，创建/确认后重读列表；错误显示稳定文案，不乐观插入一个失败候选。

## 5. 迁移与兼容

实施时查真实 heads，不预分配序号。候选/Memory 添加字段、调整来源 CHECK 和幂等/确认唯一性时，在 SQLite 隔离库验证迁移、外键删除、重复存量报告。

存量 agent_message/doc 引用按实际证据标记，未验证旧 ref 或派生来源不升为 authorized；旧索引在检索时同样排除 unverified，且不得由 D 补建。保留数据和确认历史，通过可验证适配器补齐证据后恢复相应访问；不能把无法验证的原文从列表返回来规避检索限制。回滚旧程序可能不理解 manual/rag_chunk：优先受控关闭写入并前滚修复，不能清掉新记录来强行 downgrade。

## 6. 受影响文件与风险

backend：schemas/memory.py、models/memory.py、services/memory_rag.py、api/memory.py、来源 helper、迁移及对应测试。
frontend：types/api/stores memory、MemoryEditorDialog、MemoryRagPanel、MemoryManager 及现有组件测试。
不修改 agent runtime 或管理员 Provider 配置页面。

重点风险：来源复制洗权限、提交后丢响应、确认竞争、来源 FK 置空、legacy 推断过度、UI mock 掩盖 API 不一致。每项都有 A-AC 对应真实接口/数据库回归。
