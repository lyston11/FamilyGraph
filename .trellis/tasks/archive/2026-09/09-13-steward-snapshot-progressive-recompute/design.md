# Design — Steward 短事务重算与渐进家谱加载

状态：已实现，完成主线接合和最终本地验收，2026-09-14。需求以 [prd.md](prd.md) 为准；规划时事实出处见 [research/current-state.md](research/current-state.md)。本文数值为预算/验收目标，最终实测及限制见 [集成验收](research/integration-acceptance.md)。

## 1. 边界与核心决策

一个主任务负责这条跨层一致性链。后端计算、发布模型和前端协议共享同一版本合同，按 implement.md 的步骤串行集成，不建立重复的父子任务。

- SourceFact、账号/成员资格、词典、用户动作状态仍为真源。
- 图与称谓服务提供不可变输入上的纯计算；同一 viewer 的授权图复用，不跨 viewer 复用未剪枝数据。
- Steward 协调租约、快照、调度、分批保存、正式发布及恢复。
- PFV 提供同一 generation 的确认骨架与完整 target 结果；浏览器只渲染服务端认可的内容。
- 通知/动作卡等交付采用 generation 门控的幂等待办；模型继续使用既有 assist 预算、provider 和授权机制。
- 纯 CPU 搜索使用有界 spawn 进程执行器；DB Session 和 ORM 对象不得传入计算进程。maintenance 不等待整族计算。
- 保留 SQLite WAL、5 秒 busy timeout、现有 worker 开关和每空间单活跃作业；移除旧的整族长写事务路径。

## 2. 用户体验与完成范围

### 2.1 首次进入

有当前有效的已发布结果直接显示。否则登记本人视图需求，优先准备已授权、在现有遍历规则下可达的确认骨架。节点展示当前安全名称/字段，确认连线使用 topology_edges；缺个人称谓显示“整理中”，可以浏览已有授权档案。

骨架使用统一图原语的有界可达性，不枚举每个 target 的全部替代路径；保留 MAX_PATH_DEPTH、partner 不延伸、桥接授权等规则。不包含隐藏或不可达人物，不能直接展示 active member 清单。

随后按当前选中目标（若请求提升）、近亲距离、稳定 user id 顺序计算；每个 target 的主路径、替代路径、称谓和确定性解释作为完整单元展示。既有模型优化/推测为独立可选层，确认称谓不等待模型。

### 2.2 刷新和失败

- 输入未变化：复用完整结果；周期维护不制造重复加载。
- 图/权限变化：新 generation 替换授权骨架、撤下失效称谓，保留仍存在节点的位置/视口；不保留已撤权人物或旧路径作为占位。
- 仅称谓变化：复用有效结构路径，重做展示，骨架不重排。
- 本人目标齐全：结束本人加载提示；全空间其他必需工作不进入本人分母。
- 单 target 失败/预算耗尽：明确部分失败，不计为 no_path 或成功；有限重试、重复点击合并。
- 断网：停止无限旋转，短暂保留展示有效期内的内容；有效期结束后遮蔽，恢复网络先重新授权。
- 整代失败/superseded：仅在输入与权限仍有效时展示明确的部分结果，不能标 current；旧回执不能继续推进。

## 3. 逻辑模型与原子边界

以下为存储职责/不变量。迁移可采用新增表或 generation 分区，但不能继续无版本地原地分批覆盖子行。

| Entity | Required contract |
| --- | --- |
| Input revisions | 空间及 viewer/展示范围输入版本；业务变更与版本推进同事务，普通进度不推进输入版本 |
| StewardGeneration | space、job、lease attempt/owner、输入版本集合、固定 execution cursor、必需工作清单、状态、valid_until、完成计数 |
| ViewGeneration | account/root/space、viewer 内单调公开 generation 序号、内部 StewardGeneration、结构/展示摘要、骨架、目标状态、revision |
| Versioned results | generation/view/target 唯一；完整 found/no_path/failed 结果与证据；旧 pair 不覆盖新代 |
| Retry budget | 按有效输入指纹、viewer/target/计算阶段持久记录尝试次数、退避和耗尽状态；换 generation 或周期扫描不重置预算 |
| Publication | 空间正式发布指针和不可变 viewer 结果清单；未变化视图可引用已验证旧结果，最终不逐行复制 |
| Delivery intents | 预备 finding/卡片/建议/通知/assist 计划，唯一业务键、期望输入/对象 revision、状态/有限重试；generation 发布后才可消费 |

