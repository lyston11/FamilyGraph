# Design — Steward 后续能力：授权知识、个人路径解释与地区称谓

> 本文是待实施技术方案，不是当前代码行为；现状证据见父任务 findings。

## 延期状态与设计草案

这是 deferred research 任务，当前交付是需求、评估方案和决策记录，不表示本任务剩余的共享 RAG、新地区包或个人解释能力已批准上线。2026-09-13 新授权的称谓生产/模型优化已由 [称谓能力闭环](../09-13-steward-kinship-capability-closure/design.md) 接管；其专用 terminology 输出不受这里的整体延期表述限制。

候选路径：调用现有 memory_rag.search_rag 的 Steward consumer，仅 current space 的 confirmed shared 文档；先授权过滤再检索，文档 tombstone/撤回使相关解释 evidence 失效。结构事实仍通过 SourceFact/relationship resolver 查询。
个人解释使用只读 PFV evidence snapshot + viewer 显示投影，输出仅 supporting_fact_ids/解释模板槽位，应用前按 projection-consistency 的版本 fence 重验。
地区包仅扩 TermRegistry 内容及测试，不新增第二套关系算法；个人/空间纠正优先级继续胜出。

后继称谓任务复用上述 viewer、路径、词条优先级和批次边界，但自动结果是可撤销个人显示投影，不是自动晋升 space 词条。shared RAG 仍由本任务 R1 负责。实施共享 Terms/PFV/assist 文件时以已经集成的称谓合同为基础，串行交接，不恢复旧的“所有模型不得输出称谓”限制。

## 需要之后收敛的产品决策

- 哪些共享资料能带来可测增益、由谁管理授权；没有资料样本前不启用检索。
- 个人解释展示位置及首批地区/方言；未指定前不虚构地区覆盖需求。
- 若容量超限，部署环境是否接受独立进程；不先引入 Redis/图数据库。

以上决策不阻塞本轮六个修复任务，也不伪装为“无阻塞问题、可直接实施”。
