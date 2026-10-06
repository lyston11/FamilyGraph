
## 2026-09-12 研究轮

已基于当前 `family-recommendations`、`relationship_graph`、`personal_family_bridge` 和现行安全记忆完成研究，详见 `research.md`。结论为 NO-GO：当前发布继续保持跨空间发现关闭；显式 bridge 只作为双方 anchor 本人分别同意后的授权通道，不得被改造成主动 MatchBroker。未来若重启，必须先解决不可枚举响应、未成年人/跟踪风险、单次绑定 token、撤权竞态和双边 opt-in 语义。

本轮只补研究材料和准入条件，未生成真实人物候选、未调用外部 Provider、未修改产品代码；任务继续保持 planning/deferred_design_review。


## 2026-10-06 验收与归档

**交付物已按 AC-1..AC-4 逐项核对**：

| AC | 状态 | 证据 |
|---|---|---|
| AC-1 | 满足 | `research.md` 输入/输出/权限差异表（当前 within-space vs 未来 cross-space） |
| AC-2 | 满足 | `research.md` 最小状态机（`OFF → opted_in → candidate_issued → redeemed → expired/withdrawn/blocked`）+ 双边 opt-in/withdraw/expiry/token replay 语义 |
| AC-3 | 满足 | `research.md` 威胁模型 **10 条**（≥8），含枚举、侧信道、家暴/未成年人、token 重放、撤权竞态、provider/日志泄露、恶意批量探测 |
| AC-4 | 满足 | 决策材料：**NO-GO / 保持 deferred**；准入门槛 6 条已列 |

**决策结论：NO-GO**。现有 `PersonalFamilyView` bridge 是双方 anchor 本人分别同意后的
显式授权通道，**不等同于**跨空间陌生人发现；不得改造成主动 MatchBroker，不得让
recommendation 查询全局空间。

**为什么这是完成而非未做**：AC-4 明确要求「拿不出避免越权泄漏的设计和真实用户价值时
结论应为不实施」。本轮研究给出的正是这个结论及其门槛，因此任务目标已达成。

**保护信号**：`#298`（bridge 需双方分别同意）、`#307`（任一未认领则无访问效果）、
`#325`（搜索不得成为隐藏内容发现路径）已在项目记忆中，不依赖本任务 active。
