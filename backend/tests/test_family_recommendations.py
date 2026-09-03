from __future__ import annotations

from conftest import create_user_with_pin

from app.models.space import FamilySpace, SpaceMember, SpaceProfileRef
from app.services import family_recommendations, personal_family_view
from app.utils.timeutil import utcnow


def _space(db, owner, member):
    space = FamilySpace(name="推荐空间", owner_id=owner.id, kind="lineage", created_at=utcnow())
    db.add(space)
    db.flush()
    for user in (owner, member):
        db.add(
            SpaceMember(
                space_id=space.id,
                user_id=user.id,
                added_by=owner.id,
                role="member",
                status="active",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        db.add(
            SpaceProfileRef(
                space_id=space.id,
                user_id=user.id,
                added_by=owner.id,
                status="active",
                created_at=utcnow(),
            )
        )
    db.commit()
    return space


def test_recommendations_empty_without_current_view(db_session):
    owner = create_user_with_pin(db_session, "甲", "111111", claim_status="claimed")
    other = create_user_with_pin(db_session, "乙", "222222", claim_status="claimed")
    space = _space(db_session, owner, other)
    payload = family_recommendations.recommendations_payload(
        db_session, account=owner.account, space_id=space.id
    )
    assert payload["view_status"] == "never_computed"
    assert payload["items"] == []
    assert db_session.query(personal_family_view.PersonalFamilyView).count() == 0


def test_dismiss_requires_existing_recommendation(db_session):
    owner = create_user_with_pin(db_session, "甲", "111111", claim_status="claimed")
    other = create_user_with_pin(db_session, "乙", "222222", claim_status="claimed")
    space = _space(db_session, owner, other)
    try:
        family_recommendations.dismiss_recommendation(
            db_session,
            account=owner.account,
            space_id=space.id,
            target_user_id=other.id,
            category="confirmed_kinship",
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    else:
        raise AssertionError("expected safe 404")
