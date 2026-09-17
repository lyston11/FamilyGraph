# C 执行证据：管家可中断总截止与结算收尾预算（2026-09-17）

基线：`main@4102bb3`（含 4102bb3 的 C～I 规划）。分支 `feat/09-17-steward-hard-deadline`。
运行环境：Python 3.12、httpx 0.28.1（`backend/.venv`）。全部测试用仓库测试库夹具，未访问运行中的生产库。

## 1. 复核反例（修复前）

用本地 socket 服务（先延迟 300ms 发响应头，再延迟 300ms 发正文）对旧实现调用
`_post_json(..., timeout=0.4)`：实测 **约 638ms** 才抛 `ReadTimeout`。旧实现只在
`iter_bytes()` 返回一块数据之后检查 monotonic deadline，因此阻塞在等待响应头或等待
下一块数据期间无法中断。

## 2. 实现（`app/services/steward_assist.py`）

| 变更 | 说明 |
| --- | --- |
| `_post_json_async` | `httpx.AsyncClient` + `asyncio.timeout(timeout)`；覆盖连接、发送、响应头、读取与解压。等待期真正取消在途 I/O |
| `_post_json`（同步入口） | 在工作线程内 `asyncio.run` 桥接；检测到运行中的事件循环即 `RuntimeError`（结构性误用 fail-closed） |
| 超时语义 | 统一抛 `httpx.ReadTimeout`（unknown 保守计费）；**不复用** `connect_failed`——请求交给 transport 后无法证明上游未处理 |
| `_send_budget` | 单笔预算 = `min(STEWARD_ASSIST_TIMEOUT_SECONDS, 剩余租约 - _SETTLEMENT_RESERVE_SECONDS)`，最小发送窗口 `_MIN_SEND_WINDOW_SECONDS` |
| 出事务再核对 | 事务提交后再核对一次预算；窗口不足则本笔从未发出 |
| `_release_unsent` / `_release_remaining_unsent` | 本人份仍有效时把未发送预留回退为 `skipped`（`insufficient_budget`，零计费）；身份失效则留给恢复器 |
| JSON 解析前后核对 | 不接受超预算的“成功”（解析本身同步、不可抢占，由响应字节上界限制超差） |

`skipped` 不进入 `_BUDGETED_STATUSES`，因此未发送的释放不消耗调用/token 预算。

## 3. 受控场景实测（新回归 `tests/test_steward_assist_deadline.py`，10 项）

| 场景 | 预算 | 实测 | 结果 |
| --- | --- | --- | --- |
| 响应头停顿 300ms + 正文停顿 300ms | 0.4s | 约 0.40s | 预算内中断；旧实现约 0.638s |
| 仅正文停顿 2.0s | 0.4s | 约 0.40s | 预算内中断 |
| 持续慢 chunk（每块 50ms，无穷） | 0.4s | 约 0.40s | 预算内中断 |
| 不可路由地址连接等待 | 0.4s | 约 0.40s | 预算内中断 |
| 连续 6 次超时 | 0.2s | — | 6 条连接均被客户端主动关闭（服务端观察 EOF），无残留 |

容差断言：`0.35s <= elapsed < 0.75s`（预算 + 机器调度容差），远低于旧实现约 0.638s。

## 4. 预算与计费断言

- `_send_budget`：100s 租约 → 配置 timeout；5s 租约 → 3s（扣 2s 结算预留）；1s 租约 → ≤ 0。
- transport 实际收到的预算：6s 租约场景 ≤ 4s（配置 30s 不构成上界）。
- 提交后窗口不足：transport 从未被调用，attempt 全部 `skipped` / `insufficient_budget`、零计费。
- `MAX_MODEL_CALLS_PER_JOB=1` + 一笔超时：仅 1 次发送，剩余 `skipped`，`_budget_state` 计数为 1。

## 5. 验证命令与结果

```bash
cd backend
.venv/bin/pytest -q tests/test_steward_assist.py tests/test_steward_assist_deadline.py   # 63 passed
.venv/bin/pytest -q tests/test_steward_assist.py tests/test_steward_assist_deadline.py \
  tests/test_steward_terminology_runtime_quality.py \
  tests/test_steward_terminology_delivery_integration.py \
  tests/test_steward_runtime_recovery.py tests/test_steward_delivery_recovery.py          # 99 passed
.venv/bin/ruff check .          # All checks passed
.venv/bin/ruff format --check . # 399 files already formatted
.venv/bin/mypy app              # Success: no issues found in 206 source files
.venv/bin/pytest -q             # 1659 passed, 3 skipped
```

既有 B 回归（逐笔保全、混合批次、慢 chunk、失租拒写、unknown 不重放）全部保持通过。
`test_lease_deadline_stops_followup_sends` 与
`test_returned_result_is_persisted_before_the_next_send` 按新预算语义更新：前者现在断言
未发送预留立即释放为 `skipped`（而非留给恢复器），后者把第二笔停顿延长到超出
「预算 + 结算预留」以真正耗尽租约。

## 6. 未做 / 边界

- 未做真实 Provider 调用、未部署、未重启服务（交 G）。
- `_SETTLEMENT_RESERVE_SECONDS=2.0` 为常量，未做成环境变量；如后续需要按部署调参，
  需连同设计评审（当前无证据表明不足）。
- 事件循环内调用同步入口 fail-closed 属结构性保护，生产路径（有界执行线程）不受影响。
