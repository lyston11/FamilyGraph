"""数据权利命令（F-5/AC-F6）：导出 / 更正 / 删除 / 争议决议。

统一状态机：data_right_requests（pending → processing → completed | rejected；
completed 后可惰性 expired）。所有异步产物继承 VisibilityPolicy（本人导出 =
self_private 全字段），下载有过期与审计；删除先发布冻结事件再执行真源删除并
传播 tombstone 失效事件；operator 决议走 break-glass 理由 + 审计，且仅返回
最小必要数据，不产生日常家庭浏览权。
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import config
from app.commands.context import ActorContext, command_transaction, load_actor
from app.commands.members import delete_profile_core
from app.errors import (
    BREAK_GLASS_NOTE_REQUIRED,
    CLAIM_DISPUTE_NOT_FOUND,
    DATA_RIGHT_EXPORT_NOT_READY,
    DATA_RIGHT_INVALID_TRANSITION,
    DATA_RIGHT_REQUEST_EXPIRED,
    DATA_RIGHT_REQUEST_NOT_FOUND,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models.attachment import Attachment
from app.models.relation import Relation
from app.models.space import FamilySpace, SpaceMember
from app.models.user import User
from app.models.v2_foundation import ClaimDispute, DataRightRequest, ProfileFactReview
from app.services import audit
from app.services.domain_events import emit
from app.services.visibility import PURPOSE_EXPORT
from app.utils.timeutil import utcnow

logger = logging.getLogger(__name__)

REQUEST_TYPES = ("export", "correct", "delete")
# 更正申请允许的目标字段（其余字段拒绝；结构化日期经 enrich 校验）
CORRECTABLE_FIELDS = ("name", "gender", "birth", "death", "bio")


def _request_or_404(session: Session, ctx: ActorContext, request_id: int) -> DataRightRequest:
    """请求存在性 + 归属校验：非本人请求与不存在同一 404（防枚举）。"""
    request = session.get(DataRightRequest, request_id)
    if request is None or request.requestor_account_id != ctx.account_id:
        raise_api_error(404, DATA_RIGHT_REQUEST_NOT_FOUND, "数据权利请求不存在")
    return request


def create_data_right_request(
    session: Session,
    ctx: ActorContext,
    *,
    request_type: str,
    payload: dict[str, Any] | None = None,
) -> DataRightRequest:
    """创建自助请求（subject = 本人）；类型合法性与更正字段白名单在此校验。"""
    actor = load_actor(session, ctx)
    if request_type not in REQUEST_TYPES:
        raise_api_error(422, VALIDATION_ERROR, f"未知请求类型 {request_type}")
    if request_type == "correct":
        fields = (payload or {}).get("fields")
        if not isinstance(fields, dict) or not fields:
            raise_api_error(422, VALIDATION_ERROR, "更正申请必须携带 fields")
        unknown = set(fields) - set(CORRECTABLE_FIELDS)
        if unknown:
            raise_api_error(422, VALIDATION_ERROR, f"不可更正的字段: {sorted(unknown)}")

    now = utcnow()
    with command_transaction(session):
        request = DataRightRequest(
            requestor_account_id=ctx.account_id,
            subject_profile_id=actor.id,
            type=request_type,
            status="pending",
            scope="self",
            policy_version=config.POLICY_VERSION,
            payload_json=payload,
            created_at=now,
        )
        session.add(request)
        session.flush()
        emit(
            session,
            event_type=f"data_right.{request_type}.requested",
            aggregate_type="data_right_request",
            aggregate_id=request.id,
            payload={"type": request_type, "subject": actor.id},
            actor_account_id=ctx.account_id,
        )
        if request_type == "delete":
            # 冻结合同：Agent/RAG 处理方必须消费该事件停止对该档案的新处理
            emit(
                session,
                event_type="profile.delete.requested",
                aggregate_type="profile",
                aggregate_id=actor.id,
                payload={"request_id": request.id},
                actor_account_id=ctx.account_id,
            )
        audit.write_audit(
            session,
            action="data_right_requested",
            actor_id=actor.id,
            target_id=request.id,
            ip=ctx.ip,
            detail={"type": request_type},
        )
    return request


def list_own_requests(session: Session, ctx: ActorContext) -> list[DataRightRequest]:
    load_actor(session, ctx)
    return list(
        session.scalars(
            select(DataRightRequest)
            .where(DataRightRequest.requestor_account_id == ctx.account_id)
            .order_by(DataRightRequest.id.desc())
        ).all()
    )


# ---- 导出处理（事务外异步执行，独立会话）----


def _export_payload(session: Session, subject: User) -> dict[str, Any]:
    """结构化导出：本人 self_private 口径全字段 + 关系/空间/附件元数据/确档历史。"""
    from app.services.visibility import evaluate, payload_from_decision

    decision = evaluate(session, subject, subject, purpose=PURPOSE_EXPORT)
    profile = payload_from_decision(decision, subject)
    if profile.get("created_at") is not None:
        # JSON 序列化：datetime → ISO 文本
        profile["created_at"] = profile["created_at"].isoformat()

    edges = list(
        session.scalars(
            select(Relation).where(
                (Relation.from_user == subject.id) | (Relation.to_user == subject.id),
                Relation.status.in_(("active", "pending")),
            )
        ).all()
    )
    relations = [
        {
            "id": e.id,
            "from_user": e.from_user,
            "to_user": e.to_user,
            "dir_class": e.dir_class,
            "label": e.label,
            "status": e.status,
        }
        for e in edges
    ]
    memberships = [
        {"space_id": m.space_id, "role": m.role, "status": m.status}
        for m in session.scalars(select(SpaceMember).where(SpaceMember.user_id == subject.id)).all()
    ]
    owned_spaces = [
        {"id": s.id, "name": s.name, "kind": s.kind}
        for s in session.scalars(
            select(FamilySpace).where(FamilySpace.owner_id == subject.id)
        ).all()
    ]
    attachments = [
        {"id": a.id, "type": a.type, "title": a.title, "created_at": a.created_at.isoformat()}
        for a in session.scalars(select(Attachment).where(Attachment.user_id == subject.id)).all()
    ]
    fact_reviews = [
        {
            "item_type": r.item_type,
            "status": r.status,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        }
        for r in session.scalars(
            select(ProfileFactReview).where(ProfileFactReview.profile_id == subject.id)
        ).all()
    ]
    return {
        "exported_at": utcnow().isoformat(),
        "policy_version": config.POLICY_VERSION,
        "profile": profile,
        "relations": relations,
        "space_memberships": memberships,
        "owned_spaces": owned_spaces,
        "attachments_metadata": attachments,
        "fact_reviews": fact_reviews,
    }


def process_export_request(request_id: int) -> None:
    """后台任务：生成导出文件并落盘（独立短会话；不共享请求会话）。

    文件写入 uploads/exports/，expires_at 按 config.DATA_EXPORT_TTL_HOURS。
    """
    from app.config import UPLOADS_DIR
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        request = session.get(DataRightRequest, request_id)
        if request is None or request.type != "export" or request.status != "pending":
            return
        result = session.execute(
            update(DataRightRequest)
            .where(DataRightRequest.id == request_id, DataRightRequest.status == "pending")
            .values(status="processing")
        )
        if result.rowcount != 1:
            return
        session.commit()

        subject = session.get(User, request.subject_profile_id)
        payload = _export_payload(session, subject)  # type: ignore[arg-type]

        exports_dir = UPLOADS_DIR / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"data_export_{request_id}.json"
        (exports_dir / file_name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        now = utcnow()
        with command_transaction(session):
            fresh = session.get(DataRightRequest, request_id)
            assert fresh is not None
            fresh.status = "completed"
            fresh.result_path = file_name
            fresh.finished_at = now
            fresh.expires_at = now + timedelta(hours=config.DATA_EXPORT_TTL_HOURS)
            emit(
                session,
                event_type="data_right.export.completed",
                aggregate_type="data_right_request",
                aggregate_id=request_id,
                payload={"file": file_name},
                actor_account_id=fresh.requestor_account_id,
            )
            audit.write_audit(
                session,
                action="data_right_export_completed",
                actor_id=fresh.subject_profile_id,
                target_id=request_id,
                ip=None,
                detail={"file": file_name},
            )
    except Exception:
        logger.exception("export processing failed for request %s", request_id)
        session.rollback()
    finally:
        session.close()


def open_export_file(
    session: Session, ctx: ActorContext, request_id: int
) -> tuple[DataRightRequest, Path]:
    """下载前置校验：归属 → completed → 未过期（惰性过期清理文件）。

    过期是终态事实：在同一事务内持久化后再返回 410（不随失败回滚）。
    """
    from app.config import UPLOADS_DIR

    load_actor(session, ctx)
    now = utcnow()
    expired_now = False
    stale_path_str = ""
    with command_transaction(session):
        request = _request_or_404(session, ctx, request_id)
        if (
            request.status == "completed"
            and request.expires_at is not None
            and request.expires_at <= now
        ):
            # 惰性过期：置状态、删文件、审计（有过期下载）
            expired_now = True
            stale_path_str = request.result_path or ""
            request.status = "expired"
            emit(
                session,
                event_type="data_right.export.expired",
                aggregate_type="data_right_request",
                aggregate_id=request.id,
                payload={},
                actor_account_id=ctx.account_id,
            )
            audit.write_audit(
                session,
                action="data_right_export_expired",
                actor_id=ctx.user_id,
                target_id=request.id,
                ip=ctx.ip,
                detail={},
            )

    # 事务外清理过期文件（外部 I/O 不进事务）
    if expired_now:
        stale_path = UPLOADS_DIR / "exports" / Path(stale_path_str).name
        if stale_path_str and stale_path.exists():
            stale_path.unlink()
        raise_api_error(410, DATA_RIGHT_REQUEST_EXPIRED, "导出文件已过期，请重新申请")

    with command_transaction(session):
        request = _request_or_404(session, ctx, request_id)
        if request.status != "completed" or not request.result_path:
            raise_api_error(409, DATA_RIGHT_EXPORT_NOT_READY, "导出文件尚未生成或已失效")
        path = UPLOADS_DIR / "exports" / Path(request.result_path).name
        if not path.exists():
            raise_api_error(410, DATA_RIGHT_REQUEST_EXPIRED, "导出文件已过期，请重新申请")
        audit.write_audit(
            session,
            action="data_right_export_downloaded",
            actor_id=ctx.user_id,
            target_id=request.id,
            ip=ctx.ip,
            detail={},
        )
    return request, path


def execute_delete_request(
    session: Session,
    ctx: ActorContext,
    request_id: int,
    *,
    confirm_name: str,
) -> tuple[DataRightRequest, list[str]]:
    """执行删除类数据权利请求：复用删除核心（tombstone 事件由核心发布）。

    请求决议事实先于删除落库；请求行随账号级联消失（self-delete），
    执行留痕以 audit_log + domain_events 快照为准。
    """
    actor = load_actor(session, ctx)
    purge: list[str] = []
    with command_transaction(session):
        request = _request_or_404(session, ctx, request_id)
        if request.type != "delete":
            raise_api_error(409, DATA_RIGHT_INVALID_TRANSITION, "该请求不是删除类请求")
        if request.status != "pending":
            raise_api_error(409, DATA_RIGHT_INVALID_TRANSITION, "该请求已处理")
        subject = session.get(User, request.subject_profile_id)
        if subject is None:
            raise_api_error(409, DATA_RIGHT_INVALID_TRANSITION, "档案不存在或已删除")

        # 决议事件/审计先行（此时 user/account 行仍在，FK 合法）
        emit(
            session,
            event_type="data_right.delete.executed",
            aggregate_type="data_right_request",
            aggregate_id=request.id,
            payload={"profile_id": subject.id, "requestor_account": ctx.account_id},
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="data_right_delete_executed",
            actor_id=actor.id,
            target_id=request.id,
            ip=ctx.ip,
            detail={"type": "delete", "subject": subject.id},
        )

        deleted = delete_profile_core(session, ctx, subject, confirm_name=confirm_name)
        purge = deleted.purge_image_paths
    # 请求行已随级联消失：脱离会话以免后续属性访问触发刷新错误
    session.expunge(request)
    return request, purge


def resolve_correction_request(
    session: Session,
    operator_ctx: ActorContext,
    request_id: int,
    *,
    approve: bool,
    note: str,
) -> DataRightRequest:
    """operator break-glass 决议更正申请：理由必填；批准时应用白名单字段。"""
    if not note.strip():
        raise_api_error(422, BREAK_GLASS_NOTE_REQUIRED, "平台人工处理必须提供理由（break-glass）")
    with command_transaction(session):
        request = session.get(DataRightRequest, request_id)
        if request is None or request.type != "correct":
            raise_api_error(404, DATA_RIGHT_REQUEST_NOT_FOUND, "数据权利请求不存在")
        if request.status != "pending":
            raise_api_error(409, DATA_RIGHT_INVALID_TRANSITION, "该请求已处理")

        if approve:
            subject = session.get(User, request.subject_profile_id)
            if subject is None:
                raise_api_error(409, DATA_RIGHT_INVALID_TRANSITION, "档案不存在或已删除")
            fields = (request.payload_json or {}).get("fields", {})
            from app.commands.members import _enrich

            applied: list[str] = []
            for field_name in CORRECTABLE_FIELDS:
                if field_name in fields:
                    setattr(subject, field_name, _enrich(fields[field_name]))
                    applied.append(field_name)
            emit(
                session,
                event_type="profile.updated",
                aggregate_type="profile",
                aggregate_id=request.subject_profile_id,
                payload={"fields": applied, "updated_by_operator": True},
                actor_account_id=operator_ctx.account_id,
            )

        request.status = "completed" if approve else "rejected"
        request.payload_json = {**(request.payload_json or {}), "_resolution_note": note}
        request.finished_at = utcnow()
        emit(
            session,
            event_type="data_right.correct.completed" if approve else "data_right.correct.rejected",
            aggregate_type="data_right_request",
            aggregate_id=request.id,
            payload={"approve": approve, "by_operator_account": operator_ctx.account_id},
            actor_account_id=operator_ctx.account_id,
        )
        audit.write_audit(
            session,
            action="admin_data_right_resolved",
            actor_id=None,
            target_id=request.id,
            ip=operator_ctx.ip,
            detail={
                "type": "correct",
                "approve": approve,
                "note": note,
                "break_glass": True,
                "operator_account": operator_ctx.account_id,
            },
        )
    return request


def resolve_claim_dispute(
    session: Session,
    operator_ctx: ActorContext,
    dispute_id: int,
    *,
    outcome: str,
    note: str,
) -> ClaimDispute:
    """operator 决议认领争议：evidence 原文永不覆盖，结果写独立列。"""
    if outcome not in ("resolved_claim", "resolved_reject"):
        raise_api_error(422, VALIDATION_ERROR, "未知争议决议")
    if not note.strip():
        raise_api_error(422, BREAK_GLASS_NOTE_REQUIRED, "平台人工处理必须提供理由（break-glass）")
    with command_transaction(session):
        dispute = session.get(ClaimDispute, dispute_id)
        if dispute is None:
            raise_api_error(404, CLAIM_DISPUTE_NOT_FOUND, "争议不存在")
        if dispute.status != "open":
            raise_api_error(409, DATA_RIGHT_INVALID_TRANSITION, "争议已处理")
        dispute.status = outcome
        dispute.resolution_note = note
        dispute.resolved_at = utcnow()
        emit(
            session,
            event_type="claim_dispute.resolved",
            aggregate_type="claim_dispute",
            aggregate_id=dispute.id,
            payload={"outcome": outcome, "profile_id": dispute.profile_id},
            actor_account_id=operator_ctx.account_id,
        )
        audit.write_audit(
            session,
            action="admin_claim_dispute_resolved",
            actor_id=None,
            target_id=dispute.id,
            ip=operator_ctx.ip,
            detail={
                "outcome": outcome,
                "note": note,
                "break_glass": True,
                "operator_account": operator_ctx.account_id,
            },
        )
    return dispute
