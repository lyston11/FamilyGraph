# 策略边界取证与方案取舍

本文件记录本轮规划的源码证据，不作为默认注入材料。不包含生产/开发数据库导出、密钥、prompt 或 thinking。历史调查叙述不等于本轮复验。

## 本轮检查的方法

只读工作区源码/测试/规范和环境隔离说明；没有访问远端、调用真实模型、运行测试或修改业务代码。任务创建前工作区干净。以下行号是规划时定位，后续以符号名为准。

## 源码证据

| 文件/符号 | 观察 | 设计影响 |
|---|---|---|
| `agent/src/policy.ts:20` `INJECTION_MARKERS` | 包含 `system prompt`、`system message`、`绕过限制` 等普通语言片段 | 不能当作确定越权证据 |
| `agent/src/policy.ts` `containsMaskedData` | 字符串匹配 `masked/遮罩/已脱敏`，对象也递归检查 | 普通措辞和可信元数据混在一起 |
| `agent/src/policy.ts:301` `sanitizeToolResult` | masked 分支安全替换含单词 masked | 可被后续同一谓词再次命中 |
| `agent/src/policy.ts:392` `beforeProviderRequest` | 最终 payload 仍检查 masked、secret、Provider；会抛错 | 只改 context 跳过 assistant 不足以消除词面误报 |
| `agent/src/policy.ts:552` context handler | 对全部消息作关键词/脱敏检查，命中删除整条消息 | 可能影响工具消息配对，来源不代表授权 |
| `agent/src/worker.ts:474` | session.prompt 完成后检查 blockingViolationCount；部分类型落入 POLICY_SECRET_LEAK | 原因与终态映射需要统一，不能只依赖最终检查 |
| `agent/src/worker.ts:565` | catch 独立映射 SECRET_IN_PROVIDER_PAYLOAD 为 SECRET_LEAK | 两条路径必须一致 |
| `agent/src/session.ts:327`、`:348` | 创建 guard，onPayload 调用 beforeProviderRequest | 最终 gate 已有接线位置，不需另建 transport |
| `backend/app/services/policy_guard.py` input_hook/tool_call_hook | 关键词与 secret 同属拒绝判据 | 后端须同步拆分；保留 enforce 返回合同 |
| `backend/app/services/policy_guard.py` context_hook | kind/data、trust/untrusted_data、结构化 masked 合同 | 权威结构检查不能降为自然语言 notice |
| `backend/app/api/agent.py:361` | input_hook 由家庭 Agent 入口执行 | Assistant 输入是本次直接受影响路径 |
| `backend/app/api/internal_agent.py:830` | tool_call_hook 由内部工具端点执行 | 跨层工具负例必须保留 |
| `backend/app/api/internal_agent.py:693`、`:963` | 两类 run 均在 context 端点走服务端 context_hook | 不创建 Steward 专用安全例外 |
| `frontend/src/api/agent.ts:100` | 既有 POLICY_* 错误显示 | 新错误补文案，保留历史码 |

## 现有测试与语义迁移

`agent/test/policy.test.ts` 有关键词立即 handled、删除 user message、instruction-like 工具结果被拦等旧语义断言，也有真实密钥、scope、allowlist、local/cloud、输出上限和 token-cap 保护。本任务修改前者必须有明确 PRD 映射，后者不得随之删掉。

`agent/test/worker.integration.test.ts` 使用已有真实 SDK 集成 seam，并断言政策失败结算 `POLICY_TOOL_BLOCKED` / `POLICY_SECRET_LEAK`；可扩展其 transport 计数和 SDK 自动重试，不以新建纯 mock 架构代替。

后端已有 `test_policy_guard.py`、`test_agent_tools.py`、`test_steward_tools.py`、`test_provider_proxy.py`；前端已有 `src/api/__tests__/agentErrors.spec.ts`。

## 历史 run 48 的证据限制

之前对话提到同 input_hash 的一成一败、部分 Provider 200、guard 记录 4 次违规但未保存具体 kind。规划不将这些叙述升级为本轮重新验证的结果。

即便上述观察成立：

- input_hash 不涵盖全部运行时策略、工具返回和模型输出，不能排除其他原因。
- 已有请求返回 200 不能排除后来某次请求被 beforeProviderRequest 阻断。
- 模拟助手文字触发关键词，证明某种机制能误报，不证明 run 48 使用了该文字。
- 未记录具体 kind/文本时，不能声明“确认误报”“没有泄漏”或把 `masked` 确认为实际触发器。

结论：历史原因未知；修复已证实机制并让以后可诊断。不为归因收集敏感原文。

## 被否决方案

1. **只跳过 assistant 消息**：遗漏最终 payload 检查，且模型可能复述外部不可信内容，不能无条件信任。
2. **只补错误码**：提高诊断但保留误报和继续消耗 token 的行为，仅可作为 A 阶段。
3. **移除所有 masked 检查**：删除了真实的服务端受限数据合同，违反安全要求。
4. **保存完整 prompt/thinking 查原因**：扩大敏感数据留存，非本任务必要手段。
5. **用另一个模型判注入**：引入依赖、成本与新的不确定性，无法替代工具授权。
6. **新建通用策略/溯源平台**：既有 service/SDK 边界足够承载本次需求，优先最小改动。

## 环境边界

`deploy/production/README.md:31` 的隔离矩阵定义开发环境为 `/home/ubuntu/projects/FamilyGraph` 与 systemd 用户单元；线上为 `/home/ubuntu/fg-prod` 和 `familygraph-prod-*` Docker 栈。用户明确要求只处理开发环境，线上手动发布。任务不得自动部署/回滚线上，也不得为验收修改 Provider 白名单。

## 留待实施的技术验证

真实 SDK 如何 abort/retry、工具并行中的启动边界、来源坐标稳定性和不透明消息字段，须在锁定版本类型/文档及真实集成测试中核实。具体门槛见 `implement.md` P0，不是让用户重新选择产品范围。
