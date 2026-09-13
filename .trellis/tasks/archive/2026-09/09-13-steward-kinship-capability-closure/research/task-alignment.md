# Trellis 职责与依赖对齐

日期：2026-09-13。本表是本期后继职责说明，不改变其他任务的完成状态、已有提交或实验结论。

## 基线在规划期间前进

初始审计基线 4649e48；其他会话随后把主分支推进到 ad12eb6，其中 f08089c 已归档 topology，ea6f74f 使拓扑与推测层共存。原称谓/建议/assist/通知关键源文件在该区间未变化，PFV 增加结构拓扑输出，故原缺陷证据仍适用。执行前再取最新基线，不重复实现已完成成果。

## 唯一职责表

| 任务 / 实际状态 | 原冲突或交叉 | 本轮处置 |
| --- | --- | --- |
| 09-11-steward-capability-followups / planning | 整体 deferred 把个人称谓模型能力一并延期 | 已改 prd/design/implement/notes：称谓显示、生产、模型与专用反馈归新总任务；shared RAG、额外路径解释、地区包和性能研究留原任务 |
| 09-13-agent-memory-capability-plan / in_progress，研究已交付 | E-O11 和“所有新增能力延期”会覆盖用户刚选用的称谓闭环 | 已改 prd/design/implement 和决定登记的职责文字；保留 MR-23/26 实证、18 个预算断言与研究状态；一般 card 排序收益仍由 E 评估 |
| 09-13-agent-memory-rag-remediation / 执行中 | 父 PRD 将 E 可选能力统一延期，旧 out-of-scope 禁止任何既有任务文档调整 | 已加后续明确授权的称谓例外及配置/migration 串行边界；A～D 的原修复范围不变 |
| 09-13-steward-memory-evidence-projections / planning | 新建 MR-23/26 后续包涉及 suggestion evidence、notify 副作用与共用模型文件 | 已改 prd/design/implement：一般原子候选证据版本和键族修复归该包；称谓链不依赖它；不得把已有派生称谓再变成关系确认待办 |
| 09-13-family-tree-relationship-topology / 已归档，f08089c | 结构边与个人摘要容易被混淆；共享 PFV/schema/types/FamilyTreeView | 消费已集成 topology_edges；不再作为等待中的任务，不重做布局。个人节点/摘要称谓与直接结构端点分开 |
| 09-13-steward-inferred-tree-layer / 已归档，8ba7255 | 原禁止模型输出称谓，且当时不做 suggestion/inferred 去重 | 作为历史边界：candidate 仍限原子关系；新 terminology 是后继扩展。A 负责共用显示和来源状态，不重新开启历史任务 |
| 09-13-steward-assist-platform-switch-admin / 已归档，4649e48 | 三类开关不能覆盖第四类 | B 沿现有 DB∧env/space/Provider 治理增量扩展；不再另做一套平台治理。主检出的同名未跟踪目录不是新待办 |
| 09-13-steward-term-autofix / 已归档 | 早期 PRD 写自动 space 词条，最终代码未如此实施 | 已在历史 PRD 顶部补范围说明并指向最终 design/本后继任务；不更改旧验收结果或历史 |
| 09-11-steward-cross-space-discovery / 延期 | 个性化称谓可能被误用为跨空间读取授权 | 本期不启用跨空间发现，不扩大模型数据 scope；现有本人个人词条的跨空间显示规则不等于读取其他空间路径 |
| memory-contract-repair、rag-retrieval-citations、assistant-context-compaction、rag-index-lifecycle | 大部分与称谓无直接冲突；D 共用 platform_features/maintenance/迁移 | 不修改其业务范围；B 的配置/migration 与相关工作串行，旧客户端遗漏字段保留现值 |

## 新父子任务依赖

```text
当前已集成推测层 + topology + 平台治理
  → A：viewer 呈现/derived API/通知有效状态/旧详情
  → B：个人投影/term_preference 生产/terminology/反馈/治理
  → 总任务跨层验收、串行集成、归档、清理
```

A/B 都改 Terms/PFV/Suggestions/类型，禁止并行写。MR-23/26 包如先实施，双方读已集成合同；如后实施，必须保留 A/B 的 viewer、非待办、个人恢复反馈及相关证据显示规则。仅只读研究可并行。

## 共享文件处理规则

- 主检出是任务文档与生命周期入口；业务代码只在 task.py start 记录的 worktree。
- 本轮文档修改仅限职责、依赖、历史范围说明，不修改其他任务 task.json 状态、实验结果、harness 或代码。
- 外部会话仍可能修改同一主检出。集成前逐文件复核 diff，选择性提交本任务文件；不要 git add -A、reset、rebase 或覆盖他人工作。
- merge 后 archive 并立即按 AGENTS 清理已合并干净的 worktree/分支；若任务尚未启动，不伪称已有代码或清理结果。
