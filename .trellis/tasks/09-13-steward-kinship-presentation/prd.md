# 管家个人化称谓统一输出与通知展示

## Goal

让用户在通知、档案和家族树看到同一管家称谓及清楚方向，消除“生物学亲子”和把推测当事实的表述，同时让旧通知和处理状态可用。

父任务：[称谓能力闭环](../09-13-steward-kinship-capability-closure/prd.md)。本子任务负责父 R-01/02/06，当前仅规划。

## Dependencies

最终方案评审后启动；以已集成的 steward-inferred-tree-layer、family-tree-relationship-topology（f08089c 已归档）和最新 origin/main 为基线。保留 topology 的真实端点/布局，共享 PFV/schema/types 的后续修改串行。B 必须等待本任务合同完成并串行集成，不能仅凭父子树位置并行。

## Requirements

- A-R1（P1）：新增 viewer 绑定的服务端呈现合同，优先消费当前 PFV/Terms。通知和推测面板不再把 fact_type 翻译为默认称谓；参考人、目标和推测状态明确。依据：steward_suggestions.py:575、SuggestionReviewDialog.vue:36–59。
- A-R2（P1）：统一来源枚举，覆盖 derived 及后续 steward；修复真实 kinship API 对 derived 的拒绝。依据：terms.py:993 与 schemas/kinship.py:32,62,75,96。
- A-R3（P1）：当前姓名、路径和字段经授权后投影；不可见中间节点、撤权、旧 PFV 不泄漏；未知性别/长幼安全降级。依据：steward_suggestions.py:581–594 直接读姓名。
- A-R4（P1）：模型线索显示待核实；只将已验证相关路径作为依据，整空间快照不能充当证明。已由确认关系得到的称谓自动显示，不重复发称谓关系提案。通知/推测层同来源状态一致。依据：steward_suggestions.py:286–329、InferredEdgePanel.vue:158。
- A-R5（P2）：统一本人有效状态和可用动作；ignored/terminal/expired/stale 不仍显示 pending。按 ID 获取旧详情；submit 保留关联提案和真实状态，无“必须双方确认”伪承诺。依据：notifications.py:190、allowed_actions:487、NotificationsView.vue:125、stewardSuggestions.ts:99。
- A-R6：旧 API 字段兼容；旧数据无需逐条改写，新前端缺 presentation 时用中性空态，不回退到 raw enum。普通读取不调用模型或改关系事实。

## Acceptance Criteria

| ID | 需求 / 父验收 | 可观察结果 |
| --- | --- | --- |
| A-AC1 | A-R1 / AC-01 | 父/子/旁系三个 viewer 同一家庭显示各自称谓；主谓方向明确，默认界面没有“生物学亲子” |
| A-AC2 | A-R2 / AC-03 | 真实 resolve API 返回 derived 并成功渲染；全部相关 schema、类型、来源徽章一致 |
| A-AC3 | A-R3 / AC-02 | 遮罩姓名、隐藏中间节点、失效授权、未知/同年出生不泄漏或猜称谓 |
| A-AC4 | A-R4 / AC-10 | 旧模型线索不显示确定性证明；相关路径数真实；通知/推测面板同源状态一致 |
| A-AC5 | A-R5 / AC-09 | 21 条以上可打开旧详情；忽略/过期/终態同步按钮与计数；提交结果保留 linked_proposal |
| A-AC6 | A-R6 / AC-05/11 | 读取无模型调用/事实写入；旧载荷/坏类型安全降级；原事实枚举仍可供命令内部使用 |

## Out of scope

不实现模型调用与自动偏好生产（归 B），不改家族树几何，不改真实关系同意规则或扩展确认入口，不批量重写历史事实/通知数据。

## Decisions

服务端拥有称谓与方向语义，前端只渲染。个性化称谓不改变真实结构边含义。普通通知文案无需透露生物学分类；继养监护边界仍保留准确语义。
