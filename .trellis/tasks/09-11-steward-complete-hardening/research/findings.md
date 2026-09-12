# Steward 问题与后续事项台账（2026-09-11）

基线 HEAD `7af9e1f`，存在用户已有未提交空间/前端改动。本轮只读代码并写任务材料；23 条是本次 Steward 范围内已识别事项，不是全仓漏洞扫描结论。P1/P2 表示修复优先级，不把潜在风险写成已发生攻击。

子代理探查因上游 503 均无结果；下列依据由主线程定点核查。历史 46+16 测试是 09-09 会话执行记录，本日未重跑。

### F01

- 类别：配置/运维前提；优先级：P2。
- 主责任务：[09-11-steward-production-ops](../../09-11-steward-production-ops/prd.md)。
- 原文定位：`docker-compose.yml:57-63；backend/app/config.py:132；scripts/dev-up.sh:50`。
- 现状：Compose 总开关/worker 默认 0，模型辅助另有平台+空间双开关；当前 Compose 未列出 STEWARD_ASSIST_* 透传。
- 影响/处理：部署可出现空间 UI 已打开辅助、进程仍未运行的状态；默认关闭本身不是安全漏洞，也未读取实际 .env 判断服务是否开启。

### F02

- 类别：已核实缺口；优先级：P1。
- 主责任务：[09-11-steward-production-ops](../../09-11-steward-production-ops/prd.md)。
- 原文定位：`backend/app/services/steward.py:595 reaper_pass；:685 execute_steward_job；backend/app/services/maintenance.py:43`。
- 现状：只有过期 leased/running 由 reaper 回队；普通执行异常直接 settle failed。
- 影响/处理：临时失败没有有限退避执行策略，需保留终态不复活和人工重跑关联。

### F03

- 类别：已核实缺口；优先级：P2。
- 主责任务：[09-11-steward-production-ops](../../09-11-steward-production-ops/prd.md)。
- 原文定位：`backend/app/models/steward.py:50；backend/app/services/steward.py:422 enqueue_steward_job；maintenance.py:43`。
- 现状：integrity_scan/admin_rerun 有原因枚举；backend/app 范围未找到周期生产扫描或正式重跑 API，维护循环只消费 queued。
- 影响/处理：空闲空间到期卡、停用期间遗漏事件、重启追补不能仅靠“有队列泵”保证。

### F04

- 类别：已核实缺陷；优先级：P1。
- 主责任务：[09-11-steward-assist-execution](../../09-11-steward-assist-execution/prd.md)。
- 原文定位：`backend/app/services/steward.py:639 run_steward_job；:754 run_assists hook；backend/app/services/steward_assist.py:59 _post_json / :571 begin_nested`。
- 现状：BEGIN IMMEDIATE 外层事务仍打开时进入同步 HTTP；SAVEPOINT 不释放数据库外层写锁。
- 影响/处理：慢模型延长全库写锁，其他空间/请求写入等待；规划用独立连接故障注入证明修复。

### F05

- 类别：已核实缺陷；优先级：P1。
- 主责任务：[09-11-steward-assist-execution](../../09-11-steward-assist-execution/prd.md)。
- 原文定位：`backend/app/services/steward_assist.py:111 _record_call；:150 _seq_done；backend/app/services/steward.py:639`。
- 现状：模型审计与 core 同事务提交；_seq_done 只能看到已落库行。
- 影响/处理：发送后崩溃可丢失本地调用记录，再次重试可能重复计费；历史 design 已承认权衡，但代码 docstring 宣称不重复花费。

### F06

- 类别：已核实缺陷；优先级：P1。
- 主责任务：[09-11-steward-assist-execution](../../09-11-steward-assist-execution/prd.md)。
- 原文定位：`backend/app/services/steward_assist.py:98 _budget_state；:164 _parse_response；:208 预算判断；:480 ranking degraded`。
- 现状：只累计 succeeded，失败/格式不合格调用不计数；没有出站前 token 预留，缺 total 可记 0。
- 影响/处理：配置的每 job 调用/token 上限不是严格上限；需要保守预留与最终结算。

