# Research: E-O1～12 能力决定登记

- Query: 哪些议题本轮有实证、哪些保留现状或延期，以及后续唯一责任归属。
- Scope: internal；父审计、E 设计、两个 Steward 实证和离线预算方案。
- Date: 2026-09-13

## Findings

本轮新增实证是 **MR-23/MR-26 的真实服务链合成复现** 和 **E-O6 全请求预算的纯合成方案分支**。其他能力没有执行产品质量/成本实验，不能标为已评估采用。所有新增生产能力保持明确延期；仅将已确认的两个缺口交给主线程已创建的 P2 规划包，尚未实施。

以下状态使用本任务 design 的“明确延期”；“保留现状”描述过渡行为，不假称已经做过未执行的比较。成本/延迟上限没有在实验前冻结的议题禁止开始真实模型比较，更不能在看到结果后调整门槛。

### 决定表

| ID | 状态与本轮实证 | 当前保留行为 / 延期理由 | 重新评估触发与采用门槛 | 唯一后续责任 |
|---|---|---|---|---|
| E-O1 主动检索 | 明确延期；未做工具/一次预取对比 | 保留一次预取；A/B/D 稳定词法基线未交付给 E | 同冻结追问/二步实体/无需检索集，对比来源覆盖/Recall@5/MRR、额外调用和 p95；权限零越界、事前冻结成本上限；最多额外 2 次检索只是候选方案 | E/O1 评估责任；采用后由 E 拆唯一 assistant retrieval 实施包 |
| E-O2 显式聊天保存 | 明确延期；未做入口或转化实验 | 优先本人原始 user 消息方案；派生回答来源图未设计完 | A 最终来源/幂等合同稳定；否定/临时信息/他人引用/撤销/重复等集验证；创建 pending 后明确确认，不把点击当永久记住 | E/O2；采用后唯一显式保存入口任务 |
| E-O3 自动候选 | 明确延期；未运行 extractor 比较 | 保持默认关闭；用户负担/精确率未测 | 独立 opt-in、source span 去重和待确认；事前冻结候选精确率、漏检、确认负担、调用费用门槛；无证明收益就不接 | E/O3；采用后唯一 opt-in 候选任务 |
| E-O4 授权导入 | 明确延期；未运行解析链 | 现有 service 不算可用上传功能；格式/权属/UI 范围未选 | A/B/D 稳定后先限定文本/Markdown；真实上传授权、解析失败、同名换版、撤销和引用反例；成功阈值含零跨空间读取 | E/O4；采用后唯一 ingestion 任务 |
| E-O5 语义/混合/rerank | 明确延期；无新质量/性能数据 | 保留词法；不能用旧坏召回来夸大向量收益 | 在 B 改善后的相同核心/独立扩展集比较 Recall@5/MRR、无关命中、来源有效率、索引/删除延迟、资源/成本；先定外发/本地要求与上限 | E/O5；采用后唯一 hybrid retrieval 任务 |
| E-O6 全请求预算 | 明确延期（生产采用）；[18 个离线方案断言](context-budget-design-results.md)通过 | 方案分支已验证；未导入 app、未验证 tokenizer 或实际 SDK envelope | C/B 稳定后验证真实 Provider 窗口、完整 payload 计量、超长当前输入/工具结果/压缩后复算、明确拒绝；预算误差与支持模型范围先冻结 | E/O6；采用后唯一 full-request budget 任务 |
| E-O7 跨 Run 摘要 | 明确延期；没有持久摘要实验 | 保留 C 局部一致性修复与原始文字恢复 | 先选原始对话限定策略或完整依赖策略；scope/version/cursor、撤权/删除/游标尾部去重和 lease 幂等全部通过，成本可接受 | E/O7；采用后唯一 scoped summary 任务 |
| E-O8 编辑 / scope | 明确延期；未做修订链 | 不原地静默改知识，不把 RAG 来源改 manual | A/D 来源合同稳定；新 revision/再次确认、旧引用失效、扩共享/降敏拒绝反例与并发通过 | E/O8；采用后唯一 memory revision 任务 |
| E-O9 删除 / 保留 | 明确延期；未选择保留期限或执行物理删除 | 区分后续不可召回、历史展示、备份/Provider 已外发内容 | 产品选择数据对象/保留期/历史遮罩；可逆试运行报告和不可逆步骤另行评审；不能把软删除描述为全部抹除 | E/O9；采用后唯一 retention policy 任务 |
| E-O10 Steward shared RAG | 明确延期；六类场景协议已交接，尚未比较模型 | 保留当前结构输入，不读 private/session、不覆盖 DerivedFact | A/B/D service 合同稳定；六类合成权限场景、来源有效率/增益/成本通过后才选用 | **既有 09-11-steward-capability-followups 的 R1**；E 不另建实现分支 |
| E-O11 反馈 / 行为投影 | 明确延期；MR-26 条件风险已复现，反馈排序收益未测 | 保留 TermRegistry 与现有 ActionCard；不接无消费者投影、不自动接 rebuild | MR-26 先修键族保留；反馈特征须有授权消费者、有限时间窗，与确定性基线比较后才采用 | **MR-26：09-13-steward-memory-evidence-projections**；剩余反馈评估只归 E/O11 |
| E-O12 证据 / 冷却 / 延迟 | 明确延期（扩展）；MR-23 已复现，未测实际扫描延迟或到期 UX | 保留同证据 dismissed、当前下一 core 投影时序；不新加通知 | 相关证据归因/版本修复先过完整生产链；到期重现由产品选择；延迟改进需实测并仅作幂等投影、不循环调用模型 | **MR-23：09-13-steward-memory-evidence-projections**；剩余 UX/时延评估只归 E/O12 |

