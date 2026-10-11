"""Shared attachment read projection for browser and Agent paths."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.user import User
from app.services import custody
from app.services.disclosure import disclosed_categories
from app.services.space_fsm import is_active_member
from app.services.visibility import (
    LEVEL_HOUSEHOLD_DETAIL,
    LEVEL_LINEAGE_SUMMARY,
    LEVEL_SELF_PRIVATE,
    evaluate,
)


def readable_attachment_types(
    session: Session,
    *,
    viewer: User,
    account: Account,
    target: User,
    space_id: int | None,
) -> frozenset[str]:
    """Return image/link types visible in one explicit viewer/space context.

    This is deliberately a projection, not a new authorization policy: browser
    attachment routes and Agent grant rechecks share the same `visibility`
    decision and disclosure categories. A failed context simply returns no types
    so callers can expose their own safe 404/empty semantics.
    """
    if space_id is not None:
        if not is_active_member(session, viewer.id, space_id):
            return frozenset()
    elif not custody.resolve_relation(viewer, target).edit:
        return frozenset()
    decision = evaluate(session, viewer, target, space_context=space_id)
    if decision.level == LEVEL_SELF_PRIVATE:
        return frozenset({"image", "link"})
    if target.profile_status == "provisional" or decision.source == "pending":
        return frozenset()
    if decision.level == LEVEL_HOUSEHOLD_DETAIL:
        return frozenset({"image", "link"})
    if decision.level == LEVEL_LINEAGE_SUMMARY:
        categories = disclosed_categories(session, target, space_id)
        return frozenset(
            kind
            for kind, category in (("image", "photos"), ("link", "attachments"))
            if category in categories
        )
    return frozenset()


__all__ = ["readable_attachment_types"]
