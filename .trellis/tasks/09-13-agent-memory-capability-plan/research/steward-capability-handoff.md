# Research: Steward 能力与修复所有权交接

- Query: MR-23/MR-26 修复、shared RAG 和平台开关如何保持单一实施责任、不重复建设。
- Scope: internal；现有任务 PRD 与本轮研究结果。
- Date: 2026-09-13

## Findings

E 本轮只交付研究探针、证据和决定记录，生产代码/迁移/配置均未实施。修复前 JSON 不能作为发布通过凭据。A/B/C/D 的实际合同由各自实施与集成结果决定；此处引用的是审定设计，交接时必须对照最终代码。

### MR-23 / MR-26

主线程已创建有界 P2 规划包 .trellis/tasks/09-13-steward-memory-evidence-projections，作为两项生产补丁唯一所有者；当前仅规划、未实施。本研究不改该任务文档/状态，不和既有能力任务争抢生产文件。两项修复均会涉及 steward 相关链，建议串行实现：

| 顺序 | 范围 | 独立验收 | 禁止顺带 |
|---|---|---|---|
| 1 | steward.py 的 rebuild owned-key 删除范围 + 相关回归 | [MR-26](behavior-projection-rebuild-results.md) 的 4 组合与无事件反例 | maintenance 接线、事件水位、迁移、TermRegistry 替代 |
| 2 | candidate 相关证据合同、版本模型/唯一性、投影与回归 | [MR-23](steward-candidate-evidence-results.md) 的真实多 job 链及来源/并发反例 | 以全空间 hash 作为版本、自动事实确认、未经选择的新提醒 UX |

MR-23 schema 方案需要先确定可验证的支撑事实归因；不能只改 digest。现有 upsert 自动产生 Notification，所以“新证据版本”和“重新提示”必须分别评审。若归因/UX 没有收敛，先保留原行为并排入具体待决项，不能记已修复。

### 既有 Steward 能力任务

实际 shared RAG / 个人路径解释 / 地区称谓实现所有者保持为 [09-11-steward-capability-followups](../../09-11-steward-capability-followups/prd.md)。已只读核对该 PRD：R1 shared-only、R2 viewer 隔离、R3 TermRegistry/locale、R4 有观测才性能重构。E-O10 只交来源合同与评估协议，不创建第二个 RAG 实现分支。

供原任务采用的 A/B/D 交接合同：

- Steward 使用自己的 job/space/consumer 身份，不伪造 Assistant AgentRun，不读取私人聊天/private memory。
- 输入只可包含当前有效、已确认且来源授权允许该空间 shared 消费的内容；来源撤销/过期/版本失效实时重验。把检索内容标为不可信数据。
- 辅助结果仍过 schema、证据 ID、授权与写回栅栏；不得覆盖确定性 DerivedFact 或直接确认关系。
- 引用绑定真实 included 的 ContextBuild/文档片段版本；不能把任意 citation 字符串当授权，不能把所有检索命中都归为“实际采用”。
- 本地 Provider 限制、预算和 lease/取消在检索到模型写回全程保持；没有合法资料就回退当前结构输入。
- 采用前重读 A/B/D 最终 service schema、撤权行为与引用合同；本交接不硬编码尚在实施的函数签名。

六类冻结场景供原任务继续运行：有效 confirmed shared 家事；相互冲突的 shared 摘要；过期/已撤销来源；只有 private 资料；来源要求 local provider；完全没有资料。每类都先验证共享/来源权限反例，再比较结构基线与新增检索的质量、来源有效率与成本。本轮没有运行这些模型比较。

### 既有平台辅助开关任务

唯一所有者保持为 .trellis/tasks/09-13-steward-assist-platform-switch-admin。其 PRD 已按主线程授权在**主检出**只读核对；该未提交任务不在 E worktree，因此不复制、不更新、不重建其状态。

PRD 的过程记录同时包含“平台 env 未配置时零调用”和“环境处置后 candidate/explanation 成功”两段。E 的合成开关矩阵不能推出今天线上为零，也不能证明线上当前开关已开。

admin API、管理界面、审计、家庭端有效原因提示、环境与 DB 的平台优先级讨论均归该任务。E 不改这些文件/迁移，不依据记忆/RAG 的 deployment AND DB 合同擅自重写 Steward 平台治理设计。实际上线前由该所有者验证最终有效开关模型；E 本次 fake runner 记录的是 checkout 当下的 config AND space 行行为。

### 后续执行者应领取的证据

- [探针与事前协议](steward-reproduction-protocol.md)
- [全部脱敏观测](steward-capability-results.json)
- [MR-23 逐阶段结果与最小方案](steward-candidate-evidence-results.md)
- [MR-26 键族结果与最小方案](behavior-projection-rebuild-results.md)
- [E-O1～12 决策登记](capability-decision-register.md)

## External references / Related specs

仅使用本地真实任务 PRD、源文件及父任务研究。历史 .trellis/spec/backend/steward-action-card.md 仅帮助追溯旧合同；当前所有权以现行任务文档和 AGENTS 为准。

## Caveats / Not Found

- 没有对既有两个任务作状态变更、文件复制或 Git 操作。
- P2 修复包已由主线程创建、尚未实施；最终排期和取舍归主线程。
- shared RAG 六类场景和真实成本/质量比较均仍待原任务执行。
