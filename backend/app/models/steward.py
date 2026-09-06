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

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StewardJob {self.id} space={self.space_id} {self.cause}"
            f"/{self.status} cursor={self.trigger_cursor}>"
        )


class StewardModelCall(Base):
    """child run 审计（09-01 决策"另立任务"的兑现，迁移 0034）。

    Steward 每次模型辅助调用一行；与 Assistant 的 generic AgentSession/AgentRun
    完全隔离（不伪造 AgentRun）。prompt 只存 sha256 摘要与长度，**永不存明文**
    （prompt 只含白名单结构化字段，见 services/steward_assist 红线）。
    per (job_id, assist_kind, seq) 唯一：job crash 重试据此幂等跳过，不重复花费 token。
    status: succeeded | failed（transport/解析失败）| degraded（provider 不可用）
    | skipped（预算耗尽）。
    """

    __tablename__ = "steward_model_calls"
    __table_args__ = (
        CheckConstraint(
            "assist_kind IN ('candidate','ranking','explanation')", name="ck_smc_assist_kind"
        ),
        CheckConstraint(
            "status IN ('succeeded','failed','degraded','skipped')", name="ck_smc_status"
        ),
        sa.UniqueConstraint("job_id", "assist_kind", "seq", name="uq_smc_job_kind_seq"),
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

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StewardModelCall job={self.job_id} {self.assist_kind}/{self.status}"
            f" tokens={self.total_tokens}>"
        )


class StewardLlmCandidate(Base):
    """LLM 关系候选池（内部；R3 红线：不经过确定性矩阵绝不进卡片/任何正式写入）。

    候选只含空间可见 user id + 关系种类 + 理由文本；呈现与产品化另立任务。
    candidate_digest = payload sha256，(space_id, digest) 唯一：跨 job 重复候选静默跳过。
    """

    __tablename__ = "steward_llm_candidates"
    __table_args__ = (
        CheckConstraint("status IN ('proposed','dismissed')", name="ck_slc_status"),
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
    status: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StewardLlmCandidate space={self.space_id} job={self.job_id}"
            f" {self.candidate_kind}/{self.status}>"
        )
