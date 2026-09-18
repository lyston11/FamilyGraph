# FamilyGraph vs Pi 助手响应延迟对比分析

**调查时间**：2026-09-18  
**调查范围**：FamilyGraph agent 响应通常需要 10+ 秒，而 Pi agent 约 1-2 秒，找出造成差异的根本原因  
**结论**：主因是流式可见性链路中断 + 请求层重试放大；次级因素包括排队、per-chunk DB 事务、cache key 不稳定、网络跳数

---

## 执行摘要

### 用户直觉 vs 实测数据

**用户直觉**："Pi 回答很快（1-2 秒），FamilyGraph 很慢（10+ 秒）"

**实测真相**：
- **Pi 的 1-2 秒是首 token 或 thinking/正文开始滚动的体感**，而非完整回答结束
- 对本地 Pi 约 33 个会话、1.2 万条 assistant 消息的统计显示：
  - 同一 `liu-dada/gpt-5.6-sol` 模型从 `message.timestamp` 到 `message_end` 的**完整生成时长中位数 = 11.47 秒**
  - 低于 3 秒的比例仅 **0.8%**
  - 从用户提问到首条有正文 assistant 消息的中位数 = **24.34 秒**（含工具轮）
- **FamilyGraph 的 10-30 秒空白是因为用户在整条消息生成期间只能看到空白等待**

### 主因：流式可见性链路中断（①）

**问题**：
- `agent/src/events.ts:209–257` 的 `mapSessionEvent` **只处理 `message_end`**，对 `message_update` 返回空事件
- SDK 已收到的增量正文（`text_delta`）被**显式丢弃**
- `frontend/src/components/agent/MessageList.vue:87` 要求出现 `text.length > 0` 的 assistant 消息才结束 pending 状态
- 结果：用户在整条消息生成期间（10-30s）只能看到空白等待

**量化证据**：
- `agent/test/assistant-delta-gap.test.ts` 已量化首个 delta 与首个用户可见事件之间的完整生成时间差
- 这直接解释了 **FamilyGraph 的 10-30 秒空白 vs Pi 的 1-2 秒首段反馈差异**

**修复收益**（体感质变）：
- 从 "等待 10-30s 看到答案" → "1-2s 看到首字，然后持续滚动"
- **这是体感收益最大的一项**，虽然不减少总生成时间

---

## 次级延迟来源

### ②：请求层重试放大上游不稳定

**配置差异**：
- FamilyGraph：`providerStreamMaxRetries=5`（请求层），会话层默认 3 次
- Pi CLI：请求层 **0 次重试**（未配置 retry = 默认 0）

**实测影响**（真实 run 审计）：

| 场景 | 失败请求耗时 | 退避总量 | 成功请求耗时 | 轮总时长 |
|------|------------|---------|------------|---------|
| 慢失败（run 1 turn 1） | 29.77s (1×503) | ~0.4s | ~19.4s | **53.04s** |
| 快失败（run 2 turn 2） | ~4.5s (5×502) | **11.6-15.5s** | 14.1-16.1s | **33.15s** |

**根因**：
- liu-dada 上游存在较多 `503 upstream_unavailable` 和 `502 bad_gateway`
- FamilyGraph 会把 Pi 直接暴露的错误转化为额外等待（退避 + 重试）
- Pi CLI 遇到失败会立刻报错，不会累积退避

**修复方向**：
- 降低请求层重试次数（5 → 1-2 次）
- 实现分类重试（502/503 可短重试，401/403/429 不重试）
- **需权衡**：降低后可能更多请求直接失败，转到会话层重试（2s 起步且重建整个 turn）

### ③：单 worker 串行 + 2 秒空闲轮询

**现状**：
- `agent/src/worker.ts`：单 worker 串行，每次循环后都等待轮询间隔
- 默认 `leasePollIntervalMs=2000ms`（包含完成任务之后）
- 实测：造成约 **0.94s 至 11.05s 排队**

