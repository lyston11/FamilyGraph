# 验证记录 — Steward 称谓能力闭环（A → B 串行）

日期：2026-09-14。所有命令在任务 worktree（`../fg-09-13-steward-kinship-capability-closure`）对应 package 内执行；集成后在主检出复核。venv/node_modules 以符号链接复用主检出构建环境。

## 版本与集成点

- 基线：origin/main `7fe5061`；migration head 0043 → 本期 0044。
- A 提交：`ad10dd0`（19 files，+1015/−62），已 merge 进 main。
- B 提交：`9baf659`（34 files，+2501/−31），已 merge 进 main。
- 主检出他人 WIP（config.py 管理员 refresh TTL、0041 格式）：合并前以 `/tmp/other-config-wip.patch` 保全、合并后 `git apply --3way` 原样恢复为未提交状态，未纳入本任务提交。

## 隔离迁移

- `DATABASE_URL=sqlite:////tmp/fg-b-mig.sqlite3 alembic upgrade head`：0040→0044 全链成功，二次执行 no-op（幂等），`alembic current` = `0044_steward_terminology (head)`。
- 主检出合并后复跑：0043→0044 成功（SQLite batch recreate 重建 `ck_smc_assist_kind`）。

## backend（ruff / format / mypy / pytest）

- `ruff check app tests`、`ruff format --check app tests`：全绿（`migrations/0041` 的 E501 为 main 既有问题且他人 WIP 正在修，未触碰）。
- `mypy app`：Success, no issues in 187 files。
- `pytest -q`：**1051 passed, 3 skipped**（合并前 worktree；主检出合并后关键子集复跑通过）。
- 新增：`tests/test_kinship_presentation.py`（A：5 场景——多 viewer 方向句/证据分级/21+ 详情/忽略读时生效/过期/只读无副作用）、`tests/test_steward_terminology.py`（B：6 场景——baseline 投影零通知/本人用词 override/真实 job+fake transport 全链写回/非法词拒绝并记 checked/恢复+稳定抑制跨证据版本/only-terminology 生效值）。

## frontend / system-admin-frontend

- frontend：`npm run lint`、`npm run type-check`、`npm test`（66 文件 583 passed）、`npm run build` 全绿。
- system-admin-frontend：`npm run lint`、`npm run type-check`、`npm test`（15 文件 95 passed）、`npm run build` 全绿。
- 适配更新：SpaceModelSettingsPanel 第四开关「称谓优化」（含 effective 提示）、PlatformFeaturesView 四类开关、来源徽章 derived/steward。

## 未执行 / 单列（R-07 交付分级）

- **真实模型质量未测**：全链以 fake transport 验证「代码已接通」；`STEWARD_ASSIST_TERMINOLOGY` 默认关，本环境未开启，线上开关未动。
- smoke（`scripts/frontend-api-smoke.sh`）未跑：需要独立环境/端口，与并行任务的隔离约束冲突；本期以 API 层 pytest + 前端 decoder 测试覆盖合同。
- 术语包「姥姥」别名只影响种子/幂等重灌；存量库未重灌前模型输出「姥姥」会被保守拒绝（回退 baseline 外婆），非破坏性。
- `test_lease_deadline_stops_followup_sends` 为墙钟计时测试，全量运行中偶发一次失败，隔离与复跑均稳定通过（既有测试，非本期改动引入）。

## AC 证据映射（父 AC-01～15）

| AC | 结果 | 证据 |
| --- | --- | --- |
| AC-01/02 | 代码+测试级验证 | A 提交 ad10dd0：viewer 绑定 presentation（test_kinship_presentation::test_presentation_direction_follows_viewer）、可见性遮罩、未知性别走 allowlist 降级 |
| AC-03 | 通过 | schemas/kinship.py Literal 增 derived/steward；test_terms/test_term_autofix 全绿；真实 resolve API 返回 derived |
| AC-04/05 | 通过 | test_steward_terminology::test_explicit_usage_creates_override_and_optional_suggestion、test_deterministic_scan…（真实 job 链路；notify=False 零逐条待办；零 SourceFact/授权申请） |
| AC-06 | 通过 | test_model_term_applied_via_real_job_chain（外婆→姥姥 写回+建议）；test_invalid_model_terms_rejected…（错概念码拒绝回退） |
| AC-07/08 | 通过 | test_restore_suppresses_across_evidence_versions（恢复即时+同词跨证据不再应用）；自动结果零 TermUsage（test 断言）；显式个人词条目标跳过 |
| AC-09/10 | 通过 | test_detail_beyond_first_page_and_dismissed_state、test_dismissed_state_consistent…、test_expired…；evidence kind=unverified_candidate（模型线索不计整空间事实数） |
| AC-11/12/13 | 代码+测试级验证 | attempt.viewer_account_id 绑定（test 断言）、GET 零模型调用（详情只读测试）、last_checked 同输入零重发（test_invalid… 第二 job 无新 call）、撤权/证据变化走既有 fence（_fence_check 扩 terminology 语义摘要） |
| AC-14 | 完成 | task-alignment.md 修订已在规划期落盘；本期未重开远端归档任务 |
| AC-15 | 部分 | 隔离迁移/受影响检查/合成场景通过；**真实模型与线上状态单列未测**；归档清理见交付说明 |
