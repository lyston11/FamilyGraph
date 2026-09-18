# 助手低延迟：执行计划

规划阶段已完成只读生产核查与 Pi vs FamilyGraph 对比分析（见 `research/latency-evidence-2026-09-18.md` 和 `research/pi-vs-familygraph-latency.md`）。主会话内联执行，不使用子智能体。

**当前状态**：已完成调查并确定根因，进入实施阶段。

## 实测基线（决定了顺序）

### 生产环境历史 run（2026-09-18）

线上两次真实 run：排队 0.94s / 11.05s；工具执行约 1ms；结算与正文相差约 11ms。

→ 首轮读表把「单轮 33.15s / 53.04s」当成模型生成，**当晚用出站审计更正**：那是「失败请求 + 退避 + 成功请求」之和，成功请求本身为 3.9–19.0s。主导的可避免项是**上游不稳定被请求层重试放大**，含两种形态：慢失败（单次 503 耗时 29.8s）与快失败+用满退避（5×502 + 请求层总退避约 11.6–15.5s）。

### Pi vs FamilyGraph 对比（2026-09-18 统计）

**核心发现**：Pi 的「1-2 秒」体感来自**首 token 或 thinking/正文开始滚动**，而非完整回答结束。

对本地 Pi 约 33 个会话、1.2 万条 assistant 消息的统计（同一 `liu-dada/gpt-5.6-sol` 模型）：
- 完整生成时长（`message.timestamp` → `message_end`）**中位数 = 11.47 秒**
- 低于 3 秒的比例仅 **0.8%**
- 从用户提问到首条有正文 assistant 消息的中位数 = **24.34 秒**（含工具轮）

**根本差异**：
- **Pi**：SDK delta → 实时显示 → 用户 1-2s 看到首字 → 持续滚动 → 10-20s 后完整
- **FamilyGraph**：SDK delta → **被显式丢弃**（`events.ts` 只处理 `message_end`）→ 用户等待 10-30s 空白 → 整段出现

详见 `research/pi-vs-familygraph-latency.md`。


## 优化策略与执行顺序

根据 `research/pi-vs-familygraph-latency.md` 的调查结果，按优先级分级执行：

### 🔴 P0 - 立即执行（低成本 + 高收益）

#### P0-1: 部署已修复的轮询优化 ⚡️ ✅ 已完成（2026-09-18）

**现状**：worktree b067079 已改 2000→250ms + 去掉后睡眠。

**已完成**：b067079/4744549 已并入 main（d0ad77d）并推送；远端 backend 与 agent sidecar 均已 git pull + 重建 dist + 重启（`familygraph-api` / `familygraph-agent` 均在 2026-09-18 08:12 UTC 后运行新版本）。健康端点与 sidecar 启动日志正常。

**待验**：真实 agent run 的排队时间（`first_leased_at - created_at`）< 1s 仍需真实样本；未取得真实 run 前不写“已生效”。

#### P0-2: 实现安全增量显示 🔥 ✅ 已完成（2026-09-18，待部署与真实浏览器验收）

**现状（已修复）**：`events.ts` 曾丢弃所有 `message_update`

**收益**：体感从"等 10-30s 看到答案"变成"1-2s 看到首字，然后持续滚动"（**体感收益最大**）

**已实现的协议（与规划草案不同，以下为实际交付）**：

1. **sidecar 映射**（`agent/src/events.ts`）：新增 `assistant.text_delta` / `assistant.text_reset` 两个事件类型。
   - `mapSessionEvent` 保持**无状态**，不映射 `message_update`（草案中的 `content_index`/`message_id` 字段已去掉：SDK 未提供跨 delta 稳定的消息 id，且聚合在 buffer 内完成，不需要它）。
   - 有界聚合在 `RunEventBuffer`：prose 累积到非 `message_update` 事件处 flush，单帧上限 `MAX_PROSE_FRAGMENT_CHARS=2000` 码点（按码点切分）。
   - `auto_retry_start`（SDK 丢弃失败尝试并在同 turn 内重生成）发 `assistant.text_reset`。
