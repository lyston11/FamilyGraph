# Design：来源生命周期先于索引补建

## 1. 状态与身份

复用 A 的基础来源规则，但区分 source_is_materializable（与某个阅读者无关的来源合法性）和 can_read(actor, space)（请求侧动态授权），后台不能假扮某个成员决定全局索引状态。规范 document 唯一键为 source_type/source_id/revision；复用现有 document.index_version 作为活动指针，文中 active_index_version 是其逻辑名称，不新增第二个可独立写的真源。chunk 唯一键改为 document_id/index_version/chunk_index，内容摘要用于一致性检验，不能用修改同一 ID 文本的方式覆盖旧引用。

| 来源/投影状态 | 允许动作 |
|---|---|
| 合法已确认、当前 revision 从未物化 | 有界创建 document/chunks |
| 合法、同 revision/切分版本已 active | 返回已有投影，不删块 |
| 合法、明确的 index_version 升级 | 生成新版本 chunks，验证完整后原子切换 |
| 来源整体 revoked/deleted/expired 或根来源永久失效 | 保持来源 tombstone；任何 index_version 都不能绕过 |
| 某读者 membership/disclosure 暂不满足 | 仅动态拒绝该读者，不写全局 tombstone；资格恢复后重新授权 |
| 存量 invalidated 且原因不明 | 视为不可自动恢复，报告待核验 |
| legacy/unverified | 保留原记录及确认历史，不新增索引，不被检索/ContextBuild 读取 |

区分 source_invalidated 与 index_superseded。前者跨索引版本有效；后者只能由明确的索引升级流程产生，不能把任意历史 invalidated 自动解释为可恢复的旧切分版本。新的来源 revision 必须有其合法来源/确认依据，不能为绕过 tombstone 自动递增。

## 2. 原子物化与幂等

把 index_memory 重复执行语义固定为 ensure 当前合法投影，不再先 delete chunks 再盲目建。服务层先 revalidate，flush 前再检查来源版本/状态；必要时条件更新 document 和约束竞争处理。

并发时以唯一约束发现其他执行者已物化，读取同一结果；冲突内容不一致则稳定报错并保留证据，不 last-write-wins。旧库建立唯一约束前，输出重复来源组、状态和引用依赖的元数据报告；不能自动删除其中一条或重写来源。存在无法安全归并的重复组时阻断唯一约束迁移和新维护职责启用，完成独立的获准数据处置后才能继续。

B 负责确定性切块，D 负责维护事务与换版。新版本 chunks 全部写好后才把 document 的 active_index_version 切过去；失败保持旧的合法投影可用。只有来源仍合法时才能回滚索引算法版本。失效来源无论旧版新版都不可检索。

所有新搜索/ContextBuild 必须满足 chunk.index_version == document.index_version（活动指针）并满足来源状态；即使 staging/旧 chunks 已进 FTS 也不得返回。RAGHit.index_version 读取实际行版本，不能使用当前硬编码常量。历史引用和已保存 RAG 依赖按原 chunk ID/revision/index_version/hash 精确解析，再重验来源权限；算法旧版不等于来源撤销，合法旧片段可被精确读取，不重定向到新块。

legacy/unverified 与来源 tombstone 分开：只有真实来源适配验证可以解除隔离；维护扫描不能自行认证。解除后下一全轮检查其 FTS/活动投影是否完整，不能因为 document 已存在就永久跳过缺失索引。A resolver 的来源有效判断不要求原片段仍为“当前检索算法版本”。

## 3. 维护职责与触发

新增独立 RAG 索引维护职责，使用已有 maintenance 生命周期，但不借用 Steward job、BehaviorProjection 或辅助开关。具体持久对象可命名 RAGIndexMaintenanceState，按物化策略版本记录游标、轮次、水位、失败重试和最近成功时间；表/字段最终名称在迁移设计时固定。

