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
- **逐笔结算（09-17 B）**：每笔请求返回后**立即**在其执行身份仍有效时结算（状态/usage/latency/合法产物）并提交，然后才发下一笔；已落库终态不被后续请求失败改写为 `in_flight`/`unknown`。批次执行权在发送、逐笔结算、最终应用三处各自重验；失租/接管后旧执行者零结算（行留 `in_flight` 交恢复器）。不可确认窗口（收到响应后、提交前进程退出）仍是 unknown，不承诺“任何结果都必保全”。
- **混合批次部分成功可消费（09-17 B）**：同批存在 `unknown`/`failed` 不阻止**独立且仍通过写回栅栏**的成功产物被应用（恢复器同样如此：已有持久产物优先于 has_unknown 早退）。批次终态仍如实为 `failed`（`network_unknown`/`transport_failed`）并保留部分应用事实，绝不伪装完全成功；共同栅栏失效仍整批 `superseded`。合法空 `items` 与 terminology degraded 仍沿 `_mark_terminology_checked` 记已检查，不因“items 非空”才当已检查。
- **总截止（09-17 B，09-17 C 加固）**：单笔 HTTP 的 `timeout` 是**整笔墙钟上界**（含连接/发送/headers/body/解析）。B 的读取循环检查不足以覆盖“等待响应头/等待下一块数据”的阻塞窗口（本地 socket 实测 400ms 预算约 638ms 才返回），C 起改为 `httpx.AsyncClient` + `asyncio.timeout(timeout)`，在等待期真正取消在途 I/O；`_post_json` 保留同步入口，在工作线程内 `asyncio.run` 桥接，检测到事件循环即 fail-closed。超时统一抛 `httpx.ReadTimeout`（按 unknown 保守计费），**不复用** `connect_failed`：请求交给 transport 之后无法证明上游未处理，只有 httpx 自身的 `ConnectError/ConnectTimeout` 才是确定未发送。不断收到小块数据不得延长总时长；JSON 解析前后再核对同一截止，不接受超预算的“成功”。
- **结算预留与出事务再核对（09-17 C）**：单笔预算 = `min(STEWARD_ASSIST_TIMEOUT_SECONDS, 剩余租约 - _SETTLEMENT_RESERVE_SECONDS)`，且不得低于 `_MIN_SEND_WINDOW_SECONDS`；租约剩余不足以覆盖结算预留时**根本不发**。事务提交到实际发送之间还要再核对一次（BEGIN IMMEDIATE 锁等待与提交本身消耗墙钟），窗口不足则本笔从未发出，由 `_release_unsent` 在本人份仍有效时回退为 `skipped`（`insufficient_budget`，零计费、非 unknown）；身份已失效则留给恢复器。批次收尾用 `_release_remaining_unsent` 一次性释放仍未发送的预留——`skipped` 不进入 `_BUDGETED_STATUSES`，因此不消耗调用/token 预算。
- **预算**：发送前预留调用次数 + 输入/输出 token 上界 + 墙钟；failed/degraded/invalid-output 同样消耗预算；输出 cap=min(辅助上限, 剩余预算)。
- **写回栅栏**：发送前与应用前各一次短事务重验——空间设置、provider id/模型、policy_version、源事实 digest/revision、卡片状态/revision、候选受众、租约。任何变化 skip/supersede（安全原因码入审计）；禁用辅助后旧在途响应不得落文案。
- **上限**：`STEWARD_ASSIST_MAX_PROMPT_BYTES` / `MAX_RESPONSE_BYTES`（流式读，超界即断）、单批候选/卡片数、批次并发、`BATCH_LEASE_SECONDS` 租约、单次 HTTP timeout=min(配置, 剩余租约)。
- **红线（改代码前必读）**：候选先落 `steward_llm_candidates`；legacy/unsupported 沿原审核投影，versioned 及其 sibling 反向候选只作内部证据核验，不新增建议、收件人、通知或推测边，详见 [候选相关证据版本](steward-candidate-evidence.md)。候选绝不直接进卡片/任何正式写入；排序必须通过"严格排列"校验且按 recipient 分组；解释只输出结构化 `{reason_code, supporting_fact_ids, template_slots}` 由确定性模板渲染。prompt 输入只允许白名单结构化字段，绝不含 masked 值、高敏感类别或私人 Session/Memory。

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
- `term_preference` 的 TTL 与通知是**刻意关闭**的，不要"补上"（2026-09-15 实测 535 条全 `expires_at=NULL`、全 `proposed`，一度被误判为遗漏）：
  - `steward_terminology.py` 在 `upsert_suggestion` 之后显式 `suggestion.expires_at = None`，注释写明可选偏好由**证据变化与显式反馈**退役，而非定时器；普通 kind 仍走 `default_expires_at`。
  - 该 kind 以 `notify=False` 写入（可选偏好，不产生逐条待办），因此**不存在刷屏**；`term_preference` 的通知打开会一次性产生每人 N 条通知，禁止。
  - **不要给它加 TTL**：`steward_terminology.py` 的历史去重查询不带 `status` 过滤，`expired` 行会**永久挡住重建**（无恢复路径）；而同一"挡住"对 `resolved` 是刻意保护（"恢复过/保留过的建议不重生"，见该处注释）。两者无法用同一条件区分，属需单独设计的实质行为变更。
