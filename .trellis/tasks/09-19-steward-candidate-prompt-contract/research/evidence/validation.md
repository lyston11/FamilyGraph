# 验证证据：候选 prompt 契约与规模降级

日期：2026-09-19
worktree：`/Users/lyston/PycharmProjects/fg-09-19-steward-candidate-prompt-contract`
分支：`feat/09-19-steward-candidate-prompt-contract`（base `4d6fb28`，已 merge `origin/main` @ `2110177`）

## 环境

- 解释器：该 worktree 的 `backend/.venv`（指向主检出 venv，`.gitignore` 已忽略）。
- 所有自建测试显式 `PYTHONPATH=.`，工作目录 `backend/`；`conftest.py` 自动设 `DATA_DIR=tempfile.mkdtemp(...)`，未触碰生产库。

## 定向回归（最后绿）

```
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_steward_candidate_prompt_contract.py tests/test_steward_assist.py \
  tests/test_steward_assist_deadline.py tests/test_steward_guard.py \
  tests/test_steward_eval.py tests/test_steward_candidate_evidence.py \
  tests/test_steward_candidate_evidence_integration.py tests/test_steward_log_hygiene.py \
  tests/test_admin_steward.py tests/test_steward_observability.py \
  tests/test_steward_assist_platform_governance.py tests/test_steward_candidate_policy.py \
  tests/test_steward_staged_pipeline.py tests/test_steward_delivery_recovery.py
```

结果：**212 passed in 95.21s**。

## 完整后端套件

```
PYTHONPATH=. .venv/bin/python -m pytest -q -p no:randomly
```

结果：**1728 passed, 3 skipped, 175 warnings in 534.22s (0:08:54)**。

说明：首次不加 `-p no:randomly` 的运行出现 1 条 `test_steward_candidate_evidence_integration.py::test_lease_expiring_while_actual_writeback_waits_for_sqlite_writer_is_not_adopted`
失败（`database is locked`）。该用例单独运行、单独文件运行 3/3、以及在**父提交 `git stash` 后的未改动工作树上**均通过，
且该用例同时出现在另一并行任务的完整套件日志中（1757 passed + 同一失败）。判定为多 agent 并发负载下的
SQLite 写锁环境竞争，非本改动引入；清环境重跑后完整套件全绿。

## 静态门禁

```
.venv/bin/ruff check .          → All checks passed!
.venv/bin/ruff format --check . → 406 files already formatted
.venv/bin/mypy app              → Success: no issues found in 207 source files
```

## 评测门槛（fake transport，程序合同）

```
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_eval.py
```

报告 `backend/.steward-eval-report.json`：

| 字段 | 值 |
| --- | --- |
| `hard_gate_passed` | true |
| `security_cases_total` | 27 |
| `security_cases_failed` | 0 |
| `candidate_recall` | 1.0 |
| `negative_cases_empty_ok` | true |
| `prompt_version` | `dbf4c72e9ba43ea7260b8f449b9ee8b4c31b653f4ccde4e27f34cc9a05add723` |
| `fixture_version` | `{relation_matrix.json: 1, adversarial.json: 2}` |

对照：main 分支报告 `prompt_version = edb657bdc3806bdad6f4d991da8e1aee6cccc787ea68dac56073c6a9da6e8a4e`（prompt 已变更）。

## 核查期临时探针（已删除，不留在树内）

核查阶段写了 4 个临时探针文件验证技术前提，全部在验证后删除：

1. **投影单调性**：`sizes == sorted(sizes)` 成立，含端点越界（不可投影）事实的夹具亦然。
2. **预算边界逐字节扫描**：`cap` 从 1 扫到 `total(full)`，验证「要么取到最大可行子集且在预算内，要么回落全量原样」；
   实测 `fits=547, falls_back=1633`，无违例。`total(n) = [1545, 1634, 1725, 1816, 1907, 1998, 2089, 2180]`。
3. **终态优先级**：`unknown` 压过 `prompt_too_large`（实测批次 `failed`/`network_unknown`）；预算类 `skipped` 保持 `applied`。
4. **AC5 泄露面**：指标响应不含空间名/人物姓名/prompt 原文；新 fixture 用例 `security_case=false` 确实不计入硬安全门禁
   （`aggregate([case])["security_cases_total"] == 0`）；`evidence_hash` 前后稳定。

## 未运行 / 无法核实的项

- **未跑真实模型质量评测**：fake transport 只证明程序合同（投影 / policy / 封闭 schema 校验）。**不声称候选质量已改善**。
- **未读生产库**：不声称线上事实分布已越过约 478 条阈值，也不声称线上已发生该降级。
- **未做生产部署**（S7 需单独授权）。
- **未跑前端门禁**：本任务未改前端代码。`system-admin-frontend/src/api/steward.ts` 的 `StewardStatus` 接口不声明
  `metrics` 字段，新增的 `assist_skipped*` 计数不影响前端解码（`expectObject` 允许额外字段）。
- 隔离 API smoke 脚本与浏览器验收未运行：本任务无 API 形状变更（仅新增两个只读计数），
  且无前端交互变更，相关证据由定向回归覆盖；如需可另行补跑。