建议第一版使用单调 Memory ID 的有界全轮巡检加失败项重试：
- 每轮固定扫描上界和最后已处理 ID，批次默认小于等于 100 条并附事务时间预算；具体值以合成库测量校准。
- 完成一个全轮后从起点进入下一轮，以覆盖低 ID 记录后来重新满足条件/策略换版；不能只存永久向前游标而漏掉旧记录。
- 用独立失败队列/记录保存 source ID、稳定错误码、重试计数/时间，不保存正文；坏记录不阻塞其他来源，全轮巡检同样遵守失败项 next_retry_at，不能绕过退避。
- 游标按最后扫描 ID 前进，失败登记成功后也能前进；物化结果或失败登记与游标推进在同一短事务提交。停机从已提交游标恢复，单条失败可用 savepoint 隔离。
- 多进程使用持久 lease attempt/轮次/策略版本的条件更新作为栅栏；过期执行者不得推进/回写游标或切换活动版本。document 唯一约束只防重复，不能代替栅栏；切换时还要校验当前目标 index_version，不能被旧执行者改回旧版。
- RAG 部署能力允许时维护循环能存活/被调度，即使平台初始关闭；每 tick 重算有效开关。RAG-only 场景不能依赖 AGENT_RUNTIME/STEWARD 才启动。
- 开启只改变配置，下一 tick 推进；关闭后不拉新批次，已经在处理的批次提交前再次检查有效状态。

若实施时复用已有队列表/租约，先检查其身份、消费者和终态是否适合，不能以“复用”为由把 RAG 索引塞入 Steward 行为学习管线。新增文档来源以后各有自己的游标命名空间，不把尚未接入的上传视为已有工作。

## 4. 撤销、时效与权限竞争

物化时检查来源本身 active/confirmed、retention、revision 及 A 的根来源永久有效性；检索/ContextBuild/引用详情再按当前 actor 校验 membership/disclosure 等动态权限。共享基础 resolver 的不同结果类型，不能只在建索引时校验一次，也不能把读者暂时失权转为来源永久失效。

来源全局撤销可失效所有依赖投影；单个读者退出空间只影响该读者访问，不全局 tombstone 其他人来源。RAG 副本在新的 private scope 中仍受原空间来源有效性约束。本人原始 user 消息已确认独立快照按 A 合同处理；不能把 Assistant 派生消息或 legacy 当成独立快照。

过期发现后的行为须可重复；维护补建和 expire 不得互相把状态来回写。状态更新/实际读取有竞争时，以当前数据库来源状态和事务条件为准。已发出的聊天文字、Provider 和备份保留不在索引失效承诺之内。

## 5. FTS 修复和观测

FTS 的物理 repair/rebuild 只从当前合法 chunks 建搜索投影，不能调用可能改变 Memory/document 业务状态的“补建所有内容”分支。区分 materialization（业务来源到投影）、reindex（切分版本换版）和 FTS repair（同样投影恢复搜索表）；对外文案使用普通“资料补建/索引更新/索引修复”，不暴露内部细节。

观测包含扫描/新增/已存在/跳过/失败计数、active index_version、轮次、最后完成时间、耗时和稳定错误码。查询 hash 也不能当授权凭据；日志无文本、人物名或密钥。管理员仅看合规元数据，家庭用户按来源/空间授权计算其状态，不让总计数枚举他人私有记忆。

当前有效配置代码 AND 语义保持。引用历史归档中的互相矛盾开关文案时，明确它不是执行依据。管理端如要展示索引进度，先与正在进行的管理页面任务协调相交文件，再串行集成。

## 6. 迁移、文件和回滚

受影响：backend services/memory_rag.py、maintenance.py、platform_features.py 的必要接线，models/rag.py、新增维护状态模型/迁移和相应 API/测试。来源模型改动由 A 先完成，切分函数由 B 先完成；本任务不重写他们的选择。

实施日查询 Alembic heads/当前脏文件；在独立 DATA_DIR 迁移，有效/失效/重复/legacy 样本逐个验证。迁移加字段/约束前提供元数据报告，不能把一次清理当作无限制删除批准。

迁移保留现有 document.index_version 的活动版本含义，并替换当前 (document_id, chunk_index) 唯一约束为含 index_version 的键；revision/source_revision 的镜像一致性也要检查，不静默覆盖冲突。旧 chunk 保留原 ID/文本/版本，新旧版本进入 FTS 时由活动版本谓词裁定可召回性。

回滚优先关闭新的维护职责并保留数据，使用最后一个合法索引算法版本；任何时候 tombstone 都保持有效。若旧代码会复活已撤销记录，不允许仅回退程序后放开写入，应保持受控入口并前滚修复。物理清理和不可逆降级另立审阅计划。
