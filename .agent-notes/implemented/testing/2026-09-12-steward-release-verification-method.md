# Agent Note: Steward 发布验证用隔离 E2E 驱动 + 预算化容量采样 + 哨兵脱敏断言

Status: implemented

## Problem

此前的"验证通过"声明建立在直接调用 `run_steward_job` 的单测与历史会话报告上：调度路径没有被端到端执行过，容量只有拍脑袋数字，日志脱敏没有可重复的反证手段。发布门禁既无法证明全链路，也无法证明没有秘密外泄。

## Decision

`backend/scripts/` 三个隔离驱动（全部临时 DATA_DIR + 迁移链建库，不触业务库与 .env）：

- `steward_e2e.py`：经真实 API（家庭 :8000 + admin :8002 TestClient）走注册/认领/建档/确认关系 → 真实调度路径 `run_maintenance_tick`（绝不直调 run_steward_job）→ job/PFV/卡片/通知/建议提交与确认 → 撤权、畸形/超时 provider 注入、崩溃恢复、关闭重开；证据 JSON 只含 ID/状态/计数；
- `steward_capacity.py`：合成空间 full-pair 重算采样。实测发现单对重算秒级、200 人单对分钟级——预算改为**逐对生效**（cold/warm 各自限时截断），事实播种用 Core 批量插入（ORM unit-of-work 构造 19900 对象本身即分钟级瓶颈，播种不是被测操作），外推按实测对数线性折算并显式标注 `extrapolated`；
- `steward_migrate_roundtrip.py`：空库与旧数据双向往返（up→down→up），校验行数/索引/历史保留。

配套约定：异常日志只记关联 ID + 异常类名 + 安全错误码（`tests/test_steward_log_hygiene.py` 用合成姓名/手机号哨兵断言零外泄）；真实 provider 未运行的门禁报告一律标 partial，不得写 completed。

## Alternatives considered

- **全矩阵实测容量（50/200 人全部有序对）** — 数字最硬，但实测单对秒级 → 200 人全矩阵为小时到天级，不可用于会话内验证；改用采样 + 显式外推标注，并把"超过 lease TTL"作为 follow-up 触发器记录。
- **用单测直调 service 凑 E2E** — 快且稳定，但绕过调度/租约/事务边界，正是本轮要消除的证据形式；AC 明确不接受。
- **日志断言只查关键函数不查全链路** — 覆盖面小，回归容易从新分支漏出；哨兵断言覆盖 execute/maintenance/admin 响应面。

## Consequences

- **收益**：发布声明有可重复执行的证据链（命令 + 退出码 + JSON 证据文件）；容量外推诚实标注，超限触发独立 worker/增量计算的后续研究而不是拍脑袋。
- **代价与已知上限**：E2E 依赖进程内 TestClient，不含真实网络栈与真实 provider（此部分证据缺失需在 release-evidence 显式声明）；容量数字随硬件变化，证据文件记录硬件指纹但不可跨机比较。

## Verification

`scripts/steward_e2e.py`、`scripts/steward_capacity.py`、`scripts/steward_migrate_roundtrip.py` 退出码 0；证据文件 `backend/.steward-*-evidence.json`（已 gitignore）；`.trellis/tasks/09-11-steward-release-observability/release-evidence.md` 记录 stub/真实 provider 分列与缺口清单。

Note: 容量采样与脱敏断言的方法合同 — 见 .agent-notes/implemented/testing/2026-09-12-steward-release-verification-method.md
