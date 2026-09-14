# 活动旧副本与归档状态修复

2026-09-14，用户要求核对任务完成情况，随后授权“修复并归档”。本任务已于2026-09-13完成；本次清理导致Trellis误报in_progress的重复活动副本，沿用现有正式归档。

## 核对依据

- 实施提交 `9017a6dfc3896d1d10980406e33727c92f223984`、原归档提交 `e0e8d8d8b63cd863ad6a15a0072c37026baaa52e` 均已在main和origin/main。
- 活动目录的5个文件中，PRD、两个上下文清单和研究材料与归档逐字一致；唯一差异是task.json的status、branch、completedAt三个旧字段，没有新增工作。
- [正式任务记录](../task.json)原本已是completed，completedAt为2026-09-13。[PRD](../prd.md)的5项验收标准均已勾选；原提交记录lint、93项Vitest及build通过，当前配置页面和相关测试与该实施提交无差异。

## 已执行修复

1. 将整个未跟踪活动旧副本移至仓库外私有备份，并校验5个文件的SHA-256；正式归档和原完成日期保留。
2. 备份并通过Trellis的 `clear_task_from_sessions` 归档辅助函数清理1个过期会话指针；其last_seen_at为2026-09-12，current_run为null。
3. 将归档implement.jsonl和check.jsonl中的研究材料路径改为归档位置，避免移走旧副本后上下文注入失效。
4. 在归档元数据中补记原实施、归档提交与本次修复入口，并更新HANDOFF的实际状态。

已有 `task.py archive` 会拒绝覆盖同名归档，所以本次复用其会话清理辅助函数完成重复副本对账，不生成第二份归档，也不改写原完成日期。备份与逐项结果见[核对回执](duplicate-cleanup.json)。

## 验证范围

核对Trellis活动任务列表、归档上下文清单、旧会话指针、PRD和研究材料哈希、修改范围及Git whitespace。具体结果写入上述回执。业务源码未改动，本轮不重复运行管理员前端全套测试；原93项测试结果仅作为历史交付证据。

其他任务的工作区、会话、归档和未提交材料保留。该任务原feature分支已不存在，也没有对应linked worktree。