ViewGeneration 的目标集合在同代固定。内部可缓存 no_path；公开进度只统计该 viewer 的授权目标，不输出全空间处理对数或其他账号状态。

### 3.1 批次提交与正式发布

批次提交在独立短立即事务内 fresh-read/CAS 核验输入和租约，保存完整 target、批次唯一回执、revision 与进度。它只产生可校验 preview，不更新 last_event_cursor、不发通知、不把全作业标成功。

正式发布在全部必需工作及交付计划就绪后，再次 CAS 校验，以短事务切 publication 指针、将 generation/job 置 published/succeeded、提交固定 execution cursor、激活待办、写完成事件并登记已知后继需求。这些一起提交或一起回滚。

全部大行集/JSON 预先分批准备。最终事务只做有索引的资格/计数检查和指针/状态更新；禁止重算路径、逐 viewer 扫全图、全量复制 staging、清理历史或调用网络。

正式读者只用已发布结果；渐进协议可读同代 preview。普通 DerivedFact/Agent 消费者不误读未发布 staging；自己的按需单 pair 结果也用同一计算原语和当前输入验证。

### 3.2 水位、需求与必需工作

execution_cursor 是 lease 时固定的消费承诺；snapshot 实际输入版本是另一概念，不能用全局最大事件 ID 代替。本人完成、预览 revision 均不推进消费水位。

必需工作包含事件窗口、确定性完整性检查、需要刷新的有账号 PFV 和必须准备的交付计划。未变化视图直接引用；不为 provisional 档案伪造账号。DerivedFact 全矩阵预热不是必需工作，现有按 pair 查询仍可按需计算。

需求登记与 lease/清单封口串行化：封口前纳入本次，封口后加入持久后继集合。已在清单内 viewer 的请求只改顺序，不改分母；新账号/权限通过输入失效触发新代。

后继条件为“更高事件水位 OR 未满足视图需求 OR 输入版本漂移”，包括同水位的新 demand。完成/废弃当前代与保留最高水位、登记后继必须同事务，不依赖进程内回调。复用 integrity_scan 的无 succeeded 短路语义，防止同水位需求丢失。

## 4. 输入快照与缓存

### 4.1 真实一致快照

新只读 Session 显式 BEGIN，将必要事实、节点/字段、授权、词典、版本记录和时间边界作为同一个 SQLite 快照读取。复制为不可变 DTO 后关闭事务/连接，再构图与计算；不能把 Session 上下文当作 pysqlite legacy 模式下自动存在的快照。

按 viewer 权限剪枝，不读取 private Session/Memory/RAG，不把整族原始图交给所有查看者。快照读取有数据量预算，不能以长 read transaction 阻碍 WAL checkpoint。

### 4.2 分层指纹

| Layer | Inputs |
| --- | --- |
| 结构/授权 | viewer/root/space、有效节点/边、事实内容及 revision、成员/ref/披露、bridge scope/revision、性别、图与路径算法版本 |
| 展示 | 结构摘要、可用于长幼判断的出生信息/可见字段、个人/空间/地区/系统词条及偏好、展示/策略版本、适用管家称谓投影 revision |
| 推测 | 独立有效开关、推测状态/证据/revision、上限和路径依赖；不改变 confirmed 缓存语义 |
| 时间 | bridge 到期、字段权限/年龄边界等最近到期时间 valid_until |

实现前形成输入生产者清单：domain_events、资料/披露、成员/ref、词条、桥接、推测状态与设置入口。每项明确同事务版本推进和失效测试，补齐未发事件的入口。发布只读有界版本记录，不在写锁内重新计算全图 hash。

指纹在搜索前比较；有效命中直接返回。同一图构建一次，DerivedFact/PFV 复用结构结果；仅展示变化不搜索路径。负缓存区分确定 no_path 与未计算/超预算，对外维持不可枚举语义。

自己的 staging、进度、publish/completed 事件不进入输入指纹。模型产生的新有效展示投影可触发一次有指纹去重的展示刷新。

## 5. 执行、租约与恢复

### 5.1 执行节奏

协调器只做短 DB 步骤、派发和回收。有界 spawn 执行器初始单 CPU worker；通过现有作业租约的短事务检查限制全局活跃作业数量，不引入第二套任务队列。

