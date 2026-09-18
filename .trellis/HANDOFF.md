# FamilyGraph · Trellis 当前交接

> 更新：2026-09-15。Memory/RAG 累计验收后，管家渐进重算已在 `dee91a1` 合入 main、由 `27fe936` 归档；MR-26 `cff6f8e` 与 MR-23 `b43602d` 已合入 main 并归档，任务 worktree/分支已清理。本文是交接快照，最终集成与归档证据见各任务执行记录，代码通过不等于已部署。
> 当前设计与验收要求看对应任务的 `prd.md`、`design.md`、`implement.md` 及最新研究记录；已实现行为看代码、迁移、测试和 Git 集成证据。工作流以 [AGENTS.md](../AGENTS.md) 与 [workflow.md](workflow.md) 为准。本页末尾保留 v1 历史，已标为历史的 `.trellis/spec/` 条款不覆盖现行任务。
> 架构入口：[系统架构与设计](../docs/ARCHITECTURE.md)；运行与验证入口：[README](../README.md)；数据播种：[DEV-DATA-SEEDING.md](../docs/DEV-DATA-SEEDING.md)。

## 当前最需要知道的事

- **2026-09-17 双 Agent 最新复核**：原 A/B 已归档，但 `d8d3668` 的阻塞 I/O 总截止、助手源计时/重试统计仍有反例；只读线上核查显示代码同步后服务未加载修复，浏览器未验。父任务保持 in_progress，新增 C～I 七个子任务，见[最新结论](tasks/09-17-dual-agent-latency-result-integrity/research/review-summary.md)和[任务图/全部PRD入口](tasks/09-17-dual-agent-latency-result-integrity/research/remediation-task-map.md)。**C（管家可中断总截止）与 D（助手源计时与重试统计）已在各自分支实现并验证，待串行集成**；**E（网关错误分类与分层重试治理）的工程部分已实现并验证**——上游永久 4xx 不再折叠为可重试 502（实测最坏 24 次出站降为 1 次），每次出站尝试留恰好一条安全审计，两层预算显式冻结；**降低总重试预算仍未批准**（策略表见 E 证据，待用户选择）。F/G 仍待执行，H/I 为方案任务。尚未部署、未重启服务、未调用真实模型。下方旧成果表按原时点理解。

- 管家已经自动计算个人称谓，通知、建议详情、推测面板与档案称谓区已接通同一呈现链；称谓优化不需要逐条批准。闭环及质量修复已合入，详见 [最新审核记录](tasks/archive/2026-09/09-13-steward-kinship-capability-closure/research/quality-review-2026-09-14.md)。
- Memory/RAG 的 A/C/B/D 修复与 E 研究已经完成累计验收，原 B/D 20 组缺口和 D 追加恢复回归均闭合，见 [最终验收](tasks/archive/2026-09/09-14-memory-rag-acceptance-audit/research/final-acceptance.md)。管家渐进重算也已完成本地 AC1–AC9、串行集成和归档，见 [集成验收](tasks/archive/2026-09/09-13-steward-snapshot-progressive-recompute/research/integration-acceptance.md)。
- MR-26 已限定行为重建的三个所属键族，推荐忽略冷却及未知键保留；MR-23 的共同父母证书仅内部记录与核验，不因换版新增关系通知、待办或推测边，不解除既有驳回。
- Provider 配置 UX、平台辅助开关已有归档成果，两项的活动旧副本与相关会话指针均已清理。平台能力页已补修部署关闭时误报启用的问题，并展示四类辅助的来源与阻断原因。称谓能力也不再受早期“Agent 能力整体延期”描述约束。
- 本页未核查线上开关或真实模型质量。新称谓模型链使用 fake transport 验证，`STEWARD_ASSIST_TERMINOLOGY` 默认关闭。**2026-09-17 补充**：已在服务器隔离库用部署既有 Provider 跑通真实 terminology 调用链与校验器（合法同义 3/3 通过）。随后按用户确认启用生产部署开关 `STEWARD_ASSIST_TERMINOLOGY=1`（仅空间 2，`cloud_allowed=1`，架构/Provider/预算不变），有效开关与真实调用已核对；真实调用在批次租约内未返回而被收敛为 `unknown`（不重放），尚无自动消费的合法改善。管理员前端硬刷新会话恢复竞态已修复（守卫等待在途轮换、挂载晚于首次导航）。见[验收记录](tasks/09-17-admin-refresh-terminology-enable/research/model-acceptance-2026-09-17.md)。

## 称谓、事实与授权的当前合同

基础链路保持 `SourceFact → relationship_resolver → Terms → PersonalFamilyView`。管家复用这套关系与称谓引擎，自动投影只改变显示，不成为新的亲属关系事实。

