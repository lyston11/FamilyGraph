"""V2.4 Steward 与 ActionCard 合同表（迁移 0013_steward_action_card）。

三张表：
- BehaviorProjection：空间×账号粒度的行为投影（词条使用计数、卡片冷却、纠正偏好）。
  红线：只允许明确目的的聚合键（services/steward.put_projection 校验前缀），
  禁止键盘/鼠标/停留时长等泛行为监控字段。
- ActionCard：有状态推荐卡。FSM：pending→viewed/accepted；viewed→accepted；accepted→executed；
  pending/viewed→dismissed；任意非终态→expired/superseded。终态不可复活，
  并发转换用 compare-and-set revision（合同见 services/action_cards.py）。
  executed_event_id / superseded_by_id 为逻辑引用（不设 FK）：执行命令产生的
  DomainEvent 与被取代卡均只增不删，避免空间级联删除时的自引用/跨表级联环。
- StewardJob：以 space 为分区键的确定性后台作业（V2.4 起家；09-06 起可在开关
  全开时叠加模型辅助层——候选/排序/解释三类，均不改写任何确定性结论，
  见 services/steward_assist.py 与 StewardModelCall/StewardLlmCandidate）。
  trigger_cursor 记录入队时 domain_events 水位；checkpoint 只保存作业进度/版本
  （last_event_cursor、finding 签名、统计），绝不保存自由形式隐藏长期记忆。

append-only 约定不变；卡片与作业的历史行随空间删除而级联清除（投影可重建）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# ---- 枚举常量（服务层与迁移共用；CHECK 约束兜底）----
# ST-2 触发原因 taxonomy
STEWARD_JOB_CAUSES = (
    "source_fact",
    "claim",
    "membership",
    "term",
    "disclosure",
    "domain_event",
    "integrity_scan",
    "admin_rerun",
)
STEWARD_JOB_STATUSES = ("queued", "leased", "running", "succeeded", "failed", "expired")
STEWARD_ACTIVE_JOB_STATUSES = ("queued", "leased", "running")

# 卡片种类（S1 首版两种；后续按 ST-5 矩阵扩展独立 kind）
CARD_KINDS = ("household_link", "lineage_request")
CARD_STATES = ("pending", "viewed", "accepted", "executed", "dismissed", "expired", "superseded")
CARD_ACTIVE_STATES = ("pending", "viewed", "accepted")
CARD_TERMINAL_STATES = ("executed", "dismissed", "expired", "superseded")

_ACTIVE_STATE_SQL = "state IN ('pending','viewed','accepted')"
_ACTIVE_JOB_STATUS_SQL = "status IN ('queued','leased','running')"

# 09-11：attempt 状态机（reserved → in_flight → succeeded/failed/degraded/
# unknown；skipped = 从未预留/未发送）
_SMC_STATUS_CHECK_SQL = (
    "status IN ('succeeded','failed','degraded','skipped','reserved','in_flight','unknown')"
)

_CARD_KIND_CHECK_SQL = f"kind IN ({', '.join(repr(k) for k in CARD_KINDS)})"
_CARD_STATE_CHECK_SQL = f"state IN ({', '.join(repr(s) for s in CARD_STATES)})"
_JOB_CAUSE_CHECK_SQL = f"cause IN ({', '.join(repr(c) for c in STEWARD_JOB_CAUSES)})"
_JOB_STATUS_CHECK_SQL = f"status IN ({', '.join(repr(s) for s in STEWARD_JOB_STATUSES)})"


class BehaviorProjection(Base):
    """行为投影：用途受限的聚合计数（ST-3），键由服务层白名单前缀约束。

    projection_key 约定：`card_cooldown:<kind>` / `correction_preference:<concept>`
    / `term_usage:<concept>`；value_json 只存计数与时间戳。
    UNIQUE(space_id, account_id, projection_key)：同账号同键单行 upsert。
    """

    __tablename__ = "behavior_projections"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    projection_key: Mapped[str] = mapped_column(String(160), nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index(
            "uq_behavior_projections_key",
            "space_id",
            "account_id",
            "projection_key",
            unique=True,
        ),
        Index("ix_behavior_projections_account", "account_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<BehaviorProjection space={self.space_id} account={self.account_id}"
            f" {self.projection_key}>"
        )


class ActionCard(Base):
    """推荐卡：服务端状态为真源，会话消息与空间 Inbox 均按 card_id 引用渲染。

    dedupe_key = `<kind>:<subject>:<object|->`（见 action_cards.dedupe_key_for）。
    AC-ST3 由 partial unique index 兜底：同 (space, key, evidence_version) 至多
    一张活动卡；executed/dismissed 的「相同证据不再骚扰」语义在服务层裁决。
    """

    __tablename__ = "action_cards"
    __table_args__ = (
        CheckConstraint(_CARD_KIND_CHECK_SQL, name="ck_ac_kind"),
        CheckConstraint(_CARD_STATE_CHECK_SQL, name="ck_ac_state"),
        Index(
            "uq_action_cards_active_dedupe",
            "space_id",
            "dedupe_key",
            "evidence_version",
            unique=True,
            sqlite_where=sa.text(_ACTIVE_STATE_SQL),
        ),
        Index("ix_action_cards_space_state", "space_id", "state"),
        Index("ix_action_cards_recipient", "recipient_account_id", "state"),
        Index("ix_action_cards_subject", "subject_user_id"),
        Index("ix_action_cards_object", "object_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    recipient_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    subject_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    object_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    # 证据快照：仅含 fact id/type/revision 与矩阵输入标量（不含 masked 原值）
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 同 dedupe_key 内单调递增；证据变化 = version+1 并 supersede 旧活动卡
    evidence_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    proposed_action_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    privacy_effect: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    # compare-and-set 版本号：每次状态转换 +1，转换前锁内复核调用方快照
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 逻辑引用：执行命令成功后落地的 DomainEvent id（S2 执行路径写入）
    executed_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 逻辑引用：取代本卡的新卡 id
    superseded_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 09-06 模型辅助层：解释产物（NULL=未生成，呈现用模板 reason_text）；
    # 只允许复述卡内已确认事实，生成失败保持 NULL（services/steward_assist）
    reason_text_llm: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 09-06 模型辅助层：LLM 排序产物（1..n；NULL=按 created_at 既有序）。
    # 只改呈现顺序，绝不改变集合成员（资格判定仍由确定性矩阵决定）
    presentation_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ActionCard {self.id} {self.kind} v{self.evidence_version}"
            f" {self.state} r{self.revision}>"
        )


class StewardJob(Base):
    """durable steward 作业：每空间至多一个活跃 job（partial unique index 兜底）。

    租赁/结算语义复用 agent_queue 的立即事务模式；attempt 在每次 lease 时 +1，
    lease 过期由 reaper 回队或判 expired（终态不可复活）。
    """

    __tablename__ = "steward_jobs"
    __table_args__ = (
        CheckConstraint(_JOB_CAUSE_CHECK_SQL, name="ck_sj_cause"),
        CheckConstraint(_JOB_STATUS_CHECK_SQL, name="ck_sj_status"),
        Index(
            "uq_steward_jobs_space_active",
            "space_id",
            unique=True,
            sqlite_where=sa.text(_ACTIVE_JOB_STATUS_SQL),
        ),
        Index("ix_steward_jobs_lease_scan", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    cause: Mapped[str] = mapped_column(String(24), nullable=False)
    # 入队时 domain_events 水位：worker 只消费 (已完成水位, trigger_cursor] 窗口
    trigger_cursor: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    last_event_cursor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 09-11 生产调度：可重试失败的有限退避（NULL=立即可租）；到点前 lease 扫描跳过
    available_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 逻辑引用：人工重跑创建的新作业指向被关联的历史作业（终态不复活，只建新行）
    retry_of_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 安全错误分类（F16：只存白名单分类码，异常原文/SQL 参数永不落库或入日志）
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StewardJob {self.id} space={self.space_id} {self.cause}"
            f"/{self.status} cursor={self.trigger_cursor}>"
        )


class StewardSpaceSchedule(Base):
    """空间周期扫描调度（09-11 生产调度；迁移 0036）。

    每个空间一行：next_scan_at 到期时间、last_scheduled_cursor 最近一次扫描
    触发的水位、policy_version 记录追补口径。扫描 tick 用短 BEGIN IMMEDIATE
    选择到期空间并经 canonical enqueue 合同登记 core job——调度行本身不改
    任何事实（扫描绝不直接写 SourceFact/视图）。

    policy_version 变化（或行缺失=首次启用/重新启用）时该行被置为立即到期，
    触发一次有界追补；空间删除经 CASCADE 清除。
    """

    __tablename__ = "steward_space_schedules"
    __table_args__ = (Index("ix_steward_space_schedules_due", "next_scan_at"),)

    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), primary_key=True
    )
    next_scan_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_scheduled_cursor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # 每空间辅助 kind 轮转进度（terminology 公平调度；默认 0）
    assist_kind_cursor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StewardSpaceSchedule space={self.space_id} next={self.next_scan_at}"
            f" cursor={self.last_scheduled_cursor}>"
        )


class StewardAssistBatch(Base):
    """模型辅助批次（09-11 R2；迁移 0037）：canonical job 的受限辅助子阶段。

    每 job 至多一行（job_id UNIQUE）：core 短事务提交确定性结果时在同一事务
    登记本行（R1：HTTP 绝不发生在业务写事务内）。独立状态 + 独立 lease
    （lease_owner/lease_until），不能被 sidecar lease；fence_json 是注册时
    的证据快照（启用 kind、卡片 id+revision、facts 摘要、policy_version），
    发送前与写回前各重验一次（R4 写回栅栏；任何变化 → skip/supersede）。

    status: pending（待调度）→ leased（已预留 attempt 行，执行者持有 lease）
    → applying（审计已落库，写回 CAS 进行中）→ applied | failed | superseded。
    崩溃恢复：lease 过期按 attempt 状态收敛（in_flight → unknown 保守计费，
    不自动重发；succeeded+output_json → 重跑 fence 后 CAS 写回）。
    """

    __tablename__ = "steward_assist_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','leased','applying','applied','failed','superseded')",
            name="ck_sab_status",
        ),
        sa.UniqueConstraint("job_id", name="uq_sab_job"),
        Index("ix_steward_assist_batches_due", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("steward_jobs.id", ondelete="CASCADE"), nullable=False
    )
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StewardAssistBatch job={self.job_id} {self.status}"
            f" attempt={self.attempt} evidence={self.evidence_hash[:8]}>"
        )


class StewardModelCall(Base):
    """模型调用 attempt 审计（09-01 决策"另立任务"的兑现，迁移 0034/0037）。

    Steward 每次模型辅助调用（attempt）一行；与 Assistant 的 generic
    AgentSession/AgentRun 完全隔离（不伪造 AgentRun）。prompt 只存 sha256
    摘要与长度，**永不存明文**（prompt 只含白名单结构化字段，见
    services/steward_assist 红线）；响应原文同样只存 digest/长度，解析后的
    可写回产物（候选/排列/解释文本）存 output_json 供崩溃后恢复写回。

    状态机（09-11 R2/R3）：reserved（预算已预留，未发送）→ in_flight（发送中，
    无打开事务）→ succeeded | failed | degraded | unknown | skipped。
    unknown = 无法证明上游未处理（如读超时/断连）——保守计费且不自动重发
    （上游未证实支持幂等键）。billed_tokens 为保守计费值：usage 缺失/负数/
    部分字段时回落到预留值，failed/degraded/invalid-output 同样消耗预算。

    唯一性：(job_id, assist_kind, seq)（同事务重入幂等）与
    (job_id, assist_kind, subject_key, input_hash, attempt_no)（subject 维度
    attempt 键；subject_key = "facts" | "ranking:<ids>" | "card:<id>"）。
    """

    __tablename__ = "steward_model_calls"
    __table_args__ = (
        CheckConstraint(
            "assist_kind IN ('candidate','ranking','explanation','terminology')",
            name="ck_smc_assist_kind",
        ),
        CheckConstraint(_SMC_STATUS_CHECK_SQL, name="ck_smc_status"),
        sa.UniqueConstraint("job_id", "assist_kind", "seq", name="uq_smc_job_kind_seq"),
        sa.Index(
            "uq_smc_attempt_key",
            "job_id",
            "assist_kind",
            "subject_key",
            "input_hash",
            "attempt_no",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("steward_jobs.id", ondelete="CASCADE"), nullable=False
    )
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    assist_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_providers.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_chars: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="succeeded", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seq: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # ---- 09-11 批次/attempt 结构（迁移 0037）----
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_assist_batches.id", ondelete="CASCADE"), nullable=True
    )
    subject_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 预留（发送前）：保守上界输入 token 估算 + 输出 cap
    reserved_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reserved_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 保守计费结算值（缺失/负数 usage 回落预留；unknown 同样计费）
    billed_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # terminology 的 viewer 绑定（每 attempt 一个账号；旧三类保持 NULL）
    viewer_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
    )
    response_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 解析并验证通过的可写回产物（非原始 payload）：候选列表/排列/解释文本
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StewardModelCall job={self.job_id} {self.assist_kind}/{self.status}"
            f" tokens={self.total_tokens}>"
        )


class StewardLlmCandidate(Base):
    """LLM 关系候选池（内部；R3 红线：不经过确定性矩阵绝不进卡片/任何正式写入）。

    稳定身份只含关系种类与有向端点，原 payload/job/驳回历史不随证据换版。
    attribution_status=versioned 是粘性的内部处理模式，不表示关系当前已确认。
    """

    __tablename__ = "steward_llm_candidates"
    __table_args__ = (
        CheckConstraint("status IN ('proposed','dismissed')", name="ck_slc_status"),
        CheckConstraint(
            "attribution_status IN ('legacy','unsupported','versioned')",
            name="ck_slc_attribution_status",
        ),
        sa.UniqueConstraint("space_id", "candidate_digest", name="uq_slc_space_digest"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("steward_jobs.id", ondelete="CASCADE"), nullable=False
    )
    candidate_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    candidate_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    attribution_status: Mapped[str] = mapped_column(
        String(16), default="legacy", server_default="legacy", nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StewardLlmCandidate space={self.space_id} job={self.job_id}"
            f" {self.candidate_kind}/{self.status}>"
        )


class StewardCandidateEvidenceVersion(Base):
    """Immutable, space-internal support certificate; never a confirmed relation.

    The first later delivery checks the saved source IDs/revisions and records
    pending -> projected/invalidated once. A projected row attests only to that
    historical check. Provenance deletion may clear its nullable references;
    candidate/space deletion retains the existing domain cascade semantics.
    """

    __tablename__ = "steward_candidate_evidence_versions"
    __table_args__ = (
        sa.UniqueConstraint("candidate_id", "evidence_digest", name="uq_scev_candidate_digest"),
        CheckConstraint("status IN ('pending','projected','invalidated')", name="ck_scev_status"),
        CheckConstraint(
            "(status = 'pending' AND projection_job_id IS NULL "
            "AND projection_checked_at IS NULL AND invalidation_reason IS NULL) OR "
            "(status = 'projected' AND projection_checked_at IS NOT NULL "
            "AND invalidation_reason IS NULL) OR "
            "(status = 'invalidated' AND projection_checked_at IS NOT NULL "
            "AND invalidation_reason IS NOT NULL)",
            name="ck_scev_projection_result",
        ),
        Index("ix_scev_space_pending", "space_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("steward_llm_candidates.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    validation_contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    support_facts_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    source_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_jobs.id", ondelete="SET NULL"), nullable=True
    )
    source_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_assist_batches.id", ondelete="SET NULL"), nullable=True
    )
    source_model_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_model_calls.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    projection_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_jobs.id", ondelete="SET NULL"), nullable=True
    )
    projection_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    invalidation_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# ---- Staged publication, input fences and durable recovery (0044/0045) ----

STEWARD_GENERATION_STATUSES = ("running", "published", "failed", "superseded")
STEWARD_GENERATION_VIEW_STATUSES = ("pending", "ready", "failed")

_GENERATION_STATUS_SQL = f"status IN ({', '.join(repr(v) for v in STEWARD_GENERATION_STATUSES)})"
_GENERATION_VIEW_STATUS_SQL = (
    f"status IN ({', '.join(repr(v) for v in STEWARD_GENERATION_VIEW_STATUSES)})"
)


class StewardGeneration(Base):
    """One sealed, fenced generation of a space's required viewer results.

    Viewer skeletons and complete targets are staged below this row. Ordinary
    reads follow StewardPublication; progressive reads can use the latest valid
    preview. Publication, consumed cursor, completion event and delivery
    activation commit atomically without copying large results.
    """

    __tablename__ = "steward_generations"
    __table_args__ = (
        CheckConstraint(_GENERATION_STATUS_SQL, name="ck_sg_status"),
        Index("ix_sg_space_created", "space_id", "created_at"),
        Index("ix_sg_job", "job_id"),
        Index("ix_sg_space_id", "space_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_jobs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    execution_cursor: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # v4 publication fence. Empty input versions identify pre-staging legacy rows.
    input_versions_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    manifest_sealed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    required_views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ready_views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    intents_prepared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StewardGeneration {self.id} space={self.space_id}"
            f" {self.status} cursor={self.execution_cursor}>"
        )


class StewardGenerationView(Base):
    """Authorized skeleton and target-derived progress for exactly one viewer.

    Only ready/unavailable complete targets increment completed_count. A ready
    preview is local completion; the full space becomes authoritative only when
    the generation is published. Identical views can reference immutable prior
    targets through result_view_id, which the collector treats as a live root.
    """

    __tablename__ = "steward_generation_views"
    __table_args__ = (
        CheckConstraint(_GENERATION_VIEW_STATUS_SQL, name="ck_sgv_status"),
        sa.UniqueConstraint("generation_id", "viewer_account_id", name="uq_sgv_gen_viewer"),
        Index("ix_sgv_viewer", "viewer_account_id", "space_id"),
        Index("ix_sgv_generation_status", "generation_id", "status"),
        Index("ix_sgv_result_source", "result_view_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("steward_generations.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    viewer_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    root_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    completed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    structural_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    presentation_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    topology_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    skeleton_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    demand_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Reuse immutable, verified results without copying a whole view at publication.
    result_view_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class StewardRetryBudget(Base):
    """Pre-reserved attempts persist across jobs, scans, crashes and restarts.

    Search scope identifies viewer/target and structural+algorithm fingerprint;
    presentation changes do not reset an exhausted path search. Optional overlay
    budgets have their own scope. Auditable, cooldown-limited manual grants add
    a finite opportunity without resetting past attempts.
    """

    __tablename__ = "steward_retry_budgets"
    __table_args__ = (
        sa.UniqueConstraint("space_id", "fingerprint", "scope", name="uq_srb_scope"),
        Index("ix_srb_space", "space_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(48), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    exhausted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retry_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    manual_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    manual_grants: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class StewardInputRevision(Base):
    """Durable source revisions. Scope 0 is global; tombstones are never reset."""

    __tablename__ = "steward_input_revisions"

    scope_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    structural: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    presentation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    inferred: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class StewardViewTarget(Base):
    """One complete, immutable target result; absence/pending is never no_path."""

    __tablename__ = "steward_view_targets"
    __table_args__ = (
        sa.UniqueConstraint("view_id", "target_user_id", name="uq_svt_view_target"),
        CheckConstraint(
            "status IN ('pending','ready','unavailable','failed')", name="ck_svt_status"
        ),
        Index("ix_svt_view_status", "view_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    view_id: Mapped[int] = mapped_column(
        ForeignKey("steward_generation_views.id", ondelete="CASCADE"), nullable=False
    )
    target_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    distance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resolution_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    edge_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class StewardPublication(Base):
    """The only authoritative full-result pointer. Publish never copies target rows."""

    __tablename__ = "steward_publications"
    __table_args__ = (Index("ix_sp_generation", "generation_id"),)

    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), primary_key=True
    )
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("steward_generations.id", ondelete="CASCADE"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class StewardViewDemand(Base):
    """Coalesced viewer demand, independent of the event cursor and source inputs."""

    __tablename__ = "steward_view_demands"
    __table_args__ = (
        sa.UniqueConstraint("space_id", "viewer_account_id", name="uq_svd_space_viewer"),
        Index("ix_svd_unfulfilled", "space_id", "fulfilled_revision", "revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    viewer_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    fulfilled_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    focus_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    retry_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StewardDeliveryIntent(Base):
    """Prepared per-object delivery, activated by the publication transaction.

    Status stays pending while the generation is running. A published generation
    is the activation bit, so publishing is independent of the number of intents.
    DB effects and done are committed together; external calls use assist batches.
    """

    __tablename__ = "steward_delivery_intents"
    __table_args__ = (
        sa.UniqueConstraint("generation_id", "intent_key", name="uq_sdi_generation_key"),
        CheckConstraint("status IN ('pending','done','failed','superseded')", name="ck_sdi_status"),
        Index("ix_sdi_due", "status", "available_at", "id"),
        Index("ix_sdi_status_id", "status", "id"),
        Index("ix_sdi_generation", "generation_id", "status"),
        Index("ix_sdi_effect", "space_id", "effect_fingerprint", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("steward_generations.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    intent_key: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    effect_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class StewardFindingDelivery(Base):
    """Receipt for one contiguous finding occurrence, retained across GC.

    Planning a finding is not delivery. Every successor that still observes the
    same finding shares its occurrence ID until one published generation omits
    it. Only the transaction emitting the event creates this receipt.
    """

    __tablename__ = "steward_finding_deliveries"

    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), primary_key=True
    )
    signature: Mapped[str] = mapped_column(String(200), primary_key=True)
    occurrence_generation_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    delivered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class StewardInferredOverlay(Base):
    """Optional viewer projection, fenced independently of confirmed progress."""

    __tablename__ = "steward_inferred_overlays"

    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), primary_key=True
    )
    viewer_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    input_versions_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class StewardTermProjection(Base):
    """管家自动称谓投影（09-13-steward-terminology-autonomy；迁移 0044）。

    可重建的显示缓存，不是新词典：TermEntry/TermUsage 与两人晋升规则仍是
    真源。唯一键 (space_id, viewer_account_id, root_user_id, target_user_id)。
    自动 term 只改善已绑定路径的显示，不作为关系依据；个人/生效空间词条
    读取时无条件优先。status: active | suppressed | stale | unchanged。
    origin: deterministic | model（NULL=尚无自动产物）。
    """

    __tablename__ = "steward_term_projections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','suppressed','stale','unchanged')", name="ck_stp_status"
        ),
        CheckConstraint("origin IN ('deterministic','model')", name="ck_stp_origin"),
        sa.UniqueConstraint(
            "space_id",
            "viewer_account_id",
            "root_user_id",
            "target_user_id",
            name="uq_stp_scope",
        ),
        Index("ix_stp_semantic", "viewer_account_id", "semantic_hash"),
        Index("ix_stp_checked", "last_checked_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    viewer_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    root_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    concept_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 当前语义依据版本摘要（不含输出自身 revision，避免自激调用）
    semantic_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 请求摘要（semantic_hash + 模型规则/prompt 版本）
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    baseline_term: Mapped[str | None] = mapped_column(String(64), nullable=True)
    baseline_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    term: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_model_call_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_checked_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_attempt_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 稳定抑制键（account/space/target + 已验证语义 + 规范词；不含任何版本号）
    suppression_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StewardTermProjection v={self.viewer_account_id} t={self.target_user_id}"
            f" {self.status} term={self.term}>"
        )


class StewardTermSuppression(Base):
    """恢复原叫法的稳定拒绝记录（跨建议/证据/prompt 版本防重现）。

    按 suppression_key（account/space/target + 已验证关系语义 + 规范词）查找；
    不含路径事实 ID/revision、词条 revision、prompt 或反馈版本——同一关系换
    等价证据路径不能绕过恢复过的拒绝。
    """

    __tablename__ = "steward_term_suppressions"
    __table_args__ = (
        sa.UniqueConstraint(
            "viewer_account_id",
            "space_id",
            "target_user_id",
            "suppression_key",
            name="uq_sts_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    viewer_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    target_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    suppression_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_suggestion_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
