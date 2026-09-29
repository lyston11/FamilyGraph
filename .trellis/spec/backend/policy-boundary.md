# Agent 策略边界（09-29）

> 适用任务：`09-29-agent-policy-boundary`。
> 本文只定义一个可独立适用的合同：**sidecar 策略 guard 的判据、动作与硬阻断语义**。
> FastAPI 的授权/出站合同见 [agent-runtime.md](agent-runtime.md)；错误码信封见
> [error-handling.md](error-handling.md)。

## 1. Scope / Trigger

改动以下任一处前必读：`agent/src/policy.ts` 的判据或 hook、
`agent/src/worker.ts` 的策略结算分支、`backend/app/services/policy_guard.py` 的
`input_hook` / `tool_call_hook`、策略错误码文案（`frontend/src/api/agent.ts`）。

## 2. 职责边界（最容易搞反的一条）

**权限只来自服务端。** guard 是同步的第二道防线，它不得扩大 FastAPI 已授予的可见性，
也不得仅凭自然语言把整个 run 判失败。

| 判据 | 动作 | 为什么 |
|---|---|---|
| 出现 `system prompt`/`masked`/`绕过限制` 等标记词 | notice（有界诊断） | 关键词是提示，不是授权判定；换个说法就能绕过，所以它承担不了安全保证 |
| 服务端形状的受限字段（`visibility: "masked"`、`masked: true`） | block | 这是服务端合同，不是措辞 |
| 未授权工具、scope 覆盖字段、超限参数 | block | allowlist 与闭合 schema 是真正的边界 |
| 已知密钥出现在待发送正文 | block（不限角色） | assistant 消息不是豁免通道 |
| 凭据字段 / PII | sanitize + notice | 数值 token caps（`max_tokens` 等）必须保持数字类型 |
| 未确认事实 | annotate + notice | 沿用原语义，幂等 |
| 本地/云 Provider 冲突 | block | 服务端与最终出站双重检查 |

- 关键词匹配**只能**产生 notice。把它升回 block 会重新引入「模型转述自己的系统提示
  就被判失败」的误报；PRD R1 与 `agent/test/policy.test.ts` 的普通措辞用例锁定这一点。
- 反向同样不可放宽：删掉结构化 masked/trust/kind 检查、密钥检查或 allowlist 属于删除安全
  边界，不是消除误报。
- 模型自报的 `trust`/`masked` 字段不赋权；正文里出现这些字串也不构成受限数据。

## 3. 动作与协议完整性

- 三态：允许 / 处理后允许 / 阻断。notice 与 sanitize 不污染成功状态。
- **不得**因文字命中删除整条 SDK 消息。删 assistant 消息可能留下孤儿 tool result，
  破坏 tool call/result 配对；改写不透明 thinking 签名会直接使请求失效。
- 因此 `context` hook 只做观测：它记录有界诊断并返回 `undefined`（不修改 messages）。
- 安全提示文本必须是中性且不含标记词的固定串（`WITHHELD_RESULT_TEXT`）。否则 guard 生成的
  安全提示会在下一次扫描中触发 guard 自身——这是本任务修掉的真实缺陷。
- 脱敏与未确认标注幂等：二次经过 `tool_result` → `context` → `beforeProviderRequest` 不重复包裹。

## 4. 硬阻断生命周期（run 级、不可清除）

```text
active --notice/sanitize--> active
active --hard block-----> blocked（首个决定即定案，不可被后续干净 payload 清除）
blocked --retry/context/tool/provider--> 拒绝新的执行
blocked --worker settle--> failed（遵守服务端取消/失租裁决）
```

- 状态存在 `createPolicyGuard` 的 run 作用域闭包内，**不是**模块级单例（否则跨 run 串味）。
- 只有**首个**硬阻断决定定义 `blockCode`；后续事件只作诊断，不按集合遍历顺序覆盖主因。
- 阻断必须**先置位再通知停止**：`session.ts` 的 `onPolicyBlock` → `worker.markPolicyBlocked`
  同步记录并 `abort()`。worker 用 `active.policyBlockCode` 与 guard 的 `blockCode` 双读，
  避免通知与读取之间丢决定。
- `shouldStopToolCalls` 必须包含阻断条件（与 cancel/leaseLost 同级），否则阻断后仍会发出
  新的工具调用。
- **唯一能真正阻止出站的钩子是 `onPayload`**（`session.ts` 直接调
  `policyGuard.beforeProviderRequest`）。SDK runner 的 `emitBeforeProviderRequest` 与
  `emitContext` 都会 try/catch 吞掉 handler 抛错并转成 `emitError`，所以把判据放在那些
  hook 里抛错**不会**阻止出站。`beforeProviderRequest` 抛错发生在 HTTP 请求构建之前，
  pi-ai 会把它转成 stream error 且**不发出请求**。
