# Agent Note: Steward 模型输入走代号投影、输出走闭合 schema 校验，未验证文案回退模板

Status: implemented

## Problem

模型辅助的 prompt 直接取空间成员/引用的原始姓名，且不经过最终出站 payload 检查；输出侧"非空即采纳"——非空文本截断 500 字就当解释落库，前端优先展示。伪造关系、越权人物、自动入族承诺、任意 JSON kind 都能进呈现面；空间级可见集合被误当成单账号授权集合。

## Decision

`services/steward_guard.py` 固化单向安全链（迁移无新增表，信任标记存 `StewardModelCall.output_json.schema_version`）：

- 输入投影：稳定节点代号（`n001…`）+ 确认事实的 type/id/revision + 最小标量；真实姓名只在服务端按收件人当前权限替换；出站前复用 `policy_guard.before_provider_request` 做最终 payload 检查（不经 ProviderProxy、不伪造 AgentRun），cloud 撤权/本地必选时降级而非自动切云；
- 输出校验：闭合 schema——候选限原子 SOURCE_FACT_TYPES（祖辈/称谓等派生概念不可写成父母事实）、排序严格排列且按 recipient 分组、解释为 `{reason_code, supporting_fact_ids, template_slots}` 且全部字段必须存在于输入证据，再由确定性模板渲染；失败一律模板回退，绝不截断放行；
- 读侧信任门：`reason_text_llm` 仅 schema_version=2 验证行外显，旧纯文本行视为 untrusted——读取回退模板并进后台重生成队列；
- 版本化评测 `tests/fixtures/steward_eval/`（ST-5 矩阵 + 对抗例）输出独立硬安全门禁与候选召回，安全用例失败即整体失败，质量不达标只关闭对应辅助点。

## Alternatives considered

- **继续依赖 prompt 约束"只复述事实"** — 零代码改动，但已被核实为可绕过（F08）；提示词不是安全边界。
- **用另一个 LLM 审核输出** — 实现快，但把安全声明建立在另一个不可证明无幻觉的组件上，且成本翻倍；确定性校验可复现、可回归。
- **全链路假名化（连服务端展示也用代号）** — 安全冗余最大，但用户界面需要真实称谓；折中为"外发最小化 + 服务端按权限替换"，展示面保持可用。

## Consequences

- **收益**：出站内容可断言（哨兵测试证明 token/手机号/注入句/masked 值/私有 RAG 零外泄）；呈现面只出现有证据支撑的确定性文案；安全门禁成为模型开关的发布前置。
- **代价与已知上限**：模型自由度下降（只决定措辞与排序，不决定事实）；旧无版本文案需要一轮后台重生成；评测 fixtures 需随策略演进维护，阈值（召回 ≥0.9）是规划初值。

## Verification

`backend/tests/test_steward_guard.py`（投影/校验/渲染单测）、`tests/test_steward_assist.py` 出站捕获与降级、`tests/test_steward_eval.py` 跑聚合报告（硬门禁 + 召回分列，fake transport 诚实标注）。

Note: 模型输入投影与输出校验的唯一入口 — 见 .agent-notes/implemented/bug-fix/2026-09-11-steward-guard-closed-schema-and-minimal-input.md