- 建议列表是"可回看"语义：`superseded`/`expired` 行仍可被 `open_details` 打开。需要"当前待处理"视图的调用方必须自己按 `SUGGESTION_ACTIVE_STATES`（`app/models/steward_suggestion.py`，= `("proposed", "submitted")`）过滤，不要改 `list_suggestions_page` 的返回集合。
- `term_preference` 的可读值在 `value_json["term"]`；`presentation.summary` 对它是**通用文案**（"可选的称谓偏好建议，无需处理"），不是叫法。读取方（如前端行内展示）必须取 `value.term`。
- **09-16 auto-apply**：称谓改善不再走"建议→用户逐条确认"。确定性 baseline 改善直接进显示；用户 preferred usage 与模型改善写入当前 viewer 的 `projection.term`（分别 `origin=personal`/`model`），无需建议提交或通知批准。无实际改善（与 baseline 同值）不再生成建议；存量纯 baseline 建议经 `effective_state` 显示 `superseded` 而退出活跃消费与提交，保留历史与用户反馈，不加 TTL、不伪造 `resolved`。`term_preference` 不进入"待处理/待核实"分区及待办计数（`notifications.py` 把其 `pending` 投影为 `done`），可选"固定/恢复"入口只在人物称谓区。
- **模型候选词表与显示同口径**：`steward_terminology_snapshot.allowed_terms` 对原码接受 `system/locale/space` 词条，但**别名码只接受 `system/locale`**——与显示路径 `terms._registry_alias_term` 一致。否则别的原码上的空间自定义词会经模型写回被应用到本路径（模型绕过显示层的层级约束）。

## 可观测性与脱敏（09-11 release-observability）

- `GET /admin-api/v1/steward/status` 输出 metrics（core/assist 队列、失败、预算、pfv_stale、卡片计数——全部真实 DB 行）+ alerts（`queue_backlog` 阈值 `STEWARD_ALERT_QUEUE_SECONDS`（0=自动 max(2×扫描间隔,60s)）、`queue_stalled`）；worker 停而 HTTP 存活 → degraded；config 关闭是 disabled/paused，不算故障。
- **日志红线**：异常只记关联 ID + 异常类名 + 安全错误码；`str(exc)` 原文、SQL 绑定参数、模型 payload、姓名/PIN/token/key 绝不进日志/审计/响应。新增回归用合成哨兵断言零外泄。
- 验证脚本（临时 DATA_DIR）：`scripts/steward_e2e.py`（端到端）、`steward_capacity.py`（容量采样）、`steward_migrate_roundtrip.py`（迁移往返）；证据 JSON 已 gitignore。

### Gotcha: `steward_generations` 行数下降是 GC 收敛，不是循环退化

**Symptom**：巡检时发现 `steward_generations` 总数在减少（实测 53→49→40），容易误判为扫描停摆。

**Cause**：`steward_gc._collectible_generations()` 按设计回收「已被同空间更新代取代、manifest 已封存、无 publication 引用、无 view 引用」的旧代（见第 12 条）。稳态是每空间保留 current + 最新预览。

**Correct 判据**（不要用总数）：

```sql
select min(id), max(id), count(*) from steward_generations;   -- id 有空洞 = 确有删除
select max(published_at) from steward_generations;            -- 必须随扫描节奏前移
select status, count(*) from steward_jobs group by status;    -- 不应出现非 succeeded
```

