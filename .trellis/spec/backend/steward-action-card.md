# Steward 与 ActionCard 代码规范

> V2.4 实现合同。全局身份、可见性和领域命令边界以 [../architecture.md](../architecture.md) §0.1、§0.2、§0.6 为准。

## 1. Scope / Trigger

- 触发范围：`SourceFact/claim/membership/term/disclosure/domain event`、完整性扫描和管理员重跑都会登记 StewardJob；领域事件由 `services/domain_events.py:emit` 在同一 Session 内登记队列水位。
- StewardJob 的分区键是 `space_id`，执行上下文固定为 `space_id + job_id + policy_version`。worker 只读取当前空间的确认 SourceFact、有效 DerivedFact、SpaceProfileRef、成员、BehaviorProjection 和 checkpoint；不读取私人 Session/Memory/RAG，不访问其他空间内部事实。
- 浏览器 ActionCard API 的作用域也是 `space_id`。普通账号必须是目标空间 active 成员；`platform_operator` 不因平台角色获得家庭数据权限。
- 适用代码：`models/steward.py`、`services/steward.py`、`services/action_cards.py`、`services/recommendation_matrix.py`、`api/action_cards.py`、`migrations/versions/0013_steward_action_card.py`。

## 2. Signatures (command/API/DB)

### Domain/event and job commands

```python
emit(
    session: Session, *, event_type: str, aggregate_type: str,
    aggregate_id: int, payload: dict[str, Any] | None = None,
    space_id: int | None = None, actor_account_id: int | None = None,
) -> DomainEvent

schedule_steward_job_for_event(session: Session, event: DomainEvent) -> None
lease_steward_job(session: Session, *, worker_id: str, space_id: int | None = None) -> StewardJob | None
run_steward_job(session: Session, *, job_id: int, worker_id: str) -> StewardJob
expire_due_cards(session: Session, *, space_id: int, now: datetime | None = None) -> int
```

`expire_due_cards` 必须显式接收 `space_id`；空间 worker 不得调用全库扫描。

### Foundation commands used by execute

```python
create_shared_household(
    session: Session, ctx: ActorContext, *, other_user_id: int,
    name: str | None = None, commit: bool = True,
) -> tuple[FamilySpace, int]

request_lineage_membership(
    session: Session, ctx: ActorContext, *, target_space_id: int,
    target_user_id: int, commit: bool = True,
) -> tuple[SpaceMember, int]
```

ActionCard execute 只能在外层应用事务中以 `commit=False` 调用这两个命令，然后同事务写 executed 事件和卡片状态。

### HTTP API

- `GET /api/action-cards?space_id: int&state?: ActionCardState -> list[CardOut]`
- `POST /api/action-cards/{card_id}/view -> TransitionOut`
- `POST /api/action-cards/{card_id}/dismiss -> TransitionOut`
- `POST /api/action-cards/{card_id}/accept -> TransitionOut`
- `POST /api/action-cards/{card_id}/execute`，请求体为受限空对象 `ExecuteRequest`，返回 `ExecuteOut`。

`ActionCard` DB 字段必须包括 `kind`、`space_id`、`recipient_account_id`、`subject_user_id`、`object_user_id`、`evidence_json/hash/version`、`dedupe_key`、`proposed_action_json`、`reason_text`、`privacy_effect`、`state`、`revision`、`expires_at`、`executed_event_id`、`superseded_by_id` 和 `failed_reason`。证据快照只含 fact id/type/revision 与矩阵标量。

## 3. Contracts (request/response/env)

