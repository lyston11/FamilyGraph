# C 技术设计

## 边界与现状

入口保持 `execute_batch → transport → _settle_attempt → _apply_batch`；网络处于有界 assist worker 的独立 Session 之外。只替换共用 transport 的截止机制及发送预算，不改变结果应用权限。先读取 `steward_assist.py`、`steward_runtime.py` 的调用与 shutdown 路径，检查注入 transport 的所有测试调用者。

## 预算模型

1. 发送事务内复核 lease owner/attempt/deadline、业务输入与预算，计算固定请求截止。
2. `request_budget = min(configured_timeout, remaining_lease - settlement_reserve)`；若不能覆盖最小发送窗口，保持未发送并释放预留。
3. 事务提交后再次检查剩余时间，将事务等待扣除，不重新授予完整 timeout。
4. 发出请求后 deadline 不随 chunk、重试、解析或时钟回拨延长。本地时长使用 monotonic；租约仍以服务端 UTC 权威复核。
5. 收到结果后原短事务重验执行身份；预留是减少失租窗口，绝不是越过租约的许可。

预留值先结合既有 SQLite busy timeout 和短事务机制在受控锁竞争下验证；不能预留超过全部短租约而使合法批次永久饥饿。以现有配置/内部常量的最小改动为先，若需新运维项须写明默认值与上下限后再次评审。

## Transport 方案与技术门

首选验证已安装 HTTPX AsyncClient 配合 Python 结构化取消/绝对截止，覆盖 connect/write/headers/read，而非创建超时后遗留工作的线程。同步入口只在其既有工作线程内桥接事件循环；不得在持有业务写事务时调用，不扩展无界线程池。

这仍需证明 DNS、底层连接、流关闭、解压/解析及 shutdown 都可收敛。同步 JSON 解析不能由 event-loop timeout 抢占：保持响应字节上限，在处理前后核对 deadline，超时不接受成功，并量化有界处理超差。若仍不能满足 C-R1 的总预算，须换用可回收的隔离执行方案并补设计，不能把 `asyncio.timeout` 名称当作证明。

仅重新设置每阶段 timeout，或 `Future.result(timeout)` 后放任线程执行，都不是合格备选。进程退出能关闭本地资源不等于远端未处理，发送后未知仍保守计费。

## 状态矩阵

| 情形 | attempt / 应用 |
| --- | --- |
| 预留不足、出事务已到截止 | 未发送；释放预留，非 unknown |
| 网络已发送，截止/中断无可验证结果 | unknown，保守计费，禁止自动重放 |
| 响应有效且 lease 有效 | 逐笔结算后再发下一笔 |
| 响应到达但 lease 已失效 | 旧 worker 不结算，由恢复器接管 |
| 成功与 unknown 混批 | 成功产物独立过栅栏应用，整批仍如实 failed |
| 禁用/撤权/证据过期 | 保留安全审计，业务应用拒绝 |

## 兼容、回退与证据

原则上无 schema 改动；若发现必需字段，先单独写 nullable 迁移与旧行语义，再排迁移序号。transport 注入签名尽量兼容现有四 kind 测试。不得回滚已保存成果或复活 unknown。

回退范围为本任务代码；回退后恢复旧截止缺陷应明确记录，不能重新宣称 AC-04 通过。测试使用本地 socket 与合成数据，不调用真实 Provider。计时阈值需要单列调度容差，同时断言客户端关闭与活跃请求数，避免只用一次 sleep 证明资源回收。
