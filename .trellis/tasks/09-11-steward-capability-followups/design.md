# Design — Steward 后续能力：授权知识、个人路径解释与地区称谓

> 本文是待实施技术方案，不是当前代码行为；现状证据见父任务 findings。

## 延期状态与设计草案

这是 deferred research 任务，当前交付是需求、评估方案和决策记录，不表示共享 RAG、地区包或新模型功能已批准上线。

候选路径：调用现有 memory_rag.search_rag 的 Steward consumer，仅 current space 的 confirmed shared 文档；先授权过滤再检索，文档 tombstone/撤回使相关解释 evidence 失效。结构事实仍通过 SourceFact/relationship resolver 查询。
个人解释使用只读 PFV evidence snapshot + viewer 显示投影，输出仅 supporting_fact_ids/解释模板槽位，应用前按 projection-consistency 的版本 fence 重验。
地区包仅扩 TermRegistry 内容及测试，不新增第二套关系算法；个人/空间纠正优先级继续胜出。

## 需要之后收敛的产品决策

- 哪些共享资料能带来可测增益、由谁管理授权；没有资料样本前不启用检索。
- 个人解释展示位置及首批地区/方言；未指定前不虚构地区覆盖需求。
- 若容量超限，部署环境是否接受独立进程；不先引入 Redis/图数据库。

以上决策不阻塞本轮六个修复任务，也不伪装为“无阻塞问题、可直接实施”。