2. **后端**（`backend/app/services/agent_events.py`）：注册两个类型；`_validate_provisional_payload` 做闭合形状校验（额外字段/空 delta/超 `MAX_PROVISIONAL_DELTA_CHARS=4000` 一律 422）；**不物化 `AgentMessage`**。
3. **前端**（`stores/agent.ts`）：`assistant.text_delta` 追加到 `provisional: true` 临时气泡；`text_reset` 或权威 `message.assistant_added` 到达时移除临时投影；临时气泡不进 `replayCursor`、不进 aria-live 播报（`MessageList.vue` 显示「生成中…」）。

**输出安全（已核实，不是假设）**：当前 append 路径与 sidecar `message_end` 都**没有**输出侧正文扫描（`policy_guard` 只覆盖 input/tool_call/tool_result/context/before_provider_request 与 steward 出站）。所以增量分片与完整消息面对的是同一个（缺失的）检查：不得声称「前缀检查等价」，也不得把最终覆盖当作对已泄露片段的「撤回」。若后续要收紧，必须先有实际输出侧检查。

**成本**：中等（已完成前后端改动 + 回归测试）

**归属**：本任务（已承接已归档 H 的协议与安全合同，见 design「增量安全合同」）

**验证（已跑）**：`agent` 160 tests（含真实 SDK 的 `assistant-delta-gap.test.ts`）、`backend` 全量 1703 passed / 1 无关 flaky（隔离复跑通过）、`frontend` 762 tests + `type-check` + `lint` + `build` 均通过。

**待验**：真实浏览器首字时刻（服务端时钟不能证明浏览器渲染时刻，见 spec §4）。

---

### 🟡 P1 - 近期执行（中成本 + 中高收益）

#### P1-1: 稳定 prompt cache key 💾 ✅ 已完成（2026-09-18，待部署）

**现状（已修复）**：每 run 新建 `SessionManager.inMemory()` → sessionId 是随机 uuidv7 → `prompt_cache_key` 每次变化 → cache miss

**已核实的前提（不是假设）**：
- 本部署 `agent_providers.api = openai-responses`（远端实际值），而 `openai-responses` 的 `buildParams` 在 `cacheRetention !== "none"` 时**总是**发 `prompt_cache_key`（那个 `api.openai.com` 门控属于 `openai-completions`，不是生产路径）。已用真实 `streamSimple` + 假 fetch 捕获请求体验证：key = 传入的 sessionId。
- SDK 链路：`sdk.js` 把 `sessionManager.getSessionId()` 放进 stream options → 适配器 `clampOpenAIPromptCacheKey(options.sessionId)`（上限 64 字符）。
- 同一上游同模型在本地 Pi 下有 72%（261/359 行）cacheRead>0，说明上游确实有缓存能力，不是无效优化。

**实际实现（与草案不同）**：
```typescript
const sessionManager = SessionManager.inMemory(agentDir, {
  id: `fg-${projection.account_id}-${projection.session_id}`,
});
```
- 草案的 `new SessionManager(agentDir, "", piSessionId, false, settingsOptions)` 已废弃：那个 4 参位置签名不对应当前 SDK（实际是 `(cwd, sessionDir, sessionFile, persist, newSessionOptions, preloadedFileEntries)`），改用 `inMemory` 的 options 重载。
- **加了 account_id**（草案只有 session.id）：上游缓存按 provider 账号隔离，同一部署下的两个 FG 账号不能互相污染对方的会话分区。
- 只影响 cache key（压缩读的是 entries，不是 id），不改其他会话行为。

**收益**：同一 FG 会话的第 2+ 次 run 可复用前缀（估计 TTFT 省 20-40%）。**尚未实测**，需真实同会话连续两次 run 的 usage 对照。

