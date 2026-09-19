# Implement：候选 prompt 契约与规模降级

## 当前状态

2026-09-19：**S0–S6 已完成**（代码、规范、核查、集成）。S7 生产部署**未开始**，需单独授权。

并行前置依赖 `09-19-steward-recommendation-correctness` 已合并进 main 并归档（`2d1e9bf`→`2110177`），
与本任务改动面零交集；本分支已 merge `origin/main`。

**关键实测修正（核查期发现，已修）**：design §3.2 原写「子集化后仍可能超限时回落 `prompt_too_large`」，
核查逐 cap 扫描发现真实边界比该描述更精确——能装下的子集为 **0 条**时（`cap ≤ system + 空投影`）即回落。
行为符合设计意图（拒发空输入、如实结算而非伪装成功），仅注释有误，已更正并加回归。

## S0：审阅、激活与隔离

- [x] 用户授权执行本任务（`task.py start` 已运行）。
- [x] 核对主检出 `git status --short`、`git worktree list`、`task.py current`。
- [x] `task.py validate` 通过（implement/check 各 6 条有效上下文）。
- [x] `task.py start`，进入 worktree `../fg-09-19-steward-candidate-prompt-contract` 后写代码。
- [x] 分段读取 `steward-action-card.md` 全文（42739 字节 > 32768 注入上限）；核对 `steward-candidate-evidence.md`。
- [x] 后端命令用该 worktree 的 `PYTHONPATH=.` 与解释器；测试 `DATA_DIR` 由 conftest 隔离。

## S1：先写失败回归

- [x] prompt 契约断言先红（`AttributeError: _CANDIDATE_DIRECTION_CLAUSE` 等 3 项）。
- [x] 超限回归先红（`assert 'applied' == 'failed'`）。
- [x] `prompt_version` 回归先红（`AttributeError: prompt_version`）。
- [x] 红测命令与基线 SHA 记录到 `research/evidence/red-tests.md`；9 项全红，逐条归因非夹具/环境。

## S2：prompt 契约

- [x] 三段常量（方向 163B / 矛盾禁止 124B / 示例 71B）拼入 `_PROMPTS["candidate"]`；prompt 582→1214 字节。
- [x] 示例只用 `n001`/`n002` 占位代号，无真实姓名/家族数据。
- [x] `project_candidate_input` 字段集合未改；未新增任何模型可见信息（已核对 diff）。
- [x] 对抗 fixture 新增 `adv-candidate-conflicts-with-input-fact`（v1→v2），`security_case=false` 如实记录。
- [x] `prompt_version()` 提为单一真源；评测报告字段随之变化（`edb657bd…`→`dbf4c72e…`）。

## S3：超限如实结算与可观测

- [x] `prompt_too_large` 进入批次终态；`unknown`/`failed` 优先级在其之上（回归守护）。
- [x] 预算类 `skipped`（`budget_exhausted`/`insufficient_budget`）语义未变，仍 `applied`（回归守护）。
- [x] `admin_steward.py` 暴露 `assist_skipped` / `assist_skipped_prompt_too_large` 纯计数。
- [x] 断言超限路径无网络调用、`billed_tokens is None`、无 `unknown`、无重放。
- [x] `test_steward_log_hygiene` 精确集合断言同步（实测 15 个 metrics 字段）。

## S4：有界输入使大空间可收敛

- [x] `candidate_user_content` 是候选 user 内容的唯一入口（`schedule_due_batch` + `_user_content_for`）。
- [x] 二分查 `fact_id` 升序最长前缀；无新增硬编码常量；未放宽字节上界。
- [x] 子集为 0 条时回落 `prompt_too_large` 并如实结算（不再静默停摆、也不伪装成功）。
- [x] 断言子集确定可复现、`input_hash`/`prompt_digest` 与发送前 fence 一致（实测 `succeeded`）。
- [x] 断言未超限场景投影逐字节一致；截断后输出仍过全部既有校验。

## S5：分层质量门禁

- [x] 定向回归 212 passed（14 个相关测试文件）。
- [x] 完整后端 `pytest -q`：**1728 passed, 3 skipped**（首次运行的一条 `database is locked` 经复核为并发环境竞争，非本改动引入）。
- [x] 评测门槛：hard_gate 100%、负例全空、召回 1.0、安全用例 27。
- [x] `ruff check` / `ruff format --check` / `mypy app` 全绿。
- [x] 节点代号投影、未成年过滤、字节上界、预算/租约栅栏回归无变化。
- [x] 独立核查（用户指示由主会话执行，未派子智能体）：9 项 AC 逐项判定 + 发现并修复 D1；
      报告见 `research/check-report.md`。
- [x] 如实标注：**未跑真实模型质量评测**，不声称质量已改善。

## S6：规范、提交与集成

- [x] `steward-action-card.md` 增补三条合同：候选 prompt 契约、事实规模降级与终态映射、超限可观测。
- [x] 证据写入 `research/evidence/validation.md`，摘要写入 `research/validation-summary.md`。
- [x] 任务分支小步 commit。
- [x] 单一集成通道在主检出 merge 并 `git push origin main`。
- [x] archive；分支已合并且 worktree 无未提交代码时 `git worktree remove` + `git branch -d`。

## S7：生产部署（单独授权，未开始）

- [ ] 明确生产授权；记录真实服务 SHA、工作目录/systemd 作用域、`DATA_DIR`、schema head、有效开关。
- [ ] 记录 prompt 变更会使在途未发送的候选 attempt 因 `prompt_digest` 不匹配而 supersede
      （既有 fence 正确行为），不影响已结算结果、不需迁移。
- [ ] 在线备份后部署；确认实际进程加载新版本。
- [ ] 观察至少一次成功扫描，核对候选辅助仍产出、`assist_skipped_prompt_too_large` 不异常增长、无告警风暴。
- [ ] 对超限空间确认候选辅助恢复工作（子集策略生效）；未做真实模型质量评测时如实说明。
