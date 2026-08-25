# M4 交付（里程碑章程）

> 权威上下文：[HANDOFF.md](../../HANDOFF.md)、[architecture.md](../../spec/architecture.md)。父任务只做编排与门禁。

## 里程碑目标（出口）

**本机一条命令跑起来 + HANDOFF §五 v1 总体验收全过**：移动端可用、管理员后台、备份恢复演练、全量回归。

## 子任务与依赖

| 子任务 | 内容 | 依赖 |
|---|---|---|
| [m4a 移动端与可访问性](../08-25-m4a-mobile-a11y/prd.md) | 响应式/手势/动画/a11y 清单 | M1-M3 |
| [m4b 管理员后台与审计](../08-25-m4b-admin-console-audit/prd.md) | 三职责+审计页 | m0b |
| [m4c 发布、备份恢复与全量回归](../08-25-m4c-release-backup-regression/prd.md) | backup API/演练/回归清单/README | m4a、m4b |

## 审计边界修正记录

- 原 M4 的"删除档案级联"归属修正为 **m1a**（v1.0 声称已在 M1 实现但 M1 未定义——现已在 m1a 落实）。
- 备份从"拷贝文件"修正为 online backup API + 恢复演练 `[AD-6]`。
- Compose 一键启动在 m0a 已交付，此处只做生产化加固（消除 v1.0 的 M0/M4 重复出口）。

## 出口门禁

- [ ] 三个子任务验收全绿
- [ ] HANDOFF §五 七条总体验收逐条复核通过
