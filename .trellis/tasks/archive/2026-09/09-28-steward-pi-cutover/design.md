# S5 设计：Pi 载体收敛为唯一路径

## 1. 核心判断

「切换载体」与「删除 in-process」不是两件独立的事，而是**一次收敛的两个阶段**，顺序是硬约束：

```
阶段 A：部署 main（保留 inproc 默认）→ 生产开启 Pi → 验证真实闭环
阶段 B：删除 inproc 实现与开关 → 再部署
```

原因：删除 inproc 后，若 Pi 未启用，`_reserve_attempt` 写出的 `carrier='pi'` attempt 没有任何执行者——会一直停在 `reserved`，直到租约过期由 `recover_stuck_attempts` 收敛为 `unknown`。**Steward 模型辅助会静默停摆**，且表现为「花掉调用额度但没有产物」。所以不能先删。

阶段 B 在阶段 A 验证通过前**不合并进 main**，避免 main 变成「不可安全部署」。

## 2. 删除边界

### 2.1 删除（in-process 专属）

`backend/app/services/steward_carrier.py` 整个模块删除：

| 符号 | 理由 |
|---|---|
| `InprocCarrier` | 进程内直连 Provider 的执行体 |
| `AssistCarrier` / `CarrierOutcome` | 只有两个实现时的抽象；实现只剩一个，抽象无对象 |
| `CARRIER_INPROC` / `_BY_NAME` / `carrier_for` | 按 kind 解析载体，收敛后是常量 |
| `_api_path` / `_auth_headers` / `_parse_response_for` | 仅供 `InprocCarrier` 使用 |
| `PiCarrier` | 其 `execute` 是 `NotImplementedError`；服务端不执行 Pi attempt，保留它反而暗示可调用 |

`steward_assist.py` 内删除：

| 符号 | 理由 |
|---|---|
| `_post_json` / `_post_json_async` | 进程内 HTTP 发送；收敛后 Provider 出站只能经网关 |
| `_build_payload` / `_fill_model` / `_parse_response` | 仅进程内发送与响应解析使用 |
| `_inproc()` | 载体实例工厂 |
| `run_attempt` / `execute_plan_attempts` / `run_due_attempt` | 进程内租约执行入口 |
| `launch_due` / `_spaces_with_due_attempts` / `_run_in_own_session` | 进程内调度泵 |
| `_get_executor` / `_executor` | 有界线程池 |
| `_send_budget` / `_release_unsent` / `_MIN_SEND_WINDOW_SECONDS` / `_SETTLEMENT_RESERVE_SECONDS` | 仅进程内「发送前预算」判定 |
| `_carrier_for` | 载体解析；`_reserve_attempt` 直接写常量 |
| `REASON_CARRIER_NOT_INPROC` | 无引用 |

`config.py`：删除 `STEWARD_ASSIST_{CANDIDATE,RANKING,EXPLANATION,TERMINOLOGY}_CARRIER`。

`maintenance.py`：删除 tick 中的 `steward_assist.launch_due()`。

### 2.2 保留

| 符号 | 为什么保留 |
|---|---|
| `_API_PATHS`（steward_assist） | **不是** inproc 专属：`_fence_check` 用它判 `REASON_PROVIDER_API_UNSUPPORTED` |
| `_estimate_input_tokens` / `_KIND_OUTPUT_CAPS` | `_reserve_attempt` 的预算与输出 cap |
| `lease_attempt` / `open_child_run` / `settle_attempt` / `record_attempt_outcome` / `apply_settled_attempt` / `recover_stuck_attempts` | Pi 路径与恢复共用 |
| `steward_model_calls.carrier` 列与 CHECK | 历史行保留可读；新行写 `pi`（memory #399：不收紧） |
| `_carrier_for` 的**调用点**改为常量 `CARRIER_PI` | 仍要写进 attempt，只是不再解析 |

### 2.3 不适用项

E5 清单第 2 项「删除 `run_id` 为 NULL 的兼容分支」**不适用**：那段逻辑是**重租幂等**（同一 attempt 重复 open 时复用既有 run），不是 inproc 兼容分支。E1 已改变其语义，删除会破坏重租路径。此项保持不动并记录理由。

## 3. 测试接缝迁移

现状：约 85 处调用依赖「注入假 transport」观察发送前后行为：

| 入口 | 调用点 |
|---|---|
| `execute_plan_attempts(transport=, after_send=)` | 25 |
| `schedule_due_attempt` | 29 |
| `run_due_attempt(transport=)` | 9 |
| `run_attempt(transport=)` | 3 |
| patch `steward_assist._post_json` | 50 |

迁移目标：改用 Pi 路径的**真实驱动**（与 `test_steward_pi_carrier_*.py` 同形）：

```
lease_attempt(carrier="pi") → open_child_run → 签发 run token
   →（可选：在 lease 与 settle 之间改世界）
   → 内部 settle 端点带 product
```

新增共享 helper（放 `backend/tests/steward_pi_harness.py`）：

- `lease_pi_attempts(db, plan_id, *, settle_with, between=None) -> str | None`
  —— 逐个租取并结算该 plan 的 pi attempt，`between` 是 fence 观察窗口（替代 `after_send`），返回 `plan_outcome`。
- `settle_pi_attempt(db, grant, product_text)` —— 经内部端点结算，保证走真实授权与 fence。

**fence 观察窗口等价性**：`after_send` 原本在「carrier 返回后、settle 前」；Pi 路径对应「lease 后、settle 前」，`between` 挂在那里。两者都在写回栅栏之前，语义一致。

## 4. 风险

| 风险 | 处理 |
|---|---|
| 生产 Steward 静默停摆（Pi 未启用而 inproc 已删） | 阶段 B 不在阶段 A 验证前合并 |
| 迁移 0055 在生产重建 `agent_runs`/`context_builds` | 六个拒绝守卫已在生产实测通过；部署走 `deploy-prod.sh`（先备份、失败回滚） |
| 测试迁移削弱 fence 覆盖 | 每个 kind 保留变异验证；删除任一 fence 层必须失败 |
| 网关出站缺审计 | 以 `agent_provider_egress` 存在性断言（AC-2） |
