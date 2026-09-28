# S5 实施计划：Pi 载体收敛为唯一路径

前置：`task.py start` 后建分支/worktree，全部改动在该 worktree 内完成。

## 阶段 A：让 Pi 成为默认与唯一运行形态（先做，可独立验证）

- [ ] A1 `_reserve_attempt` 写 `carrier=CARRIER_PI`，不再经 `_carrier_for` 解析。
- [ ] A2 删除 `config.STEWARD_ASSIST_{KIND}_CARRIER` 四个开关；`steward_carrier.carrier_for` 的配置读取一并移除。
- [ ] A3 `docker-compose.yml` 增加 `STEWARD_PI_RUNTIME_ENABLED`、`FG_AGENT_ROLE`（默认 `both`），并删除已不存在的 `_CARRIER` 透传。
- [ ] A4 修正配置漂移：`STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE` 的 compose 默认值 1 → 2（与 `config.py` / `config.ts` 一致）。
- [ ] A5 验收：backend 受影响测试通过；`docker compose config --quiet` 通过。

**A 完成后 main 仍保留 inproc 实现**，因此可安全部署（Pi 已启用，inproc 作为未使用代码存在）。

## 阶段 B：删除 in-process 路径

- [ ] B1 删除 `steward_carrier.py` 整模块（`InprocCarrier`、`AssistCarrier`、`CarrierOutcome`、`carrier_for`、`PiCarrier`、`_api_path`、`_auth_headers`、`_parse_response_for`、`CARRIER_INPROC`）。
- [ ] B2 删除 `steward_assist` 的进程内发送与调度实现（见 design §2.1 清单）。
- [ ] B3 保留 `_API_PATHS`（fence 用）、`_estimate_input_tokens`、`_KIND_OUTPUT_CAPS`、`lease_attempt`/`open_child_run`/`settle_attempt`/`record_attempt_outcome`/`apply_settled_attempt`/`recover_stuck_attempts`。
- [ ] B4 删除 `maintenance.py` tick 中的 `steward_assist.launch_due()`。
- [ ] B5 `lease_attempt` 的 `carrier` 参数：保留（数据列仍存在），调用方传 `CARRIER_PI` 字面量或模块常量。
- [ ] B6 新增 `backend/tests/steward_pi_harness.py`：`lease_pi_attempts`、`settle_pi_attempt`。
- [ ] B7 迁移测试（约 85 处调用点 + 50 处 `_post_json` patch）到 Pi 驱动；删除只验证 inproc 的用例。
- [ ] B8 验收：`pytest -q` 全绿；变异验证（删 fence 层必失败）；`ruff check`/`format --check`/`mypy app` 通过。

## 阶段 C：生产切换

- [ ] C1 确认 main 已 push 且生产工作区干净。
- [ ] C2 `deploy-prod.sh` 部署（先备份；迁移 0055 六个守卫已在生产实测通过）。
- [ ] C3 生产设置 `STEWARD_PI_RUNTIME_ENABLED=1`、`FG_AGENT_ROLE=both`（`~/.config/familygraph/familygraph-prod.env`）。
- [ ] C4 重启 agent 与 api，确认四容器 healthy。
- [ ] C5 验收 AC-7：真实 Pi child run 闭环——`agent_runs` 出现 `kind='steward'` 行、attempt 结算、`agent_provider_egress` 审计存在、`steward_model_calls.carrier='pi'`。
- [ ] C6 观察一个调度周期，确认无 attempt 卡在 `reserved`。

## 验证入口

```bash
cd backend && .venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
cd agent && npm run type-check && npm run lint && npm test && npm run build
AGENT_SERVICE_SECRET=x SECRET_KEY=y ADMIN_JWT_AUDIENCE=a ADMIN_JWT_SECRET=b ADMIN_JWT_ISSUER=c \
  docker compose config --quiet
```

## 回滚点

| 阶段 | 回滚 |
|---|---|
| A 后 | 回退提交；Pi 开关置 0（inproc 仍在） |
| B 后 | 回退提交（inproc 实现随之回来）；生产未部署则无影响 |
| C 后 | `deploy-prod.sh <前一个 sha>`；迁移 0055 不回退（memory #399），代码回退到 E1 前会造成 schema 不一致，故 C 阶段失败优先向前修复 |

## 记录义务

- design §2.3 的「E5 第 2 项不适用」结论写入任务 implement.md 完成记录。
- 若阶段 C 未执行，必须在交付说明中写明「生产仍在旧架构」，不得以本地检查替代生产结论（memory #443）。

## 验证结果（2026-09-28）

### 本地