| 行为 | 当前处理方式 |
| --- | --- |
| 自动计算、刷新个人称谓 | 后台作业自动完成；本人词条、空间词条优先于自动投影 |
| 通知、建议、推测详情的关系表达 | 服务端从认证查看者派生 `presentation`，表达方向、参照人和证据状态；前端不把 `fact_type` 翻译成“生物学亲子”等产品文案 |
| 确定性或模型称谓改善 | 合法投影自动生效；`term_preference` 在档案称谓区提供可选反馈，新建议 `notify=false`，旧称谓通知也无需处理 |
| “保留为我的叫法” / “恢复默认叫法” | 复用本人词条；操作时校验当前语义与版本，恢复立即回退并抑制同语义重复应用；不伪造 `TermUsage` 或自动晋升空间词条 |
| 模型关系候选 | `candidate` 只提供未核实的原子线索；`terminology` 才输出受约束称谓，不能凭模型自报概念码通过语义校验 |
| 真实关系确认、入空间、授权变更 | 继续走各自授权与状态机；自动称谓不代替当事人的关系确认或授权，也不要求所有建议双方逐条确认 |
| 通用长期记忆 | Memory 候选仍须明确确认；称谓的自动显示改善不等于聊天自动写长期记忆 |

补充边界：

- viewer 必须来自认证身份。第三人之间的结构边以真实端点表达，不能把当前用户的个人称谓套到该边上。
- 只显示当前获权的姓名与路径；模型线索标记待核实，整空间事实数不能充当某候选的证明数量。
- 未读、需要处理、可选偏好、本人忽略、过期及关联提案终态分别建模；列表、详情、通知、推测树和动作须一致。
- 普通 GET 不调用模型、不创建关系事实。账号、空间或目标切换后拒收旧请求响应；撤权、事实、年龄或偏好变化后重验投影。
- 称谓辅助复用四类 assist 的 batch/attempt、预算、Provider、租约和恢复；失败或关闭时回退确定性称谓，已经显式保留的本人词条继续有效。

设计依据：[称谓闭环 PRD](tasks/archive/2026-09/09-13-steward-kinship-capability-closure/prd.md)、[设计](tasks/archive/2026-09/09-13-steward-kinship-capability-closure/design.md)、[职责对齐](tasks/archive/2026-09/09-13-steward-kinship-capability-closure/research/task-alignment.md)。

## 已合入主线的相关成果

| 能力 | 集成证据与入口 |
| --- | --- |
| 个人家族视图、结构拓扑及独立推测层 | [家族树拓扑](tasks/archive/2026-09/09-13-family-tree-relationship-topology/prd.md)、[推测层](tasks/archive/2026-09/09-13-steward-inferred-tree-layer/prd.md)已归档；个人摘要与直接结构端点分开，推测不覆盖已确认事实 |
| 称谓闭环 A/B | `ad10dd0`、`9baf659`；viewer 呈现、自动建议/投影、terminology 和偏好反馈 |
| 称谓交付后质量修复 | `c8805bf`、`d26bb83`；方向/可见性、有效状态、Keep/Restore、实时输入/租约/恢复、实际前端入口及迟到响应隔离 |
| Provider 配置 UX | `9017a6d`，归档 `e0e8d8d`；[归档任务](tasks/archive/2026-09/09-12-agent-provider-config-ux/prd.md)。活动旧副本、过期会话指针和归档上下文旧路径已清理；[核对记录](tasks/archive/2026-09/09-12-agent-provider-config-ux/research/duplicate-cleanup.md) |
| 平台辅助开关 | `969a5b0`，归档 `4649e48`；称谓 B 扩展第四类 `terminology`。补修 `73b0bbd` 使提示与返回生效值一致并展示部署阻断，管理员前端 111 测试与构建通过；活动旧副本、修复 worktree/本地分支已清理，见[修复与对账记录](tasks/archive/2026-09/09-13-steward-assist-platform-switch-admin/research/remediation-2026-09-14.md) |
| 称谓自主优化与自动应用 | `b4f1283`、`f98e824`、`7480367`；别名归一化（`Um-Dm-Dm→侄子`）、四条配偶旁系词修正与迁移 `0050`、称谓退出待办。独立核验发现并修复“别名码接受其他原码 space 自定义词”的模型词表越界。真实模型链已在隔离库验证；AC7 标记阻塞：870 条已发布目标中不存在可改善候选，模型对等长同义替换一律弃权，故未启用生产开关。见[真实模型验收](tasks/archive/2026-09/09-16-steward-terminology-auto-apply/research/model-acceptance-2026-09-17.md) |
| 管家调用结果保全与批次时限 | `e482f59`、`9c4a677`；逐笔结算（返回即在其执行身份仍有效时提交，再发下一笔）、混合批次独立产物可消费（批次仍如实 `failed`）、恢复器持久产物优先、原实现仅在chunk返回后检查截止，阻塞I/O和结算预留未闭合，已交C修复（原“整笔墙钟总截止”声明撤回）。unknown 仍保守计费且不重放。见[验收记录](tasks/archive/2026-09/09-17-steward-attempt-result-integrity/research/evidence/result-integrity-2026-09-17.md) |
| 助手响应延迟分段定位 | `076d631`、`32b80f1`；`GET /admin-api/v1/agent/latency` 新增 `assistant_phases`。**D 已修正其口径**：`created_at` 是入库时刻（sidecar 250ms 批量 flush 会把约 125ms 工具执行量化成约 1ms），精确时长改由 `agent_run_events.timing_json`（sidecar 源计时，迁移 0051）提供，`queue_wait` 改用不可变 `agent_runs.first_leased_at` 并单列 `prepare`；分母改由 run 表 LEFT JOIN，单次失败重试与尾部耗尽不再丢失；轮内压缩作为 `model_turn` 子成分单列。历史 n=2 的 33.15s 是所采持久事件区间且含重试，不能据此排除排队/context/工具/显示。**未做提速改动**，逐字显示/换模型/扩容均待用户决定。见 [D 证据](tasks/09-17-assistant-timing-observability/evidence.md) 与[验收记录](tasks/archive/2026-09/09-17-assistant-latency-diagnosis/research/acceptance-2026-09-17.md) |
| 双 Agent 延迟集成验收 | 子任务 B/A 已归档并清理；集成基线 `main@651ee07`。集成期补测两项助手事实：**① 旧`model_turn`包含重试等待**，已增下界与失败计数，但单失败/耗尽/审计遗漏和关联仍有缺口，D/E重新校准；历史10.22s仅为失败完成间隔下界，不是全部重试耗时；**② 助手实际推理档位是 SDK 默认 `medium`**（平台无档位控制项）。**父任务不归档**：最新复核重新打开AC04/08/09，并保留AC01/06/07缺口；C～G工程/验收，H/I方案决策。AC06不强制真实提速，要求可信归因；真实服务加载也尚未验证。见[集成验收](tasks/09-17-dual-agent-latency-result-integrity/research/integration-acceptance.md) |

