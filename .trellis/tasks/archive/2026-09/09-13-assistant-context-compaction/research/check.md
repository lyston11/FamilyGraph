# C 独立核验记录

2026-09-13 至 2026-09-14（Asia/Shanghai）；基线 `d1f43a5`；候选位于 `/private/tmp/familygraph-memory-rag/09-13-assistant-context-compaction`。本次按主线程授权只读业务和测试，唯一仓库写入为本记录；不提交、不改任务生命周期、不改 node_modules。读取了完整 hook 保存文件、实际 AGENTS、工作流、check.jsonl 及全部引用上下文、PRD/design/implement、候选 diff、实际 Pi SDK 和实现记录。

## Findings (fixed)

本核验者未修改业务代码。历史恢复补丁本身未发现需要修复的实现问题；下列独立发现尚待主线程取舍，不能据全包测试通过宣称全部压缩生命周期问题已解决。

## Findings (not fixed)

### P2：SDK 溢出恢复成功后，worker 仍按早先错误结算失败

- 文件/符号：`agent/src/worker.ts:198`，`SidecarWorker.executeJob` 的 `lastAssistantError`；`:209–230` 只在错误时赋值，后续成功 `message_end` 不清除；`:291–304` 只要该值非空就提交 `failed / PROVIDER_STREAM_ERROR`。
- 实际触发：真实 `buildRunSession`、`SidecarWorker` 和 Pi 0.84.3，假 client 替代后端、假 stream 替代模型、global fetch 禁止。首个普通响应返回 `maximum context length exceeded`；SDK 从完整 manager 生成摘要，发出 `compaction_end(reason=overflow, willRetry=true)`；SDK 重试返回正常 `stop`，最终回答已含早期事实，但 worker settle 仍为 `failed`，错误仍是第一次 overflow。
- 独立命令：`node /private/tmp/fg-c-review.wA3iy2/probe.mjs`。最终有效运行退出码 0（探针记录并断言上述实际状态）；它不是对“恢复成功应 succeeded”期望的通过。请求顺序为 `error → summary → stop`，最终 Pi `stopReason=stop`，settle 为 `failed / PROVIDER_STREAM_ERROR`，fetch 调用数 0。
- 来源归属：`git diff d1f43a5 -- agent/src/worker.ts` 为空，属于既有 worker 语义缺口；当前新增 worker 例只测“overflow 后摘要也失败”，未覆盖恢复成功。
- 建议：按恢复后的最终 assistant outcome 判断 Provider 失败，并新增实际 SDK 的 overflow → compact 成功 → retry 成功 → worker succeeded 回归，同时保留真正失败、policy violation、取消和 lease loss 分支。`worker.ts` 不在本核验的写入授权内，且该修复涉及 worker 终态规则，故仅报告主线程，不擅自扩范围。

### 非阻断文案精确性

`.trellis/spec/backend/assistant-history-restoration.md:27` 的“空历史或只有当前 user → manager 为空”应理解为“manager 的消息为空”。Pi `sdk.js:247–251` 仍追加 model/thinking 元数据。已通知文档所有者精确化措辞；不影响业务行为。

## 独立确认的行为

| 检查 | 实际证据 |
|---|---|
| 同一 manager 在创建前预填 | `session.ts:369–410` 使用公开 appendMessage；`:414–426` 把该实例传入 createAgentSession。Pi `sdk.js:81/241/255` 从它构建 context、恢复 state 并保存同一实例；无 create 后覆盖 state。 |
| ID 顺序、去重、当前 user | `session.ts:370–396` 与 worker `:236–251` 使用相同的最新合法 user 选择。后端 `internal_agent.py:427–436` 按持久 ID 正序投影；`agent_queue.py:238–248` 保证单 active run。专项 `session-history.test.ts:190–307` 保留不同 ID 同文本、去重重复 ID、保留 250 条允许历史；没有 recent-N。 |
| 允许正文、当前模型 | 仅恢复 user/assistant `content_json.text`，旧工具/RAG/thinking/引用/Provider 私有字段不复制；专项 `:309–388` 覆盖。显式 model 优先（SDK `sdk.js:84–87`）；零 usage 被 SDK `compaction.js:93–103/131–143` 当作缺失而走文字估算。 |
| 摘要测试非恒定答案 | `session-history.test.ts:105–121` 检查实际摘要输入是否包含 OLD_FACT；未出现 checkpoint 的普通回答不复述事实。手动/自动例还断言原始旧 user 已退出活动 context，继续请求中存在包含事实的摘要。 |
| 手动/真实自动 | 专项 `:390–469` 分别调用公开 compact、真实 prompt 后 usage 阈值和真实 prompt 前零 usage 估算阈值。实际 SDK `agent-session.js:1410/1674` 读 manager、`:1465–1468/1752–1755` 写摘要并重建 state；事件名是 compaction_start/end，reason 为 manual/threshold/overflow。 |
| Run 重建和隔离 | 专项 `:510–560` 从同一持久投影构造 fresh manager；失败 response、上一 manager 的 checkpoint 不跨 attempt/session/account。测试是同进程重建，未实际杀进程重启。 |
| 当前 user/RAG 与取消 | `worker.integration.test.ts:709–753` 通过实际 worker/parser/session 路径断言当前 user、当前 RAG 各一次，旧事件不重发；`:916–945` 在存在历史时覆盖服务端取消及 heartbeat 403，均不 settle。取消例不是摘要执行中取消的专门测试。 |
| 新 spec | Provider、零 usage、允许历史、摘要不持久化、默认压缩和当前 RAG 仅一次等描述与实现一致；不声称全请求预算或历史派生事实撤回已完成。 |

