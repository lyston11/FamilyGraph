# 双 Agent 最新子任务图与问题覆盖

状态：用户授权创建完整规划；A/B/C/D 已归档并集成到 main；E 的工程部分已集成到 main（e0ee321），E 的预算数值选择仍未批准；**H 已归档**（方案任务，约束由 09-18 承接）；**09-18 助手低延迟已完成并归档**（P0-1/P0-2/P1-1 已部署、LL-AC4 真实浏览器验收通过；P2-1 实测否决、P2-2 留档）；F/G/I 仍为 planning。父任务保持 in_progress；不改归档任务历史。所有任务主会话内联、串行处理，不使用子智能体。

## 工件入口

| 编号/优先级 | 子任务 | PRD | 设计 | 实施计划 | 交付类型 |
| --- | --- | --- | --- | --- | --- |
| C / P1 | 管家可中断总截止与结算收尾预算 | [PRD](../../09-17-steward-hard-deadline/prd.md) | [design](../../09-17-steward-hard-deadline/design.md) | [implement](../../09-17-steward-hard-deadline/implement.md) | 工程修复，待批准执行 |
| D / P1 | 助手真实阶段计时与重试统计修复 | [PRD](../../09-17-assistant-timing-observability/prd.md) | [design](../../09-17-assistant-timing-observability/design.md) | [implement](../../09-17-assistant-timing-observability/implement.md) | 工程修复，待批准执行 |
| E / P1 | 助手网关错误分类与分层重试治理 | [PRD](../../09-17-assistant-retry-governance/prd.md) | [design](../../09-17-assistant-retry-governance/design.md) | [implement](../../09-17-assistant-retry-governance/implement.md) | 工程修复已集成 main；策略选择待批准 |
| F / P1 | 双Agent受控场景矩阵与浏览器链路验收 | [PRD](../../09-17-dual-agent-controlled-acceptance/prd.md) | [design](../../09-17-dual-agent-controlled-acceptance/design.md) | [implement](../../09-17-dual-agent-controlled-acceptance/implement.md) | 零模型费用的真实隔离栈验收 |
| G / P1 | 运行版本核对、部署与真实小样本验收 | [PRD](../../09-17-dual-agent-release-validation/prd.md) | [design](../../09-17-dual-agent-release-validation/design.md) | [implement](../../09-17-dual-agent-release-validation/implement.md) | 发布/真实调用另有明确执行门 |
| ~~H~~ / P2 | ~~助手增量正文显示协议与交互方案~~ | 已归档 | 已归档 | 已归档 | 方案任务未实施；约束由 [09-18 design](../../archive/2026-09/09-18-assistant-low-latency/design.md) 承接（已完成） |
| 09-18 / P2 | 助手低延迟响应与首段交付优化 | [PRD](../../archive/2026-09/09-18-assistant-low-latency/prd.md) | [design](../../archive/2026-09/09-18-assistant-low-latency/design.md) | [implement](../../archive/2026-09/09-18-assistant-low-latency/implement.md) | **已完成并归档**；证据见[验收](../../archive/2026-09/09-18-assistant-low-latency/research/acceptance-2026-09-18.md) |
| I / P2 | 模型档位、排队与性能取舍评估 | [PRD](../../09-17-agent-performance-options/prd.md) | [design](../../09-17-agent-performance-options/design.md) | [implement](../../09-17-agent-performance-options/implement.md) | 评估决策，默认维持配置 |

每项都包含requirements、可观察AC、依赖/非目标、兼容/回退、验证入口及完成证据；implement/check.jsonl仅引用精确Spec叶和review-summary。清单虽为未来工具注入保留，本轮与后续执行仍按用户要求由主会话完成。

## 顺序与接口责任

```text
原B/A（已归档）
  → C总截止 → D源计时/请求关联 → E审计/错误分类/重试
  → F受控矩阵/真实浏览器 → G运行版本/发布/真实样本
                    D/F/G证据 → 09-18（含原H增量合同）与I参数取舍
```

09-18 与 I 可以先研究/实施，不阻塞C～G现配置修复；但 P0-2 增量落地后需按 F 式验收补一轮。D负责字段和聚合，E负责网关审计/SDK策略，F只补覆盖并把发现退回所属实现；不在多个任务各建一套观测机制。共享backend/agent/SQLite/迁移/端口的一律串行，未来代码在各自start钩子建的worktree中修改。

## 审核问题覆盖

| 问题 | 主责 | 复验/父AC |
| --- | --- | --- |
| Q01阻塞I/O超过总截止、Q02无结算预留 | C | F；AC02/03/04/05 |
| Q03入库时间误当执行时间 | D | F/G；AC01/06 |
| Q04单失败/耗尽/零事件分母 | D | E补审计、F复验；AC01/06 |
| Q05纯工具轮/压缩/首正文/浏览器盲区 | D、F | G；AC01/06/08 |
| Q06永久错误伪502与HTTPError无审计 | E | D消费、F验证；AC01/05/06 |
| Q07SDK两层retry预算不清 | E | F真实SDK假上游、I评估；AC05/06 |
| Q08矩阵/代理/SSE/浏览器/时钟精度 | F | G环境复验；AC01/08 |
| Q09代码同步未加载 | G | AC07/08/09 |
| Q10真实样本不足/不同版本不可比 | G | AC01/06/07 |
| Q11增量显示方案及安全合同 | 09-18（原H已归档） | **已实现并完成真实浏览器验收**（LL-AC4 通过） |
| Q12medium/换模型/并发/轮询/预算取舍 | I | E/09-18分离；轮询基础值已由 09-18-P0-1 定；证据与决策支持AC06 |
| Q13错误完成声明/文档/最终清理 | 父+C/D/E/F/G | AC09；不篡改原归档 |

## 仍待决定，不伪装已批准

- E：工程性分类/审计修复已集成 main（e0ee321，含首响应期限与瞬态退避上限）；改变总retry次数/等待上限仍必须有实际请求预算和可用性取舍，不在本轮选数值。实测最坏请求数/墙钟与候选策略表见 [E 证据](../../09-17-assistant-retry-governance/evidence.md)。
- G：发布窗口、回退版本与真实调用预算在执行前确认。PRD提出6逻辑/12物理请求、token/时间上限只是审阅提案，金额仍待当前计价核对，不等于调用授权。
- 09-18：P0-1 轮询已部署；P0-2 增量的采用与否已由本任务决定（承接原 H），输出安全门槛已核实为「无输出侧扫描」，实施时必须显式处理，不能以「已有 guard」放行。
- I：是否降档/换模型/扩容/真实A/B待可靠证据；维持当前配置是合法结论，不以任务创建代替选择。

## 父任务完成条件

C～G必需修复与验证闭合；09-18 的体验目标有同配置前后证据；I 给出明确有证据的采用/延期/不采用结论，不能把方案完成写成能力上线。父AC06不强迫降档/换模型，但未解决的归因/程序缺陷不能由产品延期遮盖。AC04/08/09已重新打开，最终报告逐项附证据；符合合并/干净前提后清理父/子worktree与分支。
