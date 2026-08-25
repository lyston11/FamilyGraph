# m4c 发布、备份恢复与全量回归

> 父任务：[08-25-m4-polish-admin-deploy](../08-25-m4-polish-admin-deploy/prd.md)｜依赖：m4a/m4b 及 M0-M3 全部出口｜备份契约：architecture.md §8 `[AD-6]`

## Goal

v1 发布收口：备份恢复验证 + 全量回归 + 发布文档。对应 HANDOFF §五总体验收第 1、7 条。

## Requirements

- `python -m app.backup`：SQLite online backup API 快照 → 与 uploads 一同 tar 至 /data/backups；README 明确**禁止运行期 cp 主库**。
- 恢复演练脚本：restore → PRAGMA integrity_check → 用户/关系/空间行数对比源库。
- Compose 生产化检查：数据卷持久性、镜像版本固定、重启策略；迁云步骤清单写入 README（域名/DNS/HTTPS 二选一方案文档）。
- 全量回归：M0-M4 所有子任务验收标准复跑清单（本任务目录维护 checklist.md 并勾选存档）。

## Acceptance Criteria

- [ ] 全新环境一条命令启动 + 首启初始化通过（HANDOFF §五.1）。
- [ ] 备份→清空→恢复演练：integrity_check ok 且三类行数一致。
- [ ] 回归 checklist 全绿并随任务归档。
- [ ] README 使不懂容器的人能完成"备份=执行一条命令"。

## Non-goals

- HTTPS 自动化运维；监控告警；CI/CD 流水线搭建（本地脚本即可）。