### F07

- 类别：已核实产品缺口；优先级：P1。
- 主责任务：[09-11-steward-candidate-review](../../09-11-steward-candidate-review/prd.md)。
- 原文定位：`backend/app/models/steward.py:286 StewardLlmCandidate；backend/app/services/steward_assist.py:391-425`。
- 现状：kind 为任意非空截断字符串；digest 包含 rationale；只有 proposed/dismissed；backend/app 全部引用仅模型与生成器，没有审核 API。
- 影响/处理：缺证据版本/受众/确认命令；相同建议换措辞会绕过去重。旧候选不能直接公开。安全 schema 由 quality-security 配套。

### F08

- 类别：已核实输出约束缺陷；优先级：P1。
- 主责任务：[09-11-steward-quality-security](../../09-11-steward-quality-security/prd.md)。
- 原文定位：`backend/app/services/steward_assist.py:490 maybe_explain_cards；:538 reason_text_llm；frontend/src/components/actioncard/ActionCardItem.vue:178`。
- 现状：非空输出直接截断保存，前端优先展示；“只复述事实”目前主要靠 prompt。
- 影响/处理：可显示没有证据的关系/隐私承诺。Vue 插值会转义 HTML，不误报为 XSS；修复证据验证和确定性主文案。

### F09

- 类别：已核实失效缺口；优先级：P1。
- 主责任务：[09-11-steward-projection-consistency](../../09-11-steward-projection-consistency/prd.md)。
- 原文定位：`backend/app/services/domain_events.py:20 _invalidate_personal_family_view；backend/app/commands/spaces.py:231；backend/app/services/terms.py:342`。
- 现状：PFV 失效只认部分前缀，其中 space_member. 与实际 space.membership.changed 不一致；term/disclosure/global source 影响不完整。
- 影响/处理：即使排进 job，rebuild_space_views 只处理 queued/stale/failed/never_computed，current 可能继续陈旧。

### F10

- 类别：安全风险，需定向复现；优先级：P1。
- 主责任务：[09-11-steward-projection-consistency](../../09-11-steward-projection-consistency/prd.md)。
- 原文定位：`backend/app/services/personal_family_view.py:231 _view_payload_for_view；:280 两端过滤；:298 path_json 输出`。
- 现状：读取重查节点与图，但边仅按两端可见过滤，随后直接返回保存的 path/alternative_paths。
- 影响/处理：撤销中间证据且两端仍可见时可能泄漏失效路径；不是本轮已演示的线上数据泄漏。需全路径重验回归。

### F11

- 类别：已核实称谓接入缺口；优先级：P2。
- 主责任务：[09-11-steward-projection-consistency](../../09-11-steward-projection-consistency/prd.md)。
- 原文定位：`backend/app/services/personal_family_view.py:172；backend/app/services/terms.py:164 resolve_term / :738 resolve_term_or_structural`。
- 现状：PFV 的 term=resolution.explanation_structural，没有调用已存在的四级词典。
- 影响/处理：个人偏好、空间词和地区词不能仅靠词典模块存在就视为已在个人树生效。

### F12

- 类别：已核实版本/缓存缺口；优先级：P1。
- 主责任务：[09-11-steward-projection-consistency](../../09-11-steward-projection-consistency/prd.md)。
- 原文定位：`backend/app/services/personal_family_view.py:181-182 / :313；backend/app/api/personal_family_view.py:52-55`。
- 现状：policy_version 被赋为 purpose graph；ETag 用 view id/version/input_hash；304 在完整 payload 权限重验之前返回。
- 影响/处理：策略/披露/词典变化可能无法使 ETag 失效；需授权 epoch 与真实 policy version。

### F13

