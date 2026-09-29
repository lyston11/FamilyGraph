# 技术设计：Agent 策略边界

## 1. 设计目标与约束

落实 PRD R1–R7。沿用 FastAPI 权威授权、Pi sidecar 同步 guard 和现有 settle，不新增第二套 policy engine。策略改动是共享 Agent 行为，Assistant/Steward 都必须覆盖。

历史 run 48 仅是调查入口，确切原因未知。详细事实与被否决推断见 `research/policy-boundary-summary.md`。

## 2. 边界和数据流

```text
用户请求 / 已授权数据
  → FastAPI input、tool executor、ContextBuilder、VisibilityPolicy
  → 已检查的 projection / tool output
  → sidecar input / tool_result / context
  → beforeProviderRequest（最终正文 + run 阻断状态）
  → FastAPI Provider gateway（再次检查 run、Provider、密钥与取消）
  → 模型输出 / 工具调用
  → 工具后端重新授权，或 Steward settle 产物校验 + 写回 fence
```

- 权限只来自服务端；消息 role 说明来源，不授予读取、工具执行或写回权。
- 保留既有 context 数据块 `kind=data` / `trust=untrusted_data` 合同，以及其位置上的 `visibility=masked` / `masked=true` 拒绝。正文里相同字串或自报字段不拥有该语义。
- 在已存在的 projection/工具结果解码边界校验结构；不向所有 SDK 消息附加新的通用 provenance envelope。来源使用现有消息 role、块来源和工具调用关联。
- 不把最终 provider payload 的任意对象键当作服务端可信元数据；其 secrets/Provider gate 仍对所有角色执行。
- 不修改 run token、租约、allowlist 的可信来源，或 backend Steward product validator。

## 3. 判据与动作

| 判据 | 动作 | 说明 |
|---|---|---|
| 自然语言出现注入/脱敏标记词 | notice / 原内容作为数据保留 | 含命令语气也不能仅凭关键词判整个 run 失败；不解释为授权 |
| 服务端定义的受限块、必需结构不合法 | block | 沿用服务端结构检查；sidecar 对自己接收的固定合同作防御校验 |
| 未授权工具、scope 覆盖、非法参数/大小 | block | 不删除闭合 schema 或后端复核 |
| 已知密钥在即将出站的正文中 | block | 不限角色；不得通过换成 assistant 消息绕过 |
| 既有合同允许去掉的凭据字段/PII | sanitize + notice | 保留数值 token caps；不把静默脱敏作为任意密钥出站的替代措施 |
| 未确认事实 | annotate + notice | 使用原合同语义，不重复添加标签 |
| Provider 本地/云限制冲突 | block | 服务端和最终出站双重检查 |
| 工具结果超过上限 | block | 保留现有输出上限；安全错误不继续扩大上下文 |

### 两侧一致性

后端 `input_hook` 与 `tool_call_hook` 目前将关键词和 secret 判据组合，须拆开：关键词自身不 block，secret/参数/权限仍照原合同拒绝。复用现有 `PolicyDecision` 与 allow/redact/annotate 通道，不以 notice 新 action 破坏 `enforce()` 调用方。后端输入已明确拒绝的真实策略失败仍以原后端错误码上报。

sidecar 不设 `if role === assistant then skip all checks`。来源只用于定位和区分正文/协议字段；词面提示降为 notice 后，同样适用于 user、toolResult 和 assistant。

### 消息完整性

- 去掉关键词命中即 `return []` 的整消息删除策略。
- **实测修正**：`context` hook 最终改为**只观测、不改写**（返回 `undefined`）。原计划是「保留处理但只改允许字段」，但实现时确认：能安全改写的内容只有 `content` 文本块，而脱敏已经在 `beforeProviderRequest` 对完整 payload 执行；在 context 层再改一次既不增加防护，又必须逐个处理 toolCallId、工具名、配对结构和不透明签名，风险大于收益。因此 context 只记录诊断。
- 内容处理只在受支持的文本/数据字段进行；保留 role、toolCallId、工具名、配对结构、签名和不透明 provider 字段。
- 对不能安全改写的不透明字段，保留原结构；如最终密钥检查命中，则阻断，不能为通过检查修改签名。
- 脱敏和未确认标签幂等；安全提示使用固定且不含业务正文的文本，二次经过 guard 不产生新的硬阻断。
- 不可信文本继续以数据块/工具数据提供，不能升格为 server instructions。关键词只是有限诊断信号，不承担注入安全保证。

## 4. Run 级阻断生命周期

在现有 `createPolicyGuard` 内维护一个 run 生命周期内的首个硬阻断决定，不创建全局或跨 run 状态。

```text
active --notice/sanitize--> active
active --hard block-----> blocked（不可清除）
blocked --retry/context/tool/provider--> 拒绝新的执行
blocked --worker settle--> failed（遵守服务端取消/失租裁决）
```

