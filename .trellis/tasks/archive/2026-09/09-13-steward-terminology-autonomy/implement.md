# Implement — 自动建议与模型优化

## 启动条件

- [ ] 最终方案评审完成；A 已通过并集成，记录其 commit 和呈现合同。
- [ ] 拉取最新平台/空间治理基线，与 memory/topology 交接共用文件及 migration 序号。
- [ ] task.py start 后只在本任务 worktree 编辑；注入 prd/design/implement 与真实 manifests。
- [ ] 明确实现子代理文件范围；不改他人 WIP，不在主检出编辑业务代码。

## 实施顺序

1. 固定合成案例与语义反例：外婆/姥姥、兄弟长幼未知、继养监护、合法长链、显式偏好及跨 viewer 隔离。
2. 新迁移、投影模型、viewer call 绑定、反馈字段与配置；隔离 upgrade head，核验旧行/约束。
3. 实现共用语义校验与 effective-term 选择；优先级、版本摘要、抑制和回退先验证。
4. 接 core 的确定性建议生产、viewer 去重、notify=false、本人历史用词；真实 job 测试。
5. 接 terminology prompt/schema、工作发现和各阶段分支、预算公平性、发送/写回 fence、崩溃恢复。
6. 后台刷新、PFV/档案/通知同源读取；验证输出不造成模型自激和重复建议。
7. 接本人保留/恢复操作、KinshipTermPanel 展示与缓存失效；保持可选操作、不发逐条待办。
8. 同步家庭/管理员配置入口和有效状态、健康/统计、旧客户端兼容。
9. Trellis check 独立核验；主线程抽查完整 diff、跨层测试和迁移冲突，提交并串行集成/归档/清理。

## 文件所有权

- 新投影 model/service/migration；models/steward.py、steward_suggestion.py、agent_provider.py、platform_features.py 和必要模型注册。
- services/steward.py、steward_assist.py、steward_guard.py、steward_suggestions.py、terms.py、personal_family_view.py、domain_events.py、platform_features.py；必要纯词素模块/intake_extractor 复用抽取。
- API/schema：steward_suggestions、kinship、platform_features、space_model_settings、agent、admin_agent、admin_steward 及受影响测试。
- frontend：kinship/suggestion 类型/API/store、KinshipTermPanel、SpaceModelSettingsPanel 与测试。
- system-admin-frontend：PlatformFeaturesView、相关 types/API/测试；不改 Provider 卡片重设计。
- 配置与 migration 和其他任务串行，精确文件依据启动时 baseline 再核对；不得编辑历史迁移。

## 验证

- [ ] 真实 job→batch→fake transport→产物→后续刷新→API 的三条来源，含确定性模型关闭情形。
- [ ] 输出反例/语义/权限/跨 viewer/跨空间；无效产物零词典与事实污染。
- [ ] 明确偏好优先、使用证据不伪造、恢复即时且跨 job 防重现，其他冷却键不变。
- [ ] 有界目标/低预算多 job 公平性、input/output 版本无循环、重复/恢复幂等、unknown 不重发。
- [ ] 发送前/中/写回时关开关、换 Provider、撤权、改事实/词条/反馈均拒绝过期结果。
- [ ] 环境/平台/空间/Provider 真值表、旧客户端缺字段保持、only-terminology 健康正确。
- [ ] 独立 DATABASE_URL 的 alembic upgrade head；backend ruff/format/mypy 与相关 pytest。
- [ ] frontend 和 system-admin-frontend lint/type-check、相关 Vitest、build；用户偏好和配置入口可达。

实施记录（2026-09-14，主会话直接实施）：B 已随父任务完成并集成（commit 9baf659，基于 A 的 ad10dd0）。
- 迁移 0044 / 投影与抑制表 / viewer 绑定 attempt / cursor 公平调度 / 语义校验 / 恢复抑制 / 三端配置入口按 design 落地。
- test_steward_terminology.py 六场景以真实 job + fake transport 覆盖生产链；精确命令与结果见父任务 research/validation.md。
- 单列：真实模型质量、线上开关未测（STEWARD_ASSIST_TERMINOLOGY 默认关）；「姥姥」别名依赖种子重灌，存量库保守拒绝。
