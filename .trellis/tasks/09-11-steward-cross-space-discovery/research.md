# Research — Steward 跨空间发现准入评估（2026-09-12）

## 结论

当前结论为 **NO-GO / 保持 deferred**。现有 PersonalFamilyView bridge 是双方 anchor 本人同意后的显式跨空间授权通道，不等同于跨空间陌生人发现；不能复用它实现主动匹配，也不能让 family recommendation 查询全局空间。

## 现有边界证据

- `family-recommendations` 接收明确 `space_id`，现有推荐来自当前授权 PFV/空间，不是全局发现。
- `relationship_graph` 只把 active bridge 对端成员纳入可见图，并要求 viewer 是对应 anchor；bridge 只在双方 claimed 且本人分别 consent 后 active。
- bridge 创建时要求 anchor 本人、两个 lineage space、active 对端成员和已确认 anchor 关系；管理员不能替代本人 consent。撤销会产生双方空间影响事件。
- 当前安全基线要求搜索和检索只能在选定空间与授权 projection 内运行，不能借搜索发现隐藏对象。

## 未来输入/输出差异

| 项目 | 当前 within-space 推荐 | 未来 cross-space discovery |
|---|---|---|
| 输入 | 当前 space 的 active PFV、confirmed facts | 双方主动 opt-in 的最小线索，不能是全局图扫描 |
| 输出 | 当前空间已授权人物/候选 | opaque candidate token；未兑换前不返回 person/space/path |
| 权限 | 当前用户+space 授权 | 双方本人分别同意；不产生 membership/bridge/fact |
| 结果 | 可进入现有审核流 | 只能邀请/请求进一步同意，不能自动连接 |
| 失效 | PFV/事实/成员/披露事件 | opt-out、过期、撤销、冷却立即失效 |

## 威胁模型与必须防护

1. 低熵姓名/生日/地点哈希可被枚举：禁止把哈希当匿名化。
2. 不同错误查询返回不同结果：统一空响应、时间和数量形状。
3. 通过候选数量推断隐藏家庭规模：固定上限，必要时批量/延迟响应。
4. 隐藏亲属或空间名泄露：token 兑换前不返回 identity、space、path、similarity。
5. 家庭暴力/跟踪：默认关闭、双边主动加入、单边撤出立即失效、冷却和封禁。
6. 未成年人暴露：默认完全排除未成年主体及其可推导线索。
7. token 重放/转交：单次 CAS、绑定双方 account、用途、过期时间和当前授权 epoch。
8. 撤权竞态：兑换和展示前重新检查 opt-in、consent、membership、bridge/status，旧 token 不得恢复访问。
9. provider/日志泄露：不把原始线索、模型 payload 或候选详情写日志；首版不需要模型和外部云。
10. 恶意批量探测：按 account 双侧限流、配额、冷却和审计；失败响应不能区分“无匹配”和“被拒绝”。

## 最小状态机草案

```text
OFF
 → opted_in (本人主动加入)
 → candidate_issued (双方都满足策略，单次 opaque token)
 → redeemed (双方再次确认，进入显式邀请/bridge 流程)
 → expired / withdrawn / blocked
```

`candidate_issued` 不产生关系、成员资格、bridge 或可读数据；任一方 withdraw/策略版本变化/过期后，CAS 使 token 不可兑换。

## 准入门槛

只有在用户价值研究证明主动发现明显优于双方主动输入、完成未成年人/跟踪风险评审、确定双方 opt-in 语义和最小披露字段、实现不可枚举的统一响应与 token CAS、并有隔离对抗测试后，才允许创建实施任务。否则保持当前 within-space 推荐和显式 bridge，不做全局 MatchBroker。
