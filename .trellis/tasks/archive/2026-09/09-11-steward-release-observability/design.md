# Design — Steward 端到端发布门禁与可观测性

> 本文是待实施技术方案，不是当前代码行为；现状证据见父任务 findings。

## 指标与状态归属

ops 实现 status/jobs/rerun API；本任务消费并完善观测，不重复新建 API。指标：core_queue_depth、oldest_queued_age、last_scan_at、last_worker_tick_at、core_failed、assist_failed/degraded/unknown、budget_reserved/consumed、pfv_stale、cards_created/superseded。向外聚合必须按主体权限，严禁向普通成员返回别的空间计数。
worker 指标需能区分配置关闭与故障；默认 oldest_queued_age > max(2×scan_interval,60 秒) 且核心已启用视为告警，最终阈值留在可配置 runbook。

## E2E 环境

本地验证使用独立 DATA_DIR 和 synthetic seed，启动三 listener 与必要前端并按项目 dev-up 约定；不改用户业务库和 .env。后台自动 tick 不用测试直接手动执行替代。fake provider 用受控协议服务提供成功/超时/畸形响应；真实 provider smoke 使用合成家庭和既有授权配置，记录 profile/status/字节数/usage 摘要。
原 46+16 测试是 09-09 本会话报告，09-11 只规划未重新运行；不能写成本次新通过。

## 日志修复

替换 steward.execute 的 str(exc)[:500] 与 logger.exception 可能携 SQL 参数的异常输出，保留 safe_error_code/request_id/job_id。审计可存必要内部 ID，但不存 provider Authorization、原始 HTTP 响应、数据库 statement 参数。测试使用合成 secret 哨兵进行精确否定断言。

## 发布与回滚

发布顺序 schema → 核心可靠性/读取保护 → 辅助执行/安全验证 → 候选审核 → 单类辅助 opt-in。关闭反序进行；候选 UI 下线不撤销已经提交的领域提案。恢复使用 backup + migrate forward 或经验证 downgrade，绝不对实际业务库跑测试 conftest 的 downgrade base。