- `backend/.venv/bin/pytest -q`：**1926 passed, 3 skipped**（基线 1945；差额 = 删除 20 个只测已删进程内 HTTP 客户端的用例，新增 1 个保留的响应上界用例）。
- `backend/.venv/bin/mypy app`：通过。
- `agent`：type-check / lint / **202 tests** / build 通过。
- backend `ruff check .` / `ruff format --check .` 仅剩既有 `tests/test_invitation_reachability.py` 的 1×E501 + 1×F841（与本任务无关，未改）。
- `docker compose config --quiet` 通过；解析值 `FG_AGENT_ROLE=assistant`、`STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE=2`、`STEWARD_PI_RUNTIME_ENABLED=0`。

### 生产（分两步，可回滚）

1. **先部署 `9c9abcd`（= main，含 0055 迁移、仍 inproc）**：4 容器 healthy、迁移 0054→0055、公网 health 200、admin 未外泄。数据核对：54 calls / 389 plans / 19 cards / 75 facts / 51 users 全在，`steward_assist_batches` 已消失。
   - 迁移先在隔离副本上演练过（`/tmp/fg-migtest`，DATA_DIR 隔离，memory #359）：0055 后 54 行保留、388 plans、cards/facts 不变。
2. **再部署 `fa3a0c8`（本任务）+ 同时开启 `STEWARD_PI_RUNTIME_ENABLED=1`、`FG_AGENT_ROLE=both`**：4 容器 healthy、迁移不变（本任务无新迁移）、公网 health 200、admin 未外泄。

### AC-7 结果

- **Pi 链路已在生产生效**：sidecar 持续轮询 `POST /internal/agent/steward/attempts/lease`（204 = 已授权、无工作），08:40 的调度扫描为空间 2 预留了 **9 个 `carrier='pi'` attempt**（迁移前恒为 `inproc`）。
- **但未产生成功的真实 Pi 调用**：9 个 attempt 全被发送前栅栏退为 `skipped`，原因码 `provider_unavailable`。
- 根因（**与本次改动无关**）：`resolve_runtime(space=2, agent_kind=steward)` 返回 `None`，因为空间 provider 设置的 `model='workbuddy/gpt-5.6-sol'` 不在该 provider 的 `allowed_models=['deepseek-v4.1-flash']` 内 → `policy_result='denied'`、`reason='model_not_allowed'`。
  - 该上游实际服务 `workbuddy/gpt-5.6-sol`（已用只读 `/v1/models` 探针确认），所以是**平台配置漂移**，不是上游不可用。
  - **同一漂移也阻断 assistant**：`resolve_runtime(space=1|2, agent_kind=assistant)` 同样为 `None`。这不是 Steward 专有故障。
  - **早于本次改动**：迁移前的 54 行里已有 14 次 `provider_unavailable`（最早 2026-09-20 07:27），且当时唯一的成功调用用的是旧 provider 1（`gpt-5.6-sol`）。
- 因此 AC-7 只能部分满足：**「生产已切到 Pi 且链路可达」已验证**，「一次真实 Pi 调用闭环（结算 + egress 审计）」被上游模型白名单配置阻塞，未达成。按 memory #424 不把「已启用」等同于「已产生合法输出」。

### 附带发现（迁移 0055 的历史行处理）

部署 `9c9abcd`（纯 main、0055）后，14 行原本 `succeeded` 的 in-process 时代记录变为 `skipped/provider_unavailable`。原因是 `recover_stuck_attempts` 的崩溃点④分支选择条件是 `status IN ('succeeded','degraded') AND applied_at IS NULL`，而迁移把历史行的 `applied_at` 一律留为 NULL（设计如此，见 0055 注释「never backfilled」），于是历史行被当成「已持久化未写回」重跑栅栏，栅栏在当前 provider 配置下不过，就退成了 `skipped`。
- **未造成用户可见数据丢失**：部署前后 `reason_text_llm=4`、`presentation_rank=2`、`steward_term_projections=458`、`steward_llm_candidates=29` 逐项相同。
- 但账本语义被改写（`succeeded` → `skipped`），与 memory #399「不得把已修正的数据破坏性地恢复为旧种子值」和本任务 spec「历史 `inproc` 行不回写」的**意图**相冲突。这是一个独立缺陷，需另立任务（本任务范围是载体收敛，不含 0055 的历史行语义）。

### 回滚

两步都可回：代码 `deploy-prod.sh 9c9abcd`（该 sha 仍 inproc 且已过 0055，Steward 照常工作）；环境变量备份在 `~/.config/familygraph/familygraph-prod.env.bak-20260928-081332`。迁移 0055 不回退（memory #399）。