“代码已接通”“当前环境开关有效”“真实模型质量已验证”是三项独立状态。旧平台任务的 candidate/explanation 真实调用记录不能用作 terminology 的质量验收。

## 已验收成果与继续推进的任务

| 任务 | 当前可确认状态 | 接手时应读的最新依据 |
| --- | --- | --- |
| 双 Agent Memory/RAG 总任务 | A/C/B/D修复及E研究已验收，AC-01～10通过；`a83b5d1`已合入main并push，七项归档及worktree/本地分支清理完成 | [PRD](tasks/archive/2026-09/09-13-agent-memory-rag-remediation/prd.md)、[执行计划](tasks/archive/2026-09/09-13-agent-memory-rag-remediation/implement.md)、[最新执行记录](tasks/archive/2026-09/09-13-agent-memory-rag-remediation/research/execution.md) |
| A：记忆来源与确认契约 | `d1f43a5`已合入main，手工来源、RAG依赖、候选响应和安全重试经真实API及累计回归验收 | [任务](tasks/archive/2026-09/09-13-memory-contract-repair/prd.md) |
| C：会话恢复与压缩 | `8e91c42`已合入main；同一Pi manager恢复/压缩、overflow恢复成功结算通过；跨Run持久摘要仍未实现 | [任务](tasks/archive/2026-09/09-13-assistant-context-compaction/prd.md)、[复查 PRD](tasks/archive/2026-09/09-14-memory-rag-acceptance-audit/prd.md) |
| B：中文召回与可信引用 | `b6688f8`/`bc76e95`闭合B-I01～10；执行身份、精确引用、所有读取出口、真实子预算、补足及追问通过 | [任务](tasks/archive/2026-09/09-13-rag-retrieval-citations/prd.md)、[复查台账](tasks/archive/2026-09/09-14-memory-rag-acceptance-audit/research/findings.md) |
| D：索引生命周期 | `aebee83`/`dd8157c`闭合D-I01～10及合法superseded恢复回归；唯一性、正文证据、不可变片段、租约/事务、换版和无损迁移通过 | [任务](tasks/archive/2026-09/09-13-rag-index-lifecycle/prd.md)、[复查台账](tasks/archive/2026-09/09-14-memory-rag-acceptance-audit/research/findings.md) |
| Memory/RAG 验收复查 | F-01～12通过；原红测、最终制品hash、代码绑定和局限分别保留，E延期能力未记作生产实现 | [最终验收](tasks/archive/2026-09/09-14-memory-rag-acceptance-audit/research/final-acceptance.md)、[验收矩阵](tasks/archive/2026-09/09-14-memory-rag-acceptance-audit/research/acceptance-matrix.md)、[实施计划](tasks/archive/2026-09/09-14-memory-rag-acceptance-audit/implement.md) |
| E：能力扩展准入 | 研究交付已归档，生产扩展未因此上线；主动检索、聊天保存/候选、导入、混合检索、全请求预算、持久摘要等按采用门槛保持延期 | [任务](tasks/archive/2026-09/09-13-agent-memory-capability-plan/prd.md)、[12 项决定](tasks/archive/2026-09/09-13-agent-memory-capability-plan/research/capability-decision-register.md) |
| 管家快照与渐进重算 P1 | 本地 AC1–AC9 通过；`dee91a1` 已合入 main、`27fe936` 归档，worktree/本地分支已清理 | [PRD](tasks/archive/2026-09/09-13-steward-snapshot-progressive-recompute/prd.md)、[集成验收](tasks/archive/2026-09/09-13-steward-snapshot-progressive-recompute/research/integration-acceptance.md)、[代码与清理证据](tasks/archive/2026-09/09-13-steward-snapshot-progressive-recompute/research/integration-final-evidence.json) |
| MR-23/MR-26 证据与行为投影 P2 | SP-AC1～6 通过；`cff6f8e` / `b43602d` 已合入 main 并归档，worktree/分支已清理；保留独立冷却、内部证据换版、双向隔离及写锁后的租约检查 | [任务](tasks/archive/2026-09/09-13-steward-memory-evidence-projections/prd.md)、[实施顺序](tasks/archive/2026-09/09-13-steward-memory-evidence-projections/implement.md)、[最终验收](tasks/archive/2026-09/09-13-steward-memory-evidence-projections/research/final-validation-summary.md) |
| Steward 能力后续 P3 | shared RAG、额外个人路径解释、新地区包及有观测依据的性能研究仍延期；已交付称谓和独立 P1 重算不受此状态覆盖 | [任务](tasks/09-11-steward-capability-followups/prd.md)、[设计](tasks/09-11-steward-capability-followups/design.md) |
| 跨空间发现 | 继续延期；个人叫法可复用不授予其他空间路径读取权 | [任务](tasks/09-11-steward-cross-space-discovery/prd.md) |

