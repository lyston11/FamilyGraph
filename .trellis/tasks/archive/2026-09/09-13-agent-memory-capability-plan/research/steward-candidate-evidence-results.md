# Research: MR-23 候选证据换版的生产链复现

- Query: 同结构候选在驳回后收到相关新事实，能否经真实 core/assist 再进入 Suggestion 新证据版本。
- Scope: internal；实际生产服务 + 合成临时 Alembic SQLite + fake transport，无真实模型。
- Date: 2026-09-13

## Findings

**结论：MR-23 已由静态风险升级为隔离复现确认。** 相关事实已经进入模型输入，所有批次成功应用，但稳定结构 digest 将新产物跳过；后续 core 又排除了已经投影的旧候选，Suggestion 一直保留旧证据。未修改生产代码；没有线上发生频率或真实模型质量结论。

证据：[完整 JSON](steward-capability-results.json)、[可运行 harness](steward_capability_probe.py)、[事前协议](steward-reproduction-protocol.md)。主线程提供的 checkout 为 20d03084df341f6cc5fc8fcc18042757c07d822a，源码 SHA-256 和依赖版本见 JSON.versions。真实迁移 head 为 0041_term_pack_expansion；Python 3.12.12、SQLite 3.51.1、SQLAlchemy 2.0.36、Alembic 1.14.1。

### 方法与标注

harness:398 的 mr23_case 创建合成兄妹 A/B、共同父亲 P、拟新增共同母亲 Q、无关人物 X/Y。起初只确认 P→A、P→B；其余事实先建 proposed 行以固定 ID。首次 core 前将人工标注的 expected_support_fact_ids 写入独立临时文件。相关组从 [1,2] 变成 [1,2,3,4]；它们没有注入模型 schema 或候选 digest。

harness:254 的 run_core 调用真实 enqueue_steward_job → lease_next_steward_job → execute_steward_job。辅助走真实 run_due_batch → execute_batch，只有 HTTP transport 返回固定合法 direct_sibling A/B 输出。模型被调用时记录输入 fact ID/revision、批次/attempt；不保存 prompt、Authorization 或姓名。

### 相关证据组的逐阶段结果

表中所有 core 均 succeeded；所有 assist 的 attempt 均 succeeded、batch 均 applied。初始事实 revision 都是 2（proposed→confirm）。

| 阶段 | job / batch | 模型看到的事实 | candidate | Suggestion |
|---|---|---|---|---|
| 初次 core | 1 / 1 pending | 尚未调用 | 0 | 0 |
| 初次 assist | 1 / 1 applied | 1,2 | ID 1 | 0，尚待下一 core |
| 后续 core / assist | 2 / 2 | 1,2 | 仍 ID 1 | 创建 ID 1，证据 1,2 |
| 真实 dismiss | 沿用 job 2 | 无新调用 | 仍 ID 1 | 全局 proposed、revision 2；A 的列表状态 dismissed |
| 确认 Q 两条相关事实后 core / assist | 3 / 3 | **1,2,3,4** | **仍 ID 1** | **仍 ID 1，证据只有 1,2** |
| 再次后续 core / assist | 4 / 4 | 1,2,3,4 | 仍 ID 1 | 未 supersede，A 仍 dismissed |
| 同证据再次作业 | 5 / 5 | 1,2,3,4 | 仍 ID 1 | 同上 |

candidate digest 始终为 459ab875b1803763cb6f1f68118fc302123dd32f1b685a602fea3dfb53701a0d。Suggestion evidence_hash 始终为 f1cce08347d474593a8a0fc3ad73633c54a0aaa7f484781ed270a1e42cc880b6。与此同时，batch 的全空间证据 hash 已从 b4aa6383… 变成 c915485b…，说明代码看见了证据变化，不是旧请求被缓存或模型未运行。

### 对照与排除

| 对照 | 实际 job / batch | 结果及含义 |
|---|---|---|
| 无关 X/Y 事实从 proposed 确认 | 6～10 | 5 次 fake 调用成功；全空间 batch hash 改变，但相关标注仍 [6,7]；1 candidate / 1 Suggestion，旧收件驳回保留 |
| 没有新事实，仅新的调度事件 | 11～15 | 5 次 fake 调用成功；batch hash 不变；1 candidate / 1 Suggestion |
| 已 applied batch 重放 | 3、5、8、10、13、15 | 每次返回 applied，新增 transport 调用 0 |
| 平台 candidate 关闭 | job 16 | core succeeded、effective_candidate=false、没有 batch/attempt，确实未调用 |

