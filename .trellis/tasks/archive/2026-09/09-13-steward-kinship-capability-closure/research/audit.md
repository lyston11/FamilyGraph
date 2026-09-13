# 证据与边界审计

日期：2026-09-13。代码基线：origin/main@4649e48ae271e106dca96a3bb875e97fbf379b3c，以下源码行号相对此提交。本地主检出较旧，任务目录不能直接代表远端状态。未取得可打开的截图文件；截图问题依据用户明确反馈文本和渲染源码核验。

后续核对：规划期间其他会话将 main 集成至 ad12eb6（含已归档 topology）。对 4649e48..HEAD 的差异检查显示，本期关键 suggestions/terms/assist/通知/推测面板文件未变，PFV 仅增结构拓扑输出；上述行号仍按初始基线引用。E 已交付 MR-23/26 复现并新建业务修复包，实际职责见 task-alignment.md。

## 直接缺陷与真实能力

| ID / 等级 | 出处 | 结论 / 所有者 |
| --- | --- | --- |
| F01 / 已实现 | backend/app/services/steward.py:1023–1025；personal_family_view.py:247–278；domain_events.py、maintenance.py | 管家自动计算/刷新个人称谓真实存在，无逐条审批。A/B 复用 |
| F02 / P1 静态 | steward_suggestions.py:575–599；SuggestionReviewDialog.vue:36–59 | 无 viewer 的 value 序列化和前端“生物学亲子”映射绕过称谓。A |
| F03 / P1 隔离复现 | terms.py:744,993；schemas/kinship.py:32,62,75,96；frontend/src/types/kinship.ts:10 | derived 来源缺失，响应 schema 拒绝。A |
| F04 / P1 静态 | steward_suggestions.py:138,176,270,758 | term_preference 只有处理接口、无生产者，生成 viewer 为 None。B |
| F05 / P1 静态 | steward_assist.py:63,104–124 | 三 kind 不含称谓，candidate 明确禁止派生称谓。B 独立 kind |
| F06 / P1 静态 | steward_assist.py:321–335,566,1089–1091 | candidate 上下文 space-wide、facts fence 只管 candidate、input_hash 未重验，不能直接复用于个人称谓。B |
| F07 / P1 静态 | steward_suggestions.py:286–329；steward_guard.py:217；InferredEdgePanel.vue:158 | 整空间事实快照和 schema 合格不证明关系；UI 却显示确定性依据。A |
| F08 / P2 静态 | notifications.py:190；steward_suggestions.py:487–504；NotificationsView.vue:125–128 | 本人忽略/终态未统一，旧详情依赖首页。A |
| F09 / P2 静态 | frontend/src/stores/stewardSuggestions.ts:99；SuggestionReviewDialog.vue:182 | 提交关联结果被丢弃，双方确认表述与命令不符。A |
| F10 / 已实现边界 | terms.py:268–355,443–474,497–555 | 个人词条跨空间、用词证据和两人晋升已有真源。B 不伪造用户选择 |
| F11 / P1 静态 | steward_suggestions.py:581–594 | 直接读取 User.name，未按字段可见性投影。A |

“无生产者”的检索范围：backend/app 内所有 term_preference、upsert_suggestion、project_for_job 定义/调用及测试。未据此推断线上开关。

## 已执行验证

先前在 e0e8d8d 的只读快照运行 guard/eval/suggestions/inferred/notifications 五组旧测试：39 passed，2.48s；这是基线，不是本期验收。

在 4649e48 快照运行 ResolvedTermOut.model_validate，输入 term=妹夫的父亲、source_level=derived、entry_id=None，得到 ValidationError/source_level/literal_error。没有访问真实 DB 或模型。A 仍需补真实 API 回归。

## 历史意图

- 2026-09-12 Pi 会话 01a0952f-7b63-715c-b1fa-6a3eadcdc3e4 turn 4：“管家agent能够自动算出当前用户的亲属的称谓、推荐称谓等，为每个用户单独处理他的家族树”。
- 2026-09-13 Zcode 会话 sess_1d00c56f-02ff-4d45-9c7f-79f081db7563 turn 8 反对先处理通知树才变化，turn 31 要求自动修复父亲的女儿/儿子的儿子，turn 37–38 批准当时确定性优化。
- archive/2026-09/09-12-steward-kinship-terminology 确认 viewer+space 管家职责。
- archive/2026-09/09-13-steward-term-autofix 的 PRD 仍写自动空间词条，最终 design 与 ade0a2b/e882789 实际选择词包/长幼/长链，不自动写词典。本期不把旧 PRD 当成已实现能力，也不以旧范围否定当前模型需求。

## 本期未承诺的独立问题

| 问题 | 处置 |
| --- | --- |
| 关系提案命令忽略 evidence_json；最终确认时图成环重验有缺口 | 独立事实状态机修复。本期不扩展确认入口，不宣称已修复 |
| MR-23：一般结构候选新证据/跨 job 更新 | E 已交付复现，业务修复归 steward-memory-evidence-projections；新称谓链使用自己的相关路径摘要 |
| MR-26：全量 BehaviorProjection 重建影响其他键族 | E 已交付复现，业务修复归上述 P2 包；本期直接读取本人称谓域记录，不接该 helper |
| 推测路径选择/世代传播、PFV 推测 step_json 覆盖疑点 | 和 topology/推测层交接；A 修改显示时核验单跳端点，不擅自重做几何 |

未执行真实模型质量、生产运行状态或线上数据实验；不把模型自评 evidence_supported=true 当成外部证据。
