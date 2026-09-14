# 执行记录

本记录续接用户“执行 / 继续”授权。原审计和规划校验保留其历史时间点，实际修复证据在各子任务 research/ 中追加。

## 最新验收（2026-09-14）

A/C/B/D修复与E研究交付已在累计dd8157c验收通过；D业务提交aebee83。B/D原20组问题及D追加的合法index_superseded恢复回归均闭合，父AC-01～10/F-01～12证据见 [最终验收](../../09-14-memory-rag-acceptance-audit/research/final-acceptance.md)。后端1352 passed/3既有skipped、前端660、agent118检查点+源码无差异证明，真实listener/Pi/维护95/95、API56/56、迁移8/8；原红测不改写。

A d1f43a5、C8e91c42、Bbc76e95、Ddd8157c、E67e9316以及父候选均已实际push。最终材料a83b5d1已由本会话在主检出串行fast-forward合入main（原461d691）并push；631个源码文件与受验版本一致，实际main的隔离listener/Pi/维护smoke再跑95/95，exit 0，迁移保持0047单头。

A、C、B、D、E研究、Audit和父任务共7项已通过task.py archive归档。每项归档后均立即确认分支已合入、worktree无未提交业务修改，再正常删除worktree和本地分支；未使用force。归档提交已push至9583011，逐项提交及清理回执见 [集成收尾记录](../../09-14-memory-rag-acceptance-audit/research/integration/closure.md)。主检出其他任务的40个既有dirty/untracked路径在合并时逐字保全；snapshot和Orca的worktree保留。

下列“未授权合并/待修复/未push”是此前检查点，不代表当前状态。P2 steward-memory-evidence-projections保持planning，E归档后解除活动父子关系；生产部署、真实Provider与线上库操作不在本轮范围。

## 初始实施与集成边界（历史）

- 实施基线：`20d03084df341f6cc5fc8fcc18042757c07d822a`。原审计基线仍为 `b7bd368`。
- 顺序：A → C → B → D；E 独立完成隔离复现与方案评估。
- 每项使用自己的 feature 分支和 linked worktree。后继任务从已检查、已提交的前置任务提交创建分支，获得完整修复链，不改写任何分支历史。
- 父 implement.md 已明确本轮不含合并 main / 部署。先交付可审阅的本地提交与集成验证；未合并前不删除分支或 worktree，不以强制删除满足清理要求。
- 主检出已有 AGENTS、配置、管理员前端及其他任务改动均属既有工作，不覆盖。0041 的基线 lint/format 失败在 A worktree 内独立执行机械格式化，AST 未变；结果与主检出现有纯格式改动逐字一致，主检出未被修改。
- 依赖目录只以本地 symlink 提供；提交按明确路径暂存，禁止将 `.venv` / `node_modules` 链接纳入 Git。

## 基线复查时的工作状态（历史）

| 子任务 | 状态 | 已产生的可核验材料 |
|---|---|---|
| A | 实施验收完成，提交 d1f43a5 | 后端 1050 passed/3 skipped、39 项 API/并发/迁移专项；前端 56 passed；三 listener smoke 56/56（Memory 26）；legacy 越界与前端竞态已修复 |
| C | 初版已提交；补充修复 8e91c42 本地提交、独立验收通过，待累计纳入 | 初版 2baf7a8/470b362；补丁覆盖 overflow→summary→retry stop 正确结算，完整 agent 113 passed、独立实际 Pi 0.84.3 探针通过 |
| B | 实施提交存在，复查未通过；修复规划已登记 | 历史 backend 1114、agent 109、frontend 620 全绿记录保留；累计 bd899b9 新复查 10 组缺口，18 场景最新逐项 16 失败/2 通过 |
| D | 实施提交存在，复查未通过；修复规划已登记 | 历史 backend 1138 与迁移检查保留；累计 bd899b9 新复查 10 组缺口，20 场景 17 失败/3 通过 |
| E | 研究验收完成，本地提交 bc6b500 | MR-23/MR-26 合成生产链、对照与哈希核验完成；12 项决策已记录，18 项预算研究断言通过；未改 Steward 生产能力 |

状态随验收更新。未通过的命令、环境阻塞和真实模型实验未执行都必须保留，不能用规划完成或假 transport 通过替代生产质量结论。

## 新的后续所有者

E 的完整复现已形成独立 P2 规划包 [Steward 证据版本与行为投影键族修复](../../../../09-13-steward-memory-evidence-projections/prd.md)。它由 E 建立，E 归档后保持独立 planning，负责 MR-23/MR-26 的后续业务实现；本轮没有启动。PRD/design/implement 与两个非空 context manifests 已创建，task.py validate 通过，不占用 A/B/D 迁移编号。

## 此前集成完成汇报（2026-09-14，已由下方复查更新）

以下保留此前汇报和测试执行事实；其“AC-01～09 已完成”和“未合并所以不能 smoke”的结论不再代表当前验收状态。