部分活动任务文件尚未提交，远端检出可能没有这些路径；不要据此新建重复任务。先核对主检出、已登记 worktree、同名归档及提交祖先关系。研究文档中的“尚未实现称谓”“平台开关待建”等旧时点描述，以本页所链接的后继归档和当前代码为准，历史实验结果仍保留。

## 继续实施时的关键顺序与边界

1. **Memory/RAG**：本期F-01～12已闭合；后续修改保留A来源、C恢复/压缩、B精确引用和D事务/索引合同。旧bd899b9红测只用于追溯，新的代码变化按实际影响复验，不恢复旧lease、弱引用或破坏性降级行为。
2. **渐进重算**：保留已实现的显式一致读快照→事务外计算→短事务保存目标结果→CAS 原子发布结果指针、水位与交付待办。先展示获权的已确认骨架，再逐目标补称谓；preview 不提前消费事件或发通知，本人 ready 与空间 published 分开。同进程/Engine 的共享写预算在 Session 创建前让出写入机会，不改变输入、授权与租约栅栏。
3. **渐进客户端**：`progressive=true` 显式启用；普通请求保留完整视图/安全空态。读取和 304 均先重验授权与有效期；按账号、空间、请求序号、generation/revision 拒收旧结果；采用 `X-PFV-Validated-At` / `X-PFV-Display-Until`，同拓扑补标签时保持坐标、视口和选中。
4. **MR-26→MR-23**：重建只处理自己拥有的键族，保留冷却/未知键。证据版本使用相关 confirmed 事实的 ID/revision，不用全空间 hash 代替相关依据；后续 core 捕获版本 ID，再由发布后的 candidate 交付核验。内部 `projected` 是所记录时点的核验历史，不是持续有效的亲属事实；换版不新增公开投影或解除驳回。
5. **shared RAG 与能力扩展**：以已验收的A/B/D来源/索引/引用合同为基线，仍需各自采用门槛。Steward使用自己的job/space/consumer身份，仅消费当前空间获权的confirmed shared资料，不读取私人聊天/private memory，不伪造Assistant Run。通用反馈排序仍需收益评估。

这几条线不是同一个发布批次。它们共享 `maintenance`、`platform_features`、Steward/PFV、部分前端与迁移；按 [AGENTS.md](../AGENTS.md) 串行处理相交文件和主线集成，保留其他会话 WIP。

## 迁移与集成检查点

