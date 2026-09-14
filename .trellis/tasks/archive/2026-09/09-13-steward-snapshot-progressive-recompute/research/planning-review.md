# 规划复核记录

日期：2026-09-14。仅文档与静态代码核验；尚未实施、迁移、测量性能或操作生产。

## 已收敛决定

- 用户明确选择先显示授权确认骨架，后逐批补齐个人称谓。
- 正式需求、设计和步骤见 [prd.md](../prd.md)、[design.md](../design.md)、[implement.md](../implement.md)；proposal.md 只保留历史推导。
- 一个一致性任务串行集成后端/协议/前端。产品问题已收敛，等待用户批准最终规划摘要，不以 UX 选择代替实施评审。

## 独立核验与修订

前轮分别核验前端、快照/权限和 generation 方案，证据在 [current-state.md](current-state.md)。定稿后再次只读复核 PRD/design，发现并修复：

| Finding | Resolution |
| --- | --- |
| 统一有效 fence 会阻止对失效输入/租约的回收 | design §5.2 按 claim、正常写入、supersede/reaper/cancel 区分 CAS 前提，防旧 attempt 覆盖新执行者 |
| 必需目标失败后发布/水位含糊，周期扫描可能重置尝试预算 | 必需目标耗尽整代 failed、不发布/不推进水位；预算按输入/目标/阶段跨代持久，人工重试一次有界机会 |
| 对外部 HTTP 许诺不重复调用缺少发送后崩溃窗口 | 复用 ModelCall unknown 保守计费且不自动重发，通用 intent 仅幂等登记；不承诺外部 exactly-once |

独立复核确认三项闭合，无残留阻塞。主线程抽查文档原文及 steward_assist.py 的 unknown 恢复规则，更新 PRD 的 AC4/AC7 和实施验证矩阵。

## 已完成的规划检查

- PRD 按最终结构完整改写并从头复读：8 个需求、9 项验收，无未决产品选择、TBD/TODO 占位或临时 brainstorm 清单。
- prd.md、design.md、implement.md 存在，Markdown 本地链接有效，无行尾空格。
- implement.jsonl / check.jsonl 为真实规范/研究入口，task.py validate 通过。
- task.json 仍为 planning，未创建实施分支/worktree，现有其他任务和业务 dirty 文件未纳入本任务。

这些检查只证明规划文件完整且可加载，不证明产品功能、授权并发、迁移或性能已经通过。下一步须由用户评审最终摘要后进入实施。
