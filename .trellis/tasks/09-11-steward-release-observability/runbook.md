# Runbook — Steward 运维手册（09-11）

> 面向部署/值守。开关诊断 → 积压处置 → 故障恢复 → 回滚，全部操作不需要读取家庭内容。

## 1. 开关矩阵与状态诊断

`GET /admin-api/v1/steward/status`（:8002，需管理员会话；读状态不受引擎启用门禁）。

| state | 含义 | 处置 |
|---|---|---|
| `disabled` | `STEWARD_ENABLED=0`。不登记不执行；模型设置状态不影响本判定 | 需要管家则开启核心开关并重启 |
| `paused` | 核心启用但 `STEWARD_WORKER_ENABLED=0`：作业可排队不执行 | 积压可见（queue_backlog 告警），恢复 worker 后追补 |
| `running` | 正常 | — |
| `degraded` | running 但出现安全错误码 / queue_stalled（worker 启用却连续两个扫描窗口无结算进展） | 按 §2 排查；多数是 worker 循环未随 HTTP 进程启动 |

环境变量（全部有启动校验，越界拒启）：`STEWARD_ENABLED`、`STEWARD_WORKER_ENABLED`、
`STEWARD_SCAN_INTERVAL_SECONDS`、`STEWARD_SCAN_MAX_JOBS_PER_TICK`、
`STEWARD_RETRY_BACKOFF_FIRST_SECONDS`、`STEWARD_RETRY_BACKOFF_SECOND_SECONDS`、
`STEWARD_RERUN_COOLDOWN_SECONDS`、`STEWARD_ALERT_QUEUE_SECONDS`（0=自动）、
`STEWARD_ASSIST_*`（超时/字节上界/批租约/并发）与三类辅助开关。
模型辅助有效 = 平台开关 AND 空间级开关；只开平台不改变核心 state。

## 2. 积压与告警处置

- `alerts[].code=queue_backlog`：最老 queued 年龄超过阈值。先看 `metrics.oldest_queued_age_seconds`
  与 `metrics.core_queue_depth`；worker 关闭属预期（paused），否则按 §3 查进程。
- `alerts[].code=queue_stalled`：worker 已启用但无结算进展 → 进程存活但循环死亡的形态，
  重启 api 进程（lifespan 重建维护循环）；多 listener 共享单循环，无重复执行风险。
- 人工重跑：`POST /admin-api/v1/steward/spaces/{space_id}/rerun`
  （header `Idempotency-Key`，body `{reason, expected_policy_version}`）。
  同键重放返回同一作业；单空间默认 60s 冷却（429）；策略版本冲突 409；引擎关闭 503。
  reason 只入审计分类码，不存原文。
- `metrics.budget_reserved_tokens > 0` 持续增长：有辅助批次长期停在 reserved/in_flight；
  批租约（`STEWARD_ASSIST_BATCH_LEASE_SECONDS`，默认 120s）到期后由恢复器按 unknown 收敛；
  长期不收敛说明执行线程反复崩溃，查日志 `steward assist batch N crashed (error=<类名>)`。

## 3. 失败语义速查

| 现象 | 语义 | 自动行为 |
|---|---|---|
| job `failed` + `error_code=STEWARD_DB_LOCKED` 类 | 可重试（锁/暂时资源） | 退避 5s/30s 回队，max_attempts 3 |
| job `failed` + 其它 error_code | 确定性（输入/权限） | 终态；其他空间不受影响 |
| 辅助 call `failed/invalid_response` | 上游响应不可用 | 保守计费；批次 failed，产物不应用 |
| 辅助 call `unknown/timeout`、`network_unknown` | 无法证明上游未处理 | 保守计费；**不自动重发**；需人工决定 |
| 旧 lease 覆盖被拒 | 租约栅栏（owner+attempt+deadline） | 新持有者继续；旧执行者结算被丢弃 |

## 4. 部署顺序与回滚

**上线**：1) `alembic upgrade head`（0036→0038 均为纯增量：新表 + 加列，旧代码兼容）；
2) 开启核心可靠性/读取保护（默认开启路径）；3) 观察核心队列收敛；
4) 需要模型辅助时按单类 opt-in（candidate → ranking → explanation 逐类开）。

**回滚（反序）**：1) 关闭三类辅助与空间开关；2) 关闭 worker（paused，保留队列）；
3) 关闭 `STEWARD_ENABLED`；4) 代码回滚无需 DB 回滚——0036/0037/0038 新列对旧代码
不可见即安全；确需 downgrade 时：停 worker → `alembic downgrade`（0038→0035 各迁移
带 down，旧数据保留；见 `scripts/steward_migrate_roundtrip.py` 的验证口径）。
候选 UI 下线不撤销已提交的领域提案；已确认事实不随辅助回滚消失。

## 5. 备份恢复

- 常规：SQLite 备份文件 + `alembic upgrade head` 前向迁移（**首选**；
  备份版本 ≤ 当前 head 时迁移链自动补齐新表）。
- 谨慎：验证过的 downgrade 到备份版本再前向；**绝不**在业务库执行 `downgrade base`
  （测试 conftest 的口径只适用于其一次性临时 DATA_DIR）。
- 恢复后：`GET /admin-api/v1/steward/status` 确认 disabled→running、首轮扫描追补
  （`metrics.last_scan_at` 更新、积压清空）。

## 6. 本地验证脚本（临时 DATA_DIR，不触业务库）

```bash
cd backend
.venv/bin/python scripts/steward_e2e.py                # 端到端验收 → .steward-e2e-evidence.json
.venv/bin/python scripts/steward_capacity.py           # 容量采样 → .steward-capacity-evidence.json
.venv/bin/python scripts/steward_migrate_roundtrip.py  # 迁移往返
.venv/bin/python -m pytest -q                          # 全量回归
```
