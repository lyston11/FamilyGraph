# C 实施与验证记录

日期：2026-09-13。基线：`d1f43a5`（已验证 A）。工作分支 `feat/09-13-assistant-context-compaction`，worktree `/private/tmp/familygraph-memory-rag/09-13-assistant-context-compaction`。

## 改动边界

行为缺口是恢复历史只进入 Pi agent state，没有进入压缩读取的 manager。本次在实际接线 `agent/src/session.ts:365` 创建内存 manager，按后端已排序的持久消息 ID 顺序预填允许的 user/assistant text，然后交给 `createAgentSession` 恢复。按 ID 去重；当前 user ID 排除，由既有 worker prompt 一次。删除 create 之后独立覆盖 state 的路径。

本次写入文件：

- `agent/src/session.ts`：同一个 manager 的历史预填、ID 去重；保留当前 Provider/model 与默认压缩配置。
- `agent/test/session-history.test.ts`：新增 19 条真实 Pi SDK 专项回归。
- `agent/test/worker.integration.test.ts`：新增 2 条 worker 回归；既有取消、租约撤销、Provider 拒绝用例增加历史；假响应绑定实际请求模型，支持真实 error 流。
- 本记录。

业务代码只修改 session.ts；没有修改 worker.ts、后端、协议、依赖、lockfile、迁移或数据库。其他已有任务文件改动由主线程持有，未覆盖。实现子代理未提交、合并、切分支或操作任务生命周期。

## SDK 证据

`npm ls @earendil-works/pi-ai @earendil-works/pi-coding-agent --depth=0` 确认两包实际版本均为 `0.84.3`，依赖通过既有 symlink 读取，未修改 node_modules。

下列路径以 `agent/node_modules/@earendil-works/pi-coding-agent/dist/core/` 为前缀：

- `session-manager.d.ts:217` 公开 `appendMessage`，`:333` 公开 `SessionManager.inMemory`。
- `sdk.js:81` 从传入 manager 构建 context，`:84` 优先显式 model，`:241` 恢复该 context 到 agent state，`:255` 将同一个 manager 传入 AgentSession。
- `agent-session.js:1400` 是手动 compact；`:1410` 读取 manager，`:1465` 保存 compaction，`:1468` 从 manager 重建 state。
- `agent-session.js:1560` 是实际自动判断 `_checkCompaction`；`:1665` 是 `_runAutoCompaction`，`:1674` 读取 manager，`:1752` 保存 compaction，`:1755` 重建 state。
- `agent-session.js:1679` 发出 `compaction_start`，自动路径完成后发出 `compaction_end`，`reason=threshold|overflow`。锁定版本没有 `auto_compaction_*` 事件名；测试按实际公开事件与原因验证自动行为。
- `compaction/compaction.js:160` 的阈值是 `contextTokens > contextWindow - reserveTokens`。零 usage 历史走文字大小估算，不伪造已计费 usage。

## 红测与修复后结果

业务修改前，在实际 `buildRunSession` 上运行 `npm test -- test/session-history.test.ts`，三条核心回归全部失败（2026-09-13 23:21 Asia/Shanghai）：

| 回归 | 修复前实际结果 | 修复后结果 |
|---|---|---|
| 初始化历史一致性 | state 有 2 条允许历史，manager context 为 `[]` | manager 分支、manager context、agent state 都包含 2 条历史；首次模型请求包含历史和一次当前 user |
| 手动压缩 | `Nothing to compact (session too small)` | 摘要请求读到早期合成事实；旧 user 原文退出活跃 context 后，后续模型请求仍含摘要中的事实 |
| 自动阈值压缩 | 普通响应 usage=30,010，但 manager 无可压缩历史，没有 `compaction_start` | 真实 `prompt()` 后发出 threshold start/end；摘要和继续请求都读到早期事实 |

修复后同三条核心测试全部通过，再扩展为专项 19 条。上述红测只记录测试 harness 修正完成之后、业务修改之前的一次有效复现。

## 手动和自动触发条件

所有专项测试使用实际 SDK、假 streamOverride、合成对话，global fetch 被阻断并在 teardown 断言零调用。每条测试的临时 agentDir 会清理。worker 集成只访问随机本地端口的 mock internal API，Provider 仍为假流。

共同长历史：早期 user 唯一事实、assistant 确认、92,000 字符的近期合成文字（约 23,000 tokens）、assistant 确认，最后一条当前 user 在预填时排除。默认 `reserveTokens=16,384`、`keepRecentTokens=20,000`、`enabled=true` 始终保留。

