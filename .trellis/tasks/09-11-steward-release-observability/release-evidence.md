# Release Evidence — Steward 端到端发布门禁与可观测性

> 生成：2026-09-12（实施会话）；环境：macOS arm64（Apple Silicon），Python 3.12。
> 证据分级：**stub** = 本仓 fake transport / 进程内 TestClient；**真实 provider** = 本轮未运行（见文末缺口清单）。

## 1. 隔离端到端验收（R1/AC-1）— stub，已通过

命令：`cd backend && .venv/bin/python scripts/steward_e2e.py`（退出码 0）
证据文件：`backend/.steward-e2e-evidence.json`（只含 ID/状态/计数，无个人内容）

流程与结果（全部经真实 API + 真实调度路径 `maintenance.run_maintenance_tick`，未直接调用 run_steward_job）：

| 场景 | 结果 |
|---|---|
| 注册/登录/建档/建空间/成员加入 | OK（user_id 1-3，space_id 1） |
| 双向确认关系（connection accept → confirmed SourceFact） | OK |
| 自动 tick → job/PFV/通知 | job succeeded；pfv_rows=2 且 pfv_current=2；notifications=2 |
| admin :8002 观测状态（真实 DB 指标） | state=running，metrics 全量可读 |
| 模型辅助（fake provider 成功模式） | 批次 applied，call succeeded，llm_candidates≥1 |
| 建议审阅→提交（202）→端点当事人确认（200 confirmed） | OK；建议 resolved 随领域决议 |
| 撤权 | revoke 200；卡片取代路径记录（本场景无到期卡，见缺口） |
| 畸形响应注入 | call failed/`invalid_response`×2，**core 全部 succeeded**（隔离成立） |
| 超时注入（1.5s 超时 vs 5s 服务端） | call **unknown/`timeout`**（保守计费，不自动重发） |
| 进程中断恢复（过期 lease） | reaper 回队 → 重新执行 → succeeded（attempt 2） |
| 关闭期间 | 0 个新作业入队（不追补不执行） |
| 重新启用（扫描到期） | 追补停机期间遗漏事件：1 job executed，全部 succeeded |

实施会话在 E2E 中发现并修复的真实缺陷：`POST /api/steward-suggestions/{id}/submit` 路由
手工构造 `JSONResponse` 未对 datetime 做 JSON 归一（服务层单测直接调用覆盖不到渲染层），
已改为 `jsonable_encoder` 并补路由级回归
（`tests/test_steward_suggestions.py::test_submit_route_returns_json_and_replays_idempotently`）。

## 2. 可观测性（R2/AC-2）— 已实现 + 回归

`GET /admin-api/v1/steward/status`（仅 :8002）输出：
- `switches`：core/worker/relation_engine/pfv/model_assist_platform/model_assist_spaces 各自可解释状态；
  只开模型设置不改变 state（核心关闭恒为 disabled）。
- `metrics`：core_queue_depth / oldest_queued_age_seconds / last_scan_at / last_worker_tick_at /
  core_failed / assist_failed / assist_degraded / assist_unknown / budget_reserved_tokens /
  budget_consumed_tokens / pfv_stale / cards_created / cards_superseded —— 全部来自真实 DB 行。
- `alerts`：`queue_backlog`（阈值 `STEWARD_ALERT_QUEUE_SECONDS`，0=自动 max(2×扫描间隔, 60s)）、
  `queue_stalled`（worker 启用但连续两个扫描窗口无结算进展）。
- state 语义：config 关闭=disabled/paused（**不产生 stall 告警**）；worker 停而 HTTP 存活（积压 + 无进展）=degraded。
  core succeeded 与 assist failed 可分别观察（metrics 分列），失败有 `recent_error_codes` 而非“全部成功”。

回归：`tests/test_steward_observability.py`（指标来自真实行、degraded 判定、config-off 非故障、
阈值可配置、unknown 单列）。
实施会话修复：`test_config.py` 阈值越界测试未在恢复重载前删除环境变量，导致
`STEWARD_ALERT_QUEUE_SECONDS=86401` 泄漏进后续测试（monkeypatch 卸载不重载模块）——已修。

## 3. 日志与审计脱敏（R3/AC-3）— 已实现 + 回归

- steward/assist/maintenance 路径异常日志只记**关联 ID + 异常类名 + 安全错误码**；
  不输出 `str(exc)` 原文、SQL 绑定参数、模型 payload、姓名/PIN/token/key。
- 审计（admin_access_audits）只存理由分类码 + 关联 ID；`test_rerun_reason_never_persisted`、
  rerun reason 哨兵断言（姓名/手机号不入 audit/checkpoint）。
