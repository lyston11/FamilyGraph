# Design — 自动称谓建议与模型优化

## 1. 真源和有效词

Terms 继续负责四级词条、长幼和长链；SourceFact/resolver 决定路径。新增 `steward_terminology` 服务管理可重建自动投影、工作发现及反馈读取，不作为新关系算法。

基线计算与自动选择分开：先调用 resolve_term_or_structural 得到 baseline，再由只读的 effective-term 选择器结合当前授权路径选择自动投影。个人/active 空间词条无条件胜出；没有有效自动项时返回原 baseline。PFV、compose_resolution_view、A 的呈现服务统一接入；无 target 上下文的词典管理接口仍只管理显式词条。

## 2. 持久化合同

新迁移新增 `StewardTermProjection`，唯一键 `(space_id, viewer_account_id, root_user_id, target_user_id)`，外键随账号/空间/人物删除级联。核心字段：

| 字段组 | 内容 |
| --- | --- |
| 当前语义 | concept_code、规范化路径/source revisions、semantic_hash、baseline_term、baseline_source |
| 可用产物 | term（可空）、origin=deterministic/model、source_model_call_id、status=active/suppressed/stale/unchanged、revision |
| 请求记录 | request_hash、last_checked_hash、last_attempt_at、last_model_call_id、last_attempt_status、retry_after、规则/prompt 版本 |
| 审计 | created_at、updated_at；不存模型原始 prompt、姓名或生日原值 |

三个身份用途分开：semantic_hash 是当前依据版本摘要，含本人/空间/目标、相关路径/source revisions、可公开长幼和适用词条/偏好；request_hash 另含模型规则/prompt 版本。自动输出自身 revision 不进入二者。另定义稳定 suppression_key=`account/space/target + 已验证关系含义/必要 qualifier + normalized_term`，不含路径事实 ID/revision、词条 revision、prompt 或反馈版本；同一关系换等价证据路径不能绕过拒绝。源路径变更先使旧 term 失效，再按新依据重验，不能只因显示相同就继续使用。

semantic_hash/request_hash 均为每目标摘要；分组 ModelCall.input_hash/context_hash 则覆盖本次序列化的整个目标集合，并在 fence 保存每目标摘要。last_checked 按每目标记录，增加其他目标或改变组大小不能重新调用已检查目标。稳定关系含义使用 validator 已验证的规范语义（例如同胞/性别/长幼），不是原始 PathStep 串；不能归一为可信语义的表达不参与自动模型优化。

扩展 StewardModelCall：新增 nullable viewer_account_id（旧三类保留 null），kind CHECK 接受 terminology；subject_key=`terminology:<account>:<root>:<targets_digest>`。batch.fence_json 的该组保存有界目标 ID 和输入摘要；不得靠模型 echo 确定归属。恢复时使用这些服务端字段重建上下文。

沿用 StewardSuggestion/Recipient 承载可选偏好，不另建审批状态机。upsert 增加必填于 term_preference 的 viewer_account_id、notify 参数；value 是严格校验的 concept_code/term/target/projection_id/semantic identity，不能混入任意文本。去重按本人+空间+目标+语义+规范词，同依据包括旧终态检查，不能只有 active 行去重。

Recipient 增加有限 preference_feedback（kept/restored）及时间，Suggestion 的受限 value 保存 suppression_key；普通 dismissed 只隐藏建议，不代表恢复。恢复记录按稳定 key 跨建议/证据版本查找，防止换 suggestion ID/prompt/词条 revision 或等待到期后重新应用同词。模型反馈输入有上限，但持久拒绝查找不能因截选上限失效。

单行投影写入规则：

| 事件 | 更新规则 |
| --- | --- |
| core 重算、语义输入未变 | 仅刷新 baseline 元数据；保留有效自动 term、origin、反馈和 last_checked，不能覆盖模型成果 |
| core 重算、相关依据改变 | 旧 term 标 stale；重验后的当前依据才能复用/替换；稳定拒绝记录继续有效 |
| 确定性长链规则改善 baseline | baseline 已直接生效，可产可选建议；不再建立一个与 baseline 相同的自动 override，也不提供无意义恢复按钮 |
| 本人明确 TermUsage 提供合法优选词 | 没有个人/active 空间词条和对应拒绝时应用 deterministic override；这项明确用词优先于模型，不再送同目标给模型改写 |
| 合格模型词 | 同输入 CAS 更新 term/origin/model_call/revision；相同词幂等，更新不能清反馈 |
| 空输出、非法输出或网络失败 | 只写本次尝试/检查结果；已有且仍有效的 term 保留，不清空、不伪造新建议 |
| 恢复默认叫法 | 原子核验 suggestion.revision、projection.revision、semantic_hash；旧建议引用了已替换投影则 409，无副作用；写 restored 并抑制该词，返回当前确定性 baseline |
| 显式个人/空间词条生效 | 读取立即优先词条；旧自动项失效，恢复旧建议不得修改显式词条 |

恢复表示“使用当前默认叫法”，不是重放曾经的失效字符串。can_restore 仅在当前有有效自动 override 且不同于 baseline 时为 true；界面按钮使用“恢复默认叫法”。