- kind 仅为 `household_link | lineage_request`；动作仅为 `create_household | request_lineage`。状态为 `pending | viewed | accepted | executed | dismissed | expired | superseded`。
- FSM：`pending --view/accept--> viewed/accepted`，`viewed --accept--> accepted`，`accepted --execute--> executed`；`pending/viewed --dismiss--> dismissed`；任一非终态可 `expire`/`supersede`，终态不可复活。每次转换 `revision + 1`，使用 compare-and-set。
- 去重键由 kind、subject、object（或 `-`）组成，数据库唯一索引再组合 `space_id + dedupe_key + evidence_version`；相同证据的活动卡不得重复创建，新证据或资格失效 supersede 旧活动卡。
- DomainEvent 是 append-only。`source_fact.*`、`claim.*`、`profile.*`、`space.membership.*`、`term.*`、`disclosure.*`、其他 domain event 会触发对应空间 job；`card.*`/`steward.*` 内部事件不再次入队，避免递归。全局事件只 fan-out job 水位，不将其他空间事实注入当前快照。
- BehaviorProjection key 只允许 `card_cooldown:*`、`correction_preference:*`、`term_usage:*`；dismiss 写入当前空间×收件账号的 `card_cooldown:<kind>`。checkpoint 只保存 cursor、policy/version、finding 签名和统计。
- 环境变量：`STEWARD_ENABLED`（默认 false）、`STEWARD_LEASE_TTL_SECONDS`、`STEWARD_MAX_ATTEMPTS`、`STEWARD_CARD_TTL_DAYS`、`STEWARD_COOLDOWN_DAYS`。关闭 flag 时 Steward/Card 入口返回 503，但不删除 DomainEvent 历史。
- execute 成功必须产生目的明确的 `card.executed`/领域命令事件并把卡置 executed；accept 只进入确认阶段，不写 SourceFact、不发送 membership request、不合并空间。

## 4. Validation & Error Matrix

| 条件 | 结果 |
|---|---|
| flag 关闭 | 503 `STEWARD_DISABLED` 或 `ACTION_CARD_FLAG_DISABLED` |
| 空间不存在、非 active 成员、跨空间 card id | 404 `CARD_NOT_FOUND` 或 403 `SPACE_FORBIDDEN_ACTOR`，不得泄露存在性 |
| operator 但无空间成员资格 | 与普通无权主体相同拒绝，不放宽授权 |
| 未 identity_confirmed、fact 非 confirmed、friend/colleague、端点不可见 | 不生成推荐卡；execute 以 `CARD_EXECUTE_REJECTED` 拒绝 |
| revision 不匹配/并发转换 | 409 `CARD_REVISION_CONFLICT` 或 `CARD_STATE_CONFLICT` |
| 活动卡 expires_at 已过、或终态再次操作 | 410 `CARD_EXPIRED` |
| execute 时 SourceFact revision、profile、membership、target space、VisibilityPolicy、披露或 cooldown 改变 | 409 `CARD_EXECUTE_REJECTED`，卡保持 accepted 可重试；不得静默写入 |
| request_lineage 目标不是指定 LineageSpace、目标端点不再 active、客户端改写 target_space_id | 409 `CARD_EXECUTE_REJECTED` |
| 不受支持的 kind/action/projection key/job cause | 422，对未知输入 fail-closed |

partner 只有双方确认且双方允许披露时可生成共同 Household 建议；spouse 可生成共同 Household 或指定 LineageSpace 申请；parent/child、sibling、guardian 依当前空间 kind 和创建选择判定。任何关系都不自动合并 LineageSpace 或暴露父母/兄弟姐妹。

## 5. Good/Base/Bad Cases

- Good：同一 `space_id`、同一 confirmed fact revision 的重复 dirty event 合并到一个活跃 job；worker 重试同一 cursor 不产生第二张活动卡。
- Good：用户先 accept，再在确认弹层看到服务端 target space/privacy effect；execute 重查后调用 Foundation command，创建 Household 时只创建一个 household，不改变双方 LineageSpace。
- Base：dismiss pending/viewed 卡，状态进入 dismissed 并建立同 kind cooldown；过期扫描只处理传入空间的 pending/viewed/accepted。
- Bad：把 `space_id` 从 execute body 当作权威目标、直接给 `SpaceMember` 写 active、在 Steward 中写 SourceFact，或把 platform_operator 当 break-glass 使用；这些都必须拒绝/禁止。
- Bad：把卡片 payload 的 masked 原值、Agent 摘要、停留时长或键鼠事件保存进 evidence/checkpoint/BehaviorProjection；不得这样扩展 schema。

## 6. Tests Required (with assertion points)

