from __future__ import annotations

import pytest
from conftest import create_agent_fixture, create_space_member, create_user_with_pin
from fastapi import HTTPException
from sqlalchemy import select

from app.models.personal_family_view import PersonalFamilyViewNode
from app.services import personal_family_view
from app.services import source_facts as sf


def _confirm(session, fact_type: str, subject_id: int, object_id: int, space_id: int) -> None:
    fact = sf.create_source_fact(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        provenance="manual_entry",
        space_id=space_id,
    )
    sf.transition_source_fact(session, fact, "confirm")


def test_personal_family_view_contains_only_confirmed_reachable_people(db_session) -> None:
    _account, space = create_agent_fixture(db_session, name="pfv")
    viewer = create_user_with_pin(db_session, "pfv-viewer", "123456", gender="f")
    parent = create_user_with_pin(db_session, "pfv-parent", "123456", gender="m")
    unrelated = create_user_with_pin(db_session, "pfv-unrelated", "123456", gender="f")
    for user in (viewer, parent, unrelated):
        create_space_member(db_session, space.id, user.id)
    _confirm(db_session, "biological_parent", parent.id, viewer.id, space.id)

    view = personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
    node_ids = set(
        db_session.scalars(
            select(PersonalFamilyViewNode.user_id).where(PersonalFamilyViewNode.view_id == view.id)
        ).all()
    )
    assert node_ids == {viewer.id, parent.id}
    payload = personal_family_view.view_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    assert payload["status"] == "current"
    assert payload["edges"][0]["concept_code"] == "Um"
    assert unrelated.id not in node_ids


def test_personal_family_view_requires_active_membership(db_session) -> None:
    _account, space = create_agent_fixture(db_session, name="pfv-denied")
    viewer = create_user_with_pin(db_session, "pfv-denied-viewer", "123456", gender="f")
    with pytest.raises(HTTPException) as exc_info:
        personal_family_view.get_view(db_session, account=viewer.account, space_id=space.id)
    assert exc_info.value.status_code == 404