## 3. 生产发现与非打扰展示

core 完成 PFV 后执行有界称谓扫描：

1. 对 current 且已授权的个人路径计算 baseline；跳过显式个人/active 空间词条、无路径、隐藏或不支持的路径。
2. 有确定性长链简化或该用户已明确选择的可验证同义词时，更新自动投影/生成可选建议；无改善不造建议。首次建图仅可登记可选建议，不发通知。
3. 最近本人 TermUsage 只提供已明确选择的同概念依据；不从他人行为或自由聊天推断偏好，不调用全量 BehaviorProjection rebuild。
4. model 输出经校验后进入相同应用入口。建议默认 notify=false；KinshipTermPanel 读取本人当前目标的建议（现有 list 增 kind/target 过滤或 resolve 响应携带有限引用，采用同一授权 helper）。

模型目标包括可简化长链、存在合法同义候选或本人已表达用词而默认结果不一致的路径。没有可校验改善空间不发模型。推测路径可生成带 inferred 状态的确定性显示；首期模型自动优化只对确认路径，避免为未知事实制造更确定的称呼。

## 4. Prompt 和输出

单独的 terminology prompt 核心约束：

> 你负责改善给定查看者对亲属的称呼。关系路径及语义由系统提供，不能修改或增补事实。保持方向、继养监护、配偶/伴侣和已知长幼；未知就使用中性表达。尊重明确偏好及拒绝记录。可以输出可由给定词素、同义词及路径组合验证的自然称谓；无改善返回空列表。

候选名字、已有叫法作为 untrusted_data，不得承载指令。candidate prompt 继续限定原子类型；文档明确其禁止派生称谓仅限该 kind。

输入以 viewer_ref、target_ref 代号表示，含路径结构、concept_code、baseline_term、可验证 alias/组合元数据、age_order=elder/younger/unknown、本人相关偏好/拒绝及 context_hash。不含姓名、原始生日、无关事实或其他账号数据。

输出为严格版本化对象：`{version:1, context_hash, items:[{target_ref, concept_code, term, reason_code}]}`。reason_code 仅 synonym/shorter_chain/preferred_usage；不输出自由 rationale、工具调用或事实提案。term 采用现有 64 字上限；列表数量不超过输入目标数，拒绝重复/额外目标和多余字段。

## 5. 语义校验

不信任 model concept_code；从服务端目标路径取得真值，用已有词条、词素和前缀/残链规则核验 term 的含义。可抽取 intake_extractor 的纯词素 metadata 供 parser/validator 共用，避免循环依赖或复制词表。

- 同义词必须属于该获准路径语义；例如 Uf-Uf 的外婆/姥姥。
- 合法组合词逐段覆盖真实路径或已验证等价片段；无授权依据不得凭空缩成另一个关系。
- 必须保留继亲/收养/监护及配偶/伴侣区别；生物关系词不能替代养亲路径。
- 哥哥/弟弟、伯/叔等还要验证 qualifier。现有码相同不是证明；未知/同年/日历不可比或未披露一律不选具体长幼。
- 性别未知不使用额外性别词；同义字样不增加“亲生/同父同母”等证据中没有的限定。
- 未知词、解析歧义、无改善或比 baseline 更繁琐的组合不自动应用；记录有限 reason code 并回退。不中断 core，不生成“请批准模型词”的待办。

显式用户自定义词条仍按原功能处理；模型不得借它修改事实或读取其他用户的私人叫法。

## 6. 执行、预算和公平性

复用 register_batch_for_job → schedule_due_batch/_reserve_attempt → execute_batch → _validate_output → _apply_batch/recover_stuck_batches。每个入口显式处理 terminology；未知类型直接拒绝，不能默认按 explanation/ranking 处理。

每 job 最多 2 个 viewer 组、每组最多 8 个目标；输出 token cap 单独设在 kind 上，但仍计入现有总 calls/tokens 预算，不扩大 6 次/20,000 tokens 默认总额。输入/响应字节沿现有限制。

新增 StewardSpaceSchedule.assist_kind_cursor（默认 0）保存每空间自己的轮转进度，在预留短事务内读取并更新；不得用全局 job.id 取模。有界 kind 队列从该空间 cursor 开始交错预留，跳过无工作 kind，成功预留后推进 cursor；viewer/target 按 last_attempt_at、稳定 ID 排序。缺 schedule 的合法手动作业按现有初始化方式建立进度。只记录调度进度，不新增 worker；未轮到的目标留后续周期，baseline 不等待。单个工作连最小 token 成本都无法满足时明确 budget_too_small，公平性不承诺突破配置额度。

预留记录目标 last_attempt。跨 job 的去重/重试以持久 ModelCall 与 batch 中目标摘要为真源，投影上的 last_attempt/last_checked 只是索引缓存，重建投影不能清掉调用历史。规则如下：