- 手动路径（`session-history.test.ts:390`）：真实 `session.compact()` 从 manager 读取旧事实；保留尾部的长度足以让早期 user 退出原文 context。假摘要仅在摘要请求确实包含事实时返回它，后续 prompt 必须读到该摘要。
- 响应后的自动路径（`:411`）：窗口 40,000，初始历史估计约 23,000，未达到 23,616 阈值；普通响应报告 30,010 usage 后由真实 SDK 自动触发 threshold。测试从不调用 compact 或私有自动方法；断言 start/end、summary、后续请求，并确认只压缩一次。
- 提交前的自动路径（`:453`）：窗口 38,000，阈值 21,616；恢复的 assistant usage 为零，SDK 按历史文字估算，在本轮 user 进入前自动压缩。捕获请求顺序为 summary → normal，摘要请求无当前 user，普通请求包含一次当前 user 和旧事实摘要。
- 过限失败（`:471`、`worker.integration.test.ts:755`）：普通请求返回 context overflow，SDK 实际尝试摘要；摘要也失败时保留明确 `compaction_end(reason=overflow, errorMessage=...)`。manager 保留原始历史，worker 结算 `PROVIDER_STREAM_ERROR`，没有成功终态或静默 recent-N 截断。

假正常回答在未看到 checkpoint 时不复述旧事实，避免自动压缩后仅靠保留的本轮回答“碰巧记住”而通过测试。这证明接线与可见性，不衡量真实模型摘要质量。

## 验收映射

| 验收项 | 覆盖证据 |
|---|---|
| C-AC1 | `session-history.test.ts:267` 初始化 manager/state/首次请求一致；`:295` 连续 250 条允许历史完整保留，顺序按 ID，时间戳不重排 |
| C-AC2 | `session-history.test.ts:190` 参数组：空历史、单当前 user、连续 user、同文本异 ID、重复历史/当前 ID、非文字与未知角色、合法空文字、无当前 user；预填零模型/工具调用；worker `:709` 仅一个后端 user_added |
| C-AC3 | `session-history.test.ts:390` 真实手动摘要及下一次请求 |
| C-AC4 | `session-history.test.ts:411` 真实响应后 threshold、`:453` 真实提交前 threshold；均无手动 compact 代替 |
| C-AC5 | `session-history.test.ts:309` 仅恢复正文；`:510` 失败 attempt 重建；`:536` manager/摘要不跨 Run 或账户/空间/session；worker `:709` 当前 RAG 一次性、旧事件不重发 |
| C-AC6 | `session-history.test.ts:357` Completions/Responses 当前 Provider/model 绑定；`:471` 过限失败；worker `:871` 拒绝、`:916` 取消、`:933` 租约授权撤销；全包 policy/provider 等原有回归通过 |

## 完整检查

在本 worktree 的 `agent/` 执行：

| 命令 | 结果 |
|---|---|
| `npm run lint` | 通过 |
| `npm run type-check` | 通过 |
| `npm test` | 13 个测试文件，108 条通过（新增 21 条：SDK 19 + worker 2） |
| `npm run build` | 通过 |
| `git diff --check` | 通过 |

全包测试时间 2026-09-13 23:37 Asia/Shanghai，Vitest 3.2.7，总耗时 4.43s。专项构造与 worker 集成共 30 条通过。类型检查过程中修正测试辅助函数的 AgentMessage 结构兼容，最终无类型错误。

没有运行后端/前端全包或真实 listener smoke：本次未更改协议与这两端代码；A 的后端验证由主线程持有。本次没有真实 Provider 调用、生产数据/开关/自动压缩频率/模型质量测试。

## 交接与限制

不新增跨 Run 持久摘要，不保存完整 Pi 执行状态，不删除持久聊天，不把本修复称为全请求 token 预算。任意超长 system/history/current user/RAG/tools/output 的总预算仍由 E 设计；SDK 的默认估算与摘要调用仍可能过限。

历史 assistant 正文按既有合同恢复，其中可能含过去来源的事实；本次只阻止单独重放旧 RAG blocks、工具结果、thinking 和 Provider 私有块，不能宣称完成历史派生事实撤回或物理擦除。

B 后续修改 context/citation/worker 时应保留本套回归，尤其是当前 user/RAG 只一次、摘要来源与原始工具材料不重放的边界。独立检查、提交、归档与 worktree 清理由主线程负责。
