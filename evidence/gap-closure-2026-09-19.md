# F 验收缺口补齐：补验记录（2026-09-19）

**追加**记录，不覆盖已归档的 `09-17-dual-agent-controlled-acceptance/evidence/matrix.md`。
本文件只记录原 F 未覆盖的格与口径修正。

基线：`main@a89ada5` → 补验 SHA `0952983`（含 `9f9f470` 的撤权收敛修复）。
全程零真实模型费用（假上游只模拟协议与时序）。

## 复现入口

```bash
python3 scripts/smoke/run_controlled_acceptance.py --report /tmp/gap-controlled.json
python3 scripts/smoke/run_browser_acceptance.py   --report /tmp/gap-browser.json
./scripts/frontend-api-smoke.sh --report /tmp/gap-smoke.json    # 退出 2 = blocked
```

## 一、取消计时改为端到端可归因（G-R1）

原 A6-1 的 `converged_after_s` 顺序是「发取消 → `collect_worker()` 等 worker 退出 →
才开始计时」，度量的是「worker 已退出后再查到终态」，**不能**代表用户点击取消的等待。
已改为三个独立量（同一 harness monotonic）：

| 量 | 含义 | 实测 |
| --- | --- | --- |
| `cancel_accept_ms` | 发取消 → cancel API 返回 | **19ms** |
| `terminal_visible_ms` | 发取消 → 观察到终态 | **3375ms** |
| `worker_stop_ms` | 发取消 → worker 子进程退出 | **10525ms** |

**口径边界**（必须随证据一起读）：

- `terminal_visible_ms` 包含 `wait_terminal` 的轮询粒度 **0.25s**；
- `worker_stop_ms` 包含维护循环节奏 `MAINTENANCE_INTERVAL_SECONDS=5`（收敛被取消的
  Run 依赖该 tick），因此它是「维护 tick 上界 + worker 自身收尾」，不是服务端精度；
- 三者都由本机 monotonic 测量，同钟可相减；不跨机。

原「304s」是租约过期路径的旧观测，仍是修复前行为的有效历史证据。

## 二、取消与真实失败的竞态（G-R2）

`_settle` 的既有语义此前**没有测试锁定**。新增两条回归（`backend/tests/test_agent_queue.py`）：

| 回归 | 断言 | 结果 |
| --- | --- | --- |
| `test_cancel_then_failed_settle_keeps_failed` | 取消后结算 `failed` → 终态 `failed` + 原错误码，无改判审计，事件为 `run.failed` | pass |
| `test_reaper_does_not_override_a_run_that_already_failed_after_cancel` | 落终态后 reaper 不覆盖（`reaper_pass == 0`） | pass |

反向（取消 + `succeeded` → `cancelled` + 改判审计）由既有
`test_cancel_running_then_settle_overrides_to_cancelled` 覆盖。

两条新回归在还原修复后均转红（已验证）。

**结论**：「取消吞掉真实故障」不成立——`failed` 原样保留，只有 `succeeded` 被改判。
错误分母不会因为用户取消而消失。

## 三、撤权的真实浏览器语义（G-R3）

原 F 把这格记为「属另一轮环境搭建」而略过，但 F-R4 明确要求覆盖**失权**。
新增三格，用两个真实主体（朱标收流 / 朱元璋撤权），撤权经真实 API
`DELETE /api/space-memberships/{id}`。

| 格 | 断言 | 结果 |
| --- | --- | --- |
| UI2-8 | 撤权后不得再在该空间创建会话（服务端授权边界） | pass（`revoke_http=204`、`new_session_http=403 SPACE_FORBIDDEN_ACTOR`） |
| UI2-9 | 撤权后 Run 收敛为终态且不再声称仍在生成 | pass（`run=failed timed_out=None`、`provisional=0`、文案「你已不是该空间的活跃成员，本次回答已停止」） |
| UI2-10 | 刷新后不复活旧内容、不越权读到新内容 | pass（`reload_items=0`、`sessions=0`） |

