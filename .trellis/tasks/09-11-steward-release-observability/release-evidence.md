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
  - 新状态数据（`unknown`/`in_flight` attempt + `steward_suggestion` 通知）：
    downgrade 0035（先保守收敛/删除不可兼容投影）→ re-upgrade，数据与约束可恢复；
    该场景由 roundtrip 脚本第 4 步覆盖。
  head 链：`0035_space_lineage_link → 0036 → 0037 → 0038_steward_suggestions`。
- 关闭→重开：E2E 场景 12/13（上表）。
- 恢复：备份恢复 runbook 见 [runbook.md](runbook.md)；**绝不**对业务库执行 conftest 的
  `downgrade base`。
- 发布顺序（runbook）：schema → 核心可靠性/读取保护 → 辅助执行/安全验证 → 候选审核 →
  单类辅助 opt-in；关闭按反序。

## 6. 全量回归门禁

- `cd backend && .venv/bin/python -m pytest -q` → **963 passed, 3 skipped**（退出码 0；含审计修复回归）。
- `ruff check .` 全仓 0 错误；`ruff format --check .` 全仓通过；`mypy app` 0 错误（原 5 个既有错误已修）。
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

## 8. 审计修复轮（2026-09-12）

外部审计确认四项发布/正确性阻断与若干低级风险，全部修复并回归：

| 阻断 | 修复 |
|---|---|
| 全局事件广播到所有空间（调度器对无 space_id 事件 `SELECT all FamilySpace`） | 作用域权威上移 `domain_events.resolve_event_space_ids`：事件空间 ∪ payload 空间 → 全局人物事件按 active membership/ref/桥接受权范围收敛；解析不出空间不登记作业。回归：`test_global_source_fact_event_scopes_to_authorized_spaces` |
| 桥接事件只给 A 空间入队（payload.space_ids 被忽略，B 侧只能等周期扫描） | 调度器统一走同一解析合同，payload.space_ids 并入。回归：`test_bridge_event_enqueues_both_sides` |
| 租约过期后仍提交 succeeded（结算前无 deadline 复查） | `run_steward_job` 结算前复查 `lease_expires_at`，过期则整体回滚（含本事务派生写入），由 reaper 按过期回收重队。回归：`test_settle_rejected_when_lease_expires_during_execution`。配合容量证据（200 人 4.6h ≫ TTL 300s），独立 worker/增量计算 follow-up 优先级提升 |
| 生产数据上迁移回滚不可用（0038 建议通知违反旧 kind CHECK；0037 unknown/in_flight 违反旧 status CHECK） | 0038.down 先删建议通知（可再生投影，应急路径，仅下行）；0037.down 先把 reserved/in_flight/unknown 收敛为 failed（`downgrade_forced`，保留计费行）。`steward_migrate_roundtrip.py` 扩展第 4 场景（真实新状态数据 down→up）实测通过 |

低级风险关闭：

- PFV recompute 入队失败日志 `exc_info=True` → 只记异常类名（SQL 绑定参数不外泄）。
- 辅助预算参数纳入启动校验：`STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB / MAX_TOKENS_PER_JOB / MAX_CARDS_PER_JOB` 加上下界，`STEWARD_ASSIST_TIMEOUT_SECONDS` 限 [0.1, 300]，越界拒启。
- findings 证据 user-id/fact-id 混用：证据快照改按 pair 用户端点匹配事实，并在快照中记录事实端点。
- 畸形 finding pair 导致整批投影跳过 → 逐条 try/skip。
- 建议分页过滤欠返 → over-fetch 循环；每批消费后推进内部游标，集满时游标落在最后消费行，不丢行也不重复查询。
- 确定性 finding 显式端点、布尔型畸形 pair 与空间维度 memory/RAG 事件均有回归覆盖；后者不登记 Steward 作业。
- Steward 事件窗口复用 `resolve_event_space_ids`，过滤无关全局及 memory/RAG 事件；
  `test_consume_window_filters_unrelated_global_and_memory_events` 回归通过。
- 评测/E2E 证据 JSON 保持 gitignore：属可再生本地产物（内含临时路径），由脚本随时重建；发布声明引用命令 + 退出码，不依赖入库的历史工件。

任务元数据同步：七个任务由 completed 改回 in_progress（release_gate=partial），子任务
implement.md 清单补记实际完成项，父/延期任务相对链接随解除归档恢复有效。
