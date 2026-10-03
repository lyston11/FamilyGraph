# 运行时多租户资源隔离与重试预算

## Goal

在不等待数据库迁移完成的前提下，先为 Assistant(account) 与 Steward(space) 建立可证明的运行时资源隔离、控制面保留容量、公平调度和单一 run-level retry/time budget。

## Scope

- Assistant 以 `account_id`、Steward 以 `space_id` 为 tenant key；同时支持 global、kind、control-plane 预算。
- 分离 control/model/tool/background limiter 或 worker pool；控制请求不等待模型流、工具突发和索引维护。
- 消除 provider retry 与 session retry 的乘法放大，定义错误类别、总尝试次数、总墙钟预算、退避和 circuit scope。
- 增加不含 prompt、正文、凭据和 SQL 参数的 queue/saturation/retry 诊断。
- 在当前 SQLite 环境先做运行时并发实验，但不改变 SQLite 作为过渡存储的定位。

## Dependencies / non-dependencies

- 依赖父任务定义的 resource key、控制面优先、kind/token/fence/settle 合同。
- 不依赖 PostgreSQL、Redis 或 pgvector 实现即可完成第一阶段的运行时测试；后续必须在 PostgreSQL 迁移后的连接/事务模型上复验。
- 不得把 Redis 或 sidecar 内存状态当作持久 run/lease 真源。

## Acceptance Criteria

- 单 account/space 的突发不能占满另一 tenant、另一 kind 或 control-plane 的保留容量。
- heartbeat/lease/cancel/settle 在 model/tool/retry 过载下注入时仍在有界时间内完成。
- 单个 provider/DERP 故障只消耗该 upstream/kind/tenant 的 retry budget；不形成 6×4 无界乘法。
- 取消、撤权、租约恢复、egress 一次一审计、fence 和 settle 合同回归通过。
- 真实并发矩阵与故障注入证据写入任务 research，不以单 run 成功代替验收。
