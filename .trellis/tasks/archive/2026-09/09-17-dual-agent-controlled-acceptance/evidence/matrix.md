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

`run_controlled_acceptance.py`：**41 / 41 通过**。
`run_browser_acceptance.py`：**19 / 19 通过**。

D-F1/D-F2/D-F3 三个缺陷已分别在 `09-17-assistant-retry-governance`（取消分类 + 快速收敛）、
`09-19-assistant-compaction-attribution`（压缩 run 级归属）与
`09-19-assistant-provisional-terminal-mark`（取消后终态标记）修复并集成；上表为集成后复跑。

### A 组 助手（F-R1）

F-R1 列举的 11 类场景与格的对应（缺一格即视为未覆盖）：

| F-R1 场景 | 覆盖格 | 执行方式 |
| --- | --- | --- |
| 无工具 | A1-run、A1-1..A1-5 | 受控场景（真实 sidecar 进程内驱动） |
| 只读工具 | A2b-run、A2b-1/2 | 受控场景 |
| 纯工具轮 | A2a | 复用 `agent/test/worker.integration.test.ts`（真实 Pi SDK + 假上游） |
| 多轮 | A2a、A3-2/A3-3 | 同上 + 受控场景 |
| 自动压缩 | A3-1..A3-4 | 受控场景（真实 SDK 阈值路径） |
| 两 run 排队 | A4-1/A4-2 | 受控场景 |
| 单失败成功 | A5-layers | 复用 `agent/test/retry-governance.test.ts` |
| 失败耗尽 | A5-layers、A5-transient | 同上 + 受控场景 |
| 空最终回答 | A2a | 复用 `agent/test/worker.integration.test.ts` |
| 取消 | A6-1/A6-2 | 受控场景 + UI2-6/UI2-7（真浏览器） |
| 失租 | A6-lease、B3 | 复用 agent 回归 + 管家受控格 |

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
| A3-1 | 压缩作为 run 级阶段单列（`run.compacted`） | pass | `compaction_samples=1` |
| A3-4 | 真实 SDK 的压缩落在 turn 之外（run 级归属前提） | pass | `compaction_starts=2 in_turn=0 after_agent_end=1` |
| A4-1 | 两 run 排队后均达终态 | pass | 均 succeeded |
| A4-2 | `queue_wait` 用 `first_leased_at` 而非持久事件 | pass | `[0.454s, 1.326s]`，第二个真实等待 |
| A5-permanent | 永久 4xx 恰好 1 次出站 | pass | 出站 1 次（期望 1） |
| A5-permanent-audit | 该尝试留恰好一条 egress 审计 | pass | `egress_rows=1` |
| A5-transient | 两层重试仍生效 | pass | 24 次出站（`6×4`），且审计 25 行 |
| A6-2 | 取消后不产生权威正文/增量帧 | pass | `assistant=0 deltas=0` |
| A6-1 | 取消收敛为 `cancelled` | pass | `run=cancelled converged_after_s=0.01` |

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

### sidecar 复用套件（真实 Pi SDK + 假 provider 流）

| 格 | 覆盖 | 结果 |
| --- | --- | --- |
| A2a | 纯工具轮 / 多轮 / 空最终回答的终态与「不熄等待」 | pass（`worker.integration.test.ts`） |
| A5-layers | 单失败后成功、失败耗尽的真实出站数、退避中取消中断 | pass（`retry-governance.test.ts`） |
| A6-lease | 心跳拒绝（撤权/失租）后跳过结算且不回写 | pass（`worker.integration.test.ts`） |

