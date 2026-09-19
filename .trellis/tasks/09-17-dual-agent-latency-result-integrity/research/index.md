# 双 Agent 延迟研究路由

当前基线为审核时 `main@d8d3668`。研究只记录事实与方案，不授予新实现、生产变更或真实模型调用许可。

**2026-09-19 收口**：AC-01～09 已全部闭合，总验收报告与子任务证据见
[总验收报告](final-acceptance-2026-09-19.md)（含用户慢响应的三层归因、未闭合项清单、
发布与真实小样本证据入口）。

- [总验收报告](final-acceptance-2026-09-19.md)：**最新默认入口**；AC-01～09 逐项判定。
- [最新复核结论](review-summary.md)：Q01～Q13、证据边界、职责与安全约束。
- [新一轮任务图](remediation-task-map.md)：C～I的工件链接、执行顺序、父AC映射和待决策项。
- [复核证据](evidence/review-audit-2026-09-17.md)：5个红测、正式定向回归与只读远端时点快照，按需读取。
- [集成验收](integration-acceptance.md)：顶部是最新AC状态，下方保留原集成时记录。
- [初始研究与集成期summary](latency-summary.md)：历史来源；过度归因已标纠正，不作为当前默认注入。
- [初始源码与只读基线](evidence/latency-baseline-2026-09-17.md)：初始问题取证。
- [助手阶段历史样本](evidence/assistant-phase-decomposition-2026-09-17.md)：n=2旧run与持久事件间隔，不能替代源计时和新版本效果。
