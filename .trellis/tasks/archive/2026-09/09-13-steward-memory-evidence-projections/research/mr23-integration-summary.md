# MR-23 集成摘要

## 结论与决定

在已集成的渐进重算基线上，使用真实发布 core → 发布后 delivery 注册 assist → fake candidate transport → 后续 core/delivery 的链路验证。证据版本使用已有 candidate intent kind，独立稳定 key 与捕获的 candidate/version ID；内部意图不得调用公开建议或推测投影。

## 实施影响

- 可复用 `test_steward_staged_pipeline._run(deliver=False)` 和 `test_steward_assist` 的 provider/setting/fake/run helpers；新测试必须自行设置 autouse 开关与真实 `advance_search`，导入函数不会继承原 fixture。
- “尚无完整证书”保留单边 confirmed 父母事实，不能没有任何 facts，否则 assist 不注册 candidate。
- 同一 Steward setting 上设置 `inferred_tree=True`，不要再调用会插入重复 setting 的 helper。
- 私人驳回走 `steward_suggestions.dismiss_suggestion`，共享驳回走 `steward_inferred.dismiss_edge`；主链通过真实 delivery 创建原建议/边。
- 精确版本 ID 用例：V1 来自 P 配对，先确认 Q 配对，再准备 core 的 V1 意图，最后才记录 P+Q 的 V2。这样输入未在准备后变化，可证明旧意图不能顺手消费 V2；另测真正输入变化触发 fence。
- Delivery lease 用原 `_claim_due` 捕获真实 claim 后，以独立 Session 改 intent lease/owner/attempt，继续真实 drain；不是修改已完成 job 的 lease。
- 0049 在首个 DDL 前复用 0048 祖先预检。0048 helper 加可选 `planned` 参数，默认保持原逻辑；0049 用自身 revision 计算并传入计划。当前 `-1` 只撤销 0049；`-2` 在 merge 图中歧义，必须无 DDL 拒绝。无需计划缓存。
- `test_steward_terminology_publication_migration.HEAD` 改为动态单头；保留两父节点、数据/trigger/index、roundtrip 断言。RAG 与 Memory 的深降级 DDL=0、schema/head 原样断言不得弱化。
- 已复现 assist 取写锁等待跨过租约截止后仍应用的问题；在取得 writer 后使用 `max(caller_now, live_now)`，保留未来测试时钟的收紧作用，阻止旧采样时间延长真实租约。

## 持续约束

稳定 candidate 身份不重排；证据 JSON 不存姓名/模型自由文本；原 revision、当前空间及父母节点均重验；versioned 按无向端点对阻止两个公开入口。内部 projected 仅是所记录时点的历史核验，不是持续有效关系。

## 何时读取完整证据

夹具没有生成 batch/candidate、深降级提前改了 schema、捕获版本测试被输入 fence 短路、租约测试改错对象，或需要重新评估相对迁移路线时，读取 [调查证据](evidence/mr23-integration-investigation.md)。

修改写回时间检查或怀疑数据库取锁等待造成租约绕过时，读取 [真实写锁反例](evidence/lease-writeback-investigation.md)。