**本轮补验发现并修复了一个 P1 缺陷**（详见
`evidence/defect-revocation-no-convergence.note.md`）：撤权后 Run 不收敛——旧逻辑
把成员资格失效当成可重试的租约丢失回队，新 attempt 立刻又 403，直到 `attempt`
耗尽（约 `3 × 300s`）才收口为 `expired`。用户在这 15 分钟里看着一个永远不会完成的
「生成中…」。修复后同一场景在下一个维护 tick 内收敛为 `failed` +
`AGENT_MEMBERSHIP_REVOKED`。

> **该缺陷是首轮 F 无法发现的**：它只在「被撤权者本人正在收流」时才出现，而首轮
> 只登录了空间 owner 一个账号。这正是把该格记为「属另一轮环境搭建」的代价。

### 证据边界（必须随结论一起读）

- **`_fetch_new_events` 只复核账号归属**（`agent_session.account_id == account_id`），
  **不**逐事件复核空间成员资格。撤权后该账号仍能读到**已持久化**的历史事件
  （实测 `raw_read_status_after_revoke=200`）。本记录**不**声称「撤权即时切断读取」。
  UI2-10 断言的是「刷新后不复活/不越权显示」，不是「服务端拒绝读取」。
- **服务端 worker 侧零写回**由既有 `fence_execution` 的成员资格检查承担
  （403 `AGENT_TOKEN_SCOPE_MISMATCH` / `reason=active_membership_missing`），
  已有 `backend/tests/test_internal_agent_api.py` 覆盖；本轮只引用该证据，
  并在 DB 时间线上确认撤权后无工具调用、无业务写回、无新模型请求。
- 撤权后**已渲染**的历史正文是否立即隐藏属显示策略，不作为安全结论；UI2-9 只断言
  「不再声称仍在生成」这一合同项。

## 四、口径修正（G-R4）

| 位置 | 原表述 | 修正 |
| --- | --- | --- |
| `agent-runtime.md` 压缩 | 「压缩是 `model_turn` 的子成分」（事实性错误） | 压缩是 run 级阶段（`run.compacted`），`model_turn` 本就不含压缩；两者不得相减 |
| `agent-runtime.md` 取消失效 | 「已实测缺陷（未修）」 | 已由 D-F1 修复（机器可读 409 + 立即收敛），本段改为历史归因 |
| `agent-runtime.md` `model_turn` | 「含上游重试与轮内压缩」 | 「含上游重试；压缩单列」——不得称纯推理 |
| `settle` | — | 明确它是**持久事件间隔估计**，不是精确的结算 CPU 耗时 |

## 门禁

| 检查 | 结果 |
| --- | --- |
| `ruff check .` / `ruff format --check .` / `mypy app` | 全绿（401 文件 / 206 源文件） |
| `pytest`（backend 全量） | **1716 passed, 3 skipped** |
| `vitest run`（agent 全量） | 167 passed / 18 files；lint + type-check 全绿 |
| `npm test`（frontend 全量） | 767 passed / 72 files；lint + type-check + build 全绿 |
| `run_controlled_acceptance.py` | **41/41** |
| `run_browser_acceptance.py` | **22/22**（原 19 格 + 新增 UI2-8/9/10） |
| `frontend-api-smoke.sh` | **56/56，退出 0** |

## 仍未覆盖（如实记录）

- **跨机时钟精度**：本记录不报告跨机毫秒；三个取消量均为本机 monotonic。
- **真实 Provider 的模型质量与线上延迟**：属 G（需批准费用）。
- **`expired` 的前端文案**：`finishRun(partition, 'expired')` 不传 errorCode，
  故无专用文案。不在本轮缺陷路径上，未扩大范围。
- **撤权后历史读取的授权语义**：现状按账号归属放行（见上「证据边界」），
  是否应收紧为逐事件空间复核属独立设计决策，未在本轮改动。
