# Steward 候选审核、冲突待办与站内通知闭环

> 父任务：[09-11-steward-complete-hardening](../09-11-steward-complete-hardening/prd.md)
> 状态：实现回归已通过（2026-09-12）；发布门禁 PARTIAL——真实 provider E2E 未运行，不得标记 completed（审计结论见 ../09-11-steward-release-observability/release-evidence.md §8）。

## Goal

把有证据的模型候选与确定性冲突变成可理解的待办，用户可以看理由、驳回、补资料或显式发起关系提案；正式关系仍由有权当事人完成确认。

## 已核实依据

本任务负责 [F07](../09-11-steward-complete-hardening/research/findings.md#f07), [F08](../09-11-steward-complete-hardening/research/findings.md#f08), [F14](../09-11-steward-complete-hardening/research/findings.md#f14), [F15](../09-11-steward-complete-hardening/research/findings.md#f15)。严重性、原始代码锚点、已实现基线与不确定性见共享 findings；这些条目是范围内证据，不声称覆盖全仓所有安全问题。

## Dependencies

`09-11-steward-production-ops`, `09-11-steward-assist-execution`, `09-11-steward-projection-consistency`, `09-11-steward-quality-security`。Trellis parent/children 不自动执行依赖，开始前按本节检查。

## Requirements

### R1 建议的证据与状态

使用闭合建议种类、来源、主体、受众、证据版本、状态、revision 和过期时间；历史裸 JSON 缺证据的候选不直接公开。相同结构证据不同模型措辞视为同一建议，驳回与冷却按受众隔离。

### R2 受众与操作权限

列表只显示当前 active 成员有权处理且端点/证据对其可见的建议。空间管理员可做本空间资料巡检，不因角色代替两个 claimed 当事人确认关系。未知种类只有隔离/丢弃，不能透传为新命令。

### R3 明确用户动作

relation_proposal 先展示双方、关系方向、证据、隐私影响，再由用户点击发起；状态仅为 proposed/pending。最终确认复用有权当事人的 domain command/FSM。term_preference 只改本人显示词；identity_duplicate/gap 转既有资料/去重流程，不自动合并或补父母。

### R4 并发、撤回与审计

审阅、发起、确认、驳回均 CAS revision；写命令同事务校验证据/权限/当前状态并记录关联。关系建议 submitted 不等于 SourceFact confirmed。失效建议不能恢复，改证据可产生关联的新建议。

### R5 复用通知与前端

扩展现有 Notification、GET/read/read-all、NoticeItemRow 和 stores；ActionCard 通知已存在，不重建。新增 candidate/finding 引用；通知已读只改 read_at，点击详情才展示安全建议，最后确认才调用命令。

## Acceptance Criteria

| ID | 需求 | 可观察验收 |
|---|---|---|
| AC-1 | R1 | 同结构候选仅改 rationale 不生成第二待办；缺证据旧候选不显示；不同收件人独立驳回，证据变更 supersede。 |
| AC-2 | R2 | owner 非关系端点、普通成员、当事人、代管权限各自精确 allowed_actions；未知/隐藏建议统一 404。 |
| AC-3 | R3 | owner 接受建议只生成提案，SourceFact 不直接 confirmed；当事人完成既有授权步骤才入图。父母角色不能被 elder/younger 的模糊映射改成错误生物关系。 |
| AC-4 | R4 | 两个并发 submit 恰好一个领域副作用；访问被撤销统一 404，可见但证据变化/过期分别 409/410，均无正式写入；响应丢失重试返回同一关联对象。 |
| AC-5 | R5 | 已读不发申请/改事实；现有卡通知无重复；新通知站内可达，空间切换/账号切换/401 丢弃迟到响应，375px 可使用。 |

## Out of scope

不重建已存在的 core、ActionCard 通知或认领推荐；不恢复 generic Steward AgentRun/AgentJob，不做独立聊天人格、自动事实确认、自动发申请/入空间、私有会话读取、任意工具执行。其他子任务的模块由其负责，本任务只遵守接口。

## 决策状态

用户已授权生成规划，并接受确定性核心、可选模型辅助、用户确认和站内通知路线。工程参数为本方案初值；任何权限/产品范围改变需回到规划。
