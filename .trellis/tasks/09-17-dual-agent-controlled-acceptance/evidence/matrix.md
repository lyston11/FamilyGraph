# F 受控验收：matrix、证据与结论

基线：`main@802925f`（含 09-18 P0-1/P0-2/P1-1 与 E 全部集成）。
全程零真实模型费用：假上游只模拟协议与时序，真浏览器 + 真 sidecar + 真 FastAPI。

| 环境 | 版本 |
| --- | --- |
| Python | 3.12.12 |
| FastAPI / SQLAlchemy / Alembic / httpx / pydantic | 0.115.6 / 2.0.36 / 1.14.1 / 0.28.1 / 2.13.4 |
| SQLite | 3.51.1 |
| Node | v24.14.1（`@earendil-works/pi-coding-agent` 0.84.3，tsc 5.8，vitest 3.2） |
| 隔离 | `mkdtemp(fg-controlled-*)` + `free_ports(5/6)`；与 launchd 隧道 8000/8001/8002 分离 |

复现入口（两个脚本，均自动分配端口、迁移隔离库、退出即删除 DATA_DIR）：

```bash
# 助手/管家/观测/重试矩阵（真实 sidecar 进程内驱动，29 格 + 6 个复用套件格）
python3 scripts/smoke/run_controlled_acceptance.py --report /tmp/f-matrix.json

# 真浏览器 + 真实传输链路（真 sidecar 进程 + 真前端构建 + headless Chrome，14 格）
python3 scripts/smoke/run_browser_acceptance.py --report /tmp/f-browser.json \
  --screenshot .trellis/tasks/09-17-dual-agent-controlled-acceptance/evidence/browser-claim.png
```

## 逐格结果

`run_controlled_acceptance.py`：**35 / 38 通过**（3 失败，见缺陷 D-F1/D-F2；其中
A3-4 是判定 D-F2 因果的诊断格）。
`run_browser_acceptance.py`：**17 / 17 通过**。

### A 组 助手（F-R1）

| 格 | 断言 | 结果 | 关键证据 |
| --- | --- | --- | --- |
| A1-run | 无工具轮真实链路达终态 | pass | `worker=pass run=succeeded` |
| A1-1 | 首段正文在完整答案前到达公共流 | pass | 3 delta 帧，顺序 `text_delta` < `message.assistant_added` |
| A1-2 | 权威消息恰好一次且内容完整 | pass | `assistant=1` |
| A1-3 | sidecar 源计时已持久化 | pass | `timing_json` 2 行 |
| A1-4 | **源计时与持久间隔可区分** | pass | 源 `2712ms` vs 持久间隔 `2319ms`（flush 量化 250ms） |
| A1-5 | **注入口延迟归到正确阶段** | pass | 注入 900ms → `first_text_ms=908`，`queue_wait=973ms` 未被污染 |
| A2b-1/2 | 只读工具轮：工具入流 + 恰一条权威正文 | pass | `tools=2 assistant=1` |
| A3-2 | 压缩后仍产出权威正文 | pass | `run=succeeded assistant=1` |
| A3-3 | 摘要请求与作答请求分开，历史源完整 | pass | 摘要调用 1 次，`context_messages=23` |
| A3-1 | 压缩作为 `model_turn` 子成分单列 | **fail** | `compaction_ms` 采样 0 |
| A3-4 | 真实 SDK 的压缩落在 `turn` 内（D 的前提） | **fail** | `compaction_starts=2 in_turn=0` |
| A4-1 | 两 run 排队后均达终态 | pass | 均 succeeded |
| A4-2 | `queue_wait` 用 `first_leased_at` 而非持久事件 | pass | `[0.454s, 1.326s]`，第二个真实等待 |
| A5-permanent | 永久 4xx 恰好 1 次出站 | pass | 出站 1 次（期望 1） |
| A5-permanent-audit | 该尝试留恰好一条 egress 审计 | pass | `egress_rows=1` |
| A5-transient | 两层重试仍生效 | pass | 24 次出站（`6×4`），且审计 25 行 |
| A6-2 | 取消后不产生权威正文/增量帧 | pass | `assistant=0 deltas=0` |
| A6-1 | 取消收敛为 `cancelled` | **fail** | `run=failed error=SIDECAR_ERROR` |

### B 组 管家（F-R2，复用既有套件，不重写）

