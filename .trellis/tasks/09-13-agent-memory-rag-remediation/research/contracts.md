# 跨任务合同摘要

本文件是 context manifests 的精简共同入口。事实以 [审计](audit.md) 为准，目标以各任务 PRD/design 为准；规划交付后用户已明确回复“执行”，按依赖实施。适用规则来自仓库 AGENTS.md 和本次用户明确任务，不把标记“历史资料”的 .trellis/spec 文档当现行门禁。

## 1. 实施边界

父任务统筹验收；默认 A→C→B→D，各子任务独立分支/worktree、相交文件/迁移/SQLite/端口串行。E 可并行做只读研究，业务方案获选后指定唯一实施所有者。本次“执行”授权 start、业务修复、相关检查和本地提交；不默认部署、改线上开关或启动额外能力。任务 worktree 实际路径以主检出 task.json 为准；本轮使用 /private/tmp/familygraph-memory-rag/ 下的隔离目录。

现有任务保持原所有权：Steward shared RAG/个性化文案等归 09-11-steward-capability-followups；辅助平台开关 admin/API/UI/审计归 09-13-steward-assist-platform-switch-admin。Provider 管理、家族树及已有用户改动不属于本次文件范围。

验证共享依赖的 worktree 时，不能假定 `.venv/bin/pytest` 会导入当前 checkout：editable install 可能指向主检出。Python 入口显式 `PYTHONPATH=当前 backend`，使用 `.venv/bin/python -m pytest`，并核对 `app.__file__`。真实 listener smoke 的 public/internal/admin 三个端口均独立动态分配，不能占用现有开发服务。

## 2. 来源与生命周期（A 主责，B/D 复用）

- 新 UI 明确 source.kind = manual / agent_message / rag_chunk；旧无来源手工/RAG 请求形状相同，继续拒绝并提示刷新，不能推断 manual。
- agent_message 独立快照只接受本人会话中本人原始 user；Assistant/工具/RAG 派生消息不适用，未来保存须保留引用依赖。
- rag_chunk 的 ID/句柄只是定位；服务端精确读取 document/chunk/revision/index_version，重验根来源/可见性/敏感度。private 副本也不脱离原空间授权。
- HTTP raw_quote 保留，ORM source_quote 显式映射。来源快照和依赖也保存到 Memory 本体，不仅放 candidate FK。
- legacy/unverified 保留原文/确认历史/scope，未经真实适配验证不检索/入 AgentContext；本人管理面仅获权元数据与待验证提示，不返回原文/摘要。
- 来源永久撤销/删除/到期、读者动态失权、来源待验证是三种结果；后台 materializable 与请求 can_read 分开，共用基础来源规则。
- 本人原始 user 的已确认快照不因聊天 FK SET NULL 伪造为 manual；RAG 副本持续依赖原授权。
- 创建先 flush/DTO JSON 校验再 commit；账户内操作 key+指纹，确认唯一/一致重放。幂等不绕过读取权限。

## 3. 检索与预算（B）

真实入口为 ContextBuilder→memory_rag；memory.py/rag.py 是门面，不另接一个同名旧 builder。词法 query plan、安全短词后备、有限唯一追问、先授权与有界补足；来源材料始终是 untrusted_data。

Memory.content 默认是确认摘要，raw_quote 不自动全文索引。切块句段优先、有界重叠、可定位。B 只解决 RAG 子预算，C 只修 Pi 一致性；全请求 system/history/user/RAG/tools/results/output reserve 归 E。

## 4. Pi 与历史（C）

在 createAgentSession 前用同一 SessionManager.inMemory + appendMessage 预填合法历史，删除仅赋 agent.state.messages 的接线。按消息 ID 去重、当前 user 排除后由 worker prompt 一次；不同 ID 相同文本不合并。不用 prompt 重放历史，不改 node_modules，不关闭自动压缩。

手动与实际自动路径均需看到旧事实并在压缩后继续使用。历史仍是允许的 user/assistant 文字，不独立重放旧 RAG blocks、工具结果或 thinking；历史 Assistant 正文中已有的事实可能仍存在，C 不负责历史擦除。跨 Run 摘要另设计来源依赖。

## 5. 引用与事件（B）

当前来源句柄不包含 Run，可以合法多轮复用；认证需绑定有效 run_id/attempt/build_id 且属于 included items。现有 token 尚无 attempt fencing；B 要补 signed attempt/DB 核验，同 attempt context 重读复用而不竞争创建。旧 attempt 不能借新 build，来源变化可令 build 明确失效。

原请求 fingerprint 与认证后结果分离，(run_id,seq) 同请求重试返回原提交；未提交事件才认证当前来源，保持事务原子性。内部 context_reference 不作为公开字段。

完整引用有限元数据存入消息，公开 payload 总 UTF-8 ≤16 KiB 且保留 role/text/web；正文占满时按 run_id/seq 授权补取，不截正文/抬限制。历史/SSE/补取均按当前权限投影；citations 只放完整可读项目，受限来源以 unavailable_citation_count 提示。来源正文走授权详情，不嵌入公开日志/事件。

## 6. 索引身份与维护（B 合同，D 运维）

规范 document 唯一 source_type/source_id/revision，现有 document.index_version 是活动指针；chunk 唯一 document_id/index_version/chunk_index。同源同切分版本重试保留 ID/内容；RAGHit 返回实际版本。

新检索要求 chunk.index_version == document.index_version；旧引用/保存依赖按原块 ID/版本/hash 精确读取，算法旧版不等于来源撤销。来源 tombstone 跨算法版本禁止复活；unknown invalidated 不自动改作 index_superseded。

D 先修状态/幂等，再接有界维护。全轮扫描/失败退避覆盖旧低 ID；物化或失败登记/游标同事务；持久 attempt/策略版本栅栏阻止旧 worker 回退游标或活动版本。RAG-only/平台晚开启能推进，开关 PUT 不同步全库索引。FTS repair 不修改业务来源。重复数据无法安全归并时阻断新约束/维护，不能默默删。

## 7. 能力与证据（E/全体）

Steward 现有状态不是模型经验学习，shared RAG/偏好投影无生产消费者；TermRegistry 真实存在。ActionCard 冷却有生产读写但受 BEHAVIOR_PROJECTION_ENABLED 限制，Suggestion 到期语义另议。MR-23/26 先用完整合成生产链复现；原事件集重建只动所属键族，不扩成必需的新水位系统。

MR-23 fixture 单独标 expected_support_fact_ids；当前候选输出尚无支撑事实归因，不假装字段已存在。core 后辅助候选下一 core 才显示是异步取舍，不能与结构去重混为一个问题。

旧 211 pass 是审计基线，手动压缩证据不等于生产自动频率；本轮未查线上开关/真实模型质量/生产延迟。每个修复和每个可选能力单独回填验证，规划完成不代表已经修复。
