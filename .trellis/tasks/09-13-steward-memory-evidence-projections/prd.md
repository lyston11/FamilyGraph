# PRD：Steward 证据版本与行为投影键族修复

## Goal

消除行为投影重建误删其他冷却数据的风险，并让同结构推测能够记录可验证的新相关证据，同时保留用户已经表达的驳回意愿。

研究来源：[能力评估 E](../archive/2026-09/09-13-agent-memory-capability-plan/prd.md)。2026-09-14 E 研究归档后，本包解除活动父子关系，是 MR-23 / MR-26 的唯一后续业务所有者。用户在查看本任务后明确回复“执行”；现已通过 `task.py start` 进入独立分支/worktree，按 MR-26 → MR-23 串行实施。E 研究已产生真实服务链的隔离复现；本次执行不新增关系提醒或授权待办。

## Evidence

- [MR-26](../archive/2026-09/09-13-agent-memory-capability-plan/research/behavior-projection-rebuild-results.md)：行为投影开启后，账户级/空间级重建均删除真实推荐服务创建的非所属冷却键；关闭时保留。
- [MR-23](../archive/2026-09/09-13-agent-memory-capability-plan/research/steward-candidate-evidence-results.md)：相关支撑事实已进入假模型调用，但同结构候选和 Suggestion 仍保留旧证据；无关/无新增事实对照保持原状态。
- 上述是合成迁移库、实际生产服务和 fake transport 的证据，不证明线上发生频率或真实模型质量。

## Requirements

- SP-R1：重建只能更新其负责的投影类别；推荐冷却及未知类别须完整保留，无事件重放也不能清空它们。
- SP-R2：同一事件集重复重建结果一致；账户级重建不得影响其他账户；行为功能关闭时保持现有不执行语义。
- SP-R3：同结构推测的相关证据变化可形成可追溯版本；结构相同且证据相同不重复，无关事实变化不产生新版本。
- SP-R4：只有可验证、当前获权且有效的支撑事实可构成证据；原始模型断言不能直接当作关联证明或确认关系。
- SP-R5：保留旧版本及收件人的驳回历史；证据记录与重新展示/通知分别处理。首版已冻结为仅记录和核验内部版本，不因换版解除驳回，不新建 Suggestion、recipient、Notification 或推测边；已有建议的证据与确认历史不改写。
- SP-R6：后续 core 才投影的当前异步行为继续保持；本包不借机引入模型重复调用、维护调度或能力开关。

## Acceptance Criteria

| ID | 结果 |
|---|---|
| SP-AC1 | MR-26 原四组合以及无事件、未知键族、其他账户反例均保留非所属数据；所属数据两次回放相同 |
| SP-AC2 | MR-23 真实多作业链中相关证据形成新版本；无新/无关证据与已应用批次重试不重复 |
| SP-AC3 | 无权、已撤销、revision 不符的事实或过期租约的写回不可采用；不支持归因的类型明确降级 |
| SP-AC4 | 并发执行不重复写入或覆盖旧版本；来源与确认历史可追溯，迁移不破坏性去重 |
| SP-AC5 | 同证据驳回继续有效，未批准的再次提醒不发生，通知无重复；可见策略有明确记录 |
| SP-AC6 | 真实迁移库、相关服务链和静态检查通过；记录版本、命令、结果与尚未测试的限制 |

## Boundaries

不实现 Steward shared RAG、TermRegistry 替代、行为水位系统、自动关系确认或平台 admin 开关。共享知识能力仍归 [既有 followups](../09-11-steward-capability-followups/prd.md)；已集成的平台开关和称谓闭环沿用现有合同。

本任务是用户另行选用的 E 后续修复，不回写 A～D 的交付结论。与渐进重算任务相交的 Steward 文件和迁移串行处理；采用其先集成的接口与迁移 head，不解决或覆盖其他 worktree 的在途冲突。

首版证书只覆盖 `direct_sibling` 的共同 `biological_parent(P,A)` / `biological_parent(P,B)` 事实对。继亲、收养、监护不混入该证书；未覆盖候选标记为未归因，保留原结构去重。证书是指定时点的历史核验结果，不是新的已确认亲属事实，不扩大任何查看者权限。

## 与称谓闭环的职责交接

用户另行选用的 [称谓闭环](../archive/2026-09/09-13-steward-kinship-capability-closure/prd.md) 负责通知个人表达、term_preference 生产、terminology 模型及专用反馈。本任务保留 MR-23 一般原子候选证据版本和 MR-26 键族修复；不将自动称谓重新设计成关系确认任务。共享 steward_suggestions/assist/models/通知文件及 migration 串行实施，使用对方先集成的合同。

共同父母等可验证路径若已足以提供派生称谓，本任务可以记录证据版本，但不能据此新增必须确认称谓的通知；内部证据换版不等于需要新增 SourceFact。称谓恢复反馈属于个人显示域，不被本任务的通用冷却或全量重建清除。
