# Design：错误推荐的确定性拦截与存量收敛

## 1. 基线与边界

规划基于当前主检出代码、`docs/ARCHITECTURE.md`、`.trellis/HANDOFF.md`、后端 Steward/候选证据规范，以及已归档 `09-13-steward-memory-evidence-projections` 的 PRD/design/implement。旧证书规则是锁定行为，不因本次发现误推荐就扩大证书或关掉全部 unsupported 线索。

当前生产主链为 staged core -> publication -> `steward_delivery`，不能只改 `steward.py` 的旧同步路径。规划本身不修改业务代码或现行 Spec；以下合同变更待用户审阅批准后实施并同步 Spec。

## 2. A：关系候选安全判据

### 2.1 采用最小、可解释的推荐抑制规则

新增一个供多个消费者共用的窄模块，拟定 `services/steward_candidate_policy.py`，只负责候选是否可公开消费；不承接正向证书、状态机或称谓计算。先核实有无等价 helper；若已有完全符合范围的实现则复用。

输入为候选 kind/端点及授权后的 confirmed 事实快照，输出安全原因码或允许消费。首版针对本次证实的 `direct_sibling`：

| 已知事实 | 处理 |
| --- | --- |
| A -> B 或 B -> A 的 confirmed biological_parent | 抑制 sibling，`confirmed_parent_child` |
| A -> B 或 B -> A 的 confirmed adoptive_parent / step_parent | 保守抑制模型 sibling 推荐，`confirmed_parent_child`；不宣称亲属多重身份在现实中绝不可能 |
| A 经连续 confirmed biological_parent 到 B，或反向可达，至少两步 | 抑制 sibling，`confirmed_biological_ancestry` |
| 仅 guardian、spouse、partner、co-parent 或其他不同类型关系 | 不能仅因关系不同就判 sibling 矛盾；保留原校验 |
| 只有收养/继亲/监护混合长路径 | 不归约为生物祖先；不在本次祖先规则内 |
| 没有上述冲突且无完整共同父母支撑 | 仍是 unsupported，不自动确认为真，也不一律隐藏 |
| 有完整共同生物父母支撑 | 保留既有 versioned 内部证书路径；不新增公开待办 |

这是一条自动推荐的保守安全规则，不是全局 SourceFact 互斥约束。用户手工维护多重亲属身份的授权流程不被扩大限制。若图同时存在支持与冲突，也不能据此产生公开 sibling 推荐；既有内部证书的历史语义不改写。

事实范围复用 `_space_visible_user_ids` + `_applicable_confirmed_facts`：当前空间或 global、confirmed、所有经过节点在内部获权集合。API 先完成现有 viewer 授权；抑制原因不得向 viewer 输出未授权事实/中间人。不能直接调用会遍历全局父系且无本空间过滤的 `source_facts._ancestors_within`，也不以称谓字符串推断。

使用标准集合与迭代 DFS/BFS，按访问集去环，单快照建 biological parent 邻接表并在一批内复用，避免按建议逐条查询整图。不人为截成两代；循环或预算耗尽不得当作已证明无冲突。规模超出现有可处理预算时明确阻止本次公开消费并记录安全失败，不放开候选或伪造关系。

### 2.2 接线位置与责任

1. `steward_guard.validate_candidate_output` 保留闭合 schema、代号、未成年等既有校验；不向纯结构校验塞数据库查询。prompt 可增加方向示例和“不得与已给事实冲突”的规则，但不是验收的安全屏障。
2. `steward_suggestions.project_for_job` 与 `steward_inferred._edge_from_candidate` 都调用同一判据。保留候选原 payload/model call 审计；冲突候选可留内部记录，但不 upsert 公开对象、不生成 recipient/通知。
3. `steward_delivery._apply` 的旧 candidate 意图在实际交付时重验；不能只在 prepare 阶段过滤。内部 evidence_version 意图仍走原证书分流，保持输入/租约/attempt 栅栏。
4. 建议 `source_state/effective_state`、list/detail/notification/allowed actions 共用当前冲突判据：未解决的错误线索有效状态为 superseded，不仍显示可提交。保持私人 dismissed 的优先级、正常已确认关联提案的历史终态。
5. `submit_suggestion` 在原授权、revision、命令事务中再验；推测边 `confirm_edge/reinstate_edge` 同样重验。没有合法写动作时返回既有错误 envelope 对应冲突响应，不创建 SourceFact、不增加确认回执。先核对现有错误码复用，不盲目新增 API 枚举。
6. 推测边 `active_edges`、列表/详情及 PFV overlay 消费都检查有效性；已缓存 overlay 必须受修复策略/版本与证据重验约束，不能只阻止新边而继续显示旧错边。
7. 不更改 `kinship_presentation` 来把错误候选“改名”为正确亲子；在上游抑制错误对象，保留候选自身方向的既有呈现合同。