- 当前已集成的迁移单头为 `0049_steward_candidate_evidence`；其父 `0048_steward_terminology_publication` 连接 `0047_rag_lifecycle_integrity` 与 `0045_steward_staged_publication`。0049 原地扩展候选表并保存不可变证据版本，有证据或已采用归因时拒绝丢弃。数据库实际应用状态须另行核查，不将代码集成当作生产迁移。
- 0047已在隔离FK OFF/ON真实连接验证；重复来源/镜像冲突首项DDL前拒绝，0045/0047危险downgrade保留历史块与正文证据，不以删数据使往返通过。
- 0048 已完成称谓 A/B、质量修复和渐进发布的主线接合；两种升级次序均保留业务数据并恢复 60 条输入触发器。深降级须在首个 DDL 前预检计划路径中的 RAG/Memory/Steward 历史及待办，拒绝后 schema 和 head 均原样保留。
- 在隔离数据库验证合并后的 upgrade、旧数据兼容及所要求的回退限制；维护租约、引用依赖和进行中的发布不能被破坏性降级抹掉。运行期 SQLite 备份使用 `python -m app.backup`。
- 任务状态通过 Trellis 命令维护；只清理已经合入且没有未提交代码的 worktree/分支。活动目录的旧副本、未合并分支和其他会话资料不能随本页更新删除或归档。

接手时先做只读核对：

```bash
git status --short --branch
git worktree list
python3 ./.trellis/scripts/task.py current --source
python3 ./.trellis/scripts/task.py list
# 逐笔核对需依赖的提交；存在于仓库不等于已合入 main
git merge-base --is-ancestor <commit> main
```

## 验证证据及仍未完成的事项

| 范围 | 已有证据 | 实际限制 |
| --- | --- | --- |
| 称谓质量修复 | backend 1119 passed / 3 skipped，frontend 618 passed；lint、类型检查与构建通过，合并后相关 backend 59 passed；[记录](tasks/archive/2026-09/09-13-steward-kinship-capability-closure/research/quality-review-2026-09-14.md) | 3 项为既有延期的 break-glass 测试；真实 terminology 模型质量、生产 smoke 未测；关系最终确认的成环/证据留存仍按原 PRD 延期 |
| Memory/RAG累计验收 | 后端1352/3既有skipped、前端660、agent118检查点及代码一致证明；中文16/16、英文2/2、扩展7/10；真实listener/Pi/维护95/95、API56/56、迁移8/8；[矩阵](tasks/archive/2026-09/09-14-memory-rag-acceptance-audit/research/acceptance-matrix.md) | B/D原20组及追加恢复回归闭合；未测真实Provider/生产规模，扩展集3项仍未命中，E可选能力与MR-23/26 P2保持边界 |
| 渐进重算最终本地验收 | 后端 1535 passed / 3 既有 skipped，前端 740、管理员前端 111；静态检查与构建通过。30/50/200 人、两次真实 300 秒扫描、Chrome 桌面五次及移动端两主题、API smoke 56/56 均通过；[冻结源码与完整结果](tasks/archive/2026-09/09-13-steward-snapshot-progressive-recompute/research/integration-acceptance.md) | 200 人全空间冷算约 416 秒，本人 ready 约 54 秒，确认骨架先出；写预算仅覆盖同进程/Engine。性能数值为本机样本，目标服务器迁移、部署与运行观测未执行 |
| MR-23/MR-26 修复验收 | 定向 188 passed；完整后端 1594 passed / 3 既有 skipped；六个既有测试导入整理后 43 passed；Ruff/format（394 文件）、mypy（205 源文件）和独立审查通过；[验证记录](tasks/archive/2026-09/09-13-steward-memory-evidence-projections/research/validation.md) | fake transport 与合成迁移库；仅 direct_sibling 的共同父母证书，projected 为历史核验。未运行真实模型或生产迁移，未重复前端/浏览器/容量实测 |

各行均绑定其链接任务的冻结代码与验证环境。没有改变线上开关或部署。未来代码变化按受影响范围重新验证，smoke 退出码 2 仍代表环境阻塞。

## v1 决策的历史与取代关系

v1 的第一人称体验、全局图/空间视图分离、SQLite WAL、在线备份和附件授权等基础原则继续保留。以下旧语义已经有后继实现：

| 历史条目 | 当前交接口径 |
| --- | --- |
| D2/D3、v1 非目标中的 Agent/互反称谓 | 原子事实、关系解析、TermRegistry 与 viewer 呈现已落地；自动称谓与关系事实分开 |
| U5/QU1=B 的“直系结构边自动完整互见” | 以当前 `visibility.evaluate` 的四级可见性、字段披露与 purpose 收紧为准 |
| A4 与早期平台角色 | 独立系统管理员主体、密码认证、admin listener/JWT 域；家庭权限继续按空间授权 |
| T1 中的 Element Plus | 当前双前端使用 Naive UI；以依赖与源码为准 |
| v1 的固定任务数、未补设计清单与“可发布”结论 | 仅描述 2026-08-25 的历史交付，不能替代上文当前任务及验收状态 |

