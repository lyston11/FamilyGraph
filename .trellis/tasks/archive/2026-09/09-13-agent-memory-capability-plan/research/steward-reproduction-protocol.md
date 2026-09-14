# Research: Steward MR-23 / MR-26 隔离复现协议

- Query: 同结构候选在相关证据变化后是否能进入新 Suggestion 版本；行为重建是否保留自己不拥有的亲属推荐冷却。
- Scope: internal；仅 E worktree 生产代码只读、临时 Alembic SQLite 与 fake transport。
- Date: 2026-09-13

## Findings

本文件先冻结实验方法，实际结果另见运行 JSON 与结果报告。实验不修改生产代码，不连接开发/线上数据库，不启动服务，不调用真实模型。研究范围来自本任务 design §9/§10 和父 validation-plan V-E05/V-E06。

### MR-23

1. 在临时库建立两个子女 A/B、共同父亲 P、候选新增母亲 Q、无关人物 X/Y，全部是合成账户/空间成员。
2. 初始确认 P→A、P→B；Q→A、Q→B、X→Y 预先建 proposed 行。fixture 在首次 core 前将 expected_support_fact_ids 写入临时证据文件；它是人工场景标注，不进入模型输出或生产 digest。
3. 通过真实 enqueue/lease/execute_steward_job 完成 core；真实 run_due_batch/execute_batch 仅在 transport 层替换为固定结构 direct_sibling A→B。
4. 初次 assist 后观察内部候选；下一 core 观察 Suggestion；经真实 dismiss_suggestion 将 A 的收件状态改为 dismissed。
5. 三组独立空间分别确认 Q 两条相关事实、确认 X/Y 无关事实、保持所有事实不变。再次真实 core/assist，再次后续 core；还运行同证据新作业和同已应用 batch 重放对照。
6. 每阶段记录 job/batch、有效开关、模型 attempt、真实 fake transport 调用、candidate ID/digest、Suggestion ID/hash/全局状态与收件人状态。记录模型所见 fact ID/revision；不存 prompt、密钥或姓名。
7. 另跑平台候选开关关闭的 core，区分“没有调用”与“调用成功但结构去重”。不手造 candidate、不手造 digest、不直接调用 upsert/project。
8. freeze clock 只固定模块 utcnow；不替换生产业务分支。合成候选不证明 LLM 能正确推导关系。没有测自动扫描等待时间，也不据此采用新提醒 UX。

### MR-26

1. 同一账户/空间同时建立 card_cooldown、correction_preference、term_usage、kinship_recommendation_dismissed。
2. card/term 的白名单 DomainEvent 经真实 emit 写入，事件是合成 fixture；亲属推荐键使用真实 PFV rebuild 和 dismiss_recommendation 产生。另一账号仅有亲属推荐键，没有重放事件。
3. 调真实 rebuild_behavior_projections 两次，比较非本键族完整保留、自己三键族的值/时间与两次语义一致性。单独报告缓存行 ID，不要求删除重建后的 ID 稳定。
4. 行在启用时建好，再分别测试 behavior flag off/on × account/whole-space 两种重建范围。关闭不应删除既有值；开启是否删除非本键族由结果判定。
5. helper 生产调用搜索范围 backend/app 与 scripts；实验只证明被调用时的效果，不推论线上已经丢数据。

### 输入代码与规范

- backend/app/services/steward.py:266：rebuild_behavior_projections。
- backend/app/services/steward.py:815、:922、:998：实际核心 runner、执行包装、core/assist 登记次序。
- backend/app/services/steward_assist.py:443、:653、:883、:1174：批次登记、调度、执行、候选去重。
- backend/app/services/steward_suggestions.py:270、:301、:329、:606：建议投影、已投影候选排除、证据快照、真实驳回。
- backend/app/services/steward_guard.py:217、:349：输出合同与结构 digest。
- backend/app/services/family_recommendations.py:22、:27、:217：独立冷却键和实际生产写入。
- backend/tests/test_steward_assist.py:176：真实同步批次测试先例；test_steward_suggestions.py:153 的 upsert 测试不是本次方法。
- .trellis/spec/backend/steward-action-card.md：历史合同参考；当前代码与本任务审定设计为本次实证依据。index 已声明规范历史性，且原三键白名单已经过时。

## External references

未使用外部网页。真实版本、迁移 head、生产源码 SHA-256、Python/依赖版本由 harness 记录。E checkout SHA 由主线程只读核对后提供：20d03084df341f6cc5fc8fcc18042757c07d822a。

## Caveats / Not Found

- 没有真实模型质量、费用、线上开关或生产频率结论。
- 假模型会固定输出同一合法结构；它用于验证持久化/去重链。
- MR-26 不验证真实个人词事件的全套生产消费语义，只验证 helper 的键族删除范围与允许事件回放。
- E 只写研究材料；任何生产字段/迁移/调度修复由主线程指定独立所有者。
