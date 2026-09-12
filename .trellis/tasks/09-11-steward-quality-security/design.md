# Design — Steward 模型辅助安全修复与质量评测

> 本文是待实施技术方案，不是当前代码行为；现状证据见父任务 findings。

## 安全链

`Steward input projection → context/input policy → provider setting/revision → before_provider_request → bounded transport → typed output validator → evidence/permission fence → presentation/review projection`。
复用现有 policy_guard 的判断与脱敏能力，写专用 consumer adapter 传入 local_required/cloud_allowed；不能使用 run-bound ProviderProxy 就绕过最终 payload 检查。不得为复用 Proxy 伪造 AgentRun。

输入优先本次随机或稳定受限的节点代号与已确认 fact type/id/revision，不需要真实姓名时不发送。需要展示的名字只在服务端按 recipient 当前权限替换；space-wide visible 集合不是每个账号的授权集合。rank 按 recipient_account_id 分组，本批标识不暴露其他收件人。

解释输出建议结构 `{reason_code, supporting_fact_ids, template_slots}`，字段/ID 必须在输入证据内，再由确定性模板渲染。自由文本可选辅助句经验证仍不得替代核心原因/隐私警告；不声称仅依赖另一个 LLM 审核即可证明无幻觉。模型决定措辞与顺序，权限和正式关系用确定性判定。

候选输出形状对齐 candidate-review，未知 kind 丢弃；digest 基于结构/证据而非 rationale。relation_proposal 只指向原子 SOURCE_FACT_TYPES；祖辈/称谓等派生概念不能被模型写为父母事实。

## 评测工件与阈值

建议 `backend/tests/fixtures/steward_eval/` 保存不含真实家庭数据的 JSON fixtures；覆盖每行 ST-5 的正负例至少各一个，加 ≥12 条对抗例。输出 `case_id, policy_ok, candidate_valid, evidence_supported, ranking_permutation_ok, fallback_reason`，聚合独立安全门槛和候选召回；所有应为空的负例必须为空。
离线 fake 测试验证程序合同，真实模型评测用相同合成输入另行运行并记录模型/提示词版本。`90%` 是本次规划初始质量阈值，可在扩大 fixture 后提升，不能用均分覆盖安全失败。

## 兼容/回滚

默认三类关闭语义不变。原 reason_text_llm 只有纯文本且无验证版本的行视为 untrusted，读取回退模板、后台重新生成；不批量当作已验证输出。关闭单一辅助不会删除证据或影响 canonical core。