<details>
<summary>展开 2026-08-25 的 v1 交接与修订历史（历史资料）</summary>

以下保留旧版本内容用于追溯。其“权威来源”“锁定”“待定”“非目标”及发布状态均指当时版本；与上文后继设计冲突的条款不再指导当前实施。

### FamilyGraph · Trellis 交接摘要 v1.1（历史）

> 项目名：**familygraph**（原名 jiapu-web）｜状态：**v1 已实现完成并可发布**（2026-08-25，见修订历史 v1.3）
> 本文档是所有任务与 PRD 的权威来源。配套文档：[spec/architecture.md](spec/architecture.md)（全局架构设计，含全部 `[AD-n]` 审计默认假设）。
> 决策分三层：**锁定决策**（用户确认，不得重议）→ **审计默认假设 `[AD-n]`**（实现前可推翻，推翻需在此登记）→ **待定决策**（见 §七）。

---

## 一、最终目标

构建一个现代家谱协作 Web 平台：**人人有账号**，从自己的小家庭空间出发，把亲人逐一"拉进来"；家庭空间相连自然涌现出家族空间。底层数据为**单一全局关系图**——"接入大家谱"只是建立一条连接，永远不存在两棵树的实体合并/去重问题。

一句话：让每个家庭成员都能低门槛地维护以自己为第一人称视角的家庭空间，并安全地与整个家族连通。

## 二、任务结构（v1.1 按审计重构为父子结构）

结构口径：**5 个父任务（里程碑章程）+ 16 个子任务 + Bootstrap = 22 个任务**；其中 21 个业务任务的 implement/check jsonl 已填充真实条目（Bootstrap 为流程任务，无 jsonl 属正常）。

```
00-bootstrap-guidelines          初始规范与质量门禁（in_progress）
├─ M0 工程骨架与认证基座 (P0)
│  ├─ m0a 工程骨架与开发部署
│  └─ m0b 认证、首启和凭据安全
├─ M1 建档、关系与家庭空间 (P0)
│  ├─ m1a 档案、账号认领与代管权
│  ├─ m1b 关系模型与状态机
│  ├─ m1c 家庭空间与成员状态机
│  └─ m1d 三布局及基础日期能力
├─ M2 家族视图与可见性 (P1)      ← 依赖 M1 全部
│  ├─ m2a 授权矩阵与可见性模块
│  ├─ m2b 家族连通视图
│  └─ m2c 加入申请与断连流程
├─ M3 功能补齐 (P1)              ← 依赖 M2 授权模块
│  ├─ m3a 安全附件存储
│  ├─ m3b 公农历完善
│  ├─ m3c 统计
│  └─ m3d 搜索
└─ M4 交付 (P2)                  ← 依赖前四个里程碑出口
   ├─ m4a 移动端与可访问性
   ├─ m4b 管理员后台与审计
   └─ m4c 发布、备份恢复与全量回归
```

启动门槛：每个子任务激活前需具备自己的 design.md + implement.md（复杂子任务）；父级章程设计不可替代子任务执行设计——目前仅 m0a/m0b 已具备，**其余 14 个子任务激活前必须补齐**。父任务 implement.md 编排验证命令与回滚点。

## 三、锁定决策（用户确认，不得重议）

### 认证与账号
| # | 决策 |
|---|---|
| A1 | 登录 = **名字 + 6 位数字 PIN** 双匹配；两者均可自行修改 |
| A2 | 允许重名：显示名不做唯一约束，账号内部唯一 ID 区分；同名同 PIN 撞车时弹选择列表消歧 |
| A3 | 添加关系 ≈ 创建账号：系统随机生成 6 位 PIN，**仅展示一次**给创建者转交；被创建者凭名字+PIN 首登后可改 |
| A4 | 极简管理员：首次启动初始化生成；职责仅限初始化系统、重置忘记的 PIN、数据兜底修正 |

### 数据架构
| # | 决策 |
|---|---|
| D1 | **单一全局关系图**；个人家庭空间 = 显式成员集合的视图过滤，不是独立子树 |
| D2 | 关系模型 = 方向 + **结构四分类**（长辈/晚辈/平辈/配偶，唯一决定树形骨架）+ 自由称谓标签（仅展示检索用） |
| D3 | 称谓标签按创建者视角存储与显示；互反称谓自动换算列入 v2 agent 计划 |
| D4 | 连接确认：给新建账号直接连（创建者即代管人）；拉**已有账号**进空间需对方确认 |
| D5 | 档案归属创建时二选一：① 创建者永久可编辑 ② 本人登录后创建者退为只读；未登录档案权归创建者代管 |
| D6 | 一人可同时属于多个家庭空间、可创建多个空间（原生家/婚后小家分开建） |
| D7 | 日期 = 公/农历双支持：录入任一历自动换算另一种（lunar-python，含闰月），保留原文备注字段 |
| D8 | 删除规则双轨：删档案 = 本人/代管创建者/管理员 + 二次确认 + 级联删关系边；断关系/移出空间 = 发起方即可，不动档案 |