**证据边界**：这三格证明 sidecar 的程序合同与状态机；不证明真实上游的时延分布、
模型质量或线上可用性（属 G）。

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
| UI2-6 | **浏览器取消：终态由服务端裁决为 cancelled，不显示为失败** | pass | `cancel_btn=True`、`run.cancelled`（无 `run.failed`）、无错误横幅、无无限等待 |
| UI2-7 | **取消后临时正文标为终态（不再声称仍在生成）** | pass | `provisional_after=0`，已显示正文保留 |
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
| `pytest`（backend 全量） | **1712 passed, 3 skipped** |
| `vitest run`（agent 全量） | 167 passed / 18 files |
| `npm test`（frontend 全量） | 766 passed / 72 files |
| `agent npm run lint` / `type-check` | 全绿 |
| `frontend npm run lint` / `type-check` | 全绿 |
| `npm run build`（agent / frontend） | 成功 |
| `./scripts/frontend-api-smoke.sh` | **pass 56/56**，退出 0 |

（上一轮曾出现的 `test_steward_candidate_evidence_integration` SQLite writer 竞态在复跑中未再出现，
该 flake 与本次改动无文件交集。）

## 缺陷与修复（发现即记录，修复在所属任务）

三个缺陷都由本矩阵发现、按 PRD「退回所属任务」修复后集成，并在此复跑通过。
D-F1 修完后又暴露出「取消只能等租约过期（304s）」的第二层问题，一并闭合（见下）。
缺陷的原始失败日志与机理记录保留在 `defect-*.note.md`，未被通过结果覆盖。

### D-F1（P1）取消在正常心跳节奏下收敛为 `failed` 而非 `cancelled` —— 已修复

- **原现象**：真实 sidecar 运行中取消，`cancel_http=200`，但终态是
  `failed / SIDECAR_ERROR`，公共流只有 `run.failed`。3/3 复现（`status_before_cancel=running`）。
- **机理（内部请求台账证实）**：sidecar 的取消检测**只**来自心跳
  （`worker.ts` `startHeartbeat`，间隔 `max(floor(defaultLeaseMs/3),1000)`，出厂 60s 租约 → **20s**）。
  取消后心跳 20s 内不会到来，而在飞的 `events/append` 立刻返回
  `409 AGENT_RUN_NOT_RUNNING "Run 已请求取消"`；该 409 被归类为 `ConflictError`，
  而 `executeJob` 的 catch 只看 `cancelRequested || leaseLost`（都还是 false），
  于是自造 `failed + SIDECAR_ERROR` 覆盖服务端已裁决的取消。
- **判别实验（证明因果）**：租约降到 3s（心跳 1s）后心跳先到，同一场景不再产生
  `SIDECAR_ERROR`（`run=running`，`settle_requests=[]`）——失败与否取决于心跳
  是否赶在在途 append 之前。
- **修复**（`09-17-assistant-retry-governance`，commit `46d7ac9`/`cfb51e2`）：
  1. 后端在取消分支返回机器可读的 `detail.reason=cancel_requested`（`fence_execution`、
     provider 网关及其中流复核），不再靠 message 文本区分；
  2. sidecar 把该 409 映射为新的 `RunCancelledError`，并通过
     `InternalClient.onRunCancelled` 让**任何** run-scoped 响应都能上报取消，
     不再只依赖心跳节奏；
  3. worker 在心跳与 `executeJob` 两处都按取消收敛、不结算。
- **修复后发现并一并闭合的第二个缺陷**：取消现在不再由 sidecar 结算，但也没有
  任何路径收敛它，只能等租约自然过期（实测 **304s** 浏览器仍在「进行中」）。
  因 `cancel_requested` 是终态意图而非租约条件，reaper 改为同时选取被取消的 job
  并直接按 `cancelled` 收敛（不回队），审计记 `reason` 区分取消与真实过期。
- **复验**：A6-1 pass，`converged_after_s=0.01`（原 304s）；A6-2 仍 pass。
- **回归**（均可通过还原修复转红）：backend 真实端点返回 409 + `detail.reason`；
  reaper 在租约健康时收敛被取消 run、且不误回收未取消的活跃租约；agent 客户端
  分类、观察者回调、以及「仅靠 append 取消（心跳保持在 20s 外）产生 0 次 settle」。

### D-F3（P2）取消后临时正文仍标「生成中…」 —— 已修复

