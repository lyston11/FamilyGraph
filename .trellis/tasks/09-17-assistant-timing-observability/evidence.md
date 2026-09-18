# D 执行证据：助手真实阶段计时与重试统计修复（2026-09-17）

基线：`main@4102bb3`（含 C～I 规划）。分支 `feat/09-17-assistant-timing-observability`，
worktree `~/PycharmProjects/fg-09-17-assistant-timing-observability`。
环境：Python 3.12 + backend/.venv、Node + agent/node_modules（复用主检出依赖，未改动）。
全部测试用仓库测试夹具与本地 fake transport，**未访问生产库、未调用真实模型、未部署**。

## 1. 复核反例（修复前，`d8d3668`）

| 反例 | 现象 | 根因 |
| --- | --- | --- |
| 批量 flush 压缩短阶段 | 合成约 125ms 工具执行与其结束事件经真实 `append_events` 后 `created_at` 仅差 **1.301ms** | sidecar 每 250ms 批量 flush；`created_at` 是后端**入库**时刻 |
| 排队被高估 | `run.started` 由 SDK `agent_start` 产生，晚于 `getRunContext` 与 session 创建 | context/session 准备被算进“排队” |
| 单次失败后成功 → 0 | 失败段长 1 时 `末次−首次 = 0`，与“无重试”不可区分 | 只用段窗口表达重试 |
| 失败耗尽丢失 | 审计以失败结尾时尾部段从不收尾 | 仅在遇到成功时结算段 |
| 零事件 run 漏分母 | 分母来自事件集合 | 从 event 集合反推 run |

## 2. 实现

| 变更 | 位置 | 说明 |
| --- | --- | --- |
| `timing_json`（nullable） | `models/agent.py`、迁移 `0051` | sidecar 源计时的**有界内部**记录，永不进 `public_payload` |
| `first_leased_at`（nullable） | `models/agent.py`、迁移 `0051` | attempt 0→1 时写一次、不可变；与可续期 `lease_expires_at` 区分 |
| `EventTimingIn` | `schemas/agent.py` | `{source:"sidecar-v1", duration_ms, compaction_ms?}`，`extra=forbid`、上下界、`compaction_ms ≤ duration_ms` |
| `EventEntry.timing` + 指纹 | `services/agent_events.py` | 参与幂等指纹；与 internal schema 同源校验 |
| 源计时采样 | `agent/src/events.ts`、`worker.ts` | `run.started`=取权→SDK `agent_start`；正文=本轮 `turn_start`→`message_end`；工具=配对 start→end；`compaction_ms`=轮内 `compaction_start`→`end` |
| 聚合重写 | `api/admin_agent_latency.py` | 分母来自 run 表；每阶段 `basis`/`native_n`/`derived_n`；新增 `prepare`、`compaction`、`runs_without_events`、`runs_without_first_lease`；`provider_retry` 结构化 |

`compaction_ms` 是 `duration_ms` 的**子成分**（摘要请求发生在 turn 内），单独上报是为了
区分摘要与生成；无压缩即缺省，不写 0。

## 3. 受控场景实测

| 场景 | 断言 |
| --- | --- |
| 工具 125ms 但同批入库 1.3ms | `tool_call.p50_ms == 125`、`basis == source_clock`（旧实现报 1ms） |
| 取权 9s、准备 2s | `queue_wait == 9000`（用 `first_leased_at`），`prepare == 2000` |
| 历史行无源计时 | `basis == persisted_interval`、`native_n == 0`、`derived_n == 2`，不冒充精确 |
| 轮内压缩 8s / 轮总 20s | 正文事件 `duration_ms=20000` + `compaction_ms=8000`；第 2 轮无压缩不得继承第 1 轮 |
| 单次失败后成功 | `unmeasured_retries == 1`、`retry_segments == 0`、下界 `n == 0`（旧实现不可区分） |
| 连续 3 次失败耗尽 | `exhausted_segments == 1`、下界 `p50 == 10000`（旧实现尾部段丢失） |
| 零事件 run | 计入 `runs_without_events` 且仍在 `runs` 分母内 |
| 负值/畸形 timing | 422 fail-closed；读取侧按 unknown，不进分布不报错 |
| `compaction_ms > duration_ms` | 422（物理不可能，拒绝而非夹紧） |
| `compaction_ms` 用在非正文事件 | 422（防止摘要开销错配到准备/工具阶段） |
| 后端自有事件携带 timing | 422（防伪精度） |
| 同 seq 同 timing 重放 | 幂等 `duplicates`，不重复落行 |
| 家庭主体访问 | 401/403；管理员边界矩阵保持 |

## 4. 迁移与降级安全

- 迁移 `0051_run_event_timing`：两列均 nullable，旧行保持 NULL（历史不可还原，不倒推）。
- `downgrade` 拒绝条件：已有 `timing_json` 或 `first_leased_at` 证据时 `RuntimeError`，
  **先于任何 DDL**（SQLite DDL 不保证事务回滚）。
- 父级拒绝合同先于本迁移 DDL：走位从**父 revision** 开始（复现父迁移自己的 preflight），
  因此相对目标 `-3` 的 “Ambiguous walk” 在本迁移删列前就抛出。
  **反例验证**：去掉该 preflight 后 `test_deep_absolute_downgrade_refuses_before_any_ddl`
  失败（`ACTUAL_ALEMBIC_DDL_COUNT=2`，版本仍停 0051 但两列已丢）；恢复后 6/6 通过。
- 祖先迁移测试的 `HEAD` 常量从 `0050` 更新为 `0051`（跟随唯一 head）。

## 5. 验证命令与结果

```bash
cd backend
.venv/bin/ruff check .            # All checks passed
.venv/bin/ruff format --check .   # 401 files already formatted
.venv/bin/mypy app                # Success: no issues found in 206 source files
.venv/bin/pytest -q               # 1677 passed, 3 skipped

# 定向
.venv/bin/pytest -q tests/test_admin_agent_latency.py tests/test_internal_agent_api.py \
  tests/test_agent_events.py tests/test_agent_queue.py tests/test_agent_browser_api.py \
  tests/test_agent_sse.py tests/test_run_event_timing_migration.py      # 90 passed
.venv/bin/pytest -q tests/test_run_event_timing_migration.py \
  tests/test_steward_candidate_evidence_migration.py \
  tests/test_rag_lifecycle_migrations.py                                # 29 passed

cd ../agent
npm run lint && npm run type-check && npm test && npm run build   # 133 passed
```

## 6. 未做 / 边界

- **未部署、未重启服务、未调用真实模型**：运行版本核对与真实小样本属 G。
- **浏览器收到/渲染时刻未测**：SSE 到达与首帧渲染需浏览器 `performance` 时钟，服务端 UTC
  与浏览器 monotonic 不可直接相减；属 F 的受控全链验收。
- **`provider_retry` 时长仍是下界**：审计只记完成时刻、不记请求开始，段内首次失败自身
  耗时不可知；本任务只保证“次数无歧义 + 单次失败段单列 + 尾部段不丢”，不承诺完整重试预算。
- **请求级关联（run/attempt/turn/request）未落地**：需要 E 先提供各结果路径的 egress 审计；
  D 交付的是协议与聚合消费口径。
- `prepare` 的源计时依赖 sidecar 上报；旧 sidecar 不报时回退后端两次状态转换间隔
  （同一时钟，仍精确），新客户端/旧后端则字段被忽略（nullable）。