- 类别：已核实初始化/持久化缺口；优先级：P2。
- 主责任务：[09-11-steward-projection-consistency](../../09-11-steward-projection-consistency/prd.md)。
- 原文定位：`backend/app/services/personal_family_view.py:208 get_view / :333 rebuild_space_views；backend/app/api/personal_family_view.py:51；backend/app/commands/registration.py:1`。
- 现状：后台重建只遍历已有 view 行；首次读可创建/重算但该 GET 没有显式提交。自注册已直接创建 claimed，不属于 managed→claimed 事件。
- 影响/处理：不能以认领事件已接入证明所有新用户都后台初始化；需用独立 Session/API 回归确认持久化和首次合法访问路径。

### F14

- 类别：已存在能力，增量扩展；优先级：P2。
- 主责任务：[09-11-steward-candidate-review](../../09-11-steward-candidate-review/prd.md)。
- 原文定位：`backend/app/services/action_cards.py:305；backend/app/services/notifications.py:88 / :191；frontend/src/views/NotificationsView.vue`。
- 现状：ActionCard 已同事务生成站内通知，已有按收件人查询、read/read-all 与 UI。
- 影响/处理：前轮“需要新增通知系统”不准确；只新增候选/冲突引用及消费流程，不建第二套通知。

### F15

- 类别：已核实后续缺口；优先级：P2。
- 主责任务：[09-11-steward-candidate-review](../../09-11-steward-candidate-review/prd.md)。
- 原文定位：`backend/app/services/steward.py:1021 _emit_new_findings；backend/app/services/notifications.py:137 _project_item`。
- 现状：冲突/缺失主要写 steward 领域事件；通知只投影 card/member，缺有状态的人类巡检反馈。
- 影响/处理：补去重、已处理/忽略/证据失效与转资料流程；不让 Agent 自动合并身份或虚构父母。

### F16

- 类别：安全日志风险；优先级：P1。
- 主责任务：[09-11-steward-release-observability](../../09-11-steward-release-observability/prd.md)。
- 原文定位：`backend/app/services/steward.py:695 error message=str(exc)[:500]；maintenance.py:68 logger.exception；steward_assist.py:576`。
- 现状：异常原文进入错误对象/领域事件，堆栈可能带 SQL 参数；其他模型错误已仅记类名，不能说所有分支都泄漏。
- 影响/处理：用合成 token/姓名异常测试，改为安全错误码与关联 ID；本轮未读取真实敏感日志。

### F17

- 类别：执行合同缺口；优先级：P1。
- 主责任务：[09-11-steward-production-ops](../../09-11-steward-production-ops/prd.md)。
- 原文定位：`backend/app/services/steward.py:528 heartbeat_steward_job / :545 settle_steward_job / :639 run_steward_job`。
- 现状：主要检查状态，没有调用方 expected attempt/owner/deadline 的完整 fence；policy_version 随 job 保存但执行前未对现行版本重验。
- 影响/处理：多执行者、租约重领或策略更新后需拒绝旧结果；当前单进程降低出现概率，不代表合同完整。

### F18

- 类别：已核实出站政策缺口；优先级：P1。
- 主责任务：[09-11-steward-quality-security](../../09-11-steward-quality-security/prd.md)。
- 原文定位：`backend/app/services/steward.py:780 _space_visible_user_ids / :813 _confirmed_facts_brief；steward_assist.py:228 / :289；policy_guard.py:245`。
- 现状：prompt 直接取空间成员/ref/owner 及原始姓名；模型支路不调用已有最终 payload policy guard。
- 影响/处理：Provider 设置通过不等于字段可外发；需 consumer 最小化、敏感本地限制及注入/秘密检测。尚未构造真实外泄。

### F19

- 类别：已核实资源上限缺口；优先级：P2。
- 主责任务：[09-11-steward-assist-execution](../../09-11-steward-assist-execution/prd.md)。
- 原文定位：`backend/app/services/steward_assist.py:59 _post_json / :369 prompt JSON / :388 候选循环`。
- 现状：response.json 在完整响应上解析；候选数量与 prompt 字节未显式有界。
- 影响/处理：大响应/超大空间可放大内存、持锁与写入成本；限制字节/数量/deadline，输入语义校验归 quality-security。