实测样本：`min=1, max=680, count=40`（回收 640 行）+ 20 空间×2 条稳态 + `max(published_at)` 每 30 分钟前移 + 2310 jobs 全 succeeded。

**Prevention**：判定管家循环健康看 `max(published_at)` 前移与 job 状态分布，不看 generation 总行数。

## 安全链与评测（09-11 quality-security；services/steward_guard.py）

- 链路：代号投影（不发原始姓名）→ context/input policy → provider 设置/revision → `before_provider_request` 最终 payload 检查（复用 policy_guard，不经 ProviderProxy、不伪造 AgentRun）→ 有界 transport → 闭合 schema 校验（证据 ID 围栏、允许 kind、严格排列、受众范围）→ 写回栅栏 → 呈现/审核投影。
- 读侧信任门：`reason_text_llm` 仅 schema_version=2 验证行外显；旧纯文本行视为 untrusted，读取回退模板并进后台重生成队列。
- 评测：`tests/fixtures/steward_eval/` 版本化 fixtures（ST-5 矩阵 + 对抗例）；硬安全门禁（授权/事实约束用例）100% 通过才可开新策略的模型开关，候选召回阈值独立统计（≥0.9）；fake transport 证据只证明程序合同，真实 provider 质量分数不得伪造。

## 9. 一致快照、版本化预览和原子发布（0044 / 0045 / 0048）

本节取代旧的单写事务和逐 viewer 写 live PFV 的执行描述。适用任务：`09-13-steward-snapshot-progressive-recompute`。

### 9.1 Scope / Trigger

任何 Steward 扫描、按需重算、目标优先级、重试、交付、渐进读取及缓存失效改动都遵守此合同。普通查询可按 pair 计算；全空间未消费的 DerivedFact 矩阵预热不是核心发布前提。已同意 bridge 范围内的输入按查看者授权读取，不扩大空间或私人 Session/Memory/RAG 可见性。

### 9.2 Signatures

```python
# 所有参数中的 Session/ORM 均留在当前进程；返回 ViewerInput 是冻结 DTO。
steward_snapshot.read_transaction(bind)
steward_snapshot.read_viewer(bind, *, space_id, account_id, expected_versions)
steward_pipeline.binding_for(job, *, worker_id=None, expected_attempt=None)
steward_pipeline.publish(db, binding, *, summary, upper)
steward_runtime.launch_due(*, space_id=None, limit=None)
steward_runtime.shutdown_runtime(*, timeout_seconds=10.0) -> bool
```

- `steward_input_revisions`：global scope 0 + space 的 structural/presentation/inferred 版本；源表触发器同事务推进，scope tombstone 不随删除重建归零。
- `steward_generations`：固定 execution_cursor、输入版本/config 摘要、owner/attempt、valid_until、必需视图/交付计划状态；`steward_generation_views` 保存同代授权骨架、真实进度和可复用 result_view_id。
- `steward_view_targets`：view×target 唯一，完整 path/term/解释结果；pending、ready、unavailable、failed 互斥。`steward_publications` 是完整结果的空间指针。
- `steward_view_demands`：account×space 唯一，revision/fulfilled_revision 与 focus；不依赖新 DomainEvent 才存活。
- `steward_delivery_intents`：generation×business key 唯一；published 为激活门。`effect_fingerprint` 绑定效果、相关输入和有效期，同一效果的失败预算跨代保留；GC 不删除预算。`steward_finding_deliveries` 记录真正已提交的 finding occurrence，计划清单不能充当回执。
- `steward_inferred_overlays`：独立输入/时间有效期的可选推测层，不进入 confirmed 进度分母。
- GET `/api/personal-family-view?space_id=...&progressive=true`；POST `/api/personal-family-view/demand` body `{space_id, focus_user_id?, retry?}`，viewer 始终来自认证身份。
- GET `/admin-api/v1/steward/deliveries` 仅返回分页交付元数据；POST `/admin-api/v1/steward/spaces/{space_id}/deliveries/{intent_id}/retry` 使用 `{reason, expected_policy_version, expected_attempt}`，不接受家庭主体。

### 9.3 Contracts