### 2.3 存量建议与推测边

- 建议优先复用有效状态读模型：历史证据、source_candidate、recipient、dismiss/read/cooldown 不改写。不能把共同父母证书内部化当作清理工具。
- 已有活跃推测边在 `inferred_review` 中加入语义复核，即使 evidence_hash 相同也 supersede；只更新该派生边允许的状态/revision/时间，保留 rejected/confirmed 历史。特别注意 `prepare_intents` 当前调用 `active_edges`：公开读取若过滤冲突边，维护枚举必须仍按持久 active 状态取 ID，不能复用过滤后的列表导致应退役边永远得不到 review。
- 已 submitted/linked_fact 的旧对象禁止自动撤回或删除 SourceFact。界面不再鼓励错误线索；已发生人工确认的记录列入单独人工审核清单，不谎报全部修复。普通手工关系确认规则不在本任务内重构。
- 如需持久化尚未提交建议的 superseded，只能在已有交付职责中按对象 ID/revision 有界处理；不为只读退役另建维护队列或伪造替代 suggestion。

## 3. B：共同 Household 的布尔抑制

### 3.1 有限合同修订

当前 `_pair_inputs` 将 `space.kind == household` 当成计算 `share_household_membership` 的前提。这与用户“已经共同在家庭里就不应再推荐共建”的要求冲突。

拟在 `services/steward.py` 增加窄 helper，例如 `share_active_household(db, subject_user_id, object_user_id) -> bool`。以两个 SpaceMember 别名及 FamilySpace 的 EXISTS 查询实现：同一 space_id、kind=household、两端 status=active。与当前扫描空间的 kind 解耦。

- 返回 bool，不返回匹配空间 ID/名称/第三人成员/关系，不读取他空间 SourceFact/Memory/RAG，不把他空间加入 visible roster。
- 只抑制既定当前空间两端点的建议，不发现陌生人、不授予任何新读取权。
- active profile ref、owner 身份本身、pending/rejected/left 不代替 active membership；无共同 household 时 false。
- `lineage_request_possible` 仍按当前 lineage 空间恰一端 active 计算。
- `creation_choices`、partner disclosure、identity_confirmed、cooldown 等矩阵合同完全保留。
- `recommendation_matrix` 继续纯函数；更正输入字段的“本 household”注释，测试证明共享 household 仅删除 create_household，不删除 request_lineage。

这是对现行 Steward “当前空间读取”条款的有限例外提案。实施前审阅批准后，应在 Spec 独立叶中说明其负向抑制目的与禁用信息外泄边界，不能默认为全局亲属发现授权。

### 3.2 三处安全屏障

- 新生成：`steward._pair_inputs` -> 旧推荐路径及 staged `recommend` 交付共同受控。
- 旧卡复核：`steward_delivery._card_review` 和旧 `_revalidate_active_cards` 重新计算资格，使用现有 `supersede_card(reason="eligibility_lost")`；pending/viewed/accepted 可退役，dismissed/executed/expired/superseded 不复活、不改历史证据。
- 用户读取及操作：抽取可共用的当前资格读取函数，ActionCard list/detail/notification 使用有效状态，不把旧错误卡留在待处理；GET 不落库。accept 前重验；execute 在调用 `create_shared_household` 之前的原写事务重验相同 bool，失败用现有 `CARD_EXECUTE_REJECTED`，保持现行 rejected execute 的持久状态语义，后续 review 收敛。不能抛异常回滚后又宣称卡已持久 supersede。

不在通用 `commands/spaces.create_shared_household` 强制全局“一对人只能一个家庭”，因为自主建空间不是本次需要禁止的行为；限制落在管家推荐命令边界。