### F20

- 类别：验证证据缺口；优先级：P2。
- 主责任务：[09-11-steward-quality-security](../../09-11-steward-quality-security/prd.md)。
- 原文定位：`backend/tests/test_steward_assist.py:144-481；.trellis/tasks/archive/2026-09/09-06-steward-model-assist/prd.md:R4`。
- 现状：现有 16 个 fake transport 单测覆盖接口、开关、失败、预算基本分支，不等于质量评测和真实 provider 业务 E2E。
- 影响/处理：补固定评测/对抗样例，并由 release 记录合成真实 provider smoke；不能复用 Assistant 成功证明 Steward 成功。

### F21

- 类别：已核实文档/交付口径偏差；优先级：P2。
- 主责任务：[09-11-steward-release-observability](../../09-11-steward-release-observability/prd.md)。
- 原文定位：`backend/app/services/steward.py:1-22；.trellis/tasks/archive/2026-09/09-06-steward-model-assist/design.md:54；本会话 09-09 回复`。
- 现状：模块头仍写不调用 LLM/无网络，但 hook 已加入；旧 docs 说“提交后”不符合代码；前轮混淆独立 Pi 与管家是否存在。
- 影响/处理：在新任务保留纠正记录，实施后更新现行 spec/docstring；不篡改归档历史或假造新测试结果。

### F22

- 类别：延期能力；优先级：P3。
- 主责任务：[09-11-steward-capability-followups](../../09-11-steward-capability-followups/prd.md)。
- 原文定位：`backend/app/services/steward_assist.py:490；backend/app/services/memory_rag.py:620；backend/app/services/terms.py:164`。
- 现状：辅助解释目前只覆盖卡片；shared RAG consumer 与词典基础存在，但可选知识辅助/个人路径模型解释/地区覆盖需进一步研究。
- 影响/处理：登记价值、权限、数据来源与评测门槛；不是本轮必须打开的功能。

### F23

- 类别：延期产品决策；优先级：P3。
- 主责任务：[09-11-steward-cross-space-discovery](../../09-11-steward-cross-space-discovery/prd.md)。
- 原文定位：`.trellis/tasks/archive/2026-09/09-01-new-user-family-recommendations/prd.md:Out of scope；backend/app/services/family_recommendations.py:49`。
- 现状：现有推荐只从当前有权 PFV 派生；跨空间陌生人/MatchBroker 被明确后置。
- 影响/处理：建立研究任务，不算 bug，更不暗中开启全局匹配。独立 Pi 人格属于明确不做，不混入本任务。

---

## 修复状态回填（2026-09-12，执行轮结束）

> 原始台账保持 09-11 只读记录不变；本节逐条追加闭环证据。除 F22/F23（延期研究，保持活动）外，
> F01–F21 均已实现并经回归/E2E 证据闭环。真实 provider E2E 全轮未运行——凡涉及模型行为的
> 证据均为受控 fake transport 的程序合同证明，发布门禁按 partial 口径记录。

