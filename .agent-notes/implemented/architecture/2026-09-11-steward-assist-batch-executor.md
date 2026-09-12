# Agent Note: Steward 模型辅助走三阶段批次执行器，HTTP 永不进入业务写事务

Status: implemented

## Problem

Steward 的候选/排序/解释三类模型辅助原先在 core 执行器的 BEGIN IMMEDIATE 写事务内经 SAVEPOINT 同步调用 HTTP。SQLite 外层写锁在整个模型调用期间不释放：慢 provider 把全库写入阻塞数十秒，其他空间与普通请求全部停摆；发送后进程崩溃还会丢本地调用记录，重试重花 token 且预算只计 succeeded，配置上限形同虚设。

## Decision

`services/steward_assist.py` 以批次（`StewardAssistBatch`，job 唯一）为调度单元的三阶段执行器，迁移 0037：

1. core 短事务提交确定性结果，并在**同一事务**登记批次与预算预留（attempt 行状态机 `reserved → in_flight → succeeded/failed/degraded/skipped/unknown`，唯一键 `(job_id, assist_kind, subject_key, input_hash, attempt_no)`）；
2. maintenance tick 泵干 core 后至多调度一个到期批次，HTTP 在有界线程池 + 独立 Session 中执行，受墙钟 deadline 与单次 timeout 双重上界，响应体流式读并有字节上限；
3. 预发送与应用前各一次短事务栅栏（空间设置、provider、policy、证据 digest/revision、卡片状态、受众、租约），任何变化 skip/supersede；
4. 计费保守：usage 缺失/负数/部分缺失按预留结算；读超时等无法证明上游未处理的结局记 `unknown`，不自动重发。

## Alternatives considered

- **保持事务内联但给 HTTP 设短超时** — 改动最小，但写锁仍随最慢调用被占用，任何 provider 抖动都会复现全库停摆；故障注入证明第二连接无法写入，故否决。
- **引入独立 worker 进程 + Redis/消息队列** — 隔离最彻底，但无容量证据支撑新增部署面，违背本轮"不为完整性直接部署新组件"的边界；保留为容量数据触发后的后续项。
- **不修，继续 SAVEPOINT 内联** — 依赖 provider 可用性，崩溃重复计费与预算失效已核实为缺陷，不满足生产可靠性要求。

## Consequences

- **收益**：core 结果与辅助成败解耦——慢模型期间其他空间确定性作业照常推进（有第二连接写入侵扰测试）；四个崩溃点均可由批次状态机恢复；预算上限为真实硬上界。
- **代价与已知上限**：辅助调度每 tick 至多一批，吞吐受 tick 间隔限制；`unknown` 结局不自动重发意味着偶发网络抖动需要人工或下轮事件触发；跨外部 HTTP 不承诺 exactly-once 计费（上游幂等键支持未证实前禁用自动复用请求标识）。

## Verification

`backend/tests/test_steward_assist.py` 覆盖慢 transport + 第二连接写入、四崩溃点恢复、预算封顶、栅栏变化不应用；`scripts/steward_e2e.py` 在隔离 DATA_DIR 复现成功/畸形/超时三模式。

Note: 辅助 HTTP 的唯一合法执行路径 — 见 .agent-notes/implemented/architecture/2026-09-11-steward-assist-batch-executor.md