- 硬阻断先置位，再通知 worker 停止 session；同步 transport/tool 入口每次都检查状态，不能只依赖异步 abort。
- 接入现有 `session.abort()` 路径；SDK hook 是否支持抛错/终止、自动重试是否吞错，必须通过锁定版本的真实 SDK 测试验证，不能假设 `{terminate:true}` 就足够。
- `beforeProviderRequest` 和实际工具执行入口拒绝 blocked run。并行工具调用中已经发出的操作无法撤回；尚未发起的操作不得继续。所有工具仍只读。
- SDK 包装成 stream error、AbortError 或再次调用 prompt 时，worker 仍优先读取该 run 的硬阻断决定；不得误报为空回答或普通 SIDECAR_ERROR。
- 正常返回和 catch 共享同一个原因到错误码的映射。保留当前取消/leaseLost 检查及服务端权威终态规则，不重复 settle。
- worker 在循环结束后的检查保留作兜底，但不再是唯一执行制动点。
- 日志/回调失败不得恢复 active，也不得替换首个阻断原因。只记录已发生的使用量，不自动重试整个 Steward attempt。

## 5. 错误与诊断合同

### 错误码

首个明确硬阻断决定提供稳定主错误码；其他事件只作诊断，不随 Set 遍历顺序覆盖主因。

| 类别 | sidecar 运行期错误码 |
|---|---|
| 实际已知密钥进入待发送正文 | `POLICY_SECRET_IN_PROVIDER_PAYLOAD` |
| 工具不允许、scope/参数违规 | `POLICY_TOOL_BLOCKED` |
| 工具结果越界或结果契约失败 | `POLICY_TOOL_RESULT_BLOCKED` |
| Provider 不满足本地/云要求 | `POLICY_PROVIDER_BLOCKED` |
| 可信合同明确的受限数据 | `POLICY_MASKED_DATA` |
| 必需上下文合同失效或无法分类的策略阻断 | `POLICY_GUARD_BLOCKED` |

自然语言提示不生成失败码。后端原生 403/409 等继续沿现有 API 错误合同处理，不强行重命名所有历史码。运行期码沿现有 settle 字符串通道，不为 sidecar 码新增后端错误常量或数据库迁移。

家庭前端为新增码提供通用、安全、可理解的文案；历史 `POLICY_SECRET_LEAK` 保留映射。面向用户不暴露规则细节、token 命中位置或原文。

### 安全诊断

沿用现有结构化应用日志，不新增公开 run event、数据库表或 raw payload 保存。

每条 incident 的字段集合限制为：`run_id`（现有 logger 上下文）、`rule_id`、`stage`、`source_kind`、`action`、可获得的 `turn_index` / `tool_call_id` / 消息索引、`occurrences`。规则和来源都是代码定义枚举。不要将任意工具名、未知字段名或错误正文拼进 detail；工具标识仅记录已注册 canonical name。

- 用 run 内来源坐标 + rule + stage 去重；不以正文或正文哈希作关联键。没有稳定来源 id 时明确为 unknown，不伪造跨轮一一对应。
- 最多保留 64 条不同诊断项；其余按固定类别累计 dropped/total 计数。首个硬阻断必须保留，notice 不能把它挤掉。
- 出现硬阻断即记录一次安全结构化日志，结束时记录摘要；避免进程退出只留下计数。
- 不记录匹配值、prompt、thinking、密钥、工具输入输出或内容指纹。固定摘要可沿现有 settle 安全文本记录，详细原因留内部日志。

## 6. 改动位置与兼容

预期最小范围：

- `agent/src/policy.ts`：判据/动作、消息处理、阻断状态、安全事件。
- `agent/src/worker.ts`、`agent/src/session.ts`：停止和错误映射的必要接线；仅在确有必要时调整工具执行入口。
- `backend/app/services/policy_guard.py` 及直接调用点：拆分关键词与确定违规，验证 `enforce` 返回协议不变。
- `frontend/src/api/agent.ts`：错误码文案；对应 `agentErrors.spec.ts`。
- 对应 policy、worker 集成、backend 授权和 Provider 测试；规范更新聚焦本任务合同。

不改变 ContextOut/settle 的 wire schema、不迁移数据库、不更换 SDK 或 Provider。若现有结构确实不足以表达某个必需安全信息，先修订设计和跨层合同，不用正文猜测代替，也不擅自扩大为通用 metadata 重构。

## 7. 交付与回退

A 阶段先统一安全诊断和分类，保留当前拒绝判据；B 阶段再同时调整两侧关键词判据与阻断生命周期。两阶段在同一任务内顺序完成，不以 A 完成宣称全部修复。

开发环境验收前核对代码 SHA、`dist` 构建、systemd 用户服务、env 来源及数据库路径；只重启所需开发服务。真实验收使用当前已授权模型，不为制造成功放宽 provider 白名单或改写业务数据。

无需 schema 回退。代码回退使用可审查的 revert/此前已验证构建，不 reset/rebase 共享分支、不还原数据库。B 阶段若出现回归，先回到 A 的诊断版本，不回退为“关闭 guard”。线上发布完全不在执行清单内。

## 8. 风险与实施前技术验证

- 关键词不再硬拦改变了旧测试期待；必须用越权与密钥负例证明真正边界仍在，不能仅删除失败测试。
- 新的阻断状态须对真实 SDK 的自动重试和多工具轮有效；以实际 transport/工具执行次数验收，不只测试状态变量。
- 老记录无法恢复具体原因；不得把后来的复现实验当作历史 run 的事实。
- 检查签名/不透明消息字段时不能盲目遍历并改写协议；保留结构，最后出站密钥检查仍不能绕过。
- Assistant 输出侧完整 DLP 不属于本任务，不能用本次修复宣称浏览器输出已有全面保护。
