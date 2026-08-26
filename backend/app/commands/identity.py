"""身份确档命令（F-1）：认领-确认合并转换与确档清单决议。

claim_and_confirm 是 PRD F-1 唯一允许的合并转换（本人确认自己）：首登
「这是我」一步同时完成 accounts.status managed→claimed 与本人 profile
provisional→identity_confirmed。D1 的两条状态机保持独立 —— 除本命令外，
任何路径都不得联动两条状态机；他建 provisional 档案永远不能被创建者代为确认。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import (
    CLAIM_DISPUTE_NOT_FOUND,
    IDENTITY_INVALID_TRANSITION,
    raise_api_error,
)
from app.models.user import User
from app.models.v2_foundation import ClaimDispute, ProfileFactReview
from app.services import audit, identity_fsm
from app.services.domain_events import emit
from app.utils.timeutil import utcnow


def claim_and_confirm_own_identity(session: Session, ctx: ActorContext) -> dict[str, bool]:
    """「这是我」合并转换：Account 认领（若未认领）+ 本人档案身份确认。

    返回实际发生的转换；两者均已完成时 409（无可做之事）。
    """
    actor = load_actor(session, ctx)
    with command_transaction(session):
        claimed = False
        confirmed = False
        if actor.account.status == identity_fsm.ACCOUNT_MANAGED:
            identity_fsm.claim_account(session, actor.account)
            claimed = True
            emit(
                session,
                event_type="account.claimed",
                aggregate_type="account",
                aggregate_id=actor.account.id,
                payload={"user_id": actor.id},
                actor_account_id=ctx.account_id,
            )
        if actor.profile_status == identity_fsm.PROFILE_PROVISIONAL:
            # 仅限本人自己的档案；created_by 等任何他人主体不可达此处
            identity_fsm.confirm_profile_identity(session, actor)
            confirmed = True
            emit(
                session,
                event_type="profile.identity_confirmed",
                aggregate_type="profile",
                aggregate_id=actor.id,
                payload={"confirmed_by_account": ctx.account_id},
                actor_account_id=ctx.account_id,
            )
        if not claimed and not confirmed:
            raise_api_error(
                409,
                IDENTITY_INVALID_TRANSITION,
                "身份已确认，无需重复操作",
            )
        audit.write_audit(
            session,
            action="identity_confirmed",
            actor_id=actor.id,
            target_id=actor.id,
            ip=ctx.ip,
            detail={"account_claimed": claimed, "profile_confirmed": confirmed},
        )
    return {"account_claimed": claimed, "profile_confirmed": confirmed}


def list_own_fact_reviews(session: Session, ctx: ActorContext) -> list[ProfileFactReview]:
    """本人的确档清单（含已决议历史）。"""
    load_actor(session, ctx)
    return list(
        session.scalars(
            select(ProfileFactReview)
            .where(ProfileFactReview.profile_id == ctx.user_id)
            .order_by(ProfileFactReview.id)
        ).all()
    )


def decide_fact_review(
    session: Session,
    ctx: ActorContext,
    review_id: int,
    *,
    decision: str,
    note: str | None = None,
) -> ProfileFactReview:
    """清单项决议：仅档案本人可操作；confirmed/disputed 单向终态。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        review = session.get(ProfileFactReview, review_id)
        if review is None or review.profile_id != actor.id:
            # 非本人清单项与不存在同一 404（防枚举）
            raise_api_error(404, CLAIM_DISPUTE_NOT_FOUND, "确档项不存在")
        if decision == identity_fsm.FACT_DISPUTED and note:
            # 争议附言并入 item_ref_json（证据原文保留，仅追加 reviewer 备注）
            review.item_ref_json = {**review.item_ref_json, "reviewer_note": note}
        identity_fsm.decide_fact_review(session, review, decision, actor.id)
        emit(
            session,
            event_type="profile.fact_review.decided",
            aggregate_type="profile",
            aggregate_id=actor.id,
            payload={"review_id": review.id, "item_type": review.item_type, "decision": decision},
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="fact_review_decided",
            actor_id=actor.id,
            target_id=review.id,
            ip=ctx.ip,
            detail={"decision": decision, "item_type": review.item_type},
        )
    return review


def eligible_for_recommendation(user: User) -> bool:
    """推荐资格查询辅助：identity_confirmed 且存活（AC-F2；推荐池本体属 V2.4）。"""
    return identity_fsm.recommendation_eligible(user)


def raise_claim_dispute(
    session: Session,
    ctx: ActorContext,
    *,
    profile_id: int,
    evidence: dict[str, Any],
) -> ClaimDispute:
    """发起认领争议：保留 evidence 原文；平台人工兜底走独立审计接口。"""
    load_actor(session, ctx)
    with command_transaction(session):
        dispute = ClaimDispute(
            profile_id=profile_id,
            raised_by_account_id=ctx.account_id,
            evidence_json=evidence,
            status="open",
            created_at=utcnow(),
        )
        session.add(dispute)
        session.flush()
        emit(
            session,
            event_type="claim_dispute.raised",
            aggregate_type="claim_dispute",
            aggregate_id=dispute.id,
            payload={"profile_id": profile_id},
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="claim_dispute_raised",
            actor_id=None,
            target_id=dispute.id,
            ip=ctx.ip,
            detail={"profile_id": profile_id, "by_account": ctx.account_id},
        )
    return dispute


def withdraw_claim_dispute(session: Session, ctx: ActorContext, dispute_id: int) -> ClaimDispute:
    """发起人撤回 open 争议。"""
    with command_transaction(session):
        dispute = session.get(ClaimDispute, dispute_id)
        if dispute is None or dispute.raised_by_account_id != ctx.account_id:
            raise_api_error(404, CLAIM_DISPUTE_NOT_FOUND, "争议不存在")
        if dispute.status != "open":
            from app.errors import DATA_RIGHT_INVALID_TRANSITION

            raise_api_error(409, DATA_RIGHT_INVALID_TRANSITION, "争议已处理")
        dispute.status = "withdrawn"
        dispute.resolved_at = utcnow()
        emit(
            session,
            event_type="claim_dispute.withdrawn",
            aggregate_type="claim_dispute",
            aggregate_id=dispute.id,
            payload={"profile_id": dispute.profile_id},
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="claim_dispute_withdrawn",
            actor_id=None,
            target_id=dispute.id,
            ip=ctx.ip,
            detail={"by_account": ctx.account_id},
        )
    return dispute
