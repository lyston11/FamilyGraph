# main 集成、归档与清理

2026-09-14：本期交付已合入并 push 到 main，七个完成任务均已归档，对应 worktree 和本地 feature 分支全部清理。MR-23/MR-26 的 P2 继续保持 planning；E 按研究交付验收，没有扩张为生产能力实施。

## 集成与实际 main 验证

主检出以单一串行通道从 `461d691` fast-forward 到 `a83b5d1`，随后 push。该提交包含最终审查材料、A/C/B/D 修复、E 研究和累计验收证据；业务代码检查点为 `aebee83`。没有 reset、rebase、amend 或 force 操作。

- [合并回执](main-integration.json)记录原 main、累计提交及本地保护范围。
- [源码和制品核对](main-code-integrity.json)：backend、agent、frontend、shared、scripts 的631个源码文件、可执行位和Git包树与受验代码一致；30份历史及24份最终制品、19份gzip原文哈希全部匹配。
- 在实际 main 检出重新执行 Agent build，exit 0；检查 Alembic 单头为 `0047_rag_lifecycle_integrity`。
- 重新执行 `backend/.venv/bin/python scripts/smoke/run_agent_memory_smoke.py --report <独立报告>`，结果 **95/95、exit 0**。见 [JSON](main-agent-memory-smoke.json)及[原日志](logs/main-agent-memory-smoke.log.gz)。该入口使用新的临时数据库与空闲端口，运行实际迁移、listener、维护、SidecarWorker和Pi；只有模型流为合成，外部网络尝试为0。
- 未重复源码无变化的后端1352/3 skipped、前端660和Agent118检查点全套；沿用[最终验收](../final-acceptance.md)中绑定到同一代码的结果。生产库、线上开关、真实Provider质量和部署不在本轮范围。

## 逐项归档和清理

每次先在主检出运行 `task.py archive`，紧接着核验已合入且无未提交业务改动，再运行正常的 `git worktree remove` 和 `git branch -d`。A/C/E 的未跟踪依赖链接经确认是 symlink 后仅 unlink；主检出的真实依赖目录保留。远端 feature 分支保留作为提交备份。

| 任务 | 最终分支提交 | 归档提交 | worktree / 本地分支 |
| --- | --- | --- | --- |
| A memory-contract-repair | `d1f43a5` | `509e2af` | 已清理 |
| C assistant-context-compaction | `8e91c42` | `2043833` | 已清理 |
| B rag-retrieval-citations | `bc76e95` | `4fd40bb` | 已清理 |
| D rag-index-lifecycle | `dd8157c` | `a64660b` | 已清理 |
| E agent-memory-capability-plan（研究） | `67e9316` | `b17b2f6` | 已清理 |
| memory-rag-acceptance-audit | `a83b5d1` | `74e0676` | 已清理 |
| agent-memory-rag-remediation（父任务） | `a83b5d1` | `9583011` | 已清理 |

归档提交已 push 至 `9583011`。逐项分支、完整SHA、旧worktree路径和删除前置条件见 [清理回执](archive-cleanup.json)；[命令日志](logs/archive-cleanup.log.gz)保留Trellis及Git输出。七项均处于 `.trellis/tasks/archive/2026-09/`，task.json 状态为 completed。

[P2证据与行为投影](../../../../../09-13-steward-memory-evidence-projections/prd.md)在E归档后解除活动父子关系，仍为 planning，没有新增分支、业务代码或部署。原E证据链接和称谓交接行保留。snapshot、Orca及其他任务不在本次归档/删除范围。

## 材料迁移与保护

[链接迁移回执](archive-links-applied.json)记录首次迁移的35份可维护文件；12份冻结Markdown按原字节保留。冻结历史文件中的旧路径、旧提交和失败结果是当时的执行证据；读取当前结果应从[最终验收](../final-acceptance.md)进入，不能将旧路径失效改写成原测试未执行。

归档后8个任务的Trellis清单、336个本地链接、状态、原始哈希与源码绑定均已核对；结果见 [归档布局核验](archive-layout.json)。本目录新增回执另列[制品清单](artifacts.json)，不修改原30份和最终24份清单。可从源码包树与a83b5d1一致的仓库根用[只读核验脚本](verify_delivery.py)重验，传入当前根目录、归档Audit路径和独立输出路径。

合并前将本任务旧镜像及主检出全部dirty/untracked文件保全到本地私有临时目录，只移开已经纳入累计候选的八个任务镜像及字节相同的0041格式镜像。其他任务40个既有dirty/untracked路径在合并时逐字一致，未纳入本期提交。保护备份保留；没有从旧快照恢复其他会话随后新增的工作。