- 空间对抗：另一个空间、私人 Session/RAG、operator 身份均不能改变结果；断言 404/403 且无跨空间行被读取。
- Job 幂等：重复事件只有一个活跃 job；重复 cursor、crash/retry 不重复 DerivedFact、活动卡或 executed event；断言 checkpoint/cursor 和行数。
- FSM/并发：覆盖 pending 直接 accept、view/accept/dismiss、终态不可复活和两个并发 accept 一成功一 409；断言 revision/state。
- 推荐矩阵：未确认、proposed/disputed、friend/colleague、partner disclosure、spouse、parent/child、sibling、guardian 逐行断言 kind、action、reason 和 privacy effect。
- execute 负向：篡改 fact revision、撤 membership、target space、过期、cooldown、VisibilityPolicy/披露变化均拒绝；断言卡保持 accepted、SourceFact/member 行无静默变化。
- 领域事件/投影：term/disclosure/membership/card 事件产生正确 job 或不递归；dismiss 写入允许的 cooldown key，非法 projection key 被 422 拒绝。
- 空库迁移与 API：0013 可从空库重放；list/view/dismiss/accept/execute 的响应含 `revision`、错误统一 envelope；前端和后端 kind/action 枚举一致。

## 7. Wrong vs Correct

### Wrong

```python
# 客户端提供的 target_space_id 覆盖了卡片已确认的目标。
target_space_id = request.target_space_id
session.add(SpaceMember(space_id=target_space_id, status="active"))
```

### Correct

```python
# 从服务端卡片 payload 取目标，先重查证据/权限，再复用领域命令。
target_space_id = int(card.proposed_action_json["space_id"])
_revalidate_card(session, ctx, card, target_space_id=target_space_id)
request_lineage_membership(
    session, ctx, target_space_id=target_space_id,
    target_user_id=other_user_id, commit=False,
)
```

原因：ActionCard 是建议和确认状态，不是授权凭据；最终领域命令必须再次执行空间、事实、可见性和 FSM 校验，并在同一事务中落事件。

## 模型辅助层（09-06 引入；09-11 加固后现行合同）

- **三类辅助点**（候选/排序/解释）：core 事务提交确定性结果后，**同一短事务**登记 `StewardAssistBatch`（job 唯一）；HTTP 只发生在批次执行器内有界线程 + 独立 Session 中，**任何业务 DB 写事务内禁止网络调用**。有效开关 = 平台 `config.STEWARD_ASSIST_*` AND 空间 `agent_space_provider_settings.assist_*`（仅 steward 维度消费，默认全关=零调用零写入）。
- **attempt 审计**：`steward_model_calls` 为 attempt 行，唯一键 `(job_id, assist_kind, subject_key, input_hash, attempt_no)`；状态机 `reserved → in_flight → succeeded/failed/degraded/skipped/unknown`。prompt 明文永不落库（只存 sha256 摘要/长度/预算快照）；usage 缺失/负数/部分缺失按预留保守计费；unknown（读超时等无法证明上游未处理）保守计费且**不自动重发**。
- **预算**：发送前预留调用次数 + 输入/输出 token 上界 + 墙钟；failed/degraded/invalid-output 同样消耗预算；输出 cap=min(辅助上限, 剩余预算)。
- **写回栅栏**：发送前与应用前各一次短事务重验——空间设置、provider id/模型、policy_version、源事实 digest/revision、卡片状态/revision、候选受众、租约。任何变化 skip/supersede（安全原因码入审计）；禁用辅助后旧在途响应不得落文案。
- **上限**：`STEWARD_ASSIST_MAX_PROMPT_BYTES` / `MAX_RESPONSE_BYTES`（流式读，超界即断）、单批候选/卡片数、批次并发、`BATCH_LEASE_SECONDS` 租约、单次 HTTP timeout=min(配置, 剩余租约)。
- **红线（改代码前必读）**：候选只落 `steward_llm_candidates` 内部池 + `StewardSuggestion` 审核投影，绝不直接进卡片/任何正式写入；排序必须通过"严格排列"校验且按 recipient 分组；解释只输出结构化 `{reason_code, supporting_fact_ids, template_slots}` 由确定性模板渲染。prompt 输入只允许白名单结构化字段，绝不含 masked 值、高敏感类别或私人 Session/Memory。

## 生产调度与租约栅栏（09-11 production-ops；迁移 0036）

- `StewardSpaceSchedule(space_id PK, next_scan_at, last_scheduled_cursor, policy_version)`：每 tick 用 BEGIN IMMEDIATE 选最多 10 个到期空间（`STEWARD_SCAN_INTERVAL_SECONDS` 默认 300）；扫描经 canonical enqueue 合同登记作业（`integrity_scan` 的 succeeded 短路仅在扫描路径豁免），首次启用/重新启用/策略版本变化触发有界追补。
- `StewardJob` 增 `available_at`、`retry_of_job_id`（逻辑关联，终态不复活）、`error_code`（安全分类码）。可重试错误（DB 锁/暂时资源）按 `STEWARD_RETRY_BACKOFF_*_SECONDS`（5s/30s）退避回队，`STEWARD_MAX_ATTEMPTS`（默认 3）耗尽进 failed；确定性错误（输入/权限）直接终态，不拖累其他空间。