E/O1～9、E/O11 的反馈、E/O12 的可选 UX 表示本 E 任务内部的唯一评估责任，没有创建或授权同名生产任务。它们不是 A/B/C/D 的隐含追加交付。两个 MR 的统一修复包已经由主线程创建规划；其 worktree、PRD 与实施以主线程为准，本研究不改 task.json。

### 每项方案的可执行协议与输入/授权边界

| 议题 | 既有协议 | 本轮执行程度 |
|---|---|---|
| E-O1 | ../design.md §2：Run 派生身份、最多两次候选调用、冻结追问/二步/无需检索集；V-E01 | 未测；真实成本和延迟上限未冻结 |
| E-O2/O3 | ../design.md §3：本人原始消息/span→pending→明确确认；否定/更正/玩笑/临时/派生/重复集；V-E02 | 未测；A 最终契约与 UI 范围待稳定 |
| E-O4 | ../design.md §4：上传/权属/版本/解析/授权引用/撤销闭环；V-E02 | 未测；格式选择及实际样本解析待执行 |
| E-O5 | ../design.md §5：核心集不退化，扩展集比较词法/混合/rerank；V-E01 | 未测；不得报 MRR、p95、费用虚构值 |
| E-O6/O7 | ../design.md §6：全组成计量、窗口/压缩后复核、摘要 provenance/version/cursor；V-E03 | O6 仅合成算法分支18例通过；O7 未测、未持久 |
| E-O8/O9 | ../design.md §7：revision/确认/范围/来源约束、保留对象/备份/已外发划分；V-E02 | 未测；无物理操作 |
| E-O10 | ../design.md §8 与 [交接](steward-capability-handoff.md)：六类共享检索反例；V-E04 | 交接协议完成；质量实验未执行 |
| E-O11 | ../design.md §9、[MR-26](behavior-projection-rebuild-results.md)；V-E06 | 4组合真实 helper 复现；反馈排序没测 |
| E-O12 | ../design.md §10、[MR-23](steward-candidate-evidence-results.md)；V-E05 | 15次 fake 调用、多 job、6次同 batch 重放、关闭对照；到期/扫描延迟没测 |

