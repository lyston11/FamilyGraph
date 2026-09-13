# Implement — 个人称谓呈现与通知

## 启动条件和上下文

- [ ] 已评审总任务及本子任务最终规划。
- [ ] 重新核对 origin/main 推测层/平台治理已集成状态，和 topology 完成共享文件交接。
- [ ] task.py start 后进入 task.json 的独立 worktree；生命周期命令在主检出。
- [ ] 原生注入本 prd/design/implement、父审计和 implement/check manifests；子代理只拥有明确分配文件，不回退他人工作。

## 实施顺序

1. 先新增真实 derived API 与通知视角/状态缺口回归，用合成数据库定位失败。
2. 统一 TermSourceLevel/schema/前端来源解码，修复 derived 链路。
3. 建 viewer 绑定呈现服务，接通 Suggestion/PFV 推测/notifications，保留旧字段。
4. 统一有效状态/动作/证据表达，按 ID 新增详情读取；submit 保留关联结果。
5. 更新前端 API decoder/store/通知详情/推测面板；移除 raw fact_type 标签映射，切账号清缓存。
6. 核验同来源去重和确认路径已覆盖场景；不得自动确认未知事实。
7. Trellis check 独立核验；主线程检查最终 diff/合同、提交并串行集成，再允许 B 开始。

## 文件所有权

- backend：services/kinship_presentation.py、steward_suggestions.py、notifications.py、personal_family_view.py、terms.py；steward_inferred.py 的同源状态/提案关联及共享查找 helper；api/steward_suggestions.py；schemas/kinship.py、personal_family_view.py；对应测试。
- frontend：types/api.ts、types/kinship.ts；api/stewardSuggestions.ts 与必要 kinship/PFV decoder；stores/stewardSuggestions.ts；NotificationsView、SuggestionReviewDialog、InferredEdgePanel、称谓来源标签及对应测试。
- topology 共用 PFV/schema/types/FamilyTreeView，仅串行接入显示字段，不改其真实端点和几何。
- 不拥有模型 prompt/治理迁移/真实关系确认命令；这些边界不能由实现者自行扩大。

## 验证清单

- [ ] 真实 resolve 的 derived 输出 + 长链 API 回归。
- [ ] 父、子、旁系 viewer；个人词条；未知性别/长幼、继养监护和遮罩路径。
- [ ] 21+ 通知直接详情；列表/详情/计数/按钮状态一致；提交引用留存；切账号清空。
- [ ] 旧候选证据没有被计为事实证明；通知/推测同来源状态一致；无 GET 模型或事实写入。
- [ ] 两用户的个人忽略/全局驳回区分；两入口提交复用同一提案；跨空间不复用；全局恢复不复活本人通知。
- [ ] backend ruff/format/mypy；对应 pytest（test_steward_suggestions、test_notifications、test_steward_inferred 及仓库现有 kinship/PFV 测试）。
- [ ] frontend lint/type-check、对应 Vitest、build；桌面和窄屏人工查看。

实施记录（2026-09-14，主会话直接实施）：A 已随父任务完成并集成（commit ad10dd0）。
- derived/schema/详情/有效状态/呈现服务按 design 落地；精确命令与结果见父任务 research/validation.md。
- 交接 B 的合同：TermSourceLevel=personal/space/locale/system/structural/derived(/steward)；KinshipPresentation version=1；effective_state/display_actions/get_suggestion_detail；呈现入口 services/kinship_presentation.py。