| 事项 | 状态 | 证据锚点 |
|---|---|---|
| F01 | 已修复 | production-ops：status switches 全组合可解释（`test_admin_steward.py`）；compose/README 透传 STEWARD_* 全键；E2E `admin_status` 步骤 |
| F02 | 已修复 | production-ops：`classify_execution_error` + 退避 5/30s + max_attempts 3；锁冲突/确定性失败/耗尽回归（`test_steward.py`） |
| F03 | 已修复 | production-ops：`scan_due_spaces`（BEGIN IMMEDIATE，≤10 空间/tick）+ 追补 + admin rerun API；E2E `reenabled_backfill` 追补停机事件 |
| F04 | 已修复 | assist-execution：批次 HTTP 全部移出事务（steward_assist 批次执行器）；慢 transport + 第二连接写入侵噬（`test_steward_assist.py::test_slow_http_does_not_block_other_space_writes`） |
| F05 | 已修复 | assist-execution：attempt 行四崩溃点恢复（`recover_stuck_batches`）；E2E 注入超时产生 unknown 保守计费 |
| F06 | 已修复 | assist-execution：预留/计费含 reserved/in_flight/failed/degraded/unknown；预算=2 封顶回归；畸形输出消耗预算（degraded/invalid_output） |
| F07 | 已修复 | candidate-review：`StewardSuggestion` 闭合 kind/origin/status + evidence_hash 去重（不含措辞）+ 收件人表；旧裸 JSON 候选不外显；`test_steward_suggestions.py` |
| F08 | 已修复 | quality-security：闭合 schema 校验 + 证据围栏 + 确定性模板；`reason_text_llm` 仅 schema-v2 验证行外显，旧行回退模板并进重生成队列（`trusted_explanations`） |
| F09 | 已修复 | projection-consistency：`resolve_pfv_impact` 统一事件影响（真实事件名；全局人物事件按成员/ref/桥解析；memory/rag 不触发）；逐事件矩阵回归 |
| F10 | 已修复 | projection-consistency：`_path_evidence_valid` 主/替代路径逐步重验 + 中间人可见性；隐藏中间人撤证回归；`view_is_current` 图哈希兜底 |
| F11 | 已修复 | projection-consistency：`rebuild_view` 走 `resolve_term_or_structural` 四级词典 + 词典指纹入 input_hash；个人/空间词隔离回归 |
| F12 | 已修复 | projection-consistency：policy_version=真实 `config.POLICY_VERSION`；ETag 绑授权 epoch+事实/词典版本+policy；撤权/改称谓后旧 If-None-Match 不 304 回归 |
| F13 | 已修复 | projection-consistency：注册/首次合法访问/成员加入事务内初始化 queued 视图行；首次物化显式提交跨独立 Session 持久化回归；GET 只读 |
| F14 | 已修复 | candidate-review：Notification 增 suggestion_id + 受控 kind，唯一 (recipient,space,suggestion)，标题固定模板、状态实时投影；无重复通知回归 |
| F15 | 已修复 | candidate-review：identity_duplicate/missing_information v1 只 open_details/dismiss 转既有流程，不自动合并；owner 非端点仅提案不可确认 |
| F16 | 已修复 | release-observability：steward/assist/maintenance 异常日志只含关联 ID+类名+安全码；合成哨兵不外泄回归；`error_code` 列替代 str(exc)[:500] |
| F17 | 已修复 | production-ops：run/heartbeat/settle 全部校验 attempt+owner+deadline；旧 lease 结算被拒（STEWARD_LEASE_STALE）三形态回归 |
| F18 | 已修复 | quality-security：`steward_guard` 代号投影（不发原始姓名）+ `outbound_check` 复用 policy_guard 最终检查；出站捕获哨兵回归（token/手机号/注入/masked/私有 RAG 零外泄） |
| F19 | 已修复 | assist-execution：流式字节上界读响应、prompt 字节上限、单批数量、并发上限、墙钟 deadline；超限回归 |
| F20 | 已修复（stub 口径） | quality-security：`tests/fixtures/steward_eval/` 38 例（ST-5 矩阵 + 24 对抗）；硬安全门禁 27/27、召回 1.0；报告记录 fixture/prompt 版本，model_version=fake-transport；真实 provider 未运行（release-evidence.md 缺口清单） |
| F21 | 已修复 | steward.py 模块头已改为真实 crash 合同；本节与 specs 更新保留归档历史；notes 全程记录未取得的证据 |
| F22 | 延期（保持活动） | [09-11-steward-capability-followups](../../09-11-steward-capability-followups/prd.md) 独立推进 |
| F23 | 延期（保持活动） | [09-11-steward-cross-space-discovery](../../09-11-steward-cross-space-discovery/prd.md) 独立推进 |
