# 实施计划：池与事件循环可观测性

## 状态

planning。前序三个缺陷中两个已修（事件循环阻塞、token 过期），
第三个（池尖峰）已定位现象、未测出持有者。本任务先补可观测性，再据此定位。

## P0 可观测性（R0）

- [ ] `app/services/runtime_diagnostics.py`：池等待测量（包装 `engine.connect`）
      与事件循环延迟探针（asyncio 任务）。
- [ ] 配置：`DB_POOL_WAIT_WARN_MS`(1000)、`EVENT_LOOP_LAG_WARN_MS`(500)、
      `EVENT_LOOP_LAG_INTERVAL_S`(1.0)，`ensure_ready` 校验边界。
- [ ] 接入 `app/db.py`（池等待）与 `maintenance_loop`（loop 探针，随其取消退出）。
- [ ] 日志字段仅安全标识；事件名 `db_pool_wait` / `event_loop_lag`，
      不出现推测性命名。

## P1 测试（含区分性）

- [ ] 池满时产生 `db_pool_wait`，`waited_ms ≈ pool_timeout`。
- [ ] 事件循环被同步阻塞时产生 `event_loop_lag`。
- [ ] **区分性断言**：仅池满（loop 空闲）时只有 `db_pool_wait`，无 `event_loop_lag`。
- [ ] 日志不含敏感字段（grep 断言）。
- [ ] 变异验证：删阈值判断 → 用例失败。

## P2 据此定位持有者（AC-1）

- [ ] 部署开发环境，采样真实负载下的 `db_pool_wait` / `event_loop_lag`。
- [ ] 测出池尖峰时刻的持有者、持有阶段与时长；记录触发条件。
- [ ] **未测出持有者前不实施收敛方案**。

## P3 收敛（取决于 P2）

- [ ] 按证据选择：缩短持有时间 / steward 分离池 / 限制并发叠加。
- [ ] **不调大池上限**（SQLite 单写者下只增锁竞争）。
- [ ] 回归 fence/幂等/结算/Provider 合同。

## P4 收尾

- [ ] backend 全量 pytest、ruff、mypy；agent 未改则说明。
- [ ] 更新 spec：记录两个指标与「池满 vs loop 阻塞」的判据。
- [ ] 逐项记录 AC 证据；提交、串行集成、归档、清理 worktree/分支。
- [ ] 线上未操作。

## 回退

诊断可独立回退（移除模块与配置，恢复原 `engine.connect`）。无 schema/迁移变更。

## 验收映射

| 验收 | 证据 |
|---|---|
| AC-0 | `db_pool_wait` / `event_loop_lag` 结构化告警 + 无敏感字段断言 |
| AC-1 | 真实负载下的持有者证据（P2） |
| AC-2 | 探针不依赖工作线程/连接（池满时仍记录，有测试） |
| AC-3 | 收敛后心跳不再被拖过期（若证据支持） |
| AC-4 | backend 全量检查 |
| AC-5 | Provider/fence 回归 |