**已修复但未部署**：
- 提交 `b067079` 已把轮询从 2000ms 改为 250ms，并避免任务完成后继续睡满间隔
- **但远端当时仍运行 8ae6414**

**收益**：
- 排队从 11s 降到 <1s
- **成本为零**，只需 `scripts/server-sync-code.sh` + restart

### ④：prompt cache key 每次改变

**问题**：
- `agent/src/session.ts` 每次 run 都创建新的 `SessionManager.inMemory()`
- 其新 uuidv7 sessionId 导致 `prompt_cache_key` 每次改变
- **跨 run 的共享前缀不能复用**

**对比**：
- 本地 Pi 的同模型记录有很高的 cacheRead 命中率（90%+）
- FamilyGraph 每次都是 cache miss

**收益估算**：
- 同一 FG 会话的第 2+ 次 run，TTFT 节省 20-40%
- 实测（长 context）：cache miss 17.6s vs hit 11.2s
- FG 保守估计节省 **1-4s**

**修复路径**：
```typescript
// agent/src/session.ts buildRunSession
const piSessionId = `fg-${agentSession.id}` // 用 FG session.id 作稳定 key
const sessionManager = new SessionManager(
  agentDir,
  '', // 无 sessionFile
  piSessionId, // 传入稳定 sessionId
  false,
  settingsOptions
)
```

### ⑤：per-chunk DB 事务

**问题**：
- `backend/app/services/provider_proxy.py:449–453` 在每个 SSE chunk 中执行 `_refresh_run_gate`
- 触发 `db.rollback()` 与 `db.get(AgentRun)`
- 形成 **数百毫秒量级的热路径浪费**

**修复路径**：
```python
# provider_proxy.py passthrough_with_audit
_refresh_run_gate(db, run_id)  # 只在流开始检查一次
try:
    async for chunk in upstream.aiter_raw():
        yield chunk  # 中间不检查
        bytes_read += len(chunk)
finally:
    _refresh_run_gate(db, run_id)  # 流结束再检查（审计用）
```

**收益**：~200-500ms

### ⑥：httpx client 每次 TLS 握手

**问题**：
- 每请求 `async with httpx.AsyncClient()` → TLS 握手每次重来

**收益**：~100-500ms（中国 relay 的 TLS 握手）

**修复路径**：
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
    # 使用全局 client