| 格 | 覆盖 | 结果 |
| --- | --- | --- |
| B1 | 总截止：等待 headers / 等待正文 / 持续慢 chunk 三类阻塞在预算内收敛且连接释放 | pass（4 passed） |
| B1b | connect 等待、发送前预算不足（未发送）、结算预留与释放不计费 | pass（4 passed） |
| B1c | 在**真实 HTTP transport 层**验证总截止与慢 chunk 上限、不阻塞其他空间写 | pass（3 passed） |
| B2 | 逐笔结算先于下次发送、混合批次独立成功产物、保守计费、四 kind、空 array 合法结果 | pass（10 passed） |
| B3 | 失租/接管/中断：旧执行者零业务写回（禁用、切 provider、卡终态、证据变化、进程被杀） | pass（7 passed） |
| B4 | 崩溃点恢复：发前 / 发后未审计 / 写回前各自收敛 | pass（3 passed） |
| B5 | 预算：次数、token、超长 prompt、输出 cap、usage 缺失按预留计费 | pass（5 passed） |

**B 组证据边界**：这些用例用 fake transport 与注入时钟，证明**程序合同**（预算、栅栏、
幂等、四 kind、空结果处置），不证明真实上游的时延分布或模型输出质量。
B1c 是其中唯一在真实 HTTP transport 层测量总截止的格（不靠 fake 时钟）。

### D/E 组 观测与错误分类（复用）

| 格 | 覆盖 | 结果 |
| --- | --- | --- |
| D1 | 聚合口径：分母不丢、`basis` 不混精度、`model_turn` 不冒充正文首字 | pass（21 passed） |
| D2 | 增量分片不物化 `AgentMessage`、payload fail-closed | pass（9 passed） |
| E1 | 网关错误分类：上游 4xx 不被折叠为可重试 5xx、egress 审计 | pass（29 passed） |

### UI 组 真浏览器与传输链路（F-R4/R5）

| 格 | 断言 | 结果 | 关键证据 |
| --- | --- | --- | --- |
| UI1-1 | 公共 SSE 真实到达浏览器且达终态 | pass | 13 帧，`run.settled` 到达 |
| UI1-2 | **临时正文先于权威消息到达** | pass | delta `2332ms` < 权威 `7654ms` |
| UI1-3 | 临时正文在真实 DOM 可见 | pass | 7 个 provisional 帧，34 次渲染采样 |
| UI1-4 | 权威正文**整体替换**临时投影（不重复追加） | pass | `authoritative=1`、终态 `dom_provisional=0`、文本完全一致 |
| UI2-1 | 刷新后保留且不重复、无残留临时气泡 | pass | 刷新后 2 条（user+assistant），`provisional=0` |
| UI2-2 | 终态后无无限等待、无错误横幅 | pass | `pending=False error=None` |
| UI2-4 | **断线后带 `Last-Event-ID` 续传：无重复、无缺序** | pass | 中止于 seq 3 → 续传 `4..12`，合并 `0..12` 连续且无重复 |
| UI2-5 | **跨空间切换：旧空间消息不残留** | pass | 切到「第二个家族」后面板 `after=0`，旧正文零泄漏 |
| UI2-3 | 分片节奏与注入上游一致 | pass | 到达间隔 `691/710/705/707/706/708ms` vs 注入 `700ms` |

`UI1-2/1-3/1-4` 三格合起来正是 09-18 要求的「首段正文可见时间、权威最终替换、
失败不伪装成功」补验收；`UI2-1` 覆盖「重连不重复」、`UI2-4` 覆盖「断线 Last-Event-ID
续传无重无漏」、`UI2-5` 覆盖「跨空间不串内容」。

**UI 组证据边界**：真实传输链路成立**不等于**真实 Provider 质量或线上延迟成立。
浏览器 `performance.now()` 为单机 monotonic，与服务端 UTC、sidecar monotonic
不可直接相减，本矩阵不报告跨机毫秒精度。

## 门禁

| 检查 | 结果 |
| --- | --- |
| `ruff check .` / `ruff format --check .` / `mypy app` | 全绿（206 源文件） |
| `pytest`（backend 全量） | **1707 passed, 3 skipped, 1 failed** |
| `vitest run`（agent 全量） | 163 passed / 18 files |
| `npm test`（frontend 全量） | 763 passed / 72 files |
| `agent npm run lint` / `type-check` | 全绿 |
| `frontend npm run lint` / `type-check` | 全绿 |
| `npm run build`（agent / frontend） | 成功 |
| `./scripts/frontend-api-smoke.sh` | **pass 56/56**，退出 0 |

backend 唯一的失败是 `test_steward_candidate_evidence_integration.py::
test_lease_expiring_while_actual_writeback_waits_for_sqlite_writer_is_not_adopted`
（SQLite writer 竞态，隔离单跑通过 2/2），与本次改动无文件交集，属既有 flake。

## 缺陷（发现即如实记录，不在验收任务里改业务代码）

