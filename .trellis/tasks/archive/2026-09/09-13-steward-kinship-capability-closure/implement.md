# Implement — 总任务执行与验收

## 开始前

- [x] 核实四项能力生产路径和历史意图，见 research/audit.md。
- [x] A → B 依赖和共享文件边界写入子任务。
- [x] 完成 PRD 收敛、任务对齐和真实 manifests；规划校验见 research/planning-validation.md。
- [x] 提交最终规划摘要，按 workflow-state 要求取得评审回复后再 start。（用户 2026-09-14 明确指示全量执行，视为评审放行）

创建和执行范围已获授权，不再请求任务创建许可。最后一项是当前 Trellis 的阶段评审要求。

## 执行顺序

1. 在主检出核对 origin/main、dirty 文件、活跃 worktree、migration heads。保留无关修改，不 reset/stash 他人工作。
2. task.py start 后读取真实 task.json/worktree_path；业务改动只在任务 worktree，生命周期命令留主检出。
3. 先完成 A。（用户明确要求不开子代理，A/B 均由主会话直接实施；检查以本地命令等价执行。）
4. 与 topology 串行交接共享文件，核验 A 的合同后提交并固定备份引用，按唯一集成通道集成。
5. B 以已验证的 A 和最新平台治理为基线，先迁移/语义/投影，再作业/写回，再前端反馈/配置。
6. 真实 Steward + fake transport 验 B；复核 A 的关键场景无回退。
7. [x] 逐条回填 AC-01～15 的版本/命令/结果/证据等级，见 research/validation.md。
8. [x] 主检出串行集成（ad10dd0 → 9baf659 已在 main），归档后清理见交付说明。

## 验证

有迁移时先用独立 DATABASE_URL 执行 alembic upgrade head，测试不能共用运行中的 SQLite 或其他任务端口。

各命令在对应 package 独立执行：

| 层 | 最小充分检查 |
| --- | --- |
| backend | ruff check、ruff format --check、mypy app；相关 suggestions/notifications/PFV/kinship/inferred/assist/guard/governance pytest；新增 job 链路与迁移回归 |
| frontend | npm run lint、type-check、相关 Vitest、build；通知/称谓/家族树交互 |
| system-admin-frontend（B） | npm run lint、type-check、平台开关 Vitest、build |
| 跨层 | 真实 API + 合成家庭 + fake transport；通知旧详情/切账号/自动生效/偏好恢复/窄屏 |

测试文件精确名和命令由子任务验证记录提供。完整后端 pytest 若高成本则说明未执行范围；已通过后无新变更不重复全量。需要 smoke 时使用 scripts/frontend-api-smoke.sh 的隔离环境，退出码 2 记环境阻塞，不算通过。

## 风险和回退点

- 主检出落后且 dirty：start/集成时重核，fetch + merge，不把整个工作区加入提交。
- A/B/topology 共用 PFV/schema/类型：串行交接，以完整响应兼容回归验收。
- platform/memory 共用配置和 migration：串行分配新编号，遗漏字段保持现值。
- 合法 schema 但错误称谓：语义反例、版本/权限复核，失败回退，不转人工审批。
- 输出刷新触发循环：输入/输出版本分离、同依据去重、无逐条通知、恢复抑制测试。

实施结果尚未产生。规划校验独立写入 research/planning-validation.md；只有实际产物和检查完成才记录功能完成。