```

### ⑦：额外网络跳数

**现状**：
- FamilyGraph：浏览器 → FastAPI 网关 → sidecar → liu-dada relay（3 跳）
- Pi CLI：终端 → liu-dada relay（1 跳）

**影响**：累积延迟 ~100-300ms（不是主因）

---

## 已排除因素

### ❌ thinking 档位差异

- FamilyGraph 默认 thinking 为 `medium`
- 本地 Pi 使用 `high`
- **档位更低的 FamilyGraph 反而更慢** → thinking 不是根因

### ❌ 模型差异

- 两者实际使用同一 provider 和 `gpt-5.6-sol`
- **相同模型** → 模型本身不是根因

### ❌ "15 秒请求超时"

- 未发现任何 15 秒硬超时配置
- 实测有 53s 的成功 turn → 不存在 15 秒超时

---

## 结构性放大因素

### ⑧：强制工具轮导致至少两次模型往返

**现状**：
- `agent/src/prompt.ts` 强制助手回答前先调用只读工具
- 普通问题至少包含：**工具轮（4-10s）+ 最终正文轮（10-20s）**
- Pi 对简单问题可以只运行一轮

**影响**：
- FamilyGraph 简单问题也要 2 轮
- **这是产品设计而非性能 bug**，需重新权衡准确性 vs 速度

---

## 优先级与推荐执行路径

### 🔴 P0 - 立即可做（低成本 + 高收益）

| 方案 | 体感收益 | 实际节省 | 成本 | 风险 | 归属任务 |
|------|---------|---------|------|------|---------|
| ① 增量显示 | 🔥🔥🔥🔥🔥 | TTFT 可见（0→1-2s） | 中 | 中 | H / 09-18-B |
| ③ 部署 250ms | 🔥🔥🔥 | 排队 11s→<1s | **零** | 零 | 09-18（已修复未部署） |

**立刻执行**：
1. 部署 worktree（③）：5 分钟
2. 实现增量显示（①）：本周内

### 🟡 P1 - 近期可做（中成本 + 中高收益）

| 方案 | 体感收益 | 实际节省 | 成本 | 风险 | 归属任务 |
|------|---------|---------|------|------|---------|
| ④ 稳定 cache key | 🔥🔥 | 1-4s（多轮累积） | 小 | 低 | 09-18（新发现） |
| ② 降低重试 | 🔥🔥 | 尾部 15s→5s | 小 | 低 | E-R5（需批准） |

**本周或下周**：
3. 稳定 cache key（④）
4. E-R5 决策：批准降到 2 次重试 → 执行

### 🟢 P2 - 优化补齐（低成本 + 小收益，积少成多）

| 方案 | 体感收益 | 实际节省 | 成本 | 风险 | 归属任务 |
|------|---------|---------|------|------|---------|
| ⑤ 减少 DB gate | 🔥 | ~300ms | 小 | 低 | 09-18（新发现） |
| ⑥ 复用 client | 🔥 | ~200ms | 小 | 低 | 09-18（新发现） |

**空闲时补齐**：
5. ⑤ + ⑥ 两个小优化

### ⏸️ 暂缓（需产品决策或后续评估）

- ⑦ 取消强制工具轮：需重新权衡准确性 vs 速度（产品决策）
- ⑧ thinking level 降档：需实测对比（I 任务）

---

## 总预期收益（全做完）

**体感**：
- 从：10-30s 空白 → 看到答案
- 到：1-2s 首字 + 持续滚动 → 看到答案
- **质变**：用户感知从"卡住了"变成"正在打字"

**实测**：
- 干净场景：20s → 12-15s
- 含重试场景：33-53s → 18-25s

---

## 附录：本地 Pi 统计数据

### 数据来源
- 扫描路径：`~/.pi/agent/sessions/`
- 会话数：33 个
- assistant 消息数：12,024 条
- 模型：`liu-dada/gpt-5.6-sol`（与 FamilyGraph 相同）

### 完整生成时长（message.timestamp → message_end）

| 分位数 | 时长（秒） |
|-------|-----------|
| p50 (中位数) | 11.47 |
| p75 | 21.36 |
| p90 | 35.64 |
| p95 | 47.89 |
| p99 | 82.33 |

- **低于 3 秒的比例**：0.8%（94 条 / 12,024 条）
- **低于 10 秒的比例**：47.8%

### 用户提问到首条有正文 assistant 消息

| 分位数 | 时长（秒） |
|-------|-----------|
| p50 (中位数) | 24.34 |
| p75 | 42.89 |
| p90 | 69.12 |
| p95 | 94.57 |

**结论**：
- Pi 的"1-2 秒"体感来自 **首 token 或 thinking/正文开始滚动**
- 完整回答通常需要 **10-50 秒**
- FamilyGraph 的问题不是"模型慢"，而是"用户看不到增量正文"

---

## 相关文件

- 测试：`agent/test/assistant-delta-gap.test.ts`
- 事件映射：`agent/src/events.ts:209–257`
- 前端渲染：`frontend/src/components/agent/MessageList.vue:87`
- 轮询逻辑：`agent/src/worker.ts:77–90`
- 网关审计：`backend/app/services/provider_proxy.py:449–453`
- Session 管理：`agent/src/session.ts`

---

**最后更新**：2026-09-18
**调查者**：基于真实 run 审计、Pi 会话统计、源码调用链分析