- SDK 会话层自动重试会重跑整轮（`_prepareRetry`，退避后重新请求）。阻断状态必须活过重试；
  重试期间 guard 再次拒绝，因此出站次数不增加。验收按**实际 transport 计数**判断，不看状态变量。
- `tool_call` 的 `{block, terminate}` 只影响当前工具批次，不是 run 级停止，不能替代上面的机制。

## 5. 错误分类与诊断

### 错误码（sidecar 运行期，沿 settle 字符串通道）

| 检出 | 码 |
|---|---|
| 密钥进入待发送正文 | `POLICY_SECRET_IN_PROVIDER_PAYLOAD` |
| 工具不允许 / scope / 参数违规 | `POLICY_TOOL_BLOCKED` |
| 工具结果越界 | `POLICY_TOOL_RESULT_BLOCKED` |
| Provider 本地/云不满足 | `POLICY_PROVIDER_BLOCKED` |
| 可信合同的受限数据 | `POLICY_MASKED_DATA` |
| 其余策略阻断（含必需上下文合同失效） | `POLICY_GUARD_BLOCKED` |

- **不得**再用 `POLICY_SECRET_LEAK` 当兜底：把非密钥原因报成密钥泄漏既误导排查，也等于宣称
  一次并未发生的泄漏。`POLICY_SECRET_LEAK` 只为历史记录保留前端映射，不用于新结算。
- 正常返回路径与 catch 路径必须映射到同一决定。阻断导致的 abort 在 catch 里也要报
  `policyBlockCode`，不能退化成 `SIDECAR_ERROR`。
- 映射是 `BLOCK_CODE_BY_VIOLATION` 单一真源；改一处即改两条路径。
- 非策略失败（如 `PROVIDER_STREAM_ERROR`）保持自己的码，不得因策略管道存在而被改标。

### 诊断字段

每条 incident 只含固定枚举与 run 内坐标：`rule` / `stage` / `source` / `action` /
`occurrences`，以及可得的 `turnIndex` / `messageIndex` / `toolCallId`。

- **禁止**记录原文、匹配片段、prompt、thinking、密钥、工具输入输出或内容指纹。
  `toolCallId` 先按安全字符集截断，未知工具名不得拼进 detail。
- 去重键 = 坐标 + rule + stage，**不是**正文或其哈希；没有稳定坐标时明确为 unknown。
- 最多保留 64 条不同诊断，其余按计数累计（`incidentTotal` / `incidentsDropped`）；
  首个硬阻断必须保留，notice 不能把它挤掉。
- 日志/回调抛错不得清除或替换阻断决定（否则一次日志故障就变成放行）。

## 6. 后端侧对应判据

`backend/app/services/policy_guard.py` 必须与 sidecar 同步：

- `input_hook` **不**因 `contains_prompt_injection` 阻断（用户自己的话就是自己的 prompt），
  但仍阻断 `contains_secret`。
- `tool_call_hook` 保留 allowlist、版本、参数上限与 secret 检查，去掉关键词阻断。
- `context_hook` 的 `kind=data` / `trust=untrusted_data` / 结构化 masked 拒绝不变。
- `enforce()` 的返回协议与 HTTP 错误域不变（`409` + `POLICY_*`），不新增 notice 动作。
- `contains_prompt_injection` 保留为诊断信号，docstring 已注明它不是授权判定。

## 7. Required validation

```bash
cd agent && npm run type-check && npm run lint && npm test && npm run build
cd backend && .venv/bin/pytest tests/test_policy_guard.py tests/test_agent_tools.py \
  tests/test_steward_tools.py tests/test_provider_proxy.py tests/test_steward_candidate_policy.py
cd backend && .venv/bin/ruff check app/services/policy_guard.py tests/test_policy_guard.py
cd backend && .venv/bin/mypy app
cd frontend && npx vitest run src/api/__tests__/agentErrors.spec.ts
```

关键回归入口：`agent/test/policy.test.ts`（判据/动作/粘性阻断/诊断字段/自触发）、
`agent/test/worker.integration.test.ts`（真实 SDK 下的出站计数、重试、错误分类）、
`backend/tests/test_policy_guard.py`（两侧判据一致）。

变异检查（每次改动后重跑）：删掉粘性阻断、把 masked 措辞改回 block、把占位文本改回含
`masked`、把 `tool_not_allowed` 映射改成密钥码——每一条都必须让某个用例失败。

## 8. 不在本合同的假设

- **assistant 正文没有输出侧 DLP**：guard 覆盖 input/tool_call/tool_result/context/
  provider payload，不覆盖已持久化正文的输出扫描。不得声称本次改动补齐了浏览器输出检查。
- 关键词信号不构成注入防护。真正的防护是工具授权、闭合 schema、可见性投影与 Provider 边界。
