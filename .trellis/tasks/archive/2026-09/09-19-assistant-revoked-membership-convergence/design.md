# 技术设计：撤权后 Run 的终态收敛

## 判定位置

`agent_queue.reaper_pass` 已经是「租约过期」的唯一收敛入口，且已为 `cancel_requested`
做过一次同形放宽（终态意图 → 直接收敛，不回队）。成员资格永久失效属同一类：
**重试不可能改变结果**，因此也应在 reaper 内判定并直接终态化，而不是新增第二个收敛通道。

## 判定与分支

对每个 stale job（`leased`/`running` 且租约已过期，或已请求取消）：

| 条件 | 结果 | error_code |
| --- | --- | --- |
| `cancel_requested` | `cancelled` | 无（既有语义） |
| 执行身份永久失效（成员资格非 active） | `failed` | `AGENT_MEMBERSHIP_REVOKED`（新增） |
| `attempt >= max_attempts` | `expired` | `AGENT_LEASE_EXPIRED`（既有） |
| 其余 | `queued`（回队，既有） | — |

判定顺序重要：**先**判成员资格，再判 attempt 是否耗尽。否则一个 `attempt` 已耗尽的
撤权 Run 会被记成 `expired`（租约超时），继续污染失败分母——这正是本缺陷的表现。

### 为什么用 `failed` 而不是 `cancelled`

`cancelled` 的语义是「有主体决定停止」（用户取消或服务端裁决），而撤权是「无法继续」。
用 `failed` + 专用错误码能同时满足两件事：终态诚实、错误码可区分。
`RUN_TERMINAL_STATUSES` 已含 `failed`，**无需迁移**。

### 为什么错误码要新增而不是复用 `AGENT_LEASE_EXPIRED`

`AGENT_LEASE_EXPIRED` 的语义是「租约超时且重试耗尽」。撤权时重试从未真正尝试过
（每次 lease 后立即 403），把它记成租约超时会让运维误判为 worker 或机器问题。

## 判定实现

在 reaper 的同一立即事务内，对每个 stale job 查一次成员资格：

```python
account = db.get(Account, session.account_id)
member = db.scalar(
    select(SpaceMember.id).where(
        SpaceMember.space_id == job.space_id,
        SpaceMember.user_id == (account.user_id if account is not None else -1),
        SpaceMember.status == "active",
    )
)
```

与 `_authorize_run` / `fence_execution` 使用**同一判据**（space_id + account.user_id +
status='active'），不另立一套授权规则。`space_id` 取 `job.space_id`（与 run/agent_session
同源，`fence_execution` 已断言三者一致）。

**查询失败 ≠ 失效**（R4）：SQLAlchemy 读失败会抛异常，整个 tick 回滚并在下一 tick 重试；
只有查询成功且返回 `None` 才判为失效。不得用 `try/except: revoked = True`。

## 前端

`AGENT_ERROR_COPY` 增加 `AGENT_MEMBERSHIP_REVOKED` 文案（说明是空间访问权限已变化，
不是服务故障）。`run.failed` 已会携带 `error_code` 并走该映射表，无需改 store 逻辑。

`expired` 目前无文案（`finishRun(partition, 'expired')` 不传 errorCode）。本修复不扩大
范围去补它——不在缺陷路径上。

## 测试

`backend/tests/test_agent_queue.py`：

1. 撤权 + 租约过期 → 终态 `failed` + `AGENT_MEMBERSHIP_REVOKED`，`attempt` 不增加，
   job 状态同终态，终态事件为 `run.failed` 且 payload 带错误码；
2. **attempt 已耗尽但仍撤权** → 仍是 `AGENT_MEMBERSHIP_REVOKED`（验证判定顺序，
   不是 `expired`）；
3. 成员资格**仍有效**的租约过期 → 保持回队（回归 R3）；
4. 成员资格仍有效且 attempt 耗尽 → 仍 `expired`（回归既有语义）。

前端 `agentErrors.spec.ts` 增加该码的文案断言。
