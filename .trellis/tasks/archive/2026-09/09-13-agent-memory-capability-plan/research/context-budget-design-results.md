# Research: 全请求上下文预算的离线方案探针

- Query: 怎样确保新 user、检索、工具和输出预留加入后仍检查完整请求，而不是只检查旧历史。
- Scope: internal；纯合成方案模型，不导入 app、不调用 Provider、不实现生产预算。
- Date: 2026-09-13

## Findings

[context_budget_probe.py](context_budget_probe.py) 已实际执行，[18 个方案断言全部通过](context-budget-probe-results.json)。它检验了预算决策次序与失败分支，**没有证明真实 Provider token 计量、压缩质量或生产接线正确**。E-O6 的生产采用依旧延期。

### 计量合同与两个窗口

本探针故意使用“规范化请求 JSON 的 UTF-8 字节数”作为**合成计费单位**，不称为真实 tokens，也不把 UTF-8 字节量当作已验证的所有 tokenizer 上界。窗口模拟值为 32,768 与 272,000，对应输出预留 4,096 与 16,384；数值用于覆盖大小两类窗口，不声称任一实际模型可接收这些字节。

每次 measure 包含：

| 组成 | 合成模型中的来源 |
|---|---|
| system / policy | 两个独立约束字段 |
| history | 允许历史 message 的 ID、role、content |
| current user | 当前输入完整内容，不截断 |
| RAG | handle 与 untrusted_content 包装 |
| tools | 工具名称、描述、参数 schema 的完整 JSON |
| tool results | 新加入的工具结果完整 JSON |
| Provider serialization overhead | 外层 model/request/stream/max_output_tokens 及 JSON 键/分隔/转义开销 |
| output reserve | 单独加到完整 input_size，不能重复当作可用输入 |

程序断言各组成之和精确等于本方案 envelope 的实际序列化大小，随后再加 reserve。该 envelope 是离线规范化模型，不是实际 Pi/OpenAI 协议适配器；生产必须对真实 SDK 最后发送的 payload 计量。

### 实际探针矩阵

每种窗口分别执行下面八例，另有两例全局配置错误，共 18 例：

| 场景 | 实际结果 |
|---|---|
| 完整请求各组成均存在 | 两窗口都 accepted，最终合计 ≤ 模拟窗口 |
| 长中文旧历史 | 首次超限；显式合成摘要后复测通过，早期哨兵事实仍在 |
| 大量 RAG | 有界移除末尾低排名来源，逐条记录 handle/原因；最终完整请求通过 |
| 旧历史本身可容纳，但 current user 超长 | 即使裁减 RAG、摘要后也拒绝 CURRENT_INPUT_TOO_LARGE |
| 首次请求可容纳，随后工具结果超长 | 新结果加入后重新计算，拒绝 TOOL_RESULT_TOO_LARGE |
| 工具结果使历史+结果合计超限，但摘要后可容纳 | 重新摘要/复算后通过，工具结果和当前输入均保留 |
| 摘要本身仍超限 | 必须拒绝 CONTEXT_TOO_LARGE_AFTER_COMPACTION，不以“已压缩”当作预算成功 |
| 摘要覆盖的 message ID 不符合原始历史 | 拒绝 SUMMARY_COVERAGE_INVALID，不能静默省略部分历史 |

全局错误：未知窗口 → UNKNOWN_CONTEXT_WINDOW；reserve ≥ window → INVALID_OUTPUT_RESERVE。

长历史场景在每种窗口均观察到 measure_full_request → explicit_summary → remeasure_after_compaction → final_full_request_check。工具增长场景记录前一步 accepted、加入结果后的新决策。超限时保留原输入，不靠静默丢弃历史/当前问题通过。

### 方案边界与未来生产设计

1. 真实预算应在 Provider/model 快照绑定之后获取窗口/输出上限；在实际 SDK 最终请求处观察所有组成。RAG 的局部预算与 Pi 的历史压缩触发不是这一总预算的替代品。
2. 请求、工具结果和恢复/摘要产生后都要再次完整计量。单纯在 prompt(new_user) 之前计算历史，无法发现探针中的超长 current user。
3. RAG 裁减只减少本次 included 资料并记录排除，不删除真实索引；完整来源权限/引用绑定由 A/B/D 合同负责。本探针没有测试这些权限。
4. 摘要由 fixture 人工编写，只保证此合成样本中的哨兵事实、ID 覆盖与预算分支。摘要在规范化模型中是 untrusted_summary 数据，不获得 system 指令权威；没有调用模型摘要、没有持久跨 Run 状态。
5. 生产摘要必须证明来源/授权/游标/失效正确；不能直接复用这一人工摘要构造器，不能把旧 RAG/tool 正文固化为不带来源依赖的长期摘要。
6. 真实模型 tokenizer 或可证明保守估算、实际 SDK envelope 的计量点、Provider 输出预留策略和超限 UI 合同尚待设计/验证。未知窗口/无法可靠估算时必须能清楚失败。
7. 不以该探针宣称 C 已解决完整预算，也不以 C 的 manager/state 修复宣称跨 Run 摘要已实现。

### 实际命令

在 E worktree 根执行：

~~~bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python .trellis/tasks/archive/2026-09/09-13-agent-memory-capability-plan/research/context_budget_probe.py
~~~

输出 passed_cases=18、production_budget_verified=false，结果与脚本 SHA-256 一同落 JSON。脚本通过项目 ruff check 和 format --check；检查使用 --no-cache。

## External references / Related specs

没有使用外部资料。方案依 E design §6、父 validation-plan V-E03/C 边界及审计 MR-10/MR-12。既有 Pi 0.84.3 手动压缩证据来自父任务，本探针没有重新运行 Pi。

## Caveats / Not Found

- 未验证真实 tokens、模型摘要质量、费用、p95 延迟或超长输入在任何线上 Provider 的行为。
- 未覆盖消息权限撤销、跨会话持久化、lease/cancel 和真实 SSE UI；这些是后续生产合同测试。
- 18 是本离线方案断言数，不能加进“生产修复测试通过”统计。
