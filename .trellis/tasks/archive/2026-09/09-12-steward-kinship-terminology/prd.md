# PRD — Steward 个人亲属称谓计算与家族树投影闭环

> 父任务：[09-11-steward-complete-hardening](../09-11-steward-complete-hardening/prd.md)
> 依赖：已归档的 [`09-11-steward-projection-consistency`](../archive/2026-09/09-11-steward-projection-consistency/prd.md)；发布验证关联已归档的 [`09-11-steward-release-observability`](../archive/2026-09/09-11-steward-release-observability/release-evidence.md)，模型辅助验证关联已归档的 [`09-11-steward-assist-execution`](../archive/2026-09/09-11-steward-assist-execution/implement.md)。
> 状态：in_progress；本任务的称谓回归已通过，父任务整合与最终交接尚未完成。

## 问题

当前家族树截图把“儿媳”显示为“你的儿子的妻子”。这不是可接受的产品表达差异：Steward 的既定职责就是按**当前查看用户 + 当前家族空间**计算正式亲属称谓、应用个人/空间/地区偏好，并维护 PersonalFamilyView。现有代码虽已具备 `Dm-Sf → 儿媳` 词典和 `resolve_term_or_structural` 接口，但运行结果仍落到结构描述，说明称谓计算、投影重算、数据库词典、前端消费或真实调度链路至少有一处未闭环。

## 目标

让每个用户在每个 lineage space 中看到由 Steward/PersonalFamilyView 生成的、经过授权和版本控制的正式亲属称谓；结构路径仅作为证据或无词条时的安全 fallback，不得成为正常已知关系的主标签。

## 非目标

- 不让前端自行推断亲属关系或从结构文案翻译称谓。
- 不修改 SourceFact、原始关系文本或用 LLM 直接决定结构关系。
- 不引入第二套称谓词典、第二个视图数据源或绕过现有确认/授权流程。
- 不把真实 Provider 当作确定性称谓计算的依赖；Provider 仅验证 Steward 辅助链路隔离及自动调度不破坏核心结果。

## 需求与验收标准

### R1：个人化称谓合同

对于同一 `viewer_account_id + root_user_id + space_id + target_user_id`，后端必须依据 confirmed structural path 计算 `concept_code`，再按 `personal > space > locale > system` 解析称谓。个人称谓必须只影响该账号；空间/地区/系统词典按既有优先级生效；结构描述只在无合法词条时 fallback。

### R2：典型关系黄金用例

至少覆盖并验证：儿子、女儿、妻子/丈夫、儿媳、女婿、孙子/孙女，以及截图中的 `Dm-Sf`。接口、PFV edge、树节点/边标签和关系详情面板均必须显示正式称谓；关系详情仍可显示结构路径作为证据。

### R3：Steward 投影闭环

确认关系、成员/披露变化、称谓词典变化、个人偏好变化后，相关用户视图必须被正确标记并由 Steward 自动排队/重算；重算后 `term`、`term_source_level`、版本和 ETag 一致。不能依赖用户手动刷新或直接调用某个 service 才得到新称谓。

### R4：运行时故障定位与数据修复

补充可审计但不泄露个人内容的诊断：记录视图/边的安全原因码或计数，能够区分 concept code 缺失、词典未命中、旧投影未重算、视图状态非 current 等情况。提供隔离数据上的 seed/迁移/重建验证，不能修改用户生产数据库或写入密钥/隐私内容。

### R5：真实调度链路兼容

在现有 release-observability 的真实 API + 自动 tick 路径中验证：关系确认后核心 PFV 结果独立成功；若启用真实 Provider 或 openai-compatible stub，模型辅助失败、超时、unknown、重试或恢复不得覆盖核心称谓投影。真实 Provider 证据若环境未具备，必须明确记录 partial，不得用 fake transport 冒充。

### R6：跨层一致性与安全

后端 response schema、frontend decoder/types、FamilyTreeView canvas、RelationshipDetailPanel 保持一致；前端只消费后端 term。撤权、隐藏中间人、bridge 过期和旧 ETag 场景不得因称谓修复泄露节点、空间、路径长度或失效路径。

## 完成标准

- 有针对截图场景的后端回归：`Dm-Sf` 在默认词典中得到“儿媳”，并验证 PFV edge 持久化该值。
- 有针对个人词典/空间词典/无词条 fallback/版本失效的回归。
- 有真实 API + 自动 tick 的 PFV/称谓结果证据；Provider 证据单独分级。
- 定向及相关全量 backend/frontend 检查通过；未运行的高成本检查和环境阻塞写入 task notes。
- 完成后更新父任务 findings、现行 spec 与 release evidence；不得仅凭已有历史“已修复”记录关闭本任务。
