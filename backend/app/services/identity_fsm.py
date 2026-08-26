"""身份与确档状态机（v2 Foundation，spec/architecture.md §0.3）。

三条独立单向状态机，转换点唯一且审计由调用方负责：
- Account：managed → claimed（唯一转换点 = 首登强制改 PIN 完成，PUT /me/pin）
- Profile：provisional → identity_confirmed（首登确认"这是我"流程）
- 外部事实（profile_fact_reviews 清单项）：proposed → confirmed | disputed

Account claimed 不自动确认 Profile；三条状态各自独立完成。
未完成 identity_confirmed 的人物不具备推荐资格（recommendation_eligible）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.errors import IDENTITY_INVALID_TRANSITION, VALIDATION_ERROR, raise_api_error
from app.models.account import Account
from app.models.user import User
from app.models.v2_foundation import ProfileFactReview
from app.utils.timeutil import utcnow

ACCOUNT_MANAGED = "managed"
ACCOUNT_CLAIMED = "claimed"

PROFILE_PROVISIONAL = "provisional"
PROFILE_IDENTITY_CONFIRMED = "identity_confirmed"

FACT_PROPOSED = "proposed"
FACT_CONFIRMED = "confirmed"
FACT_DISPUTED = "disputed"


def claim_account(session: Session, account: Account) -> Account:
    """Account managed → claimed 单向转换；重复认领 409。"""
    if account.status != ACCOUNT_MANAGED:
        raise_api_error(
            409,
            IDENTITY_INVALID_TRANSITION,
            "账号当前状态不允许认领",
            detail={"status": account.status},
        )
    account.status = ACCOUNT_CLAIMED
    account.claimed_at = utcnow()
    session.flush()
    return account


def confirm_profile_identity(session: Session, user: User) -> User:
    """Profile provisional → identity_confirmed 单向转换；已确认 409。"""
    if user.profile_status != PROFILE_PROVISIONAL:
        raise_api_error(
            409,
            IDENTITY_INVALID_TRANSITION,
            "档案当前状态不允许确认",
            detail={"profile_status": user.profile_status},
        )
    user.profile_status = PROFILE_IDENTITY_CONFIRMED
    user.profile_confirmed_at = utcnow()
    session.flush()
    return user


def decide_fact_review(
    session: Session,
    review: ProfileFactReview,
    decision: str,
    decided_by_user_id: int,
) -> ProfileFactReview:
    """清单项 proposed → confirmed | disputed；终态不可再转。"""
    if decision not in (FACT_CONFIRMED, FACT_DISPUTED):
        raise_api_error(422, VALIDATION_ERROR, f"未知决议 {decision}")
    if review.status != FACT_PROPOSED:
        raise_api_error(
            409,
            IDENTITY_INVALID_TRANSITION,
            "该确档项已决议",
            detail={"status": review.status},
        )
    review.status = decision
    review.decided_by = decided_by_user_id
    review.decided_at = utcnow()
    session.flush()
    return review


def recommendation_eligible(user: User) -> bool:
    """推荐资格：identity_confirmed 且存活。provisional 恒为 False（AC-F2）。"""
    return user.deleted_at is None and user.profile_status == PROFILE_IDENTITY_CONFIRMED