1. 显式 `BEGIN` 在 pysqlite legacy 模式建立一致读快照；仅 DTO/标量离开读事务。退出时 rollback 会 expire ORM，不能把需求 ORM 行留到事务外排序。
2. 先比对版本/cache，再构图、搜索；每 viewer 授权图复用。仅展示变化复用已发布结构；算法版本同时进入结构缓存与失败预算，不能只改显示版本。
3. 搜索由一个 spawn CPU worker 执行可续算 slice，maintenance 只派发；单个空间只一个活跃 job，跨空间共享有界容量。默认 `STEWARD_MAX_CONCURRENT_JOBS=4`、slice 2048 次展开、单目标 2,000,000 次展开/4MiB 状态、待算快照 64MiB；超预算明确失败，不能返回 no_path。当前查看者优先，普通视图仍有轮转份额。
4. save/heartbeat/failure/publish 每次用新 Session 校验 job、active 状态、owner、attempt、未到期租约、generation、输入/config 和 valid_until。过期 owner 不得续活；supersede/reaper 使用匹配旧绑定的失效条件，不要求旧输入重新有效。
5. 一个完整 target 的结果、状态、revision 和进度同短事务写入，重复回执不重复计数。`save_target` 的结果/边 JSON 编码和字节预算检查必须在写事务前完成，写入复用编码结果（显式 Text bind，读回仍是 JSON 对象/null）；锁内只取目标状态和视图进度列，不能为每个 target 解码整棵骨架或旧结果。目标 pending→终态、预算退款和进度推进同事务且仅执行一次。预览不推进消费水位、不写正式通知/动作、不调用模型。
6. 全部必需工作就绪后，短事务切 publication、置 job succeeded/generation published、提交固定 execution_cursor 和已捕获 demand.fulfilled_revision、写完成事件并保存同水位后继需求。任一异常整体回滚。后继只包含未满足且仍有成员资格、人物未删除的需求；失权需求终止，重新获得资格后的新请求递增 revision。最终事务不搜索、不复制全部结果、不清理历史、不调用网络。
7. 普通 DerivedFact/PFV 读者只使用有效已发布结果；`progressive=true` 可读经当前权限和版本校验的同代预览。推荐、空间统计、household 元信息、称谓呈现统一用 `current_view_payload`，不再根据旧 live 表状态阻挡新发布，也不在首次 GET 同步重算。该 helper 复用调用方的真实事务，否则创建独立显式读快照，不提交或回滚调用方。称谓来源在内部载荷保留、公开 schema 排除。成员/披露/词条/bridge/config/时间失效优先于 ETag，空态仍保留版本水位，不能让迟到数据复活。
8. `progress` 为 `pfv-progress-v1`：generation/revision、topology_revision、phase、completed_count/total_count、targets、reason_code、next_poll_ms；公开分母仅为本人授权目标。本人 ready 可早于空间 publication；required target 耗尽则整代 failed，水位不动，预算不因扫描/新代/重启清零。
9. 200/304 同请求签发 `ETag`、`X-PFV-Validated-At` 与 `X-PFV-Display-Until`（Unix 秒），CORS expose 三者。后者取短展示 TTL（默认300秒）和语义 valid_until 较早者。HTTP Date 交给服务器；重复应用 Date 会被 Uvicorn 合并为不可解析值。部分预览可304，但每次仍重验授权/版本。
10. 发布后每个本地交付效果与 intent done 同事务；用户已作出的动作/revision 优先。普通交付执行前持久预留 effect 预算和 owner/attempt/deadline 租约；成功只退还本次预留，迟到结果不能退款或改写新状态。同输入的新代、扫描、重启和 GC 不重置失败次数。管理员单项重试以 policy/attempt CAS 合并重复请求，冷却后仅授予一次机会并同事务审计，不创建 core job。仅真实回执去重 finding；通知按最多 8 名收件人一批独立补发，不因已有 finding 回执漏发。旧 assist orphan 恢复不处理 staged job。模型 reserved/in_flight/unknown 继续使用既有持久预算，unknown 不自动重发，剩余租约不足最小发送窗口不再发 HTTP。
11. 可选推测使用独立 intent lease/跨代预算；输出前重验开关、版本、每一步 viewer_path 证据；失败不回滚 confirmed 核心。
12. GC 在读快照中发现候选，短写事务只按最多 64 个候选 ID 重验全部根后分批删除旧 target/intent/view；保留 current publication、最新预览、共享 result_view_id 来源与未交付 published 效果。同 effect 后继只有 published 才能接替旧交付责任；输入已失效时无需等待后继发布即可终止旧意图。候选 SQL 必须覆盖当前 source revision、snapshot/config 和有效期，不能仅在最终重验判断失效，否则 failed 意图会永远漏选。引用查询使用显式索引，不能在写事务内全历史聚合或依赖临时反向索引。shutdown 返回 false 时，临时库/数据目录必须保留，不能在协调器仍会写入时删除。
13. 短写事务还须限制连续写入：同一进程/Engine 的 `write_transaction` 共用 FIFO 预算，累计 writer 步骤 50ms 后，在下一次 BEGIN 前统一让出 100ms；其他协调器不能填掉空档。writer 步骤包含取锁、commit/rollback 和 Session 清理，预算不宣称单笔事务可被抢占或限时。自然读/计算空档可抵扣等待；排队时不创建 Session，异常清理票据，同线程嵌套立即拒绝。心跳等时间栅栏在实际入事务后重取时间，不得用排队前时刻续活过期租约。SQLite busy_timeout 仍为 5000ms；当前 `app.serve` 的多 listener 共用进程和 Engine，这不是跨进程写入调度器。