总计 15 次 fake transport，真实模型/网络调用为 0。无新证据与无关变化组符合“同证据不重复打扰”的目标；它们不能抵消相关组暴露的更新通路缺口。

### 代码原因

- steward_guard.py:67、:217、:254：候选经校验后只保留 kind / subject_user_id / object_user_id，没有 supporting_fact_ids 或来源版本。
- steward_guard.py:349 的 candidate_digest 只哈希 kind 和两端点。
- steward_assist.py:1174 的 _apply_batch 查询同空间、同 digest；exists 非空立即 continue，未更新候选来源作业或保存新证据版本。
- models/steward.py:420 的 UNIQUE(space_id,candidate_digest) 也只允许一行稳定结构。
- steward_suggestions.py:301 的 project_for_job 永久反连接已被任意建议引用的 candidate ID。旧候选再跑 core 也不会到达 upsert。
- steward_suggestions.py:329 的 evidence 使用当前作业全部 facts。只删除前两道去重会把无关事实变化误当成候选新证据。
- steward_suggestions.py:189、:215 表明下游 upsert 已有证据相同复用、证据不同 supersede 的机制，但本次真实生产链无法把新版本送达。
- steward_suggestions.py:635 的 dismissed 是收件人字段，Suggestion 全局仍 proposed；报告没有把它误称为全局终态。

### 最小安全修复方案（建议，尚未实现）

主线程已创建唯一、有界的 P2 规划包 .trellis/tasks/09-13-steward-memory-evidence-projections，同时承担 MR-23/MR-26，尚未实施。它与共享 RAG 能力任务分开排期；建议先做无 schema 的 MR-26，再设计/验证 MR-23，避免两组 worker 同时改 steward.py。

1. 稳定结构与证据版本分离：保留 kind/有向端点结构 identity；每个有效版本有规范化 support fact ID/revision、validation_contract_version、evidence_digest、来源 batch/job。唯一性变为 space + structural identity + evidence_digest。迁移编号须在实际实施时确认。
2. **先定义相关证据归因，再增加版本。** 狭窄首版可只支持有确定性证书的类型，例如本 fixture 的共同父母事实对；其他类型继续保留原去重并报告 unsupported attribution。扩展模型 supporting_fact_ids 是另一选项，但必须验证 ID 获权、confirmed/revision、与结构的允许支撑关系；模型声明“相关”并不自动可信。
3. 生产候选输出当前没有归因，不能从 endpoints 反推出完整支撑集，也不能把 _facts_evidence 的全空间 hash 当持久证据版本。该全空间 hash 可继续用于批次 TOCTOU 栅栏，两者用途不同。
4. 每个候选证据版本只投影一次；project_for_job 使用候选已经验证的 support snapshot，投影时重验来源/权限/状态，不能替换为投影时整个空间的 facts。
5. 不原地修改旧 candidate 的证据后仍复用 candidate ID，否则“已被 Suggestion 引用”反连接仍会挡住更新，且历史来源失真。
6. 同结构/同证据重试不出新版本；相关新增/撤销/revision 变化经规则构成新版本；无关变化不产生版本。并发唯一约束、旧版本追溯与旧引用读取一起验收。
7. **版本更新与通知分开。** 现有 upsert 会调用 _record_suggestion_notifications（:214），因此不能用它顺便恢复提醒。未确定新证据重新提示 UX 前，可先只记录内部新版本；任何新的可见建议/通知策略单独评审，不默认解除旧收件人的驳回。
8. 保留后续 core 才可见的当前时序。本次只验证“至少需要下一 core”，没有测扫描延迟，不为此新增会再次调用模型的循环 job。

实施验收复用本 fixture，要求相关组能形成可审计的新版本，无新/无关两组仍复用；再补来源撤销、权限降低、两执行者并发与通知不重复。不能仅加直接 upsert 测试。

## External references / Related specs

没有外部网络资料。历史参考为 .trellis/spec/backend/steward-action-card.md 的候选审核、批次隔离和去重合同；研究判断依实际源码及本任务 design §10，不能沿用旧“payload 含 rationale”注释。

## Caveats / Not Found

- 本轮是服务链验证，不是 HTTP/浏览器或后台调度实时时延测试。
- 只使用一种合法候选结构做确定性复现，不声称已评估所有关系类型的模型召回。
- 合成事件仅触发新的生产作业；没有人为变更 digest、手造候选或直接 upsert。
- 真实模型 token/cost、线上有效开关、用户是否希望新证据重新提醒均未测/未选择。