- **发现方式**：为 D-F1 补的 UI2-6 探针（真实浏览器点「取消回答」）暴露出第二层
  问题：终态事件已到、无错误横幅、无无限等待，但 `provisional` 气泡仍在，
  `MessageList` 继续渲染「生成中…」。
- **为何是缺陷**：09-18 增量显示设计自己写了
  「failed / cancelled：保留已显示的安全部分并标终态」；实现只做了「保留」，
  没做「标终态」。用户在取消后看到一句永远「生成中…」的半截正文。
- **修复**（`09-19-assistant-provisional-terminal-mark`，commit `25602ba`）：
  `finishRun` 在终态分支去掉 `provisional` 标记（保留正文，不新增用户可见文案）；
  与 `text_reset`（丢弃/隐藏）语义刻意分开。
- **复验**：UI2-7 pass（`provisional_after=0`，正文保留）；UI2-6 拆为独立的
  「服务端裁决」格并 pass，避免两层问题合并成一个布尔而互相掩盖。
- **回归**（还原修复即 4 格转红）：failed/cancelled/expired 均标终态；
  终态后新分片不会拼进已结束的气泡；`text_reset` 仍是隐藏。

### D-F2（P2）`compaction_ms` 在真实 SDK 路径下系统性为空，且轮后压缩被算进 `settle` —— 已修复

- **原现象**：真实 SDK 的阈值压缩**确实发生**（`compaction_start:threshold`×2、
  摘要请求实发 1 次、`context_messages=23`），但 `timing_json.compaction_ms` 采样为 0。
- **机理（SDK 广播顺序证实）**：真实 SDK 在**轮次边界之外**压缩
  （`… → agent_end → compaction_start/end → agent_settled`，实测轮后那笔 **245ms**）。
  累积窗口是 `turn_start → message_end`，故恒采不到；更严重的是那 245ms 落在
  `agent_end → agent_settled` 之间，被 `settle`（最后一个非终态事件 → 终态）**错误吸收**。
  这与 D 的合同注释「摘要请求发生在 turn 内、是 duration_ms 的子成分」直接冲突。
- **修复**（`09-19-assistant-compaction-attribution`，commit `2d6fab1`）：
  删除 `compaction_ms` 子成分字段，改由新事件 `run.compacted`（sidecar-timed，
  `public_payload` 恒为 `{}`）在 `agent_settled` 承载整轮 prompt 的压缩总和；
  只累计 `agent_start` 之后的跨度（prompt 前那次属 `prepare`，不重复上报）；
  聚合口径由逐 turn 改为逐 run；`settle` 因 `run.compacted` 成为最后一个非终态事件
  而自动修正。
- **复验**：A3-1 pass（`compaction_samples=1`）、A3-4 pass
  （`compaction_starts=2 in_turn=0 after_agent_end=1`）；
  backend 断言 `settle` 由 30.5s 降为 27.5s（不再吃掉 8s 压缩）。
- **回归**（按真实 SDK 顺序，替换掉 SDK 不会产生的合成顺序）：agent 断言轮后压缩
  不在 `model_turn` 内、跨度求和、无压缩不发事件、prompt 前压缩不计入；
  backend 断言 `run.compacted` 可带 timing 而 `turn.completed` 不可、
  再上报 `compaction_ms` 属未知字段被拒。

## 未覆盖 / 明确不做

- **失权的浏览器可见语义**：UI 组没做「权限被撤回后页面如何呈现」一格，
  需要第二个账号与 membership 变更，属另一轮环境搭建。
  （取消的浏览器呈现已由 UI2-6/UI2-7 覆盖。）
- 真实 Provider 的模型质量、线上 TTFT/p95（G 的范围，需批准费用）。
- 跨机时钟精度（需另建探针对自建流测传输；本矩阵不声明跨机毫秒）。
- steward 真实上游时延（fake transport 只能证明程序合同）。
- GUI 布局人工走查（375px 双主题、浮层可关等）—— 属前端质量门禁例行范围，
  本次只覆盖助手面板的可见性、替换语义与跨空间投影。