- 作业/状态响应字段白名单：job_id/space_id/cause/status/attempt/available_at/error_code。

## 4. 容量采样（R4/AC-4）— stub，本机实测

命令：`cd backend && .venv/bin/python scripts/steward_capacity.py --sample-viewers 1 --sample-seconds 30`
证据文件：`backend/.steward-capacity-evidence.json`（硬件/规模/时长/语句数/外推标记）。

实测（Apple Silicon arm64，macOS 26.2，Python 3.12；采样按实测对数线性外推，`extrapolated=true`）：

| 规模 | 事实数 | cold 每对（实测） | warm 每对（实测） | 全矩阵 cold 估算 | 全矩阵 SQL 估算 | 超 lease TTL 300s |
|---|---|---|---|---|---|---|
| 50 人 | 1,225 | 76.0 ms（50 对） | 78.8 ms（50 对） | **186 s** | 1.00M 条 | 否（临界） |
| 200 人 | 19,900 | 416.1 ms（73 对） | 382.2 ms（79 对） | **16,560 s ≈ 4.6 h** | 64.0M 条 | **是** |

- `warm_retains_majority_cost = true`（两规模均成立）：DerivedFact 缓存命中不省图构建
  （load_graph）成本，warm 每对仍付出接近 cold 的开销——瓶颈形态被数据确认。
- `followup_needed = true`：200 人全矩阵重算远超默认租约 TTL，独立 worker / 增量计算
  按规划列为数据触发的后续研究项，本轮不实施。
- 测量方法：预算逐对生效；事实播种用 Core 批量插入（实测 0.01s），排除了 ORM
  unit-of-work 的对象构建开销进入测量。

结论标记（脚本自动产出）：full-pair cold 重算是否超过默认 lease TTL 300s →
`followup_needed`；warm 相对成本是否保留 >50%（load_graph 瓶颈形态）。
超限 → 独立 worker/增量计算列为后续项（不由本轮直接实施）。

连续两窗口无进展告警：`queue_stalled` 回归覆盖（test_stalled_worker_is_degraded_not_healthy）。

## 5. 发布与恢复（R5/AC-5）— 已验证

- 配置链：`tests/test_config.py` 覆盖 STEWARD_* 9 个键的 env→config 透传、越界拒启（fail-closed）；
  compose 模板透传断言（test_compose_template_passes_steward_observability_keys）。
- 迁移往返：`cd backend && .venv/bin/python scripts/steward_migrate_roundtrip.py`（退出码 0）
  - 空库：downgrade（steward 表全部卸除）→ upgrade head；
  - 旧数据（0035 处 seed user/space/job）：upgrade head（新表建立，旧行保留）→
    downgrade 0035（新表卸除，旧数据完整）→ re-upgrade（表/索引重建，数据不丢）。
  head 链：`0035_space_lineage_link → 0036 → 0037 → 0038_steward_suggestions`。
- 关闭→重开：E2E 场景 12/13（上表）。
- 恢复：备份恢复 runbook 见 [runbook.md](runbook.md)；**绝不**对业务库执行 conftest 的
  `downgrade base`。
- 发布顺序（runbook）：schema → 核心可靠性/读取保护 → 辅助执行/安全验证 → 候选审核 →
  单类辅助 opt-in；关闭按反序。

## 6. 全量回归门禁

- `cd backend && .venv/bin/python -m pytest -q` → **956 passed, 3 skipped**（退出码 0）。
- `ruff check` / `ruff format --check`：本任务所有触及文件 clean（仓库内其余历史遗留不属于本任务）。
- `mypy app`：本任务文件 0 错误。
- system-admin-frontend：`npm run type-check`、`npm run lint`、`npm test`（83 passed）、
  `npm run build` 全部通过（StewardOpsPanel 消费 /admin-api/v1/steward/* 合同）。

## 7. 证据缺口（诚实声明）

| 缺口 | 说明 |
|---|---|
| 真实 provider 证据未取得 | 全部模型行为由受控 fake HTTP 服务证明（程序合同）；无真实 LLM 成功/降级记录。按 R5 门禁口径，本任务发布门禁为 **partial（stub-only）**，不得标记 completed。 |
| 卡片取代路径在 E2E 中无到期卡可观察 | 本场景不构造到期卡（避免人为回拨业务时间到卡片域）；卡片取代已有单测回归覆盖。 |
| 容量外推 | 200 人全矩阵实测为小时级，证据按实测 viewer 行线性外推并显式标注 `extrapolated=true`。 |
| 无外部监控平台 | 首版按 PRD 使用现有日志与后台状态；告警为 status API 内的 `alerts` 数组，无 PagerDuty/OTel 推送。 |
