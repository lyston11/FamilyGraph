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
| C | 实施与验收完成，待提交 | agent 108 passed（SDK 专项 19 + worker 2）；lint/type-check/build 通过；红测→修复证据与 C-AC1~6 映射落 research/implementation.md 与 notes.md |
| B | 等待 A/C 完成 | 已冻结中文样本、引用认证和 16 KiB 事件合同 |
| D | 等待 A/B 完成 | 已批准的稳定索引身份、防复活和有界维护方案 |
| E | 研究验收完成，本地提交 bc6b500 | MR-23/MR-26 合成生产链、对照与哈希核验完成；12 项决策已记录，18 项预算研究断言通过；未改 Steward 生产能力 |

状态随验收更新。未通过的命令、环境阻塞和真实模型实验未执行都必须保留，不能用规划完成或假 transport 通过替代生产质量结论。

## 新的后续所有者

E 的完整复现已形成独立 P2 规划包 [Steward 证据版本与行为投影键族修复](../../09-13-steward-memory-evidence-projections/prd.md)。它是 E 的 child，负责 MR-23/MR-26 的后续业务实现；本轮没有启动。PRD/design/implement 与两个非空 context manifests 已创建，task.py validate 通过，不占用 A/B/D 迁移编号。
