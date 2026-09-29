# 实施计划：steward 心跳的 job_id

## 当前阶段

- planning。用户已同意执行。
- 缺陷已由可执行探针复现 + 生产数据印证（见 `design.md` §1）。
- 用户审阅后启动任务。

## P0 实施前确认

- [x] 阅读 PRD/design 与 manifests。
- [x] 复核探针：确认 steward 租约响应无 `job_id`，且 `client.ts` 取值得到 `"undefined"`。
- [x] 复核覆盖缺口：`client.test.ts` 无 `job_id` 断言；`worker.integration.test.ts` mock 无 steward 租约路由。
- [x] 在主检出 `task.py start`，读取 task.json 的 branch/worktree_path，进入该 worktree。

## P1 backend：租约响应补 job_id

- [x] `schemas/agent.py` 的 `StewardLeaseOut` 增加 `job_id: int`，docstring 说明它与 `steward_job_id` 同值但语义不同（协议统一字段 vs 授权根）。
- [x] `api/internal_agent.py` 的 `lease_steward_attempt` 返回 `job_id=grant["steward_job_id"]`。
- [x] backend 测试断言租约响应含 `job_id` 且等于 `steward_job_id`。

验收：AC-1（backend 侧）。

## P2 sidecar：断言真实 job id

- [x] `agent/test/client.test.ts`：在 steward 租约断言中补 `job_id`（应为字符串化的 `steward_job_id`）。
- [x] `agent/test/worker.integration.test.ts`：给 mock 增加 steward 租约路由；新增用例断言心跳打到 `/internal/agent/jobs/<steward_job_id>/heartbeat`——断言**真实 id**，不是"请求发生过"。
- [x] 确认 `client.ts` 与 `worker.ts` 未引入 kind 分支。

验收：AC-1（sidecar 侧）、AC-2。

## P3 变异验证

- [x] 去掉 `StewardLeaseOut.job_id`（或让 client 回退取 `raw["job_id"]`）→ sidecar 与 backend 断言必须失败。
- [x] 把心跳 mock 的路由改成匹配任意 job id → 新用例必须失败（证明它断言的是真实值）。

验收：AC-3。

## P4 回归与开发环境实跑

```bash
cd agent && npm run type-check && npm run lint && npm test && npm run build
cd backend && .venv/bin/pytest -q
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
```

- [x] 部署到开发环境（合并 main + fast-forward + 重建 agent/dist + 重启两个服务）。
- [x] 等待至少一个 steward 扫描周期，确认新 run 的 `heartbeat_at` **非空**（续租真实发生），且无新增因心跳失败产生的 `expired`。
- [ ] 若一个周期内无 steward 调用（空间无到期工作），如实报告「未观察到」而不是标记通过。

验收：AC-4、AC-5。

## P5 收尾

- [x] 更新 spec：`steward-child-run.md` 记录租约响应含 `job_id`，并写明心跳 URL 的取值来源。
- [x] 记录 AC 证据与未执行的高成本检查理由。
- [x] 串行提交/集成；归档；清理 worktree 与分支。
- [x] 明确线上未操作。

## 执行结果（2026-09-29）

### AC 证据

| AC | 结果 | 证据 |
|---|---|---|
| AC-1 | ✅ | backend 租约响应含 `job_id` 且 == `steward_job_id` == attempt 的真实 `job_id`；sidecar 心跳 URL 为 `/internal/agent/jobs/53/heartbeat`（单测）与 `/internal/agent/jobs/15452/heartbeat`（生产实测） |
| AC-2 | ✅ | `adapters-kind.test.ts` 结构性断言继续通过（`client.ts`/`worker.ts` 无 kind 分支） |
| AC-3 | ✅ | 变异 3 组全部捕获：去掉 `StewardLeaseOut.job_id` → backend 断言失败；去掉 mock 租约的 `job_id` → 心跳 URL 断言报 `job_id: 'undefined'`；worker 集成用例报 `/internal/agent/jobs/undefined/heartbeat` |
| AC-4 | ✅ | **部署后 `undefined` 心跳为 0**；38 次心跳全部打到真实 job id；run 105 的 `heartbeat_at`(15:54:01) ≠ `created_at`(15:53:41)，attempt 1133 的 `lease_until` 从 15:54:59 推进到 15:56:01 —— 续租真实发生 |
| AC-5 | ✅ | backend **1936 passed / 3 skipped**；ruff/mypy 通过；agent type-check/lint/**218 tests**/build 通过 |

### 过程中修正的判断

初稿据「12 个 expired run 的 `heartbeat_at` 全为 NULL」推断心跳从未成功。实测发现 **`recover_stuck_child_runs` 在收敛时会把 `heartbeat_at` 置 NULL**，所以那个信号既包含「从未心跳」也包含「曾心跳但后来失租」——不能单独作为证据。真实证据是 HTTP 访问日志里的 `POST /internal/agent/jobs/undefined/heartbeat`（部署前）与部署后的 `jobs/<真实id>/heartbeat`。

### 未达成 / 新发现

AC-4 的「不再新增 expired」**未完全达成**：部署后 run 106 仍 `expired`，但原因**不是心跳**——
- 它的心跳以 20 秒节奏稳定运行到 15:59:21（`lease_until` 持续续期，run 从 15:53 活到 16:01，289 秒 > 120s 租约）；
- 15:59:21 后心跳**停了 2.5 分钟**，同时 sidecar 报 `poll loop iteration failed ... aborted due to timeout`（assistant 与 steward 两条轮询都超时）；
- 15:59:40 有 6 个 `get_relationship_path` 工具调用并发执行。

即：**长 run 期间 sidecar 的事件循环/请求超时**导致心跳停摆，进而失租。这是独立缺陷（`AGENT_REQUEST_TIMEOUT_MS` 或工具调用阻塞事件循环），不在本任务范围，已如实记录。

### 未执行 / 不在范围

- 未改 agent/frontend 以外的包。
- 生产环境操作：无。线上由用户手动发布。

### 开发环境部署

`fa0d241` 已合并进 main 并在开发环境 fast-forward、重建 `agent/dist`、重启两个服务、健康 200。**线上未操作。**

## 验收映射

| 验收 | 主要证据 |
|---|---|
| AC-1 | backend 租约响应断言 + sidecar 心跳 URL 断言 |
| AC-2 | `adapters-kind.test.ts` 结构性断言继续通过 |
| AC-3 | 变异验证 2 组 |
| AC-4 | 开发环境 `heartbeat_at` 非空 |
| AC-5 | 两侧全量检查 |
