# 规划收敛与核验记录

日期：2026-09-13。阶段：planning。此记录证明规划产物完整，不代表业务修复或模型质量已经通过。

## 产物与需求收敛

- 总任务及 A/B 均有完整 prd.md、design.md、implement.md 和真实 implement/check manifests；无 TBD 模板。
- 四项能力分别映射父 R-01～08、AC-01～15 与 A/B 的需求/验收；父子依赖是显式 A → B。
- PRD 已按最终范围重写并从头核对；临时假设、重复问题列表已收敛为需求与审计出处，没有阻塞的业务信息缺口。
- 自动称谓无需逐条审批，显式词条优先，模型不会新增关系事实；这些决定及不纳入本期的独立风险均已写明。
- 新任务 task.json 均为 planning，branch/worktree_path 均为 null；本轮未执行 task.py start、业务修改、提交、部署或真实模型调用。

## Trellis 校验

执行 `python3 .trellis/scripts/task.py validate .trellis/tasks/<name>`：

| 任务 | implement/check 条目 | 最终结果 |
| --- | --- | --- |
| 09-13-steward-kinship-capability-closure | 3 / 3 | 通过 |
| 09-13-steward-kinship-presentation | 4 / 4 | 通过 |
| 09-13-steward-terminology-autonomy | 4 / 4 | 通过 |
| 09-11-steward-capability-followups | 5 / 6 | 通过；首次发现历史父任务已归档但 4 处 manifest 引用未移动，修正到 archive 后重验通过 |
| 09-13-agent-memory-capability-plan | 5 / 6 | 通过 |
| 09-13-steward-memory-evidence-projections | 5 / 5 | 通过 |

自定义只读检查覆盖本期新增/修订的 26 份 Markdown（不含本记录）：本地链接均存在，0 broken links；顺带修正 followups 的两个历史父任务链接。所有新 manifest 均含实际 research 文件，非占位引用。`git diff --check -- .trellis/tasks` 退出 0。

## 独立规划评审

只读子代理审阅 9 份核心规划，提出 6 个可执行性问题；主线程沿相关代码抽查并修订，复核结论为原问题全部闭合、没有遗留实施阻塞：

| 问题 | 最终合同 |
| --- | --- |
| 拒绝身份随版本漂移 | suppression_key 与证据/request 摘要分离；同语义换路径/词条 revision 不绕过恢复 |
| 单行投影多来源覆盖 | 状态表：core 保留有效产物，非法/空输出仅更新检查结果；restore 双 revision+semantic CAS+幂等 |
| unknown 跨新 job 重发 | 持久调用历史为真源；unknown 同语义跨 job/重启禁重发；未发送尝试最多两次 |
| 全局 job.id 导致空间饥饿 | 每空间持久 assist_kind_cursor；四空间交错且单次调用预算的验收 |
| 恢复与过期 lease 冲突 | 普通执行租约与恢复 CAS 接管分开；只应用持久成功产物，不在恢复中发送网络 |
| 个人忽略与全局推测驳回混淆 | 双用户/双入口矩阵；个人忽略不改树，全局状态共享，提案按同空间规范三元组复用 |

非阻塞文字建议也已同步：模型失败保留已有有效结果，没有有效自动结果时才用确定性基线，避免实现者清掉仍有效的称谓。

## 本轮限制与下一步

F03 的 schema literal_error 已隔离复现；旧 39 项测试仅作基线。本轮只改任务/交接文件，不运行与这些文档改动无关的全项目业务测试，不声称截图或代码问题已修复。

当前 workflow-state 与 trellis-brainstorm 要求最终摘要后的评审回复才能 task.py start。因此下一步是提交最终规划摘要供用户评审，然后依次启动 A/B，在隔离 worktree 完成实施和业务验收。尚未建立任务 worktree，不存在已合并分支待清理；实施收尾必须按 AGENTS 清理。