| 结果 | 同输入后续资格 |
| --- | --- |
| 成功/无变化/非法词 | 记录 last_checked_hash；同 request_hash 不自动再调用，仍有效的旧自动词保留 |
| unknown（含发送后断连） | 保守计费；同 semantic_hash 跨新 job/重启/prompt 升版均禁止自动重发，不仅终止旧 batch |
| 确认未发送的连接失败，或 reserved 崩溃后 skipped | 按同 semantic_hash 统计持久预留次数，最多 2 次（含首次），第二次最早 last_attempt+60s；超限标 retry_exhausted，不因新 job 重置 |
| HTTP 已返回错误等已发送失败 | 同 request_hash 停止自动重试；不冒充“未发送” |
| 新的相关语义输入 | 可重新入队，但先检查稳定 suppression_key；无关空间事实不算新输入 |

这是本任务新增的有界称谓再入队合同，不声称现有 assist 已具备该跨 job 重试机制。恢复器可能把 reserved 批次回 pending，预留入口仍必须执行上述全局于该目标输入的计数。

## 7. 栅栏、应用与反馈

预留、发送前、写回和恢复写回均验证当前账号/空间资格、root、全部目标及中间节点、SourceFact revisions/status、词条/用词/拒绝版本、相对长幼、policy、Provider/开关。发送内容 hash 必须等于预留 hash，不能沿用现有未重验 input_hash 的行为。

普通执行还须持有相同 lease_owner/attempt 与未过期 lease。恢复由 recover_stuck_batches 在短事务内确认原 lease 已过期，以 status+attempt+原 lease 标识 CAS 接管 applying 批次；恢复调用持有这个接管身份，不要求原 lease 重新有效。接管后只能重新校验并应用持久 succeeded 产物，不发送网络；旧执行者写回因身份变化拒绝，两恢复器仅一方可接管。unknown 仍按上表处理。

沿用现有 batch 的保守整体结算：本批任何 viewer 输入失效可使本批不应用；不得因此删除其他 viewer 先前已保存且仍有效的投影。这里不额外引入部分成功状态机。有效产物写投影/建议并只登记一次 term cause 后台刷新；不在网络事务重建全空间。后续 core 保留该有效产物，last_checked 未变不再调用。

主动“保留为我的叫法”使用现有 term_preference submit/set_personal_term；立即让本人词条优先，并失效相关跨空间个人视图。“恢复默认叫法”新增仅本人可用的 restore-term 操作，携带 expected_revision、expected_projection_revision、semantic_hash 和 Idempotency-Key。相同 key 先返回既有结果，不因成功后 revision 增长破坏安全重试；不同 key 的陈旧请求 409。原子写 restored/抑制/刷新。普通忽略不撤回已显示词。

显式个人词条已存在后不能继续用旧 restore 操作覆盖它；提示已更新并读取当前结果。后续相同语义同 term 保持拒绝，即使换 job/prompt/无关事实；真正改变关系概念时重新计算可用词。

PFV input_hash 与读取有效性包含适用投影 revision，但模型输入不含这个输出 revision。领域事件使资料、关系、可见性、词条、反馈变化联动失效。GET 不调用模型；关闭增强后立即停止使用模型投影并排队恢复 baseline。

## 8. 平台/空间治理与迁移

迁移同时扩展 models/steward.py 的 kind CHECK、ModelCall.viewer、SpaceSchedule.assist_kind_cursor、新投影/反馈字段、AgentSpaceProviderSetting 和 PlatformFeatureConfig 的 terminology bool。开关默认 false，cursor 默认 0；不改旧迁移，不为旧三 kind 回填假的 viewer。

代码同步：config；platform_features state/get/set/effective；平台 schema/admin API/审计；空间 agent schema/settings API/admin_agent 输出；admin_steward.assist_any；家庭 SpaceModelSettingsPanel/types/decoder；管理 PlatformFeaturesView/types/测试。省略新字段的旧客户端保持原值。仍是 agent_kind=steward，无新增 Agent 类型。

生效为 STEWARD_ENABLED ∧ deployment ∧ platform ∧ space ∧ Provider/外发条件；遵守已有本地/云许可，不增加逐条用户授权。模型关闭不关闭确定性称谓。统计记录 kind、数量、原因码、耗时、token 和应用率；不记录 prompt/家庭正文，不能把成功格式率叫做关系准确率。

## 9. 验证和回退

从真实 job/fake transport 进入覆盖：外婆→姥姥、合法长链、无改善、两 viewer、不同空间、显式词条、恢复、发送中撤权/改偏好。新增关键序列：模型应用→刷新→再次 core 保留产物；旧建议 restore 不能撤回新版；同关系换等价事实路径/词条 revision 仍拒绝旧词；unknown→新 job→重启零重发；未发送失败跨 job 最多两次；四空间交错且每 job 一次调用预算仍轮到各 kind；过期 lease 两恢复器竞争只写回一次。独立标注语义 expected，不能让实现自产期望值。

隔离数据库升级/旧行/重复初始化/外键验证；三端配置真值表及旧客户端保持。需要回退时停用新 kind/投影读取并刷新 baseline，保留真实词条和事实；不依赖删除用户偏好或破坏性 downgrade。
