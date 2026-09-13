from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models.space import FamilySpace
from app.services import personal_family_bridge, personal_family_view
from app.services import source_facts as sf
from app.utils.timeutil import utcnow
from conftest import create_space_member, create_user_with_pin


def _lineage_space(session, owner_id: int, name: str) -> FamilySpace:
    space = FamilySpace(name=name, kind="lineage", owner_id=owner_id, created_at=utcnow())
    session.add(space)
    session.flush()
    create_space_member(session, space.id, owner_id)
    return space


def _confirmed_link(session, space_id: int, left: int, right: int) -> None:
    fact = sf.create_source_fact(
        session,
        fact_type="spouse",
        subject_user_id=left,
        object_user_id=right,
        provenance="manual_entry",
        space_id=space_id,
    )
    sf.transition_source_fact(session, fact, "confirm")


def test_bridge_requires_both_claimed_people_and_uses_cas(db_session) -> None:
    left = create_user_with_pin(db_session, "bridge-left", "123456", claim_status="claimed")
    right = create_user_with_pin(db_session, "bridge-right", "123456", claim_status="claimed")
    left_space = _lineage_space(db_session, left.id, "left-lineage")
    right_space = _lineage_space(db_session, right.id, "right-lineage")
    _confirmed_link(db_session, left_space.id, left.id, right.id)

    bridge = personal_family_bridge.create_bridge(
        db_session,
        account=left.account,
        anchor_user_id=left.id,
        other_space_id=right_space.id,
        other_anchor_user_id=right.id,
        scope={"mode": "anchor_paths"},
    )
    assert bridge.status == "pending"
    assert bridge.consent_b_account_id is None

    active = personal_family_bridge.consent_bridge(
        db_session, bridge_id=bridge.id, account=right.account, revision=bridge.revision
    )
    assert active.status == "active"
    assert active.revision == 2
    personal_family_view.rebuild_view(db_session, account=left.account, space_id=left_space.id)
    payload = personal_family_view.view_payload(
        db_session, account=left.account, space_id=left_space.id
    )
    assert right.id in {node["user_id"] for node in payload["nodes"]}
    assert any(edge["to_user_id"] == right.id for edge in payload["edges"])
    personal_family_bridge.revoke_bridge(
        db_session, bridge_id=bridge.id, account=right.account, revision=active.revision
    )
    revoked_payload = personal_family_view.view_payload(
        db_session, account=left.account, space_id=left_space.id
    )
    assert right.id not in {node["user_id"] for node in revoked_payload["nodes"]}


def test_unclaimed_anchor_cannot_activate_bridge(db_session) -> None:
    left = create_user_with_pin(db_session, "bridge-managed-left", "123456", claim_status="claimed")
    right = create_user_with_pin(
        db_session, "bridge-managed-right", "123456", claim_status="managed"
    )
    left_space = _lineage_space(db_session, left.id, "managed-left-lineage")
    right_space = _lineage_space(db_session, right.id, "managed-right-lineage")
    _confirmed_link(db_session, left_space.id, left.id, right.id)
    bridge = personal_family_bridge.create_bridge(
        db_session,
        account=left.account,
        anchor_user_id=left.id,
        other_space_id=right_space.id,
        other_anchor_user_id=right.id,
        scope={"mode": "anchor_paths"},
    )
    with pytest.raises(HTTPException) as exc_info:
        personal_family_bridge.consent_bridge(
            db_session, bridge_id=bridge.id, account=right.account, revision=bridge.revision
        )
    assert exc_info.value.status_code == 403