### 9.4 Validation & Error Matrix

| 情况 | 必须行为 |
| --- | --- |
| 输入/config/时间变化 | 拒收旧结果；supersede 并保留后继 demand，不推进旧水位 |
| owner/attempt 已换或租约已过期 | 旧 heartbeat/save/failure/publish 全拒绝，不改新作业 |
| required target 失败/超预算 | failed 终态或有限重试；未完成不伪装 unavailable |
| 单项交付/可选推测失败 | 独立失败和退避，不撤销已发布 core；其余项继续 |
| 管理员单项交付重试 | 同 attempt 重放合并；旧 attempt/已失效或被接替的意图 409，冷却内再次授权 429；响应不包含 payload、业务键或家庭证据 |
| 非成员/非法 focus | 安全404 / 422；不接受客户端 viewer 或任意优先级 |
| 骨架暂无结果/worker 暂停 | 明确 queued/preparing/失败原因与有限轮询，不能无限转圈 |
| 时间头/协议非法或 display deadline 到期 | 客户端拒绝或隐藏内容；匹配304也不能自行延长 |
| downgrade 存在 running/failed preview、未满足需求、pending/failed交付 | 拒绝破坏性回退，保留事实与待办 |

### 9.5 Good / Base / Bad Cases

- Good：30人首次请求先拿到授权骨架，29个本人目标逐个变 ready；本人的加载结束后其他账号仍可在后台计算。
- Base：无变化扫描零路径搜索，仍准备并恢复到期检查/交付；词条变化只重做呈现，结构路径缓存继续命中。
- Bad：逐 viewer 直接覆盖 live 表后再标 generation published；旧代迟到失败伤及新 owner；把 previous planned signatures 当已发送；仅测树外单账号或显式 BEGIN 就宣告无长写锁。

### 9.6 Tests Required

