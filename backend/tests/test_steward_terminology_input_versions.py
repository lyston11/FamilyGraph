"""Raw terminology producers fence views without turning audit writes into work."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent_provider import AgentSpaceProviderSetting
from app.models.platform_features import PlatformFeatureConfig
from app.models.steward import StewardTermProjection, StewardTermSuppression
from app.models.term_registry import TermEntry, TermUsage
from app.models.v2_foundation import DomainEvent
from app.services import steward_snapshot
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture


def _versions(db: Session, space_id: int):
    with steward_snapshot.read_transaction(db.get_bind()) as reader:
        return steward_snapshot.input_versions(reader, space_id)


def _sources(db: Session):
    user, space = create_agent_fixture(db, name="terminology-input")
    now = utcnow()
    entry = TermEntry(
        concept_code="Uf-Uf",
        level="space",
        space_id=space.id,
        term="姥姥",
        status="active",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(entry)
    db.commit()
    return space.id, {
        "usage": (
            TermUsage(
                term_entry_id=entry.id,
                account_id=user.account.id,
                profile_id=user.id,
                space_id=space.id,
                source_event="manual_select",
                created_at=now,
            ),
            "created_at",
            now + timedelta(seconds=1),
        ),
        "suppression": (
            StewardTermSuppression(
                viewer_account_id=user.account.id,
                space_id=space.id,
                target_user_id=user.id,
                suppression_key="a" * 64,
                created_at=now,
            ),
            "suppression_key",
            "b" * 64,
        ),
        "projection": (
            StewardTermProjection(
                space_id=space.id,
                viewer_account_id=user.account.id,
                root_user_id=user.id,
                target_user_id=user.id,
                concept_code="Uf-Uf",
                semantic_hash="a" * 64,
                baseline_term="外婆",
                baseline_source="locale",
                term="姥姥",
                origin="deterministic",
                status="active",
                revision=1,
                rule_version="terminology-v2",
                created_at=now,
                updated_at=now,
            ),
            "term",
            "外婆",
        ),
        "space_switch": (
            AgentSpaceProviderSetting(
                space_id=space.id,
                agent_kind="steward",
                enabled=True,
                assist_terminology=True,
            ),
            "assist_terminology",
            False,
        ),
        "platform_switch": (
            PlatformFeatureConfig(id=1, steward_assist_terminology=True, updated_at=now),
            "steward_assist_terminology",
            False,
        ),
    }


@pytest.mark.parametrize(
    "source", ["usage", "suppression", "projection", "space_switch", "platform_switch"]
)
@pytest.mark.parametrize("event", ["insert", "update", "delete"])
def test_committed_terminology_input_invalidates_display_only(db_session, source, event):
    space_id, sources = _sources(db_session)
    row, field, changed = sources[source]
    if event != "insert":
        db_session.add(row)
        db_session.commit()
    before = _versions(db_session, space_id)
    events = db_session.scalar(select(func.count()).select_from(DomainEvent))
    if event == "insert":
        db_session.add(row)
    elif event == "update":
        setattr(row, field, changed)
    else:
        db_session.delete(row)
    db_session.commit()
    after = _versions(db_session, space_id)
    scope = "global" if source == "platform_switch" else "space"
    for key in ("global", "space"):
        assert after[key][0] == before[key][0]
        # The same setting row also controls the existing inferred overlay.
        inferred_delta = int(key == "space" and source == "space_switch" and event != "update")
        assert after[key][2] == before[key][2] + inferred_delta
        assert after[key][1] == before[key][1] + int(key == scope)
    assert db_session.scalar(select(func.count()).select_from(DomainEvent)) == events
    with steward_snapshot.read_transaction(db_session.get_bind()) as reader:
        assert not steward_snapshot.versions_match(reader, space_id=space_id, expected=before)


def test_projection_attempt_bookkeeping_does_not_invalidate_a_valid_display(db_session):
    space_id, sources = _sources(db_session)
    row = sources["projection"][0]
    db_session.add(row)
    db_session.commit()
    before = _versions(db_session, space_id)
    row.request_hash = "b" * 64
    row.last_checked_hash = "c" * 64
    row.last_attempt_at = utcnow()
    row.last_attempt_status = "succeeded"
    row.source_model_call_id = 123
    row.updated_at = utcnow()
    db_session.commit()
    assert _versions(db_session, space_id) == before


def test_baseline_only_projection_never_invalidates_views(db_session):
    space_id, sources = _sources(db_session)
    row = sources["projection"][0]
    row.term = None
    before = _versions(db_session, space_id)
    db_session.add(row)
    db_session.commit()
    row.baseline_term = "祖母"
    row.semantic_hash = "d" * 64
    row.revision += 1
    db_session.commit()
    db_session.delete(row)
    db_session.commit()
    assert _versions(db_session, space_id) == before


def test_removing_a_display_override_invalidates_views(db_session):
    space_id, sources = _sources(db_session)
    row = sources["projection"][0]
    db_session.add(row)
    db_session.commit()
    before = _versions(db_session, space_id)
    row.term = None
    db_session.commit()
    assert _versions(db_session, space_id)["space"][1] == before["space"][1] + 1