### UI / 产品形态
| # | 决策 |
|---|---|
| U1 | 所有视图以当前登录者为第一人称渲染（我的空间、我的长辈/晚辈） |
| U2 | 家庭空间默认首页 = 卡片画布，三种布局一键切换：画布拖拽（位置记忆）/ 树状 / 列表（按长幼排序） |
| U3 | 家庭空间 ⇄ 家族空间按钮切换，带缩放过渡动画 |
| U4 | 家族空间非实体：家庭空间相连即涌现；跨家族互不可见（暂定） |
| U5 | 可见性基线（v1.2.2 定稿）：同一 active 空间完整互见；直系结构边（elder/younger/spouse）对端完整互见（QU1=B 裁定）；其余家族可达者见必要字段（名字/称谓/世代），其余字段由归属者经披露开关选择公开与否（AD-9，默认不公开）；搜索遵循同一基线 |
| U6 | Web only；响应式适配手机浏览器，不做 App |
| U7 | v1 功能范围 = 成员管理 + 三布局画布 + 家族视图 + 搜索 + 统计 + 图片/链接附件 |

### 技术栈
| # | 决策 |
|---|---|
| T1 | 前端：Vue 3 + Vite + TypeScript + Element Plus + Pinia；画布 Vue Flow，树形布局 d3-hierarchy |
| T2 | 后端：Python FastAPI + SQLAlchemy + SQLite(WAL) + lunar-python；JWT 会话 |
| T3 | 部署：本机 Docker Compose 跑通，将来原样迁云服务器绑域名加 HTTPS |
| T4 | 规模目标几十人，SQLite 足够，不上 Neo4j/Redis/消息队列 |

## 四、审计默认假设（`[AD-n]`，详见 spec/architecture.md）

实现前可推翻，推翻必须在本表登记并同步受影响任务的 design.md：

| # | 默认假设 |
|---|---|
| AD-1 | PersonProfile/Account 实体 + ClaimState 状态字段；建档即配发待认领 Account；首登强制改 PIN 后 claimed；认领后旧初始 PIN 失效，perpetual 创建者编辑权不变 |
| AD-2 | 登录限流(5次/15分钟)、JWT 双 token + token_version、refresh_sessions 持久化(轮换+重用检测)、auth_challenges 表保证 challenge 单次使用防重放、audit_log |
| AD-3 | 首页 = 我所属空间的派生聚合视图；默认空间优先级规则；无资格时引导建「我的家庭」并走正常邀请流 |
| AD-4 | connection_request 合并关系+空间成员为一次确认；join_request 独立；完整数据授权判定公式；FSM 终态不可复活 |
| AD-5 | 删除档案 API 归属 **M1/m1a**；硬删除 + 单事务级联 + audit 快照保留；文件异步清理 |
| AD-6 | 备份走 SQLite online backup API；禁止运行期 cp 主库；恢复演练为 M4 出口条件 |
| AD-7 | 附件上传校验链(magic bytes/Pillow verify/像素上限/strip EXIF)；外链不服务端抓取；下载走授权端点 |
| AD-8 | 未成年人分级隐私 v2 待定；v1 依赖可见性基线；登出清空前端敏感缓存 |
| AD-9 | 家族空间外披露开关：五类（avatar/photos/dates/bio/attachments）存 users.clan_disclosure_json，默认全不公开；本人或代管人可改；visibility.py 在 peer/clan 可达列消费 |

## 五、验收标准（v1 总体）

1. 全新环境 `docker compose up --build` 一条命令跑通全栈，首启完成管理员初始化。
2. 完整旅程：建档（一次性 PIN）→ 家庭空间三布局正确呈现世代 → 切换家族空间看到连通大树 → 远房卡片隐私遮罩 → 申请加入对方空间获批后可见性升级。
3. 被建档者凭名字+PIN 首登强制改 PIN 后，看到以自己为中心的空间聚合视图。
4. 公农历任一录入自动互补（含闰月往返正确）。
5. 可见性基线在 **API 层**可验证：授权矩阵逐行 IDOR 测试通过。
6. 管理员能重置任意用户 PIN（旧会话即刻失效）；删除档案二次确认且单事务级联清理。
7. 手机视口核心旅程可用；备份恢复演练通过（integrity_check + 行数一致）。

