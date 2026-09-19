# G 技术设计：验收缺口补齐

## 缺口一：取消计时的可归因拆分

### 现状为何不够

`run_controlled_acceptance.py::_scenario_cancel` 当前顺序：

```python
cancelled = client.post(.../cancel)   # 发取消
report = collect_worker(worker)       # ← 阻塞等 worker 退出
cancel_at = time.monotonic()          # ← 才开始计时
run = wait_terminal(...)              # 查询终态
converged_after_s = time.monotonic() - cancel_at
```

`collect_worker` 用 `process.communicate(timeout=120)` 等待子进程结束。因此该值度量的是「worker 已退出后，终态查询返回」的耗时，与用户点击取消的等待无关。

### 三个独立量

| 量 | 起点 | 终点 | 来源 |
| --- | --- | --- | --- |
| `cancel_accept_ms` | 发起 cancel 请求前 | cancel 响应返回 | harness monotonic |
| `terminal_visible_ms` | 同上 | `wait_terminal` 观察到终态 | harness monotonic |
| `worker_stop_ms` | 同上 | worker 子进程退出 | `collect_worker` 前后 monotonic |

三者都由 harness 的 `time.monotonic()` 测量（同机同钟，可直接相减）。实现上把 `collect_worker` 移到终态观察**之后**或**并行**，不再让它挡住计时。

注意：`terminal_visible_ms` 包含 harness 的轮询粒度（`wait_terminal` 的 sleep 间隔）。必须在证据里写明该粒度，不能把它当作服务端精度。

### 竞态用例（G-R2）

现有 `_settle` 的语义是：

```python
if effective == "succeeded" and run.cancel_requested:
    effective = "cancelled"      # 成功结果被丢弃
# failed 原样保留
```

即「取消 + 成功 → cancelled」「取消 + 失败 → failed」。这是正确的（取消不该吞掉真实故障），但**没有测试锁定**。需要补：

1. 取消后结算 `failed` → 终态 `failed`，事件 `run.failed`，不被改写；
2. 取消后结算 `succeeded` → 终态 `cancelled` + `agent_run_settle_overridden` 审计；
3. 上述落终态后 `reaper_pass` 不覆盖（reaper 只选 `leased`/`running`）。

放在 `backend/tests/test_agent_browser_api.py` 与 `test_agent_queue.py`（现有 cancel 用例的邻居），复用既有 fixture。

## 缺口二：撤权的真实浏览器语义

### 为何 F 没做

F 的 UI 组只登录了 1 个账号（朱元璋，空间 owner），而撤权需要「被撤权者本人正在收流」+「另一有权主体执行移除」。F 把它记为「属另一轮环境搭建」。

### 可复用的种子

`dev_seed.py` 的 `_SEED_SPACE_MEMBERS["明皇室"]` 同时含 `朱元璋`（owner）与 `朱标`。所有演示账号 PIN 统一为 `_SEED_PIN`。因此不需要新建账号：

- 浏览器以 **朱标** 登录 → 在「明皇室」建会话并发消息（真实 sidecar 流）；
- harness 用 **朱元璋** token 经真实 API `DELETE /api/space-memberships/{member_id}` 移除朱标；
- 浏览器侧观察。

### 断言（对应 F-R4 的「失权」）

| 子格 | 断言 |
| --- | --- |
| UI2-8 | 撤权后受限内容不残留：会话/消息面板不再展示该空间内容，或按合同进入明确不可用态（非静默继续） |
| UI2-9 | 重连不复活：撤权后重新请求 SSE/刷新，不恢复旧内容、不越权读到新内容 |
| UI2-10 | 迟到事件不回写：撤权后到达的事件不追加进面板 |

服务端侧的「worker 不继续工具调用 / 旧执行者零写回」由既有 `fence_execution` 的成员资格检查（403 `AGENT_TOKEN_SCOPE_MISMATCH`）承担，已有 `test_internal_agent_api.py` 覆盖；本任务只需在补验记录里**引用**该证据，不重复实现。

### 证据边界（必须写明）

- `_fetch_new_events` 只校验 `agent_session.account_id == account_id`，**不**按空间成员资格逐事件复核；撤权后已持久化的历史事件仍可被该账号读取。这是现状，必须在补验记录里如实标注，不能声称「撤权即时切断所有读取」。
- 前端撤权呈现依赖空间上下文刷新路径（`useSpaceContext` → `spaces.loadMembers` → `resetForSpace`）。若实测未触发，属产品缺口，退回所属任务，不在本任务无边界修改。

## 风险与边界

- 不修改生产默认参数。`MAINTENANCE_INTERVAL_SECONDS=5` 保持原值；探针适配真实节奏。
- 不重跑已通过的 60 格；只补新增格 + 受影响回归。
- 新增格若因环境无法构造，如实记 `blocked` 并写明缺失原因（F-R6 要求），不得填 pass。
- 补验记录追加到 `evidence/`，不覆盖归档的 `matrix.md`。
