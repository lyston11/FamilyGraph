# Notes — Steward 端到端发布门禁与可观测性

## 2026-09-11 规划记录

- 不以“未独立部署进程”判定管家不存在；本次保留进程内 worker，先测容量与故障域。
- 指标/日志是发布诊断，不是读取全平台家庭资料的新通道。
- F21 同时覆盖过时 docstring、归档 status 与 PRD AC 勾选不一致：新 notes 记录最新证据，不篡改旧历史。

## 决策与证据边界

- 已确认路线：确定性核心 + 可选候选/排序/解释；先可靠性再用户审核；站内通知。
- 对应条目：[F01](../09-11-steward-complete-hardening/research/findings.md#f01), [F16](../09-11-steward-complete-hardening/research/findings.md#f16), [F20](../09-11-steward-complete-hardening/research/findings.md#f20), [F21](../09-11-steward-complete-hardening/research/findings.md#f21)。
- 当前状态：规划完成待实施评审；本轮不启动。实现清单全部未勾选，不代表工作已完成。
- 09-09 会话报告核心/维护 46 passed、辅助 16 passed；这次没有重复运行测试，不能作为未来改动通过依据。

## 实施与验证约定

本轮仅生成规划，未修改上述产品代码，也未重跑历史测试。文件行号以 2026-09-11 工作区为准；实施前必须读将修改的完整函数与现行 spec。
迁移必须接实施时唯一 head，不硬编码已被并行工作使用的编号；测试只用隔离 DATA_DIR。不得把 conftest 的 downgrade base 对准业务库。

## 实施结果待记录

实施后逐 AC 追加实际命令、退出码、必要的脱敏证据及剩余问题。本节是交接记录入口，不替代 PRD 验收。

## 2026-09-12 实施记录（主会话直接执行；用户指示不再使用子代理）

背景：首次子代理派发因模型请求失败中断，其未验证产物（admin steward metrics/alerts、
tests/test_steward_observability.py、scripts/steward_{e2e,capacity,migrate_roundtrip}.py、
config/compose/README 新键）由主会话逐项核实、修复并补齐交付文档。

### 逐 AC 结果

- AC-1（E2E）：`scripts/steward_e2e.py` 退出码 0，证据 `.steward-e2e-evidence.json`。
  全链路 API→事件→自动 tick→job/PFV/通知→建议提交/确认→重算；畸形/超时注入、
  撤权、崩溃恢复、关闭重开追补均符合预期。实施中发现并修复 submit 路由 datetime
  序列化 500（服务层单测覆盖不到渲染层），补路由级回归。
- AC-2（观测）：status API 输出 metrics（13 项，真实 DB 行）+ alerts（queue_backlog/
  queue_stalled，阈值可配）+ degraded/paused/disabled 可区分；回归
  tests/test_steward_observability.py。修复该测试文件引发的 config 模块属性泄漏
  （test_config.py 阈值越界用例未先删环境变量就重载）。
- AC-3（脱敏）：steward/assist/maintenance 异常日志只含关联 ID + 类名 + 安全码；
  rerun reason 哨兵断言通过；字段白名单响应。
- AC-4（容量）：`scripts/steward_capacity.py`（viewer 行采样 + 逐对预算 + 外推标注）。
  多轮运行定位出两个测量方法问题并修复：ORM 播种 19900 对象本身即分钟级瓶颈（改 Core
  批量插入）；viewer 行切换存在挂死形态（改单 viewer 采样）。最终 50 人全矩阵估算 186s
  （临界低于 300s 租约）、200 人估算 16,560s（≈4.6h，远超租约）→ followup_needed=true；
  warm≈cold 证实 load_graph 瓶颈。详见 release-evidence.md 实测表。
- AC-5（发布/恢复）：迁移往返脚本 035→038 空库+旧数据双向验证退出码 0；
  配置链/compose 透传/越界拒启回归；关闭→重开 E2E 覆盖。
  **真实 provider 未运行：门禁为 partial（stub-only），不做 completed 宣称。**

### 命令与退出码（最终状态）

- pytest 全量：955→956 passed, 3 skipped（补测试后），退出码 0
- ruff check / format --check（本任务文件）：clean
- mypy app（本任务文件）：0 错误
- system-admin-frontend：type-check / lint / test(83) / build 全过

### 交付物

- release-evidence.md、runbook.md（本目录）
- backend/scripts/steward_{e2e,capacity,migrate_roundtrip}.py + JSON 证据文件（gitignore）

### 已知遗留（MINOR）

- E2E 最终快照 budget_reserved_tokens>0：tick 停止后 pending 批次的预留行如实上报，
  属调度语义而非泄漏；evidence 已记录。
- 200 人容量为线性外推（extrapolated=true），非全矩阵实测。
