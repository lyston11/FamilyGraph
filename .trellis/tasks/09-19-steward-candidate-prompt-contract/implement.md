# Implement：候选 prompt 契约与规模降级

## 当前状态

2026-09-19：仅完成规划，任务保持 `planning`。未执行 `task.py start`，无分支/worktree，未改动任何业务代码。

**前置依赖**：`09-19-steward-recommendation-correctness` 必须先实现、检查并集成。两者共享 `steward_assist.py` / `steward_guard.py`，并行会冲突。

## S0：审阅、激活与隔离

- [ ] 用户审阅 PRD/design，确认 §3.2 的策略选择（推荐 A：有界事实子集）与 §3.1 的超限终态映射。
- [ ] 核对主检出 `git status --short`、`git worktree list`、`task.py current`；确认并行任务已合并进 main 且其 worktree 已清理。
- [ ] `python3 ./.trellis/scripts/task.py validate .trellis/tasks/09-19-steward-candidate-prompt-contract`。
- [ ] 获准后 `task.py start`，进入 task.json 记录的 worktree 后再写业务代码。
- [ ] 分段读取 `steward-action-card.md`（42739 字节 > 32768 注入上限）全文；核对 `steward-candidate-evidence.md`。
- [ ] 后端命令用该 worktree 的 `PYTHONPATH=.` 与解释器；自建测试环境显式 `DATA_DIR=<隔离目录>`。

回滚点：仅创建隔离现场；未合并分支不强制删除。

## S1：先写失败回归

- [ ] prompt 契约断言：当前 prompt 缺方向语义、缺矛盾禁止、缺示例 → 红测（逐条断言，非子串匹配）。
- [ ] 超限回归：构造超 `STEWARD_ASSIST_MAX_PROMPT_BYTES` 的事实集，断言当前批次终态为 `applied` 且 admin 无 `skipped` 计数 → 红测。
- [ ] `prompt_version` 回归：断言 prompt 变更后 `_prompt_version()` 变化（当前无该断言）。
- [ ] 记录红测命令与基线 SHA 到 `research/evidence/red-tests.md`，确认失败原因来自目标缺陷而非夹具。

验收映射：AC1/AC3/AC4/AC5。

## S2：prompt 契约

- [ ] 按 design §2 补齐方向语义、矛盾/重复禁止、最小示例；保持"只输出 JSON、无自由文本"形状。
- [ ] 示例只用占位代号，绝不含真实姓名或真实家族数据。
- [ ] 不改 `project_candidate_input` 字段集合；确认不新增任何模型可见信息。
- [ ] 对抗 fixture 新增"与输入事实矛盾的同辈候选"用例（本任务不要求校验器拦截，只要求存在且结果被如实记录）。
- [ ] 断言 `prompt_version` 随变更更新；确认评测报告字段随之变化。

验收映射：AC1/AC2/AC3/AC9(AC8)。

## S3：超限如实结算与可观测

- [ ] 区分 `prompt_too_large`（确定性输入问题）与预算类 `skipped`（可重试）：前者进入批次终态与 `error_code`，不归入 `unknown`，不计费、不重放。
- [ ] `admin_steward.py` 按 `error_code` 暴露 `skipped` 计数；响应不含 prompt/事实/姓名/空间内部内容。
- [ ] 断言超限路径无网络调用、无预算消耗、无重放、无 `unknown` 计费。
- [ ] 断言既有良性 `skipped`（预算类）语义未被改变。

验收映射：AC4/AC5/AC7。

## S4：有界输入使大空间可收敛

- [ ] 按 design §3.2 实现确定性事实子集（fact_id 升序取前 N，N 由字节预算反推）；不新增硬编码常量、不放宽上限。
- [ ] 子集化后仍超限时回落 `prompt_too_large` 并如实结算。
- [ ] 断言子集选择确定可复现（同输入同子集），`input_hash`/`prompt_digest` 稳定，发送前 fence 不误判。
- [ ] 断言未超限场景行为与变更前逐字节一致（`project_candidate_input` 输出不变）。
- [ ] 断言截断后输出仍通过全部既有校验，未放宽任何强度。

验收映射：AC6/AC7/AC8。

## S5：分层质量门禁

在任务 worktree 的 `backend/` 执行：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_assist.py tests/test_steward_assist_deadline.py tests/test_steward_guard.py
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_eval.py tests/test_steward_assist_platform_governance.py tests/test_admin_steward.py
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_candidate_evidence.py tests/test_steward_candidate_evidence_integration.py
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
PYTHONPATH=. .venv/bin/python -m pytest -q
```

- [ ] 断言硬安全门禁 100%、负例全空、候选召回 ≥0.9 继续通过；报告落 JSON 并记录 `prompt_version`。
- [ ] 确认节点代号投影、未成年过滤、字节上界、预算与租约栅栏回归无变化。
- [ ] 独立 `trellis-check` 审查全部差异、Spec 一致性与 AC 逐项证据。dispatch 首行必须为 `Active task: <task.py current 返回路径>`。
- [ ] 如实标注：未跑真实模型质量评测时不得声称质量已改善。

证据写入 `research/evidence/validation.md`，摘要写入 `research/validation-summary.md`。

## S6：规范、提交与集成

- [ ] 在 Spec 中记录：候选 prompt 契约（方向/矛盾禁止/示例）、规模策略与截断语义、`prompt_too_large` 终态映射、`skipped` 计数的可观测合同。
- [ ] 更新 AC 证据映射；`task.py validate` 通过。
- [ ] 任务分支小步 commit 并 push；不提交其他任务脏文件，不 reset/rebase/force push。
- [ ] 单一集成通道在主检出 merge，复查受影响回归，`git push origin main`。
- [ ] 达到关闭门槛后 archive；分支已合并且 worktree 无未提交代码时立即 `git worktree remove` + `git branch -d`，报告结果；不满足条件保留现场并说明，禁止强删。

## S7：生产部署（单独授权）

- [ ] 明确生产授权；记录真实服务 SHA、工作目录/systemd 作用域、`DATA_DIR`、schema head、有效开关。
- [ ] 记录 prompt 变更会使在途未发送的候选 attempt 因 `prompt_digest` 不匹配而 supersede（既有 fence 正确行为），不影响已结算结果。
- [ ] 在线备份后部署；确认实际进程加载新版本。
- [ ] 观察至少一次成功扫描，核对候选辅助仍产出、`skipped` 计数不异常增长、无告警风暴。
- [ ] 对超限空间确认候选辅助恢复工作（子集策略生效），且未产生矛盾候选；未做真实模型质量评测时如实说明。
