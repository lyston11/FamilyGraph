# 实施计划：Steward 请求饥饿

## 当前状态

- **P0/AC-0 已完成**：根因是连接池与工作线程池容量错配，证据见
  `research/evidence/r0-root-cause.md`（决定性复现：池耗尽 → 请求阻塞精确等于
  `pool_timeout` 30s → AnyIO 40/40 持续被占 → 全端点静默）。
- 设计见 `design.md`；P2 的工具准入是主修复。

## 已确证的根因（实施依据）

```
连接池上限 15（POOL_SIZE 5 + POOL_MAX_OVERFLOW 10），pool_timeout=30s
AnyIO 工作线程上限 40

池 15 个连接被占 → 后续请求在 checkout 阻塞最长 30s，且**发生在 worker 线程内**
→ 40 个这样的请求耗尽全部 worker → 心跳/lease/health 拿不到线程 → 静默
```

实测（占满 15 连接后并发 40 请求）：

```
40 reqs: min=30.0s p50=30.0s max=30.0s   outcomes=['TimeoutError']
threads: t=2s..28s 持续 40/40
```

真实数据吻合：静默窗口内 lease 轮询 1414 次/2min → 6 次，之后恢复 1696 次。

## P0 定位（已完成）

- [x] 在隔离文件 SQLite + 真实 HTTP 路径复现同 run 批量工具与心跳竞争，再覆盖多个 run。
- [x] 记录请求开始/结束、采样时刻与 loop lag；在慢请求仍未结束时采样。
- [x] 区分 AnyIO worker、数据库连接等待、写锁等待；测量 borrowed/total tokens、连接占用。
- [x] 找到等待阶段（连接池 checkout）与量级（= pool_timeout 的 1–3 倍）。
- [x] 固定修复前负载与失败判据：心跳延迟预算小于现有 15s 请求超时；工具批次必须最终完成。
- [x] 修正历史错误：真实工具并发峰值 8–23（非 26）；provider 流不持有连接；事件循环不是瓶颈。

## P1 实施前确认（已完成）

- [x] 确认不改变 fence/准入/幂等/审计合同。
- [x] 已进入 worktree `/Users/lyston/PycharmProjects/fg-09-30-steward-event-loop-block`。

## P2 有界工具准入（主修复）

- [x] 新增配置 `AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS`，默认 **8**（必须显著小于
      `app.db.POOL_MAX_CONNECTIONS`=15，为心跳/lease/settle/health 留出连接余量）。
      在 `config.ensure_ready` 的 bounds 中校验 `1 <= value <= POOL_MAX_CONNECTIONS - 1`。
- [x] `execute_tool` 改为 `async def`：**先在事件循环上等名额**（`asyncio.Semaphore`），
      再 `anyio.to_thread.run_in_threadpool(...)` 执行同步体。
      等待期间不占 worker 线程、不占数据库连接。
- [x] 同步体在**独立 Session** 内完成：创建 → 授权 → policy → `agent_tools.execute` →
      commit → `finally` 关闭。禁止跨线程复用请求级 Session。
- [x] 名额只覆盖工具执行；心跳/lease/settle/health 不受限。
- [x] 排队取消：协程被取消时移除等待者。**已进入线程的任务不得提前释放名额**
      （用 `asyncio.shield` + done-callback 释放，确保名额在 worker 真正结束后才归还）。
- [x] 名额在异常/拒绝路径也必须释放。
- [x] 不新建 wire 错误协议；沿用既有可识别失败合同。

## P3 回归与可观测性

- [x] 并发回归：占满连接池后，心跳仍能在预算内得到响应（修复前应失败）。
      这是 AC-2 的核心用例，必须在修复前先确认它会失败。
- [x] 同一 run 多工具 + 多个 run 压力下：心跳成功、工具完成、排队不占 DB/worker。
- [x] 排队期间取消/撤权/失租后不得获得新的执行资格；保留已准入调用既有完成语义。
- [x] 同 tool_call_id 幂等、异常与断连后的资源回收通过。
- [x] 连接池等待超阈值时记结构化日志（实际测量阶段，非"线程池耗尽"），无正文/凭据/SQL 参数。
- [x] 既有 fence 和 Provider 合同回归通过。
- [x] backend 全量 pytest、ruff check、ruff format --check、mypy app。
- [x] agent/frontend 未改则说明不运行原因。

## 执行结果（2026-09-30）

根因与修复见 `research/evidence/r0-root-cause.md` 与 `research/evidence/acceptance.md`。

- 提交 `e9e1a87`，合并 `30906a4`，已 push；开发环境已 ff 并重启（health 1.6ms）。
- 验收：修复前 3 个窗口 **0 请求**（真静默）；修复后同长度窗口 **1146 个
  heartbeat/lease 被服务**；部署后 >10s 静默 0 次（覆盖 1841 秒）。
- 负向验证 4 组：无准入 → 心跳失败；名额 ≥ 池上限 / = 0 → 校验拒绝；取消不泄漏名额。
- backend 1939 passed / 3 skipped；ruff、mypy 在改动文件上干净。
- **指标修正**：先前用「日志唯一秒数」判断静默会漏计，已改用请求计数复核。
- run 232 修复后仍 `expired`，但该窗口内 API 持续服务请求（1146 个），
  `tool_admission_wait`=0；原因是上游 `stream_interrupted`，不属本任务范围。
- 线上未操作。

## P4 开发环境与收尾

- [x] 开发环境相同负载前后对照：health/心跳探针 + 栈/资源占用，至少一次完整长 run。
- [x] 明确 AC-0 至 AC-6 各项证据。
- [x] 更新 spec，仅记录已验证合同；提交、串行集成、归档并清理 worktree/分支。
- [x] 线上未操作；无 schema/迁移变更。

## 回退

保留 fence 与池配置，工具准入可独立回退（恢复 `execute_tool` 为同步 `def` 并移除
semaphore 与配置项）。发现跨线程 Session、取消泄漏、饥饿或授权回归时停止集成。

## 验收映射

| 验收 | 主要证据 |
|---|---|
| AC-0 | ✅ `research/evidence/r0-root-cause.md`（池耗尽 → 30s → 40/40 线程） |
| AC-1 | 修复前后同负载对照：心跳/health 延迟 + 连接/线程占用 |
| AC-2 | P3 并发回归（修复前失败、修复后通过） |
| AC-3 | 开发环境长 run 无 >30s 静默、无饿死 `expired` |
| AC-4 | fence 并发用例 + Provider 合同回归 |
| AC-5 | backend 全量 pytest/ruff/mypy |
| AC-6 | 连接等待超阈值日志可定位 |
