# Design：限定投影键族与版本化支撑证据

## 1. 先修复键族

`rebuild_behavior_projections` 的删除谓词必须与实际重放器负责的 projection_key 集合一致。以生产事件处理器已经生成的 card/term/correction 键为唯一来源，集中声明所属键族；family recommendation 冷却和未知类别不在集合中。

原账户/空间过滤条件继续保留，额外加所属键族条件。三个所属前缀为 `card_cooldown:`、`correction_preference:`、`term_usage:`；与允许写入的全局前缀集合分开。SQL 前缀匹配必须按大小写敏感的字面量比较，可使用 `substr` 等值判断，避免 `_` / `%` 通配及 SQLite LIKE 大小写折叠误删近似键。清理与事件重放处于同一事务；无匹配事件时只清理本键族，不能误删外部数据。非所属行的 ID、值和更新时间均不变；两次所属回放比较语义值，不能把更新时间差异误作幂等失败。该补丁不需要迁移或生产 maintenance 接线。

## 2. 候选结构与证据分开

保持稳定结构 identity（kind、具有方向的端点）；候选证据版本另含规范化 support fact ID/revision、validation_contract_version、evidence_digest 和来源 batch/job。版本唯一性为空间 + 稳定结构 + evidence_digest，不能采用全空间 hash：无关事实变化不应制造新版本。

首版冻结为共同 biological_parent 证书：candidate.kind 为 `direct_sibling`，存在同一父母 P 到两个不同端点 A/B 的 confirmed `biological_parent` 事实。仅收集完整配对的共同父母事实；其他亲属类型、单边父母、与端点无关的事实一律排除。全部有效配对按 fact ID 稳定排序，快照包含 ID、revision、类型、端点、事实空间；不含姓名、模型自由文本或全空间摘要。现有 resolver 不把 up/down 路径归约成 sibling 概念码，故直接核验 SourceFact 方向和类型，不依赖称谓字符串判断。

来源集合复用 Steward 当前空间可消费的 confirmed 事实口径（active 成员/引用/owner、当前空间或全局、事实双端点均在集合内）。共同父母节点也必须在集合内。它是内部空间级输入范围，不是某个账号的读取授权；首版没有向个人 API 暴露证书。SourceFact 当前没有 TTL，核验的是 state、原 revision、端点、类型和范围；lease 到期仍由既有执行栅栏处理。

未覆盖类型继续原结构去重，持久记录 `unsupported` 归因状态。旧行初始标记 `legacy`，不得把旧全空间 evidence_hash 当作已认证证书。进入证书路径后使用粘性的 `versioned` 标记：表示内部版本化处理，不表示关系当前已确认；来源后来失效也不能掉回公开候选入口。模型 schema 不新增支撑 ID 字段，不接受模型自报关联证明。

`direct_sibling` 的内部隔离按无向端点对生效：同空间任一方向进入 `versioned`，已有 legacy 反向行及后续无证书的反向模型输出也不能公开投影。两个历史 candidate ID 和原结构 digest 均保留，不重排、不归并。两个公开入口复用同一内部隔离判断；新写回继承该端点对的内部模式，防止来源失效后反向输出绕过驳回。

证据 digest 用于版本身份，现有 batch 全空间 hash 可继续用于请求快照/写回竞争校验，两者用途不合并。

## 3. 投影与读写边界

每个已验证候选版本只在后续 core 核验并记录一次内部投影。版本状态为 `pending` → `projected` / `invalidated`，保留投影 job/时间和安全失效原因码；此状态只证明所记录时点的核验结果，不是持续有效标记。投影读取保存的支撑事实及原 revision，重验当前范围与完整共同父母配对，不用当前整个空间 facts 覆盖快照。新增无关事实不影响核验，新增另一共同父母会形成另一个版本，旧版本快照不变。旧候选/旧 Suggestion 保留，禁止原地替换它们的证据或驳回字段。

与在途渐进重算集成时，接线落在其 `steward_delivery` 候选交付职责：后续 core 的只读准备阶段捕获 pending 版本 ID，每条沿既有 `candidate` kind 准备独立、有界的内部核验意图；发布后在受 generation/输入/lease fence 保护的短事务核验一条版本。该意图携带稳定 candidate/version ID，不与公开候选交付混用副作用，也不要求 candidate 必须仍为 proposed。准备阶段之后产生的版本等再后一个 core，不由旧意图顺手消费；重复交付只复用已记录终态。无新的 maintenance 队列或意图 kind，不在 publication 写锁内全空间扫描证据。

已冻结的可见策略：版本化候选不进入 Suggestion 和 inferred 两个独立投影入口；内部版本投影不调用 `upsert_suggestion`，因为仅 `notify=False` 仍会生成新的 recipient。既有 Suggestion、recipient、Notification、推测边及私人/共享驳回沿用原生命周期，不因证书换版新建、复活或改写。已派生的称谓继续由 PFV/Terms 自动计算。未归因/legacy 候选保留原先的结构去重和待核实语义，本包不把它们升级成已确认事实。

内部版本不接入 `_evidence_summary` 的 confirmed 单跳计数：当前序列化把该计数大于零视作正式关系已确认，共同父母证书不能触发这一分支。未来若增加查看者证书 API，必须另行验证所有支撑节点的个人可见性及持续来源有效性，不能直接输出内部快照。

