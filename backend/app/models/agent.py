"""Agent Runtime 合同表（迁移 0009，notes.md「统一执行模型裁定」）。

- agent_sessions：固定 account_id + space_id + agent_kind；scope 创建后不可变
  （迁移内以 BEFORE UPDATE trigger 强制，服务层无任何更新路径）。
- agent_messages：会话消息投影；idempotency_key 非空时 (session_id, key) 部分唯一。
- agent_runs：唯一执行记录（assistant Run 与 steward 执行共用同一 FSM 列）；
  partial unique index 保证每 session 至多一个 active run。
- agent_run_events：每 Run 单调 seq，先持久化再广播（RT-4）；UNIQUE(run_id, seq)。
- agent_jobs：durable queue 条目，与 run 1:1（jobs.run_id UNIQUE CASCADE；
  runs.job_id SET NULL 反向引用，删除方向：先删 job 再删 run 或直接删 run 级联 job）。

FSM：queued → leased → running → succeeded|failed|cancelled|expired；
lease 过期由 reaper 回队（attempt+1，超 max_attempts → expired）。终态不可复活。
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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

AGENT_KINDS = ("assistant", "steward")
RUN_ACTIVE_STATUSES = ("queued", "leased", "running")
RUN_TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "expired")
RUN_STATUS_CHECK_SQL = (
    "status IN ('queued','leased','running','succeeded','failed','cancelled','expired')"
)

_AGENT_KIND_CHECK_SQL = "agent_kind IN ('assistant','steward')"


class AgentSession(Base):
    """Agent 会话：scope 三元组创建后不可变（DB trigger 强制 + 服务层无更新路径）。"""

    __tablename__ = "agent_sessions"
    __table_args__ = (CheckConstraint(_AGENT_KIND_CHECK_SQL, name="ck_agent_sessions_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    agent_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentSession {self.id} account={self.account_id}"
            f" space={self.space_id} {self.agent_kind}>"
        )


class AgentMessage(Base):
    """会话消息：content_json 为结构化 UI 投影，不保存 Provider 私有 payload。"""

    __tablename__ = "agent_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','system')", name="ck_agent_messages_role"),
        Index(
            "uq_agent_messages_session_key",
            "session_id",
            "idempotency_key",
            unique=True,
            sqlite_where=sa.text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_agent_messages_session_id", "session_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AgentMessage {self.id} session={self.session_id} {self.role}>"


class AgentRun(Base):
    """唯一执行记录；interactive 与 steward 共用 FSM 列（notes.md 裁定）。"""

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint("kind IN ('assistant','steward')", name="ck_agent_runs_kind"),
        CheckConstraint(RUN_STATUS_CHECK_SQL, name="ck_agent_runs_status"),
        Index(
            "uq_agent_runs_session_active",
            "session_id",
            unique=True,
            sqlite_where=sa.text("status IN ('queued','leased','running')"),
        ),
        Index("ix_agent_runs_session_id", "session_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_messages.id", ondelete="SET NULL"), nullable=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_jobs.id", ondelete="SET NULL", use_alter=True), nullable=True, unique=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=3, server_default="3", nullable=False
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 浏览器取消请求：leased/running 时置位，settle 把本应 succeeded 的终态改判为 cancelled
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.false(), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_allowlist_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AgentRun {self.id} session={self.session_id} {self.kind}/{self.status}>"


class AgentRunEvent(Base):
    """公开事件流：seq 每 Run 单调；重复 (run_id, seq) 由服务层幂等裁决。"""

    __tablename__ = "agent_run_events"
    __table_args__ = (
        Index(
            "uq_agent_run_events_run_seq",
            "run_id",
            "seq",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    public_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AgentRunEvent {self.id} run={self.run_id} seq={self.seq} {self.type}>"


class AgentJob(Base):
    """durable queue 条目：lease 只扫 jobs，返回配对 run_id；heartbeat 打在 job 同步 run。"""

    __tablename__ = "agent_jobs"
    __table_args__ = (
        CheckConstraint("kind IN ('assistant','steward')", name="ck_agent_jobs_kind"),
        CheckConstraint(RUN_STATUS_CHECK_SQL, name="ck_agent_jobs_status"),
        Index(
            "uq_agent_jobs_space_active",
            "space_id",
            unique=True,
            sqlite_where=sa.text("kind = 'steward' AND status IN ('queued','leased','running')"),
        ),
        Index("ix_agent_jobs_lease_scan", "kind", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    space_id: Mapped[int | None] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=3, server_default="3", nullable=False
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 与 run.cancel_requested 镜像（heartbeat/context 响应附带，客户端兼容未知字段）
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.false(), nullable=False
    )
    leased_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AgentJob {self.id} run={self.run_id} {self.kind}/{self.status}>"


class AgentToolCall(Base):
    """工具调用执行台账：同 (run_id, tool_call_id) 至多一行，供副作用去重（R4）。

    仅 sidecar 携带显式 tool_call_id 时记录；重放同 id 幂等返回首次输出，
    不同工具/版本 → AGENT_TOOL_CALL_CONFLICT（409）。result_json 存未经过
    tool_result_hook 的输出，API 边界重试时照常再次执行结果策略 guard。
    """

    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        Index(
            "uq_agent_tool_calls_run_call",
            "run_id",
            "tool_call_id",
            unique=True,
            sqlite_where=sa.text("tool_call_id IS NOT NULL"),
        ),
        Index("ix_agent_tool_calls_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AgentToolCall {self.id} run={self.run_id} {self.tool_name}@{self.tool_version}>"