### Gotcha: PFV 版本漂移必须由 integrity_scan 发现

`rebuild_space_views` 的候选条件必须同时覆盖 `status` 非 current 与 `policy_version`/`computation_version` 不匹配；版本升级不会产生领域事件，GET 触发的补偿作业使用 `integrity_scan`，该原因不得被 succeeded 水位短路。重建成功必须回写两个版本字段，否则每个周期都会重复重建。
- **租约栅栏**：run/heartbeat/settle 必须传 `worker_id + expected_attempt` 并校验 lease owner + deadline；旧执行者结算被拒（`STEWARD_LEASE_STALE`）。lease 时固定执行水位 checkpoint；运行中更高水位只产生后继作业，结算不得宣告未处理水位完成。
- **admin 运维 API（仅 :8002，`app/api/admin_steward.py`）**：`GET /steward/status`、`GET /steward/jobs`（字段白名单，读不受引擎门禁）；`POST /steward/spaces/{id}/rerun`（受 `STEWARD_ENABLED` 门禁；Idempotency-Key 幂等；reason 只存分类码；60s 冷却 429、策略冲突 409、关闭 503、未知空间同形 404）。family token 一律 401；家庭 API 不挂任何后台路由。

## 建议审核闭环（09-11 candidate-review；迁移 0038）

- `StewardSuggestion`（origin= deterministic|model；kind 仅 relation_proposal|term_preference|identity_duplicate|missing_information；evidence_json 只存允许 ID/revision；evidence_hash 去重键 = space/kind/有向端点/结构化值/证据哈希，**不含模型措辞**）+ `StewardSuggestionRecipient`（per-recipient dismissed/read/cooldown）。
- 关系建议 submit → 202 `{suggestion, linked_proposal, pending_confirmations}`，只生成 proposed SourceFact（provenance=agent_proposal）；确认走 `commands/relationship_proposals.py`，确认人 = 端点本人 ∪ 合法代管，**空间 owner 非端点永远不能代确认**；路由禁止直接调 `transition_source_fact`。term_preference 仅本人提交并调既有个人词命令。identity_duplicate/missing_information v1 无 submit。
- 通知复用现有 Notification（suggestion_id FK，唯一 recipient×space×suggestion，固定模板标题、状态实时投影）；未验证 rationale 绝不进通知/列表。

## 可观测性与脱敏（09-11 release-observability）

- `GET /admin-api/v1/steward/status` 输出 metrics（core/assist 队列、失败、预算、pfv_stale、卡片计数——全部真实 DB 行）+ alerts（`queue_backlog` 阈值 `STEWARD_ALERT_QUEUE_SECONDS`（0=自动 max(2×扫描间隔,60s)）、`queue_stalled`）；worker 停而 HTTP 存活 → degraded；config 关闭是 disabled/paused，不算故障。
- **日志红线**：异常只记关联 ID + 异常类名 + 安全错误码；`str(exc)` 原文、SQL 绑定参数、模型 payload、姓名/PIN/token/key 绝不进日志/审计/响应。新增回归用合成哨兵断言零外泄。
- 验证脚本（临时 DATA_DIR）：`scripts/steward_e2e.py`（端到端）、`steward_capacity.py`（容量采样）、`steward_migrate_roundtrip.py`（迁移往返）；证据 JSON 已 gitignore。

## 安全链与评测（09-11 quality-security；services/steward_guard.py）

- 链路：代号投影（不发原始姓名）→ context/input policy → provider 设置/revision → `before_provider_request` 最终 payload 检查（复用 policy_guard，不经 ProviderProxy、不伪造 AgentRun）→ 有界 transport → 闭合 schema 校验（证据 ID 围栏、允许 kind、严格排列、受众范围）→ 写回栅栏 → 呈现/审核投影。
- 读侧信任门：`reason_text_llm` 仅 schema_version=2 验证行外显；旧纯文本行视为 untrusted，读取回退模板并进后台重生成队列。
- 评测：`tests/fixtures/steward_eval/` 版本化 fixtures（ST-5 矩阵 + 对抗例）；硬安全门禁（授权/事实约束用例）100% 通过才可开新策略的模型开关，候选召回阈值独立统计（≥0.9）；fake transport 证据只证明程序合同，真实 provider 质量分数不得伪造。

