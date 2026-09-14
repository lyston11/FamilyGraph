# 称谓能力闭环：交付后质量审核与修复

日期：2026-09-14。

用户授权直接审核、修复并提交、合并、推送、归档，本轮不创建新 Trellis 任务。原父任务及两个子任务已归档，本记录补充本轮实际修复与验证；原 `validation.md` 保留首次交付的历史结果。

## 交付版本

| 提交 | 范围 |
| --- | --- |
| `c8805bf` | 后端称谓方向、授权与状态一致性、偏好生命周期、模型执行与恢复 |
| `d26bb83` | 前端称谓入口、状态解码、缓存失效与异步响应隔离 |

两笔功能提交已串行合入 `main`。审核 worktree `../fg-steward-kinship-quality-0914` 和分支 `fix/steward-kinship-quality-0914` 已清理。

## 审核发现与修复

| 缺口 | 修复结果 | 主要实现与回归证据 |
| --- | --- | --- |
| 亲子方向、第三人参照和通知可见性存在边界错误 | 依据查看者与关系端点生成正确方向；通知复用当前授权下的个性化呈现 | `kinship_presentation.py`、`notifications.py`；`test_steward_suggestion_quality.py` |
| 私人称谓建议、已提交提案与推测树的有效状态不一致 | 按账号隔离私人建议；读取当前建议、提案和推测状态；校准证据文案并去重已确认的相同关系，持久化解决结果 | `steward_suggestions.py`、`steward_inferred.py`；`test_steward_suggestion_quality.py` |
| 保留与恢复操作可能消费过期语义，长链建议不能完整保留 | 操作时重验当前关系语义与 CAS；支持保留推导长链称谓；相关个人家族视图在同一事务刷新 | `steward_terminology.py`、`terms.py`、`personal_family_view.py`；`test_steward_terminology_quality.py` |
| 模型输入与写回校验可能遗漏当前事实、偏好及年龄变化 | 使用当前事实版本、偏好和年龄输入，严格复核模型输出及写回条件 | `steward_terminology.py`、`steward_assist.py`；`test_steward_terminology_runtime_quality.py` |
| 跨作业重试、种类轮转和租约恢复存在执行缺口 | 重试预算跨作业生效；落实种类轮转；重读数据库并核验实时租约；持久化未知结果恢复时不重复发送；跳过已结算失败 | `steward_assist.py`；`test_steward_assist.py`、`test_steward_terminology_runtime_quality.py` |
| 无关推荐卡读取可能误使纯称谓结果失效 | 将相关输入变更与无关读取区分，保留仍有效的称谓结果 | `steward_assist.py`、`steward_terminology.py`；`test_steward_terminology_runtime_quality.py` |
| 人物档案入口与前端异步状态未闭合 | 接入实际人物档案称谓区；隔离账号、空间、目标切换后的迟到响应；正确解码终态并刷新相关缓存 | `PersonProfileView.vue`、`KinshipTermPanel.vue`、建议弹窗与 stores；前端对应回归测试 |

本轮同时修正 0042、0044 迁移文件的格式；未新增迁移或改变其数据库语义。已有的 0041 迁移未提交改动原样保留。

## 验证结果

以下检查已在本轮功能交付阶段执行。收尾阶段仅补充归档与会话记录，没有重新运行无关或高成本检查。

| 检查 | 结果 |
| --- | --- |
| backend 全量 `pytest -q` | **1119 passed, 3 skipped**，42.56 秒；3 项为 `test_m4b_admin.py` 中按既有 PRD 延期的 break-glass 测试 |
| backend `mypy app` | **187** 个源文件通过 |
| 主检出 backend `ruff check .`、`ruff format --check .` | 通过，**335** 个文件格式合规 |
| frontend 全量测试 | **68** 个文件、**618 tests passed** |
| frontend lint、build | 通过；build 包含 `vue-tsc --noEmit` |
| 合并到 `main` 后受影响 backend 测试 | **59 passed** |
| `git diff --check` | 通过 |

前端最终测试日志位于 `/tmp/familygraph-kinship-review-frontend-final.log`，属于本机临时证据。

## 集成与保留边界

- 合并前记录的 **148** 个已有脏文件/未跟踪文件，在合并后及本次收尾前逐文件哈希核验一致；既有暂存区状态保持一致。
- 本轮提交只包含审核修复及其归档、会话记录；其他进行中的任务、worktree、配置和未提交业务改动继续保留。
- `main` 中已有四笔其他任务的元数据提交作为历史祖先，正常推送随分支一并前进，不重写历史。
- 本轮没有活动 Trellis 任务，因此在原父任务归档目录补充本记录，并通过 `add_session.py` 记录会话。

## 验证限制与后续边界

- 模型链路通过 fake transport 验证；真实模型输出质量与生产环境 smoke 未验证。功能开关和生产配置保持原值。
- 本轮没有修改管理员前端，因此未重跑其测试与构建。
- 原 PRD 明确延期的关系提案最终确认成环、证据留存等独立问题，仍不属于本轮已修复范围。