与称谓闭环共享的合同：称谓建议的 notify=false、viewer 绑定与 requires_action=false 由该任务负责；本任务的关系证据版本不得覆盖这些语义。已可派生的称谓通过 PFV/Terms 自动显示，不能为记录 sibling 支撑证书而要求用户再确认称谓。若先集成称谓有效状态/详情 API，本任务复用并串行扩展，禁止恢复 raw fact_type 展示或全空间事实计数。

## 4. 迁移与并发

新增候选归因字段及 `steward_candidate_evidence_versions` 子表。版本包含 candidate、空间、验证合同、相关证据 digest/不可变快照、来源 job/batch/model call、内部投影状态及投影 job/时间。唯一约束 `(candidate_id, evidence_digest)` 等价于空间内稳定候选结构与相关证据版本唯一；不改现有 `(space_id, candidate_digest)`，不重排旧对称候选端点，不合并历史行。

新增列使用原地 ALTER，禁止 batch-rebuild `steward_llm_candidates` 导致引用级联/置空。旧行回填仅为 `legacy`，不创建假证书。版本随候选/空间的领域删除清理，后续来源 job/batch/call 和投影 job 引用使用 SET NULL；不新增历史保留/GC 子系统。当前没有生产 job GC，既有 candidate→首个 job CASCADE 合同保持，并在限制中说明。

渐进重算已在 `dee91a1` 集成；已核实当前单一 Alembic head 为 `0048_steward_terminology_publication`。本任务的新迁移使用 0049，以该 head 为父节点；提交前再次检查迁移序号无冲突。重复或无法安全归并的行只报告稳定 ID/状态，阻断破坏性处理。


0049 的降级预检还须覆盖本次计划中的祖先拒绝条件，SQLite DDL 不假定可事务回滚：先建立 writer，再校验祖先与本表证据，任何拒绝均发生在首个 DDL 前。复用 0048 `_preflight_parent_downgrade` 的 SQL，通过新增可选 `planned` 参数传入从 0049 计算的实际计划；默认参数保留 0048 原行为。这样 `downgrade -1` 只移除空的 0049 结构，不会从 0048 错解相对目标。当前 merge 图的 `-2` 本身歧义，由 Alembic 在 DDL 前拒绝；绝对深降级沿现有 RAG/Memory/Steward 拒绝合同。无需引入计划缓存或重复祖先 SQL。

数据库唯一性裁决同证据版本竞争；既有 BEGIN IMMEDIATE、batch lease/attempt 和世界快照栅栏保持。候选生成/复用、版本插入、归因标记与 batch 应用同事务；后续 core 的核验和投影状态同事务。来源撤销/revision 变化后旧 worker 不能以过时证据写出新版本。重复已应用批次不重发模型；相同支持集跨 job 仍为同一版本。

实际写锁等待可能跨过租约截止；`_apply_batch` 在获得 writer 后使用 `max(caller_now, live_now)` 重采样检查。调用方未来时间可收紧恢复测试，但旧时间不得延长真实租约。过期执行者保留已有模型结果供原恢复机制处理，不采用证据。

## 5. 验证与回退

复用 E 的真实 core→assist→后续 core 调度方式，只替换模型 transport；按冻结后的策略拆分 fixture。第一条从尚无完整共同父母证书的候选开始，经真实公开投影与 dismiss 后补充完整支撑，验证内部版本与私人驳回并存；迁移样本另覆盖原已完整支撑、已投影/已驳回的历史记录。第二条从完整证书开始，首个候选即内部化，经相关/无关/无新证据及反向输出，验证从未新增关系待办或推测边。不能为照搬旧 harness 的 Suggestion 断言放开新公开入口。另补权限下降、来源撤销、版本变化、两个执行者和通知计数。

先完成无 schema 的键族补丁并独立验收；证据版本补丁串行跟进，避免共享 steward 文件和迁移。有版本数据或已采用新归因模式时，降级迁移必须拒绝丢弃记录并要求保留 schema 向前修复；空新结构才允许降级。代码回退仍须保留 `versioned` 的两个公开投影入口隔离，不能把已内部化的候选重新变成用户待办。不引入额外能力开关。

## 6. 文件责任与最小改动

- `services/steward.py`：MR-26 所属前缀及删除范围；沿用其空间可消费事实规则。
- `services/steward_delivery.py`（渐进重算先集成）：MR-23 后续 core 的版本意图准备及发布后内部核验，复用现有候选交付种类和 fence，不改核心发布原子边界。
- `models/steward.py`、模型导出与一个新迁移：归因标记及版本持久化/约束，保留现有身份和引用。 0048 仅扩展可复用降级预检 helper 的可选参数，保留其历史 SQL 与默认行为；对应 publication 迁移测试以动态单头继续验收原合同。
- 新 `services/steward_candidate_evidence.py`：共同父母证书、规范化摘要、幂等版本写入及一次内部核验；不承接公共呈现。
- `services/steward_assist.py`：结构去重改为稳定候选复用后记录相关证据；保留网络/预算/批次栅栏。
- `services/steward_suggestions.py`、`services/steward_inferred.py`：排除已版本化候选，阻止两个入口绕过内部策略；不改现有个人化展示。
- 相关真实迁移 pytest 及必要清表顺序：覆盖 AC，尤其多 job、无关证据、失权/撤销/revision、重试并发和零新待办。无前端、线上模型或生产数据改动。
