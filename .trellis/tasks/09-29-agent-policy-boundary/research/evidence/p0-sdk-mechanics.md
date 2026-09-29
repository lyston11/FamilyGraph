# P0 实施前合同验证结果（SDK 机制）

锁定版本：`@earendil-works/pi-coding-agent@0.84.3`、`@earendil-works/pi-ai@0.84.3`。
本轮只读 `node_modules` 类型/实现与既有测试，未改代码。

## 1. sidecar 不走 `before_provider_request` hook，走 `onPayload`

`agent/src/session.ts:327-348`：policy 的最终出站检查是在 `guardedStreamSimple` 的
`onPayload` 里**直接调用** `policyGuard.beforeProviderRequest(payload)`，注释明确说明
不重新经过 coding-agent runner 的 `before_provider_request` hook（该 hook 会 stringify body，
破坏 openai-compatible relay 的整数 token caps）。

- `policyGuard.extension` 里注册的 `before_provider_request` handler 仍是第二道注册，
  但**实际生效路径是 `onPayload`**。修改最终出站判据必须改 `beforeProviderRequest` 本体。
- 回归保护：`agent/test/policy.test.ts` 的 token-cap 用例断言 `max_tokens` 等保持数字。

## 2. `onPayload` 抛错 → 变成 stream error，且发生在 HTTP 之前

`pi-ai/dist/api/openai-responses.js:101-134`（`openai-completions.js:176` 同构）：

```js
try {
  let params = buildParams(...);
  const nextParams = await options?.onPayload?.(params, model);   // ← 抛错点
  ...
  const { data, response } = await retryProviderRequest(() => client.responses.create(...));
} catch (error) {
  output.stopReason = signal.aborted ? "aborted" : "error";
  output.errorMessage = formatOpenAIResponsesError(error);
  stream.push({ type: "error", reason, error: output });
  stream.end();
}
```

结论：`onPayload` 抛错**不会**冒泡给调用方，而是被转成 stream error；且因为抛在
`retryProviderRequest` 之前，**该次 HTTP 请求根本没发出**。这是「阻断即无出站」的机制依据，
不是声明。

## 3. SDK 自动重试会重跑整轮，但会被同一 guard 再次拦下

`pi-coding-agent/dist/core/agent-session.js:2213-2260` `_prepareRetry`：

- 触发条件是 assistant message 带 `errorMessage`；按 `baseDelayMs * 2^(n-1)` 退避；
- 重试前把最后一条 assistant 消息从 `agent.state.messages` 移除（因此会产生
  `auto_retry_start`，且**不重新发 `turn_start`**，与既有 events 合同一致）；
- `settings.maxRetries` 限制次数。

对 R4 的含义：guard 的 run 级硬阻断状态**必须持久到整个 run**，不能在一次 `onPayload`
抛错后就清除；否则 SDK 重试会在下一次 `onPayload` 通过检查并真正发出请求。
重试期间虽会空转退避，但不会产生新的出站——由「状态不清除」保证，需用真实 SDK 计数验收。

## 4. `tool_call` 的 `terminate` 只影响当前工具批次

`extensions/types.d.ts:803-812`：`terminate` 是「本批所有被 block 的 tool result 都为 true 时
提前结束」的**提示**，不是 run 级停止，也不阻止后续轮次。因此：
- 不能只靠 `{block:true, terminate:true}` 实现 R4 的 run 级收敛；
- 需要 guard 内 run 级状态 + worker 在循环中检查 + `session.abort()`。

## 5. 既有停止接缝已经存在

- `agent/src/session.ts:82` `shouldStopToolCalls?: () => boolean`，在
  `createDomainTools` 的 dispatch 前检查并抛 `run stop requested; tool call skipped`
  （`session.ts:392`）。**这是「阻断后不再发起工具执行」的现成插入点。**
- `agent/src/worker.ts:367` 目前传入 `() => active.cancelRequested || active.leaseLost`；
  R4 要求把它扩展为「或本 run 已被策略硬阻断」。
- `agent/src/worker.ts:371-378` 已有 `abortSession()` 包装 `session.abort()` 并记录失败日志，
  可直接复用为「置位阻断后通知停止」。

## 6. 既有测试 seam 可复用

- `agent/test/worker.integration.test.ts` 用 `streamOverride` + `scriptedStream(turns, options)`
  扮演 provider，`options.modelContexts` 收集每轮 context，`options.wirePayloads` 收集实际
  出站 payload，并已演示「`onPayload` 抛错 → 该次调用不发请求 → worker 结算」。
  **AC-5 的「transport 计数不增加」应在该文件用真实 SDK 路径断言，不新建纯 mock 架构。**
- `options.leakSecretInPayload` 已有密钥出站负例，AC-3 可在其上扩展角色维度。

## 7. 需要留意的实现约束

- `emitBeforeProviderRequest`（runner.js:778-806）会**吞掉** handler 抛错并转 `emitError`。
  因此若把阻断判据放到该 hook，异常不会阻断出站。最终出站判据必须留在 `onPayload`
  调用的 `beforeProviderRequest` 上。
- `transformContext`（`emitContext`）同样 try/catch 吞错（runner.js:764-773），
  所以 `context` handler 里不能靠抛错实现阻断。
- 结论：**只有 `onPayload` 路径能把「拒绝出站」变成可观察结果**；其余 hook 只能返回
  修改后的内容或记状态。