- `test_steward_snapshot_fences.py`：独立连接快照、一致输入、过期/旧 attempt、重复结果、发布 flush 后回滚 pointer/job/cursor/event/交付激活。
- `test_steward_input_versions.py`：无事件生产者、跨 bridge 与纯到期、inferred 分层、no-op/登录计数/发布不自失效。
- `test_relationship_snapshot_compute.py`：老 DFS 首128/排序/partner/depth 等价、pickle续算、资源上限、前置缓存与仅展示刷新。
- `test_steward_staged_pipeline.py`：骨架先出、同水位 demand、真实 receipt、assist 门控、预算耗尽、可选多跳解释、共享结果GC、停止/200/304时间头；found/no_path 保存从实际 BEGIN 到 commit 不编解码大 JSON，重复保存只推进/退款一次，读回为对象/null；相关旧Steward/assist/API回归继续通过。
- `test_steward_publication_consumers.py`：普通消费者同源读取、未发布/失效安全空态、无同步重算和 GET 零写；相关 legacy 读取兼容测试继续通过。
- `test_steward_demand_coalescing.py`：另一连接真实持写锁时已覆盖 demand 仍可只读完成；新 revision/更高事件水位、focus/retry、失效输入或租约保留 writer 与唤醒，撤权先于缓存；展示读取不解码内部搜索缓存且继续拒绝被篡改的路径证据。
- `test_steward_write_budget.py`：两个协调器排队期间无 Session/事务占用，真实独立 WAL 连接可先提交；FIFO、回滚释放、嵌套拒绝、不同 Engine 隔离、自然空档抵扣，以及排队跨过 lease/generation 有效期时拒绝心跳。
- `test_steward_runtime_recovery.py`：真实单 spawn CPU 切片让出后其他空间完成；已保存目标与半算目标中断后经 reaper/新 owner 接管，复用完整结果、重做半目标、持续计账、拒绝旧回执并只发布一次。
- `test_steward_delivery_recovery.py`：普通交付跨代/崩溃持续计账、租约隔离、单项 CAS 重试与审计、旧责任在新代正式发布后收敛、未发布后继时源输入失效仍能清理。
- `test_steward_benchmark_measurement.py`：真实独立连接在首次 COMMIT 后立即写入；SQLite authorizer 内的 COMMIT 延迟必须计入，统计回调的可控延迟不得计入显式持锁或隐式写窗口。结束时间必须在 DB-API 返回后、任何指标锁/分配/日志前固定，提交失败但仍持有事务时计到实际 rollback；SQL 异常自动回滚立即结束计时，后续清理不得二次记录。诊断 verb 使用固定白名单，注释和绑定参数均不得泄漏。
- `scripts/benchmark-steward-recompute.py --ming --sizes 50 200 --scan-windows 2`：原朱氏30人/30账号、50/200稀疏样本；真实300秒扫描、登录/lease/maintenance并发；显式持锁和隐式写窗口都报告p95/p99/max。含取锁等待的隐式窗口只能叫上界，不能据此直接归因fsync。
- 慢写记录必须关联同一事务的 hold、commit、begin wait、thread CPU 和安全调用符号，不记录 SQL 参数；不同事务的各项最大值不可相减来推断归因。旧测量在提交后统计再取终点时可能误计应用等待，修复量具前的超限样本应保留并标注未归因，不能凭下一次通过就删除。
- `scripts/benchmark-pfv-browser.py --trials 5 --report <path>`：生产前端真实Chrome，从首次PFV请求到可交互骨架；实际平移/缩放、切自由画布拖动节点、选中结构关系后，下一批称谓更新保持视口、坐标和面板；375px与双主题分别验证。
- `scripts/frontend-api-smoke.sh --report <path>`：隔离 DATA_DIR，public/internal/admin 三个 listener 都使用随机 loopback 端口；internal 不能遗漏为固定8001，否则已有开发隧道会阻止整个 serve 启动。环境 blocked 不算通过；修正测试环境后必须实际重跑接口用例。

### 9.7 Wrong vs Correct

```python
# Wrong：会持续占有SQLite唯一写锁，并把半成品暴露给读者。
with command_transaction(session, immediate=True):
    for target in targets:
        live_view.apply(resolve_for_target(session, target))
```

```python
# Correct：一致快照得到冻结DTO，关闭读事务后进行纯计算。
snapshot = steward_snapshot.read_viewer(
    bind, space_id=space_id, account_id=account_id,
    expected_versions=versions,
)
resolution = resolve_graph(snapshot.graph, target_user_id=target_id)
# save_target 在有界短事务内重验完整 fence，保存完整人物结果；
# publish 最后原子切指针/状态/水位/交付激活，不复制结果行。
```

### 9.8 与自动称谓及 Memory/RAG 的串行集成