- A→C→B→D 串行实施完成；E 研究交付完成（此前记录）。各子任务分支均已 push：
  - A `feat/09-13-memory-contract-repair`（d1f43a5）
  - C `feat/09-13-assistant-context-compaction`（2baf7a8/470b362，基于 A）
  - B `feat/09-13-rag-retrieval-citations`（97675c7 merge A+C，078f2e3，95d83f1/56bb895 仓库卫生）
  - D `feat/09-13-rag-index-lifecycle`（9add87b merge A+C+B，bd899b9）
- 迁移链最终单头 0045_rag_index_lifecycle；隔离库 upgrade/downgrade 往返通过；无序号冲突。
- AC-01～AC-09 对应证据见各子任务 research/implementation.md 与 notes.md。AC-10 的「集成后真实合同 smoke」未执行：本轮授权范围不含合并 main，无合并后运行环境；frontend-api-smoke 留待串行集成通道在合并后执行（环境阻塞时按退出码 2 记录，不算通过）。
- 分支合并 main、task.py archive 与 worktree/分支清理由人（或单一串行集成通道）执行；各 worktree 保留待集成。

## 独立复查与当前交付（2026-09-14）

用户选择“创建任务并记录分析”，新建 [09-14-memory-rag-acceptance-audit](../../09-14-memory-rag-acceptance-audit/prd.md)，保持 planning；本轮没有启动 B/D 新业务修复。父任务保持 in_progress。

- 固定基线 bd899b9，所有相关生产文件保持该提交；B/D 报告分别记录 [10 组 B 缺口](../../09-14-memory-rag-acceptance-audit/research/b-integration-check.md) 与 [10 组 D 缺口](../../09-14-memory-rag-acceptance-audit/research/d-integration-check.md)。其中 18 组有合成环境复现、2 组静态未接线。
- 主线程抽查关键源码和原合同：旧 attempt 在鉴权后换代仍能写入、精确块认证缺失、自报引用进入 SSE、internal 历史返出撤权元数据；并发重复 document、旧游标回写、失租后部分提交、换版后不可检索/回退、降级删除保存依赖等均不能以现有全套通过代替验收。
- 正面证据：冻结中文核心 16/16、英文 2/2、独立扩展 7/10；原请求撤权后幂等和精确 16 KiB 边界通过；RAG-only 晚开启、未知 tombstone 和读者/全局来源失效区分通过。
- 已在隔离累计分支实际执行 API smoke 56/56、新增三 listener+真实 SidecarWorker/Pi 正常两轮链 50/50。后者包含历史恢复、context 重取、来源撤销和丢响应重试，只有模型 stream 为假；外网尝试 0。隔离 smoke 无需先合并 main，前述延后理由已纠正；这些正常链不消除独立安全/并发反例。
- C 先前授权的 P2 补丁单独提交 `8e91c420ce4d8ef2f4a4d806027bd29d72382f94`，本地 backup 分支固定，未 push/合入 bd899b9 或 main。agent 113 tests、lint/type/build、原独立 SDK 成功结算硬断言通过；超大当前输入仍完整一次并明确失败。
- 原 MR-01～26、E 的十二项优化与 MR-23/26 后续唯一 owner 全部保留。最新父 AC 与修复 F-01～12 状态见 [验收矩阵](../../09-14-memory-rag-acceptance-audit/research/acceptance-matrix.md)。B/D 整体不通过，父 AC-03/06/07 未闭合，其他部分项按矩阵记录。
- 本轮未查线上开关、未调用真实 Provider/生产库、未合并 main、未部署或归档。所有未合并 worktree/分支保留，不能使用强制删除满足清理要求；主检出其他任务的未提交改动未纳入本次交付。

## 修复与集成执行（2026-09-14，最新授权）

用户明确授权审查验收，通过后提交、合并和归档。本会话继续处理所有 20 组 B/D 缺口，保留原审计基线和失败证据。B 候选已顺序纳入父审查提交 b348980、C 修复 8e91c42 与当前 main d7629df；仅四份父任务记录出现合并冲突，逐项核对后保留包含原 main 记录的最新复查版本。业务模块无冲突。主检出全部脏文件已保全，其他任务不纳入本轮提交。后续 B→D→最终验收→串行合并归档清理。

续接检查点：B 的 `b6688f8`、`bc76e95` 已通过主线程核验并 push（backend 1201 / agent 118 / frontend 625，真实 smoke 95/95）；C `8e91c42` 已纳入父候选并实际 push。D 在累计版本及 main `461d691` 上启动十组生命周期修复，`c253cb6` 已固定执行材料；父任务最终验收仍待 D。A 的远端分支本轮核实后补建为 `d1f43a5`。详细版本、检查范围和清理计划见 [累计执行检查点](../../09-14-memory-rag-acceptance-audit/research/integration-preflight.md)。