每调度片受目标数、展开步数、结果字节和墙钟预算约束。搜索使用显式可续算状态让出执行；进程重启可重做半个 target，已完成批次按指纹复用。128 条已找到路径不是展开预算。

活跃 viewer 优先，不同空间/普通视图轮转有份额。改变顺序不创建新代或失效结果。待算图和续算状态有内存上界；heartbeat 按 TTL 独立安排，不等待计算返回。

短事务之间还需公平的写入机会：同进程/Engine 的 staging、发布、交付、GC 和心跳入口共用 FIFO admission。累计 writer 步骤达到 50ms 后，下一次 BEGIN 前统一留出 100ms 空档；步骤包括取锁、提交/回滚和 Session 清理，自然读/计算空档可抵扣。等待时没有新 Session 或业务写锁，多个协调器不能填掉彼此空档。默认 SQLite busy handler 后段每100ms重试，单纯缩短事务或仅串行化不能防止重复抢先。此取舍降低后台写入吞吐以保留在线取锁机会，既有 busy_timeout 不变；它不抢占单笔事务或跨进程协调，实际最大值仍须负载实测。时间栅栏在实际入事务后读取时钟，排队跨过期限就拒绝，不能续活过期租约。

### 5.2 全部状态写入的 fence

正常计算者的 heartbeat、save_batch、retry 和 publish 以数据库条件更新核验完整有效 fence：

- job id、active 状态、唯一进程 owner、expected attempt；
- 当前 deadline 严格晚于当前时间，过期者不得续租；
- generation 属于本租约且仍 active；
- 相关输入 revision、策略/算法版本匹配，valid_until 尚未到。

取得租约和失效收敛有不同前置条件，不能要求已经失效的输入重新变有效：

| Transition | CAS predicate and effect |
| --- | --- |
| claim | queued、available_at 到期、仍有预算/容量；原子递增 attempt、设置唯一 owner/deadline，不要求尚未存在的旧租约有效 |
| heartbeat/save_batch/publish | 上述完整有效 fence；续租不能复活过期租约，publish 还要全部必需工作成功 |
| owner retry | 完整 owner/attempt/未过期租约与同代输入仍有效；输入已漂移则走 supersede，lease 已失效则交 reaper |
| supersede | 匹配观测到的旧 job/generation/attempt/owner 绑定和 active 状态，并验证实际输入漂移或 valid_until 到期；不要求旧输入有效；同事务保存后继需求 |
| reaper | 匹配观测到的 job/attempt/owner、活跃状态和 deadline 已过期；按预算回队/终结，不以输入匹配为回收前提 |
| cancel | 协调器有权取消的明确原因、观测到的 generation/attempt/owner 与状态 CAS；停止收回执并保留未满足需求，不要求已经失效的租约继续有效 |

每次新 Session/强制 fresh-read，不信 expire_on_commit=False 的旧 ORM 实例。任一 CAS 失败者不得修改新 owner 的状态；新 attempt 接管/恢复 generation 时也须原子更新其租约绑定，旧执行者无法再废弃被接管的新工作。

| Failure | Handling |
| --- | --- |
| 输入变化/时间到期 | superseded、停收结果，持久保留最高需求并重调度；复用指纹未变缓存 |
| lease 失效/易主 | 拒绝旧保存、续租、失败回队、发布；新执行者按 fence 恢复 |
| 计算崩溃/超预算 | 有限重试，检查点不提前推进；任一必需 target 耗尽则整代 failed，保留仍有效的明确部分预览和未满足需求，不 publish、不推进水位 |
| 批次提交前崩溃 | 重做该批 |
| 提交后回执丢失 | 唯一批次/target 键去重，进度只增一次 |
| 发布失败 | 指针、job、水位、待办激活一起回滚，预览仍未发布 |
| 发布后崩溃 | 交付待办恢复，不重复发布/通知 |

尝试预算按真实输入指纹和 target/阶段持久记录：仅换 job/generation、进程重启或定时扫描不得清零；展示变化也不清除未变化结构搜索的失败预算。扫描遇到耗尽输入只做轻量维护并报告失败，不能再次执行同一重计算。相关输入变化可建立新预算；显式人工重试按服务端冷却授予一次有界机会，重复请求合并并有审计。可选推测/辅助失败不阻止确认 core 发布。