独立探针使用不同合成事实 `REVIEW_FACT=...`，摘要从收到的对话正则提取该事实；普通响应在未见 checkpoint 时不包含事实。候选 source 经 esbuild 临时打包，所有产物只落 `/private/tmp/fg-c-review.wA3iy2`，SDK 仍读取锁定 0.84.3：

- 手动：manager/state 预填均为 4 条；实际 manual start/end；摘要请求有旧事实；下一次 prompt 有对应 checkpoint。
- 自动：窗口 60,000、首次正常响应 usage=45,010、默认 reserve=16,384；实际 threshold start/end；摘要与后续 prompt 保留旧事实；没有调用手动 compact。
- 超大当前中文输入：280,000 字符，仅当前 user 超大、旧历史很短。fake Provider 按请求文本长度拒绝；SDK 实际执行一次成功摘要并重试，两个普通请求均完整包含当前输入一次，仍然超限后发出 `Context overflow recovery failed after one compact-and-retry attempt...`；最终 stopReason=error，原投影未变。该场景独立执行通过，但尚未加入仓库持久回归。
- 恢复成功的 worker 结算：复现上方 P2。其余探针通过不消除这条发现。

## Verification

- Lint：**pass**，核验者实际在候选 `agent/` 执行 `npm run lint`，退出码 0。
- TypeCheck：**pass**，核验者实际执行 `npm run type-check`，退出码 0。
- Tests：实现者完整 `npm test` 报告为 13 文件 / 108 通过（见 [实施记录](implementation.md)）；按委派要求未重复全包。核验者实际执行上述独立探针：历史/手动/自动/超大当前输入正例通过，成功 overflow 恢复后的 worker 终态检查发现 P2。
- Build：实现者 `npm run build` 已通过，本核验未重复构建工作区。
- `git diff --check`：核验者对候选业务/测试/spec 已跟踪变更执行通过。

未执行真实 Provider、真实后端 listener、数据库重启或完整前后端测试；本轮只改 sidecar 会话接线，无协议/迁移变化。离线流证明数据路径与状态，不代表真实摘要质量、真实 token 精确度、生产自动频率或任意长度请求能成功。全请求预算、来源权限关联的跨 Run 摘要，以及历史 assistant 正文中已存在事实的保留政策仍归 E。

## 2026-09-14 修复后的独立收口核验

本节更新上方首次检查的未修复状态。核验对象为 C worktree `HEAD=470b362` 上的本次未提交 worker/test 补丁及主线程 spec 更新；仍只追加本文件，不修改业务/测试，不提交或操作其他 worktree。按委派未重复完整测试包。

### Findings (fixed)

- **原 P2 已修复**：`agent/src/worker.ts:221–226` 只在 assistant `message_end` 且 `stopReason=stop` 时清除未恢复的 Provider 错误。既有 `:209–220` 和 `:228–235` 继续记录后续错误；工具 turn、partial、compaction 事件都不能清除错误。
- **独立执行证据**：仅给原 `/private/tmp/fg-c-review.wA3iy2/probe.mjs` 新增 `settles == [{run: "42", status: "succeeded", error: undefined}]` 硬断言，再执行原命令。探针从当前候选源码重新打包，实际 Pi 0.84.3 的请求仍为 `error → summary → stop`，摘要与最终回答都含输入中的早期事实；worker 现在结算 `succeeded`。退出码 0、fetch 调用数 0。原探针内的手动、threshold、280,000 字符当前输入失败检查也全部通过。
- **失败优先级保持**：实际源码 `worker.ts:270–295` 仍先处理取消/失租及 policy violation，再于 `:297` 处理尚未恢复的 Provider 错误；catch 的 `:322` 也保留取消/失租短路。成功 stop 只清理局部 Provider outcome，不能清掉这些独立状态。
- **持久回归可信**：`worker.integration.test.ts:628` 的摘要和重试回答分别从实际请求/实际 checkpoint 提取事实；`:817` 参数例分别核对恢复 succeeded 和重试再失败，并断言失败文案为最新 `retry request rejected`。`:974` 在正常 stop 后仍验证 `POLICY_TOOL_BLOCKED`；`:1053/:1087` 分别观察取消、heartbeat 403 后实际迟到的 assistant stop，并断言不 settle。`session-history.test.ts:514` 已把原临时探针的超大当前输入案例纳入仓库，断言两次普通请求保留完整原输入一次，重试后明确失败。
- **文案已精确化**：`.trellis/spec/backend/assistant-history-restoration.md:27` 已改为“manager 的消息为空（可有模型配置元数据）”，新加入的 overflow 恢复成功合同与实际 worker 条件一致。

### Findings (not fixed)

本次复核无新增未修复问题；首次检查的 P2 和文案项均已关闭。此前明确的真实模型质量、全请求预算、跨 Run 持久摘要及真实进程重启验证边界继续保留，不因这次收口扩为已完成能力。

### Verification

- Lint：**pass**，本轮实现者已在最终候选执行 `npm run lint`，见 [实施记录](implementation.md) 的 2026-09-14 补充。
- TypeCheck：**pass**，本轮实现者已执行 `npm run type-check`；本核验未重复。
- Tests：**pass**，本轮实现者受影响两文件 35 条、完整 13 文件 113 条通过；本核验独立执行原离线探针并新增成功结算硬断言，实际通过。
- Build：**pass**，本轮实现者已执行 `npm run build`；本核验未重复。
- 本核验没有执行父任务真实后端/sidecar listener 联调，也没有合入 main；这些由主线程在基于 D 的父集成 worktree 完成。