各里程碑/子任务出口标准见对应 PRD。

## 六、非目标（v1 明确不做）

- Agent 相关：亲戚推荐、家族关系索引、互反称谓自动换算（v2 计划）
- 定位附件功能（仅预留枚举）；对象存储/S3/CDN
- Excel 批量导入 / GEDCOM / PDF·图片导出、打印样式
- 不同家族空间的可见性精细策略（暂定整体互不可见）
- 多端原生 App、小程序；手写农历换算算法
- 图数据库、Redis、消息队列等重型基础设施
- 用户注销/档案移交/认领撤销流程；回收站（软删除）
- 未成年人分级隐私、自助数据导出（均 v2 待定）
- 监控告警平台、性能压测调优

## 七、待定决策（不阻塞当前波次，激活对应任务前敲定）

| # | 问题 | 关联任务 |
|---|---|---|
| Q1 | 列表布局长幼排序生日缺失兜底（默认按创建时间） | m1d 设计时定 |
| Q2 | 改名频率限制/审计（默认随时可改） | m0b 设计时定 |
| Q3 | 管理员初始凭据交付（默认首启界面一次性展示） | m0b 实现时定 |
| Q4 | 头像与图片附件复用同一存储（默认复用） | m3a 设计时定 |
| Q5 | 家族视图默认展开深度（默认 2 层，可配置） | m2b 设计时定 |
| Q6 | 云部署域名/HTTPS 细节 | 迁云时定 |
| Q7 | v2 agent 推荐交互形态 | v2 再议 |
| Q8 | 删除「空间 owner」档案时的级联策略（当前 FK CASCADE 连带删空间；v2 改为先转移 owner 引导流） | 全项目复审 2026-08-25 登记 |

## 八、风险（v1.1 更新）

| 风险 | 影响 | 缓解 |
|---|---|---|
| PIN 在线爆破 | 账号被盗用 | AD-2 限流+锁定+统一文案+审计；PIN 空间 10⁶ 配合限流使爆破不可行 |
| 复杂家庭结构导致树形布局异常 | M1 核心 UX | §五确定性布局规则；失败回退画布模式 |
| 可见性过滤遗漏造成隐私泄露 | 信任崩塌 | visibility.py 单点实现 + 授权矩阵逐行 IDOR 测试（m2a 出口条件） |
| 错误数据造出关系环 | 数据完整性 | 关系写入环检测拒绝（DB partial index + 应用层校验） |
| 家族视图节点规模增长卡顿 | 体验 | 分支折叠；几十人规模不做深度优化 |
| 重名+同名同 PIN 消歧体验 | 登录摩擦 | challenge_token 两步流程（AD-2），概率 ~10⁻⁶×同名 |
| PIN 忘记无自助找回 | 用户锁死 | 管理员重置通道 + token_version 即时失效；管理员凭据首启一次性交付需妥善保管 |
| WAL 运行期复制导致备份不一致 | 数据丢失 | AD-6 online backup API + 恢复演练验收 |
| npm 依赖升级破坏构建 | 工程稳定 | package.json 锁版本，Compose 固定 Node 版本 |

## 九、修订历史

- v1.0 (2026-08-25)：三轮需求访谈后的初版规划。
- v1.1 (2026-08-25)：按《familygraph Trellis 项目设计与规划审计记录》重构——父子任务拆分、全局架构设计（AD-1~8）、规范填充、删除语义归属修正（M4→M1）、备份方案修正、认证安全合同补充；状态由"规划完成"更正为"产品规划初稿完成"。
- v1.2 (2026-08-25)：第二轮审计复审整改（有条件通过 → 整改中）：auth_challenges 落库防重放、refresh_sessions 持久化+轮换+重用检测、PIN 白名单三文档统一、新建 managed 档案直连例外澄清、Claim→ClaimState 更名、perpetual 失权措辞修正、AD-7/AD-8 标签补齐、任务口径更正（5父+16子）、QU1 待裁定项登记。
- v1.2.2 (2026-08-25)：**QU1 用户裁定 = 修订版 B + 披露开关补充**：同空间完整互见；直系结构边对端完整互见；其余家族可达者见必要字段+归属者逐类公开选择（AD-9，users.clan_disclosure_json 默认全不公开）。U5 锁定条目已按此更新，m2a/m2b 验收同步。
- v1.3 (2026-08-25)：**v1 实现完成**。M0-M4 十六个子任务全部交付并归档（后端 118 测试/mypy strict、前端四门禁、docker e2e 全绿）；全项目复审修复前端分层/async 阻塞等 5 项；M2/M3 专项重验 ALL PASS；Q8 登记在案。状态：**v1 可发布**。

</details>