### 3.3 跨空间状态变化与 staged 一致性

不能假定当前空间的 `StewardInputRevision` 能捕捉别处家庭成员变化，也不能仅依赖旧 evidence hash。

本方案不新增全局事件 fan-out 或跨空间关系缓存：

1. 每次 read/accept/execute 和 card/recommend 实际交付都在当前事务读 live bool；不缓存历史 bool 作为操作授权。
2. `prepare_intents` 保持每代为活动卡准备 review、为适用事实准备 recommend；不要因旧代 receipt 或 fingerprint 相同跳过资格检查。
3. 其他家庭成员资格变化后的持久更新由现有周期 integrity_scan/review 收敛；旧指针/意图不能绕过 live 重验。约定“立即”指读与命令，“后台收敛”指下一次成功扫描及其交付，默认扫描周期读取实际配置，不承诺停机或积压时五分钟必达。
4. 双方加入已有共同家庭后，旧 lineage 卡即刻不可行动；退出最后一个共同家庭后，下次扫描可产生符合原冷却/证据规则的新卡，不复活终态旧卡。
5. 修复策略版本要进入既有 policy/config 与展示/overlay 失效合同，使证据完全未变的存量也获得一次有效重验。不得借此重置网络 unknown 或既有失败预算；变更前测试预算与版本相互作用。

## 4. C：存量核查与安全处理

首选通过现有 effective_state + review/扫描完成，不提供批量 SQL DELETE/UPDATE 清理脚本。

正式执行前产出只读报告，按 candidate、suggestion、edge、card 分别计数；建议记录 object_id、space_id、端点 ID、当前状态、命中原因及最小相关事实 ID/revision。受限证据不得进入普通 API 或日志。

- 候选未获支持不等于无效；共同 household 只影响 household_link，不影响整张 lineage_request。
- 对每个 household 卡执行 shared-active-household bool，不能按“lineage 中全部卡”或旧数字筛选。
- 对 suggestion/edge 采用同一语义规则；反向记录和不同 recipient 必须区分，不能把候选数、通知数、建议数混算。
- 保留既有 notification 行和已读状态，依关联对象有效状态退出 pending 分区。修复前后确认无新增通知风暴。
- snapshot 无变更也需演练；重复 rerun/两个 worker/读写竞争不新增家庭、不回滚人工决定。
- 若旧用户操作已产生 proposed/confirmed SourceFact，另列出，不自动撤销、改类型或伪造解决状态。

## 5. 变更面与排除项

| 文件/边界 | 预期职责 |
| --- | --- |
| 新窄 candidate policy 模块 | 一份确定性负向判据及有界图检查 |
| steward_suggestions / steward_inferred | 双投影、有效读取、提交/转正重验，保全用户历史 |
| steward_delivery / 必要的 overlay 消费点 | 旧意图和存量边/卡复核，保留 publication/lease/failure budget |
| steward / recommendation_matrix | 共同 household EXISTS 与输入语义；不改创建选择 |
| api/action_cards / notifications 实际共享序列化路径 | 读侧有效状态、accept/execute 共同资格校验 |
| 既有 Steward/ActionCard/notification 测试及新 policy 回归 | 对齐 AC1-9，优先现有夹具 |
| 精确 Spec 叶及索引链接 | 记录新增抑制合同；不改冻结归档任务 |

默认不改 schema、不新增开关/依赖、不改前端组件。仅当现有前端不能正确消费已有 superseded 状态时补最小契约修复和前端测试；不能在前端复制业务判据。若需要新迁移或改用户产品规则，先更新本设计并重新审阅。

## 6. 发布与回退

隔离测试通过不等于上线。经授权后先在线备份、隔离副本演练，再部署修复并通过现有重跑入口分空间收敛。记录远端真实工作目录、systemd 作用域、运行代码 SHA、schema head 与列、有效配置、处理前后对象数和浏览器结果。

回退不得将 superseded 对象批量改回 pending，不回滚用户关系历史，不还原整个生产库覆盖期间的新数据。优先向前修复；必要时经授权暂停有问题的推荐消费/worker，并保留命令侧拦截。没有授权不能擅自关闭整套 Steward 或改变 Provider。