reaper、缓存淘汰和旧代回收按行数/字节/持锁预算分批，不能一次 DELETE 清全部历史。

## 6. 确定性检查与发布后交付

_execute_locked 全部职责迁入新流水线，不能只迁走 PFV：

| Responsibility | Prepare / publish / delivery |
| --- | --- |
| 事件窗口/失效 | 记录窗口和版本；安全失效仍在真源变更事务内；消费水位仅 publish 更新 |
| finding/重复人物/关系缺口 | 快照计算清单，分批准备幂等事件/交付计划 |
| PFV/派生关系 | 纯计算、版本化 staging；publish 指针生效 |
| 旧卡复核/新卡/到期 | 准备带对象 revision/资格依据的计划；发布后经现有命令和当前权限短事务落地 |
| suggestion/inferred | 可选层隔离，已发布代才交付；读写都重验开关、证据、中间路径节点 |
| assist/模型 | 发布后登记/领取，保留 job succeeded、provider、预算 fence；HTTP 不入 DB 事务 |

本地 DB 交付结果与 intent done 同事务；业务键沿用证据去重，重复代不重复通知。用户已提交/忽略/撤销优先；revision 冲突重验或安全跳过，不覆盖用户操作。输入改变后旧意图失效，新作业负责新输出。

外部 HTTP 不承诺跨崩溃的 exactly-once。模型复用现有 StewardModelCall 持久预留和 reserved/in_flight/unknown 恢复：已发出但结果未知时保守计费、不得由通用 intent 重试再次发送。只登记同一批次/调用一次；后续处理交既有 assist 恢复器。现有实现明确上游幂等能力未经证明，故本任务不自动重发 unknown，也不增加新的供应商幂等假设（steward_assist.py:23、:1228）。

核心成功表示“计算结果及必需交付意图已原子发布”，不表示模型/通知全部完成。管理员分别看到交付 pending/failed；有限退避耗尽可重试单项，不能重跑已完成核心。卡片查询/动作执行即时复核资格，交付排队不能延长失效卡片的可操作期。

普通交付以 effect_fingerprint 持久记录跨代失败预算，领取时绑定 owner/attempt/deadline；成功只退还本次预留。管理员可通过 GET /admin-api/v1/steward/deliveries 读取元数据，通过 POST /admin-api/v1/steward/spaces/{space_id}/deliveries/{intent_id}/retry 提交 reason、expected_policy_version 和 expected_attempt。重试经 CAS、冷却和审计只增加一次机会，不重算 core，也不重发模型 unknown。

## 7. API 与浏览器合同

### 7.1 读取与 demand

GET /api/personal-family-view 新增 progressive=true；缺省保持旧合同。仍用原 nodes/edges/topology_edges/inferred_edges 形状，增加 progress，viewer 始终从认证身份取得。

progress 至少含 contract_version、viewer 内单调 generation、同代 revision、topology_revision、phase（queued/preparing/building/ready/retrying/failed）、completed_count/total_count、公开 target 的 pending/ready/unavailable/failed、next_poll_ms 和安全原因码。

status 仍说明是否正式 current；preview ready 仅表示本人齐全。published/preview ready 均可结束本人加载提示。暂无骨架时 preparing + 安全空态；初始约 250ms 的有界快速轮询取得骨架，随后约 1 秒。旧客户端不会意外收到 running + 预览内容。

扩展 request_view_recompute 为认证 account+space 的幂等需求登记，增加 POST /api/personal-family-view/demand，支持对当前授权骨架内 focus_user_id 提升顺序或有限重试。GET 未准备好时的兼容登记仍用独立短 Session；载荷构建不承担写事务。POST 不接受 viewer、任意优先级或隐藏目标；重复请求合并，不推进输入版本。

### 7.2 安全、ETag 与展示有效期

读取先重验账号/空间、输入、时间和节点/路径授权，再选同代快照、生成 ETag。ETag 绑定协议、授权 epoch、generation/revision、最终授权载荷和状态；部分结果亦可 304，但权限/到期/新代优先，不能命中旧内容。

首版为累积快照，不引入 delta/分页恢复。每次 200/304 都在本次授权复核通过后返回明确的服务端展示有效期（受语义 valid_until 与短客户端展示期限共同限制）。304 只有带匹配 ETag、当前请求链及明确的新有效期时才能续期；缺少这些信息不得自行延长旧内容寿命。有效期可用专用响应头传递，避免每次续期改变数据 ETag；同步 CORS exposed headers、时间换算及客户端测试。