失败/回退遵循上述协议：辅助失败保留确定性结果；未确认不索引；无法证明来源就拒绝/延期；超预算清楚失败；能力收益不足保留当前模式。所有模型质量对比以 A–D 最终稳定版本为同一基线。探针的合成 usage=15 只验证批次审计通路，不能进入实际费用报告。

### MR-15 四列能力状态

前五行的实现/接线来自父审计，E 不把它们说成重做了全面审计。生产当前环境均未连接核验。

| 能力 | 实现符号存在 | 生产调用接通 | 本轮有效开关事实 | E 本轮验证 |
|---|---|---|---|---|
| Assistant 每 Run 预取 RAG | 有 | 有 | 未查线上 | 无新测量；A/B/D 独立实施 |
| 主动 memory/RAG 工具 | 原审计未发现 | 原审计未接 | 不适用 | 未测 |
| 自动候选 / 文档导入 | 空 extractor / 入库 service | 默认提取为0 / 上传入口未接 | 未查线上 | 未测 |
| embedding / 跨 Run 摘要 | 状态占位 / 未接持久摘要 | 未接 | 不适用 | 未测；预算探针不是摘要实现 |
| Steward shared RAG | 共用底层服务存在 | 原审计未见 Steward 调用 | 未查线上 | 未测 |
| Steward candidate assist | 有 | core→batch→attempt→候选 | 合成 true 时15次；平台 false 时0次 | 真实服务 + fake transport，确认 MR-23 |
| Steward ranking/explanation | 有 | 既有生产批次路径存在 | 未查线上 | 本轮关闭，没有质量/费用数据 |
| ActionCard 冷却 | 有 | 生产 set/read | 合成 behavior on 有效、off reader=false | MR-26 实验附带验证读路径 |
| term/correction projection | rebuild helper 存在 | 原审计无消费/无生产 rebuild | 合成手动调用on/off | 仅允许事件回放；不是偏好学习 |
| family recommendation 冷却 | 有 | 真实 PFV→dismiss→put/read | 合成持久键on/off均可读，rebuild on删除 | 真实 producer/helper，确认 MR-26 |
| 全请求预算 | 本 research prototype 有 | **未接生产** | 无生产开关 | 18个合成方案断言，非真实tokens |

平台开关既有 PRD 同时记录了“处置前零调用、处置后成功调用”；本表不据旧零值推断今天线上状态。开关 admin API/UI/审计/有效原因仍归 09-13-steward-assist-platform-switch-admin。

### E-AC 处置

| 验收 | 处置 |
|---|---|
| E-AC1 | O1～12 的协议、责任、决定/重启门槛覆盖；未冻结的真实成本/延迟/精确率阈值明确延期，未声称实验完成 |
| E-AC2 | 明确延期：A/B/D 稳定检索基线、冻结扩展集与真实模型/成本实验尚未齐备 |
| E-AC3 | 明确延期：source/scope/legacy/派生内容反例的能力入口实验未执行；仍由 A/D 先形成基线 |
| E-AC4 | 离线方案部分完成：两窗口与中文/新输入/工具/压缩后复核18例；真实计量器/SDK接线明确延期 |
| E-AC5 | 完成复现交付：MR-23 生产多 job 与 MR-26 混合键族on/off均有证据、限制、最小修复；生产修复未实施 |
| E-AC6 | 已区分真实TermRegistry/ActionCard/Suggestion状态；反馈收益和到期恢复/可见时延选择明确延期 |
| E-AC7 | 交接文档完成，shared RAG归既有followups，开关归既有admin任务；MR修复归新P2规划包 |
| E-AC8 | 每个实证记录版本/命令/假模型边界；无线上状态/真实质量成本推断 |

## External references / Related specs

只使用本地审计、任务设计、源码和可运行探针。相关历史规范为 steward-action-card.md、agent-runtime.md，但以当前代码和已审定任务合同为准。

## Caveats / Not Found

本登记的“明确延期”是完成研究后的诚实处置，不代表能力已经实现，也不代表所有质量门槛已经测过。主线程可归档 E 的研究交付，但不能据此把新增功能标上线或将 MR-23/MR-26 标为已修复。
