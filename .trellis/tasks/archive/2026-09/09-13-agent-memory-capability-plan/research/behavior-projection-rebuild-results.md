# Research: MR-26 行为重建的键族删除范围

- Query: 重建 card/term 投影时，是否保留 family_recommendations 独立冷却及没有重放事件的其他账户状态。
- Scope: internal；真实 helper/真实亲属推荐 producer、合成 Alembic SQLite、冻结时钟。
- Date: 2026-09-13

## Findings

**结论：MR-26 已隔离复现确认，仍是“helper 被调用时”的条件性风险。** behavior flag 关闭时两次重建完全保留原行；开启时删除了亲属推荐冷却。搜索 backend/app 与 scripts 只发现 helper 定义，没有生产调用，因此不能称为线上已经丢失冷却。

证据：[完整 JSON](steward-capability-results.json) 的 mr26、[harness](steward_capability_probe.py):639 的 mr26_case、[事前协议](steward-reproduction-protocol.md)。源码/依赖版本与 MR-23 相同：checkout 20d03084df341f6cc5fc8fcc18042757c07d822a，迁移到 0041_term_pack_expansion，所有数据来自新的临时库。

### 造数与真实入口

每组空间中 actor 有四个键：

- card_cooldown:household_link
- correction_preference:mother
- term_usage:mother
- kinship_recommendation_dismissed:<parent_id>

另一个 observer 账户仅有一个亲属推荐冷却，没有白名单重放事件。card/term 事件是合成 fixture，经真实 domain_events.emit 写入：card.dismissed ×1、term.personal_updated ×1、term.usage_recorded ×2。对应三个缓存键由真实 put_projection/set_kind_cooldown 建立。

actor 的亲属冷却不是直接构造数据库行：先通过真实 create_source_fact/confirm、personal_family_view.rebuild_view 生成可见亲属推荐，再调用真实 family_recommendations.dismiss_recommendation。随后才切换被测 behavior flag。关闭组因此代表“关闭前已存在持久状态”，没有绕过关闭写门禁造数。

### 实际结果

每个组都对同一事件集、同一 now 调用 rebuild_behavior_projections 两次。

| Behavior flag | 重建范围 | 原行数→第一次→第二次 | actor 亲属冷却 | observer 亲属冷却 | helper 返回 | 三个自有键两次值一致 |
|---|---|---|---|---|---|---|
| off | 指定 account | 5→5→5 | 保留 | 保留 | 0、0 | 是，所有行完整相等 |
| off | whole space | 5→5→5 | 保留 | 保留 | 0、0 | 是，所有行完整相等 |
| on | 指定 account | 5→4→4 | **被删除** | 保留 | 4、4 | 是 |
| on | whole space | 5→3→3 | **被删除** | **被删除** | 4、4 | 是 |

on/account 的 actor=25、space=6，亲属键 kinship_recommendation_dismissed:26 从 row 14 消失；observer=27 的 row 15 保留。on/space 的 space=7 同时删除 actor=28 与 observer=30 的两个亲属键。原始行 ID、值及时间均在 JSON，不将第二次 SQLite 复用 row ID 误解为第一次没删。

family_recommendations._cooldown_active 从 true 变为 false，说明不只是统计行减少，读侧也已经不再看到用户驳回。ActionCard 的 kind_in_cooldown 在 on 组始终 true。off 组 card reader 返回 false，而 family reader 仍可读取已有冷却，这是当前两条读路径各自的开关语义，不另推断成新缺陷。

helper 返回的 4 是重放事件数，不是最终缓存行数：两次 term.usage_recorded 汇总为一个 count=2 的键。自有键值/时间在两次重建间相等，证明“回放幂等”本身不足以保证不破坏别人的键。

### 代码原因与实际检索范围

- steward.py:100 的 PROJECTION_KEY_PREFIXES 当前允许四个键族，包含 kinship_recommendation_dismissed。
- steward.py:266 的 rebuild_behavior_projections 在 :281 检查 behavior flag。
- :297 的 account 分支 DELETE 只有 space/account 条件；:304 的 whole-space 分支删除整个空间。
- :287、:315 的 event 类型只包含 card.dismissed、term.personal_updated、term.usage_recorded。重放循环不生成亲属键。
- family_recommendations.py:22 / :217 / :230 用独立前缀保存冷却；本次真实 producer 已被执行。
- 已执行 rg -n 'rebuild_behavior_projections' backend/app scripts，唯一匹配为 steward.py:266 定义。测试调用不等于生产接线；未扫描线上动态插件或任意运维脚本。

### 最小补丁范围（建议，尚未实现）

唯一实施所有者：与 MR-23 共用主线程已创建的 P2 规划包 .trellis/tasks/09-13-steward-memory-evidence-projections；尚未实施，由主线程排期。研究代理不修改该任务状态。

1. 在 steward.py 定义**该 rebuild 自己拥有**的三个前缀，独立于包含亲属键的全局写白名单。两个 DELETE 分支都加同一 owned-key 谓词。
2. 使用真实字面前缀匹配，例如 SQLAlchemy startswith(prefix, autoescape=True) 或等价精确 substr。card_cooldown 等含下划线，不应直接用未转义 LIKE 让 _ 成为任意字符。
3. 不拥有的键始终保留原 ID、value_json、updated_at，即使该账户没有可重放事件、whole-space 的 event_accounts 为空也一样。
4. 自有键仍从原三个事件类型按 event ID 重建，count=2 等聚合不变；flag off 保持直接返回 0。保留现有调用方事务边界。
5. 增加同 fixture 的回归：on/off × account/space、其他账户、同空间非拥有键、无事件账户、整个空间无匹配事件、二次回放。断言值与行身份，不只比较返回计数。
6. 不需要迁移，不需重写 TermRegistry、不新增事件水位、不接 maintenance、不改变亲属推荐 producer。若以后有消费者要接 helper，再按完整事件来源/授权/事务合同单独设计。

## External references / Related specs

没有外部网络资料。历史规范 .trellis/spec/backend/steward-action-card.md 仍描述三个可写键族，落后于当前四前缀代码；不能从旧白名单推论当前表只存三类状态。

## Caveats / Not Found

- 本次没有修改生产 helper，风险仍待实施修复；completed=true 指探针成功跑完，不是产品已修复。
- term/correction 事件是有意构造的允许事件，用来隔离删除范围。没有验证真实个人词 producer 的空间 fan-out、全局事件归属或消费者是否应接入。
- 只跑冻结时钟下的两次同事件回放，未做并发 rebuild、真实数据量性能或物理清理。
- 现有 helper 无生产调用，不能用本结果宣称线上正在丢数据或借机开启后台重建。