**回归**：`agent/test/session-cache-key.test.ts`（3 个，已反向验证：去掉修复后「stable」用例失败）。agent 163 tests 全通过。

**成本**：小（已改完）

**归属**：09-18（本任务）

#### P1-2: 降低请求层重试预算 ⚠️

**现状**：`providerStreamMaxRetries=5`，观测尾部 11.6-15.5s 全是退避

**收益**：尾部从 15s 降到 3-5s（降到 1-2 次重试）

**技术路径**：
- 方案 A：直接降到 `maxRetries=2`，`maxRetryDelayMs=8000`
- 方案 B：实现分类重试（502/503 可重试 2 次，401/403/429 不重试）

**风险**：降低后可能更多请求直接失败 → 转到会话层重试（更贵）

**成本**：小（改 config + 可选的分类逻辑）

**归属**：E-R5（**需用户批准**）

---

### 🟢 P2 - 优化补齐（低成本 + 小收益，积少成多）

#### P2-1: 减少 per-chunk DB 事务

**现状**：每个 SSE chunk 都 `rollback + get(AgentRun)`

**收益**：~200-500ms

**技术路径**：
```python
# provider_proxy.py passthrough_with_audit
_refresh_run_gate(db, run_id)  # 只在流开始检查一次
try:
    async for chunk in upstream.aiter_raw():
        yield chunk  # 中间不检查
        bytes_read += len(chunk)
finally:
    _refresh_run_gate(db, run_id)  # 流结束再检查
```

**归属**：09-18（本任务）

#### P2-2: 复用 httpx client

**现状**：每请求 `async with httpx.AsyncClient()` → TLS 握手每次重来

**收益**：~100-500ms

**技术路径**：
```python
# main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.proxy_client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10))
    yield
    await app.state.proxy_client.aclose()

# provider_proxy.py
async def passthrough_with_audit(..., request: Request):
    client = request.app.state.proxy_client
```

**归属**：09-18（本任务）

---

### ⏸️ 暂缓（需产品决策或后续评估）

- **取消强制工具轮**：简单问题节省 4-10s，但需重新权衡准确性 vs 速度（产品决策）
- **thinking level 降档**：可能节省 2-5s，但复杂推理质量下降（归 I 任务）


## 验收与收尾

- 按 `prd.md` 的 LL-AC1～AC6 逐条给证据；真实提速结论必须有同配置前后对照。
- 未达目标保持 `in_progress`，不归档、不写"已生效"。
- 更新 `agent-runtime` spec 与父任务 summary；提交、串行集成、清理 worktree/分支。

## 命令

```bash
cd agent && npm run lint && npm run type-check && npm test && npm run build
cd ../backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
.venv/bin/pytest -q tests/test_agent_events.py tests/test_admin_agent_latency.py
.venv/bin/pytest -q
```

新测试沿现有 `agent/test/events.test.ts`、`worker.integration.test.ts`、`backend/tests/test_admin_agent_latency.py` 组织；不升级依赖。迁移在隔离库执行 `alembic upgrade head` 后再跑受影响测试。真实模型调用需单独批准样本与上限，本轮不自动执行。

## 回退

每项优化各自独立可回退：
- P0-1：配置项，回退环境变量或代码
- P0-2：事件类型，旧客户端忽略未知事件；**回退顺序为前端→sidecar→后端**（不可倒：后端对未注册事件类型返回 422，会让整批 append 失败）
- P1-1：session 构造参数，回退后恢复随机 ID
- P2-1/P2-2：代码逻辑，回退不影响外部合同

禁止只回退一端导致协议/行为不一致。

## 总预期收益（全做完）

**体感**：
- 从：10-30s 空白 → 看到答案
- 到：1-2s 首字 + 持续滚动 → 看到答案
- **质变**：用户感知从"卡住了"变成"正在打字"

**实测**：
- 干净场景：20s → 12-15s
- 含重试场景：33-53s → 18-25s