- `0048_steward_terminology_publication` 合并 `0047_rag_lifecycle_integrity` 与 `0045_steward_staged_publication`。既有 revision ID 不改名；分别从两父分支升级，不能只测一次从空库到 head。
- 同迁移增加 `ix_sdi_status_id(status,id)` 支持全局按 ID 领取 pending intent。既有 `(status,available_at,id)` 不能满足该排序，会先遍历并排序全部 pending 及其依赖；有 generation_id 的领取仍使用 generation 索引。模型和迁移同时维护，unmerge 删除本层新增索引。
- 先安装 0045 再运行 0044 术语迁移时，SQLite batch 重建 provider setting 表会丢弃其已有三个 inferred 输入触发器。0048 幂等恢复这些父级触发器；安全 unmerge 只删除本层 15 个新增 presentation 触发器。
- 一次命令降过多个父版本时，0048 在任何 DDL 前预检计划路径上的 RAG digest/context evidence、历史 chunks/失效原因、Memory provenance 和 Steward 未完待办拒绝条件。单纯 unmerge 保留全部表与业务行；深层拒绝不能先拆当前输入栅栏。
- `ViewerInput.terminology` 复制当前 viewer/space 的明确 usage（含关联词条内容与 revision）、稳定 suppression、非空投影及有效模型开关。`steward_terminology_snapshot.resolve_display_term` 从同一授权图/事实 revision/披露出生数据纯算 baseline 与有效自动词；个人/空间偏好优先，不能在逐目标 writer 中重读图。
- `term_usages`、`steward_term_suppressions`、有效自动投影和空间/平台 terminology 开关同事务推进 presentation；环境开关及静态词包/规则进入配置指纹。投影的发送、重试、检查时间和 audit 字段不失效展示；没有覆盖词的 baseline-only 行不推进 presentation。
- 输入版本另存 `search_config`（snapshot/policy/algorithm/depth/path limit），供结构路径复用与搜索失败预算使用；完整 `config` 仍约束 generation、交付及展示 hash。仅称谓规则/词包/环境开关变化必须重绘称谓、复用结构且保留失败预算；算法/策略变化必须重新搜索。升级时完整配置 hash 的格式一起更新，实际旧代保守冷算。
- 术语生产消费 sealed generation 的 confirmed targets 和共享 `result_view_id`，发布后独立交付。同 viewer 每批最多 8 个目标，显式读快照后关闭事务再计算；writer 统一核验本次完整输入、身份、publication 和租约，原子提交批次与 receipt。缓存只在 receipt 已 commit 后接受本批版本增量，回滚不得留下可通过 ABA 的缓存。
- 只有本地 `terminology` 交付可以在原发布代 structural/config/时间仍有效时重新读取最新 presentation；普通交付仍使用完整 fence。GC 和交付共用 `valid_source`，不能在第一个术语输出后误删同批剩余工作。自身输出导致的展示后继在该批交付期间暂缓；输入未变的扫描、其他空间、结构或配置已变的作业仍可运行。
- 无改善的普通 baseline 不建立空自动投影；明确 usage、已有自动词/抑制及 derived 的“保留叫法”仍处理。相同有效输入完整交付后仅复用完成回执；assist 分组也在写事务外准备，注册在确定性术语交付结束后进行，保留主线逐 HTTP fence 和 unknown 恢复。
- 前端称谓输入失效清 payload、ETag、期限和在途请求，保留同身份的坐标/视口；账号变化仍完整清理。称谓面板按 generation/legacy view_version 清旧证据与建议，同代 progress revision 不触发重复 resolve。
- 确认关系缺省称谓只消费 `current_view_payload` 的正式发布结果，缺失时显示中性线索，不能在普通呈现读取里回退到整图搜索。候选关系依旧按其自身 `fact_type` 的有向单步解析，保留第三方视角和亚型语义。
- 热视图复用读取元数据并以单行 `INSERT ... SELECT` 复制已授权骨架，不在 writer 中解码/重编码整族 JSON；结果仍通过 `result_view_id` 引用原始目标集合，计数与版本栅栏不变。
- PFV 累积载荷只读取目标 ID、状态、失败原因和展示边，不加载仅计算复用需要的 `resolution_json`。授权节点和逐步路径证据校验继续执行。
- 无 focus、无 retry 的重复 demand 可在同一显式授权读快照中合并：有效 publication 已包含本人 ready 视图，或有效 running generation 已覆盖当前 demand revision 且 job 的 owner/attempt/deadline 和最高事件水位均满足。已发布视图不因自身完成事件或无关全局事件失去 satisfied 语义；运行中遇到更高水位、新需求、失效输入或租约仍回到原短写事务并唤醒执行器。
- 集成回归入口：`test_steward_terminology_input_versions.py`、`test_steward_terminology_publication_migration.py`、`test_rag_lifecycle_migrations.py`、`test_steward_terminology_delivery_integration.py` 及主线 terminology/suggestion quality/runtime 测试；前端 `personalFamilyViewProgress.spec.ts`、`KinshipTermPanel.spec.ts` 和 `person-profile.spec.ts`。
