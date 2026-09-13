# 执行记录

本记录续接用户“执行 / 继续”授权。原审计和规划校验保留其历史时间点，实际修复证据在各子任务 research/ 中追加。

## 实施与集成边界

- 实施基线：`20d03084df341f6cc5fc8fcc18042757c07d822a`。原审计基线仍为 `b7bd368`。
- 顺序：A → C → B → D；E 独立完成隔离复现与方案评估。
- 每项使用自己的 feature 分支和 linked worktree。后继任务从已检查、已提交的前置任务提交创建分支，获得完整修复链，不改写任何分支历史。
- 父 implement.md 已明确本轮不含合并 main / 部署。先交付可审阅的本地提交与集成验证；未合并前不删除分支或 worktree，不以强制删除满足清理要求。
- 主检出已有 AGENTS、配置、管理员前端及其他任务改动均属既有工作，不覆盖。0041 的基线 lint/format 失败在 A worktree 内独立执行机械格式化，AST 未变；结果与主检出现有纯格式改动逐字一致，主检出未被修改。
- 依赖目录只以本地 symlink 提供；提交按明确路径暂存，禁止将 `.venv` / `node_modules` 链接纳入 Git。

## 当前工作

| 子任务 | 状态 | 已产生的可核验材料 |
|---|---|---|
| A | 实施验收完成，提交 d1f43a5 | 后端 1050 passed/3 skipped、39 项 API/并发/迁移专项；前端 56 passed；三 listener smoke 56/56（Memory 26）；legacy 越界与前端竞态已修复 |
| C | 实施中 | 基于 A 提交 d1f43a5；SDK 历史恢复与实际自动/手动压缩回归 |
| B | 实施与验收完成，提交 078f2e3（分支含 56bb895 收尾） | backend 1114 passed（新增 35 条检索/引用回归）、agent 109、frontend 620；ruff/mypy/eslint/build 全绿；迁移 0044 合并双头 |
| D | 实施与验收完成，提交 bd899b9 | backend 1138 passed（新增 13 条 lifecycle/lease/换版回归）；迁移 0045 合并 0044 双头；MR-25 复活回归阻断 |
| E | 研究验收完成，本地提交 bc6b500 | MR-23/MR-26 合成生产链、对照与哈希核验完成；12 项决策已记录，18 项预算研究断言通过；未改 Steward 生产能力 |

状态随验收更新。未通过的命令、环境阻塞和真实模型实验未执行都必须保留，不能用规划完成或假 transport 通过替代生产质量结论。

## 新的后续所有者

E 的完整复现已形成独立 P2 规划包 [Steward 证据版本与行为投影键族修复](../../09-13-steward-memory-evidence-projections/prd.md)。它是 E 的 child，负责 MR-23/MR-26 的后续业务实现；本轮没有启动。PRD/design/implement 与两个非空 context manifests 已创建，task.py validate 通过，不占用 A/B/D 迁移编号。

## 集成与验收记录（2026-09-14）

- A→C→B→D 串行实施完成；E 研究交付完成（此前记录）。各子任务分支均已 push：
  - A `feat/09-13-memory-contract-repair`（d1f43a5）
  - C `feat/09-13-assistant-context-compaction`（2baf7a8/470b362，基于 A）
  - B `feat/09-13-rag-retrieval-citations`（97675c7 merge A+C，078f2e3，95d83f1/56bb895 仓库卫生）
  - D `feat/09-13-rag-index-lifecycle`（9add87b merge A+C+B，bd899b9）
- 迁移链最终单头 0045_rag_index_lifecycle；隔离库 upgrade/downgrade 往返通过；无序号冲突。
- AC-01～AC-09 对应证据见各子任务 research/implementation.md 与 notes.md。AC-10 的「集成后真实合同 smoke」未执行：本轮授权范围不含合并 main，无合并后运行环境；frontend-api-smoke 留待串行集成通道在合并后执行（环境阻塞时按退出码 2 记录，不算通过）。
- 分支合并 main、task.py archive 与 worktree/分支清理由人（或单一串行集成通道）执行；各 worktree 保留待集成。