时间协议使用同一请求签发的 `X-PFV-Validated-At` / `X-PFV-Display-Until`（Unix 秒），两者和 ETag 均暴露给浏览器。HTTP `Date` 由服务器负责，接口不得重复写入；Uvicorn 会合并重复 Date，导致旧解析得到 NaN。客户端优先专用校验时间，存在但非法时拒绝使用；仅缺少专用头时兼容单个合法 Date，并扣除完整往返耗时和 1 秒精度余量。

推测结果经独立输入/路径/开关校验后作为附加层，其失败不阻塞确认骨架。未知协议安全降级；后台停止/积压给明确状态，不无限 preparing。

### 7.3 前端行为

store 按 account+space 隔离，一条更新链用请求序号与 generation/revision 拒收迟到/倒退。同代累积替换；新代完整替换结果集合，仅保留仍存在节点的非敏感布局，不拼旧行。

首骨架或主动“适应画布”才 fitView。相同 topology_revision 只更新标签与面板当前对象；选中项按稳定 ID，保留拖动/缩放。真实结构增删受控重排，失权节点移除。

building 约 1 秒轮询；ready/current 停高频提示并转低频保活/失效检查，有效期要求优先于普通间隔。隐藏暂停、前台立即重验；断网退避且有可见终态/重试。401/403/404、登出、切空间清理；短暂网络错不无条件卸载仍有效画布。

## 8. 迁移、兼容和回滚

加法迁移 input revision、generation 分区/唯一键、publication、delivery intent 与索引；先隔离库 upgrade，再验证旧缓存安全失效及旧响应。按实际 head 分配序号，不改旧 0041/他人迁移。

旧缓存缺完整指纹/generation，不能直接作为新协议结果；升计算/显示合同版本，后台按需重建，保留事实、账号及用户动作历史。

顺序：隔离检查 → 后端新执行器及旧 API 兼容 → 渐进协议 → 前端 → 小范围观察 → 扩展。复用 worker 开关，spawn 进程由现有服务管理，无需新增独立服务部署单元。

UI 回滚只关闭渐进请求，保留短事务执行器。后端回滚先停 worker，保留 generation/待办和事实，不能恢复旧长事务作为自动 fallback。未发布结果/未交付待办存在时，降级拒绝破坏性删除。生产备份只用项目 SQLite backup 命令。

## 9. 风险与发布门槛

主要风险为输入版本传播遗漏、CPU 单目标长尾、卡片/建议交付语义、历史缓存兼容、共享文件集成。分别以生产者矩阵、可取消预算、幂等交付故障注入、双协议回归及串行集成控制。

AC1–AC9 为发布门槛；首屏和持锁目标须实测。超预算明确失败/重试，不改事实算法或藏成空结果。SSE、全矩阵预热新入口、精细受影响子图算法、跨数据库改造延期，当前根治不依赖这些事项。

## 10. 实施落点与集成状态

- steward_snapshot / relationship_graph / relationship_resolver / terms：显式快照、不可变 DTO、结构复用及可续算搜索。
- steward_pipeline / steward_runtime / steward_views：有界后台执行、版本化目标、短事务 fence、正式 publication 和经验证的渐进读取。save_target 仅加载进度列并复用锁外编码 JSON，预算退款与完成状态幂等提交。
- steward_demand / steward_delivery / steward_overlay / steward_gc：持久需求、独立交付/推测、跨代预算与有界回收；查询消费者统一 current_view_payload。
- frontend PFV API/store/polling/FamilyTreeView：骨架先出、单调进度、展示有效期、输入失效与非敏感布局保持。
- 0048_steward_terminology_publication 已连接主线 0047_rag_lifecycle_integrity 与本任务 0045_steward_staged_publication，Alembic 为单 head；两父分支的升级次序、受限降级/重升级、FK ON/OFF 和事实/未交付责任保留均通过。0041/0042 仅有 AST 等价的格式整理。
- 两个性能脚本、最终源码清单及真实 API smoke 的证据均保存在 research；smoke 已补齐第三个 listener 的随机 loopback 端口，避免占用现有开发隧道。无生产操作。
