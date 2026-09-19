# 验收摘要

任务：`09-19-steward-recommendation-correctness`
状态：代码与隔离验收完成，**未部署**。

## 结果

| 层次 | 结果 |
| --- | --- |
| 定向回归（7 个文件，194 用例） | 通过 |
| 完整后端 | `1760 passed, 3 skipped`（3 项为既有 break-glass 延期测试） |
| `ruff check` / `ruff format --check` | 通过（404 文件） |
| `mypy app` | `Success: no issues found in 207 source files` |
| 隔离 API smoke | 56/56 通过，退出码 0 |
| 隔离浏览器验收 | 通过（见 `evidence/browser-acceptance.md`） |
| 隔离 dry-run 工具 | 合成样本命中正确，仅 SELECT |

## 两条缺陷的修复

1. **亲子/祖孙被推荐为兄弟姐妹**：新增 `services/steward_candidate_policy.py` 确定性负向判据（直接亲子双向、收养/继亲保守抑制、生物祖先长链、预算 fail-closed），接线到建议投影、推测边投影、读取有效状态、submit、confirm/reinstate、overlay 缓存消费与 `inferred_review` 复核。
2. **已共享家庭仍推荐共建**：新增 `steward.share_active_household()` 跨空间布尔查询，替换原「仅当前空间为 household」判据；贯穿生成、读取列表、通知、accept、execute（`BEGIN IMMEDIATE` 内重验）与后台复核。

## 自审发现并修复

主会话自查（未派子智能体）发现 1 处真实缺陷并修复：`source_state` 冲突判据优先级过低，会把
真实已确认（`resolved`）的线索改判为 `superseded`（通知域显示「已撤销」，违反 R8）。已调整优先级
并补永久回归；另撤销了一处经验证为死代码的 PFV 过滤。详见 `evidence/independent-check.md`。

## 覆盖的验收标准

AC1–AC9 均有对应用例或隔离实测（映射见 `evidence/validation.md`、`evidence/browser-acceptance.md`
与 `evidence/independent-check.md`）。

## 未做 / 限制

- **未部署**：代码通过不等于线上生效。线上收敛属 S7，需单独授权。
- **未读取生产库**：不声称线上对象数量或状态；正式处理须用 `scripts/steward_recommendation_dryrun.py` 逐条重验，禁止按旧数字（26/21）筛选。
- **未做真实模型质量评测**：本任务屏障为确定性代码。
- 未创建新迁移；`alembic head` 保持 `0051`。
- 前端无代码改动。

## 关联后继任务

`09-19-steward-candidate-prompt-contract`（planning）：补齐候选 prompt 的方向语义/矛盾禁止/示例，并让事实规模超限不再静默记为 `applied`。与本任务共享 `steward_assist.py`/`steward_guard.py`，须在其集成后串行执行。
