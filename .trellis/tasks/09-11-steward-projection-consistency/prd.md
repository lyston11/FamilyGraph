# Steward 个人视图、称谓与失效传播修复

> 父任务：[09-11-steward-complete-hardening](../09-11-steward-complete-hardening/prd.md)
> 状态：规划完成待实施评审；本轮不启动；优先级 P1。

## Goal

让个人家族树在关系、称谓、成员、披露、桥接和策略变化后及时重算，并在后台未完成时也不返回失效关系路径或借 304 保留旧权限内容。

## 已核实依据

本任务负责 [F09](../09-11-steward-complete-hardening/research/findings.md#f09), [F10](../09-11-steward-complete-hardening/research/findings.md#f10), [F11](../09-11-steward-complete-hardening/research/findings.md#f11), [F12](../09-11-steward-complete-hardening/research/findings.md#f12), [F13](../09-11-steward-complete-hardening/research/findings.md#f13)。严重性、原始代码锚点、已实现基线与不确定性见共享 findings；这些条目是范围内证据，不声称覆盖全仓所有安全问题。

## Dependencies

`09-11-steward-production-ops`。Trellis parent/children 不自动执行依赖，开始前按本节检查。

## Requirements

### R1 统一事件影响范围

集中定义事件→受影响 account/root/space 的映射；覆盖 source_fact（包括全局）、space.membership、space_profile_ref、relation、term、disclosure、profile、bridge 和策略版本。不得把所有无空间事件都无差别全局 fan-out。

### R2 初始化与状态收敛

保留已实现的 managed→claimed 双入口；补当前已有注册/首次获得合法空间访问权/首次可达路径形成后的后台初始化。无空间不创建空间，有权但无路径只生成本人/安全空态，不进入亲属推荐池。

### R3 权限与路径重验

读取前验证当前所有路径证据与端点/中间节点可见性，替代路径也需验证；只过滤两端不够。撤权事务内同步标 stale，返回安全状态/空集，不把旧路径当 current。

### R4 称谓与版本

PersonalFamilyView.term 使用现有个人>空间>地区>系统词典解析，结构性描述仅作 fallback；个人偏好变化影响同账号相关空间，不修改原始称谓文本或 SourceFact。policy_version 存真正版本而不是 purpose="graph"。

### R5 条件缓存

ETag 绑定当前授权 epoch、事实/词典版本、计算版本和 policy_version；先完成权限/版本检查再判 304。GET 不隐式写缓存且随后 rollback 丢失；缓存物化和提交边界需明确归属。

## Acceptance Criteria

| ID | 需求 | 可观察验收 |
|---|---|---|
| AC-1 | R1 | 逐条发送真实 producer 的事件，断言仅相关视图 stale；包含 space.membership.changed、term.personal_updated、全局 source_fact.revised 和 disclosure.updated。 |
| AC-2 | R2 | 认领两入口、无码注册、邀请码入空间、后加入成员、首次路径形成均无浏览器 GET 也能达到预期安全状态；不创建授权外成员/节点。 |
| AC-3 | R3 | 构造 A→隐藏中间人→B，A/B 仍可见但中间事实撤销，旧主路径和替代路径均不可输出；推荐不使用旧 current。 |
| AC-4 | R4 | 同一关系给两个用户不同个人称谓，PFV 与词典一致；改词触发版本和 ETag 变化，另一用户偏好不被覆盖。 |
| AC-5 | R5 | 持旧 If-None-Match 在撤权、bridge 过期、策略更新、改称谓后请求，绝不返回错误 304；首次物化跨独立 DB 会话可持久读取。 |

## Out of scope

不重建已存在的 core、ActionCard 通知或认领推荐；不恢复 generic Steward AgentRun/AgentJob，不做独立聊天人格、自动事实确认、自动发申请/入空间、私有会话读取、任意工具执行。其他子任务的模块由其负责，本任务只遵守接口。

## 决策状态

用户已授权生成规划，并接受确定性核心、可选模型辅助、用户确认和站内通知路线。工程参数为本方案初值；任何权限/产品范围改变需回到规划。
