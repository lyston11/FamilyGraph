# Research: E 研究交付核验记录

- Query: 复现证据、执行命令、作用域和能力决定是否真实一致。
- Scope: internal；仅本任务 research/、专用合成临时目录。
- Date: 2026-09-13

## Findings

[自动核验结果](research-validation.json) passed=true，6 份主体研究 Markdown 的本地链接均存在，E-O1～12 / E-AC1～8 全覆盖；两个脚本可解析、源码和 harness SHA-256 均与证据一致。这里的通过代表研究材料与探针完成，不代表两个产品缺口已修复。

### 实际执行命令

MR-23/MR-26：在 E worktree 的 backend 工作目录执行：

~~~bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python ../.trellis/tasks/09-13-agent-memory-capability-plan/research/steward_capability_probe.py --source-commit 20d03084df341f6cc5fc8fcc18042757c07d822a
~~~

最终命令退出 0；真实 Alembic upgrade head 从空临时库迁移到 0041_term_pack_expansion。最终运行专用目录 /private/tmp/fg-steward-capability-eval-jrb1sqcp，总探针耗时 2.676 秒；这不是线上/模型延迟指标。15 次 fake attempt 成功、6 次 applied batch 重放均零额外调用、1 次平台关闭对照；MR-26 的 4 个开关/范围组合共 8 次真实 rebuild。

初次 bootstrap 曾因合成 ADMIN_JWT_ISSUER 与 AUDIENCE 相同被现有配置校验拒绝；仅修正测试进程的合成环境值后重跑。之后调整研究脚本格式并重跑到最终哈希；没有把该 fixture 设置错误记作产品缺陷。后续无关检查没有重跑 MR-23/26。

预算方案：在 E worktree 根执行：

~~~bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python .trellis/tasks/09-13-agent-memory-capability-plan/research/context_budget_probe.py
~~~

退出 0；18 个断言通过，production_budget_verified=false。[结果](context-budget-probe-results.json)明确写 synthetic units、无实际 tokenizer、无真实摘要质量验证、不导入生产模块、不持久跨 Run 摘要。

两个研究脚本分别按以下命令检查（把文件名替换为相应脚本）：

~~~bash
backend/.venv/bin/ruff check --config backend/pyproject.toml --no-cache .trellis/tasks/09-13-agent-memory-capability-plan/research/steward_capability_probe.py
backend/.venv/bin/ruff format --config backend/pyproject.toml --no-cache --check .trellis/tasks/09-13-agent-memory-capability-plan/research/steward_capability_probe.py
backend/.venv/bin/ruff check --config backend/pyproject.toml --no-cache .trellis/tasks/09-13-agent-memory-capability-plan/research/context_budget_probe.py
backend/.venv/bin/ruff format --config backend/pyproject.toml --no-cache --check .trellis/tasks/09-13-agent-memory-capability-plan/research/context_budget_probe.py
~~~

最终均通过。仅执行两个脚本的 AST parse，没有写 __pycache__。未跑全后端 pytest/mypy、前端 build 或线上 smoke：本研究没有生产改动，不用这些检查冒充缺口修复。

### 模块来源复核

主线程提醒 .venv editable install 可能指向主检出后，核对了 harness 的执行次序：在任何 app 导入前 sys.path.insert(0, E/backend)，再导入 Alembic/app。随后在相同解释器、相同显式路径插入下执行一次只导入核验（不运行 core、不访问数据库），记录实际 module.__file__：

[模块来源证据](module-resolution-check.json)中 app、app.config、steward、steward_assist、steward_suggestions、family_recommendations 六个模块均来自 E worktree/backend。该检查是独立导入核验，不伪装成原进程记录；结合脚本入口顺序、源码哈希，未发现导入主检出问题，所以未为此重跑业务探针。

### 结果判定与校验范围

- MR-23：3 个独立空间，全部 15 次 fake attempt succeeded/batch applied；相关组输入确含新事实，而候选/建议仍各1条、旧证据/收件 dismissed 不变；对照与重放实际值一致。
- MR-26：开关关闭原行完整相等；开启的 account 与 whole-space 路径均删除非拥有键；自有键两次回放语义一致。故确认条件性缺陷，没有线上事故证据。
- 预算：两个模拟窗口各8例，加2配置错误，共18；来源授权和实际 token 上界不在该模拟证明范围。
- 文档：逐份检查 Research 标题、Query/Scope/Date、Findings/Caveats；Markdown 本地链接存在；12个议题与8个E-AC均有执行或延期处置。
- 主线程已创建唯一 MR 修复规划包 09-13-steward-memory-evidence-projections；报告已同步“已创建、未实施”，不修改其任务状态。
- 无 Git 操作，无真实模型/网络调用，无开发/线上数据库访问。所有生产源文件 SHA-256 与探针开始时一致。

初次 ruff format 未加 --no-cache，在 E worktree 根产生了 .ruff_cache。该工具副作用已经立即告知主线程负责清理；后续所有 Ruff 命令均禁用缓存，研究代理未越出 research/ 删除根目录文件。专用临时库仅为合成数据，由主线程决定保留证据或最终清理。

## External references / Related specs

无需外部资料。按项目本地工作流、E 审定文档与真实生产代码核验；历史规范仅用于追溯，不将过期规范写成当前实现事实。

## Caveats / Not Found

真实模型质量、成本、线上有效开关、扫描延迟和用户是否希望新证据重提均未验证。MR-23/26 修复尚未实施；其余能力已明确延期。研究执行者没有进行 Git、部署或管理后台状态变更；主线程按既有授权统一提交研究材料。