### D-F1（P1）取消在正常心跳节奏下收敛为 `failed` 而非 `cancelled`

- **现象**：真实 sidecar 运行中取消，`cancel_http=200`，但 run 终态是
  `failed / SIDECAR_ERROR`，公共流只有 `run.failed`。3/3 复现（`status_before_cancel=running`）。
- **机理（已用内部请求台账证实）**：sidecar 的取消检测**只**来自心跳
  （`worker.ts` `startHeartbeat`，间隔 `max(floor(defaultLeaseMs/3),1000)`，出厂 60s 租约 → **20s**）。
  取消后心跳在 20s 内不会到来，而在飞的 `events/append` 立刻返回
  `409 AGENT_RUN_NOT_RUNNING "Run 已请求取消"`；该 409 被 `InternalClient.request`
  归类为 `ConflictError` 后抛出，`executeJob` 的 catch 只看
  `active.cancelRequested || active.leaseLost`（两者都还是 false），于是走默认分支
  settle `failed + SIDECAR_ERROR`。此后服务端按「已取消」终态化，用户看到的是
  「助手服务暂时不可用」而不是「已取消」。
- **判别实验（证明因果，非推测）**：把 sidecar 租约降到 3s（心跳 1s）后，
  心跳在取消前到达，走 `markCancelRequested` → 同一场景不再产生
  `SIDECAR_ERROR`（`run=running`，`settle_requests=[]`，心跳 2 次 200）。
  即失败与否取决于心跳是否赶在在途 `append` 之前到达。
- **证据文件**：`evidence/defect-cancel.note.md`
- **归属建议**：`09-17-assistant-retry-governance`（错误分类属 E 定义的边界）。
  修复方向是让「服务端已裁决取消」这一 409 被识别为权威终态信号
  （心跳之外的第二条路径），而不是让在飞 append 的 409 变成 sidecar 自造失败。
- **覆盖漏洞**：backend `test_agent_queue.py` 只覆盖 `queued` 取消立即终态与
  heartbeat 不延长被取消租约，**没有**覆盖「sidecar 在飞时服务端改判」这条路径。

### D-F2（P2）`compaction_ms` 在真实 SDK 路径下系统性为空

- **现象**：真实 SDK 的阈值压缩**确实发生**（`compaction_start:threshold`×2、
  `compaction_end`×2，摘要请求实发 1 次，`context_messages=23`），但持久化的
  `timing_json.compaction_ms` 采样数为 0。
- **机理（已用 SDK 广播顺序证实）**：真实 SDK 把压缩排在**轮次边界之外**：
  `compaction_start/end → agent_start → turn_start → … → message_end → turn_end → agent_end →
  compaction_start/end → agent_settled`。而 `RunEventBuffer` 只在
  `turn_start → message.assistant_added` 之间累积 `turnCompactionMs`，
  并在 `message.assistant_added` 后清零，因此两处压缩都不在窗口内，`compaction_ms` 永远缺省。
  这与 D 的合同注释「摘要请求发生在 turn 内」不一致——**合同前提在真实 SDK 下不成立**。
- **证据文件**：`evidence/defect-compaction.note.md`
- **归属建议**：`09-17-assistant-timing-observability`（D）或 F 自身作为
  「D 合同与真实 SDK 行为不符」的发现；需先决定压缩应归属哪个阶段
  （例如单列 `compaction_ms` 于 `run.started` 或新增阶段），不能只把窗口放宽
  到整轮，否则会把真正等待模型的时间算进压缩。
- **覆盖漏洞**：`agent/test/events.test.ts` 用合成的 `turn_start → compaction_* →
  message_end` 顺序锁定聚合，**没有**用真实 SDK 广播顺序做回归，因此这个前提
  错误未被单元测试捕获。

## 未覆盖 / 明确不做

- **取消与失权的浏览器可见语义**：UI 组没做「点取消后页面如何呈现」与
  「权限被撤回后页面如何呈现」两格。原因：取消路径本身在 A6-1 已被证实有缺陷
  （D-F1），先验证 UI 会把缺陷固化成一个「看起来正常」的断言；失权场景需要
  第二个账号与 membership 变更，属另一轮环境搭建。两者都待 D-F1 修复后补。
- 真实 Provider 的模型质量、线上 TTFT/p95（G 的范围，需批准费用）。
- 跨机时钟精度（需另建探针对自建流测传输；本矩阵不声明跨机毫秒）。
- steward 真实上游时延（fake transport 只能证明程序合同）。
- GUI 布局人工走查（375px 双主题、浮层可关等）—— 属前端质量门禁例行范围，
  本次只覆盖助手面板的可见性、替换语义与跨空间投影。
