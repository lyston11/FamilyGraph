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
