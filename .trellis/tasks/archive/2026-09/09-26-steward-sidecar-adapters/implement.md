# E2 实施记录：sidecar `KindAdapter` 与 Pi 协议补齐（含 E3/E4 验收）

> 技术权威：父任务 `09-25-steward-pi-child-run-design/design.md`。
> 本文件记录实际交付与结果。**本任务实际覆盖 E2 + E3 + E4**：E2 的两处协议修复
> （租约端点、产物上报）只有把 Pi 载体真正跑起来才能证明，因此 E3/E4 的验收在本任务内完成。
> E5（删除 inproc 载体与开关）**未执行**，理由见 §5。

## 1. E2：sidecar `KindAdapter`

- [x] `agent/src/adapters/kind.ts`：`KindAdapter` + `assistantAdapter` / `stewardAdapter`
      （冻结单例），成员覆盖全部既有 kind 差异：`systemPrompt` / `modelPrompt` / `toolNames` /
      `slotBudget` / `cacheKey` / `emptyToolAllowlistIsInvalid` / `verifyProjection` /
      `adoptsServerConcurrency` / `reportsProductOnSettle` / `extractProduct` /
      `leaseRequest` / `decodeLease`。
- [x] `worker.ts::executeJob`、`session.ts::buildRunSession`、`client.ts::leaseJob` 零 kind 分支。
- [x] 结构性断言（变异测试验证）：`agent/test/adapters-kind.test.ts` 读四个源文件，
      出现新的 `kind === "assistant"|"steward"` 比较即失败。
- [x] 适配器单测 15 例；`client.test.ts` 新增租约路径/请求体与 settle 产物形状。

**E1 遗留的两处协议缺陷（本任务修掉）**：

1. 租约端点：后端已改名 `/steward/attempts/lease`，sidecar 仍打旧路径 → 实测 404。
   修法：路径与请求体由适配器给出，且**不再发送 `space_id`**——sidecar 不知道空间拓扑，
   由服务端选一个有容量且有到期工作的空间（`StewardLeaseRequest.space_id` 改为可选）。
2. 产物上报：`executeJob` 从不把产物交给 settle，而 child run 拒绝消息类事件，
   所以产物无路可走；服务端会在 `_settle_attempt` 的 `assert text is not None` 处失败。
   修法：`reportsProductOnSettle` + `extractProduct`。

## 2. E3：terminology 走 Pi 载体

- [x] **prompt 文本归属修正**：进程内载体发送 `_PROMPTS[kind]` 作为 system message，
      `prompt_digest` 覆盖它；Pi 载体原本只发 sidecar 的通用 prompt，两侧会问出不同问题。
      新增 `steward_instructions` 投影字段，sidecar 删除本地 steward prompt（不留 fallback）。
- [x] **结算分两阶段**：`settle_run` 持立即事务，hook 不能调会自己开事务的 `settle_attempt`。
      拆为 `record_attempt_outcome`（phase 1，调用方持锁）+ `apply_settled_attempt`
      （phase 2，自有事务，幂等）。
- [x] **child run 自行收敛**：`recover_stuck_child_runs` 原本只置 `cancel_requested`，
      但 `reaper_pass` 选 `AgentJob` 而 steward run 的 `job_id` 恒为 NULL，没人写终态。
      改为在本函数内写 `expired`（不是 `cancelled`）。
- [x] **网关可达**：`/runs/{id}/provider/*` 用 assistant-only 别名授权，steward 请求 403。
      改为按 token 的 kind 分派；并且 `resolve_runtime` / `resolve_for_run` **都必须带
      run 自己的 kind**（否则 steward run 读到 assistant 的空间设置，报「云被禁止」）。
      顺带修掉 assistant 授权器先读 `run.session_id` 再判 kind 的断言（对 steward run 会 500）。
- [x] **租约按 carrier 选行**：不过滤会让进程内调度泵租到 `pi` attempt 并卡到租约过期。
- [x] 验收：`tests/test_steward_pi_carrier_terminology.py`（10 例）——载体等价（同一输入两条载体
      产出结构等价且应用结果一致）、egress 审计以 child run 为 `target_id`、崩溃点⑤收敛、
      撤权拒绝、回退无孤儿、网关可达、无残留默认 kind 断言。

## 3. E4：candidate / ranking / explanation

- [x] 三类逐个走 Pi 载体，**未改任何生产代码**（E3 的链路与服务端校验器已经通用）——
      这正是 E1 重构的目的：换 kind 只改一个配置值。
- [x] 每 kind 的围栏经 Pi 路径断言（变异测试验证）：
      candidate 只落原子 `SOURCE_FACT_TYPES` 且证据变化即退休；ranking 严格排列（部分排列整体拒绝）；
      explanation 只可引用给定证据、渲染文本来自服务端模板。
- [x] 载体等价：三类各比较 settle 状态、原因码与产物**结构**（标识符与文本按类型归一，
      因为每次迭代建自己的空间，原文与显示名合法地不同；内容由单空间用例精确比较）。
- [x] `tests/test_steward_pi_carrier_remaining_kinds.py`（7 例）。

## 4. 验证结果

| 检查 | 结果 |
|---|---|
| backend `pytest -q` | **1939 passed, 3 skipped** |
| backend `ruff check` / `ruff format --check` / `mypy app` | 通过（2 条 ruff 违规在 main 上既有，与本次无关） |
| agent `type-check` / `lint` / `test` / `build` | 通过（**200 tests**，E2 前 181） |
| 迁移往返 | 未涉及迁移 |

**未运行**：`frontend` / `system-admin-frontend` 全量（本次未触碰这两个包）；
`docker compose config --quiet`（E2 未改 compose）；真实 HTTP 端到端（E3/E4 用真实 internal app
的 TestClient 驱动，未起 uvicorn + 真实 provider）。

## 5. E5 未执行的理由（如实记录，不降低校验）

父设计把 E5 定为「**可选，稳定后**」，其三项在本轮均不成立：

1. **删除 `InprocCarrier` 与 `STEWARD_ASSIST_<KIND>_CARRIER`**：四个 carrier 仍全默认 `inproc`，
   生产未切换。删掉就是删掉当前唯一的发货路径——E5 自己的回退表也依赖这些开关存在。
2. **删除 `run_id` 为 NULL 的兼容分支**：该分支（`open_child_run` 里 `if attempt.run_id is not None`）
   是**重租幂等**，不是 inproc 兼容分支；E1 改变语义后，这条清单项已不适用。
3. **收紧 CHECK**：memory #399 明确不得破坏性改写历史行。

结论：E5 需要「生产已切换 Pi 载体并稳定运行」这一前置事实，本轮不具备。不执行。

## 6. 回退

`FG_AGENT_ROLE=assistant`（sidecar 完全不租 steward）或逐 kind `STEWARD_ASSIST_<KIND>_CARRIER=inproc`。
E2/E3/E4 不涉及迁移与数据。