## 9. 09-13 短事务重算与 generation 合同（现行实现）

> 追加章节（2026-09-14，任务 09-13-steward-snapshot-progressive-recompute）。与上文 V2.4「单立即事务」描述冲突处，以本节为准。

### 9.1 执行器事务模型（services/steward.py）

- `run_steward_job` 不再把整族计算包进单个 `BEGIN IMMEDIATE`。阶段：开始栅栏（短事务置 running）→ 写锁外 CPU 计算 → 有界短写事务分批落库 → 发布事务（fresh-read 完整租约栅栏 + settle + generation 置 published + 消费水位 + 完成事件）。
- 派生缓存重算用 `derived_facts.compute_pair`（纯读取解析）+ `apply_pair_result`（写入）；每 `STEWARD_DERIVED_COMMIT_CHUNK`（默认 50）对一个立即事务。写锁上界 ≈ 单块 upsert，而非整族计算。
- 计算期间由 `_LeaseHeartbeat` 独立线程按 TTL/3 短事务续租（自有 SessionLocal；失去租约置 lost，发布仍会被 fresh-read 栅栏拒绝）。
- PFV 重建 `rebuild_space_views(per_view_commit=True)`：每视图独立提交；单视图失败回滚自身并标记 failed。

### 9.2 发布代次与进度（services/steward_generations.py + 迁移 0044）

- `steward_generations`：空间发布代次（running/published/failed/superseded）+ 执行游标 + 空间输入指纹。开始时残留 running 代次原子置 superseded；执行失败收敛 failed。
- `steward_generation_views`：per-viewer 真实进度（ready/failed + completed/total）。渐进 API 的 progress 块优先读这里；无代次行回退 PFV 行计数。running 代次超过 2×lease TTL 无更新按 retrying 处理。
- `steward_retry_budgets`：按 (space, fingerprint, scope) 跨代持久的必需阶段重试预算——换 job/重启/扫描不清零；输入变化（新指纹）即新预算；失败达 `STEWARD_STAGE_MAX_ATTEMPTS` 整代失败（不发布、不推进水位）。
- 无变化短路（R5）：`unchanged_since_published`（指纹一致且该代次无 failed/pending 视图）→ 跳过派生与视图重建，但到期检查/出卡/辅助登记照常执行。有失败视图的代次不算完整发布。

### 9.3 渐进读取与 demand（api/personal_family_view.py）

- `GET /api/personal-family-view?progressive=true` 附加 `progress`（contract pfv-progress-v1：phase/generation/revision/completed/total/next_poll_ms）；缺省响应不含该字段（`response_model_exclude_unset`）。304 仅限 current（与旧合同一致），200/304 均签发 `X-PFV-Display-Until`（epoch 秒，`PERSONAL_FAMILY_VIEW_DISPLAY_TTL_SECONDS` 默认 300）。
- `POST /api/personal-family-view/demand`：认证身份即 viewer（`get_current_view` 复核成员资格，404 fail-closed）；`focus_user_id` 必须在当前授权骨架可见集内（422 拒绝越权/隐藏目标）；同空间活跃作业存在即合并（already_active），不推进输入版本。
- 推测层输出前独立重验：`effective_enabled` 关闭即整层不输出；`viewer_path` 逐步重验，失效剥离 viewer_term/viewer_path。
- 管理端 `/admin-api/steward/status` 新增 `delivery_backlog`（辅助批次 reserved/in_flight/failed 计数）与 `latest_generation`（最近代次状态 + 视图计数），区分「核心发布完成」与「交付积压」。

### 9.4 基准脚本

`scripts/benchmark-steward-recompute.py`：隔离 DATA_DIR 临时库，稀疏二叉谱系（30/50/200 人），输出冷/热重算墙钟与冷算期间独立连接并发写延迟分位数（AC1 证据；50 人 p99≈20ms）。max 偶发 >500ms 尖峰（macOS WAL checkpoint/fsync 疑似），整改方向为视图行分块应用与 checkpoint 调优。
