"""MR-26: rebuilding card/term projections must preserve independent state."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models.space import FamilySpace
from app.models.steward import BehaviorProjection
from app.models.user import User
from app.services import (
    domain_events,
    family_recommendations,
    personal_family_view,
    source_facts,
    steward,
)
from conftest import create_space_member, create_user_with_pin, seed_space_with_owner

_ProjectionKey = tuple[int, int, str]
_ProjectionSnapshot = dict[_ProjectionKey, tuple[int, dict[str, Any], datetime]]


@pytest.fixture()
def projection_time(monkeypatch: pytest.MonkeyPatch) -> datetime:
    moment = datetime(2026, 9, 14, 12)
    monkeypatch.setattr(config, "BEHAVIOR_PROJECTION_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_COOLDOWN_DAYS", 7)
    for module in (
        domain_events,
        family_recommendations,
        personal_family_view,
        source_facts,
        steward,
    ):
        monkeypatch.setattr(module, "utcnow", lambda: moment)
    return moment


def _space(session: Session, owner: User, *members: User, name: str) -> FamilySpace:
    space = seed_space_with_owner(session, owner.id, name=name)
    for member in members:
        create_space_member(session, space.id, member.id)
    return space


def _snapshot(session: Session) -> _ProjectionSnapshot:
    # Read columns so a deleted ORM object's identity-map state cannot hide data loss.
    rows = session.execute(
        select(
            BehaviorProjection.id,
            BehaviorProjection.space_id,
            BehaviorProjection.account_id,
            BehaviorProjection.projection_key,
            BehaviorProjection.value_json,
            BehaviorProjection.updated_at,
        )
    )
    return {
        (row.space_id, row.account_id, row.projection_key): (
            row.id,
            deepcopy(row.value_json),
            row.updated_at,
        )
        for row in rows
    }


def _seed_owned_cache(
    session: Session, *, space_id: int, account_id: int, now: datetime
) -> set[str]:
    """Seed stale values, including a key with no corresponding replay event."""
    prior = now - timedelta(days=3)
    steward.set_kind_cooldown(
        session, space_id=space_id, account_id=account_id, kind="household_link", now=prior
    )
    values = {
        "correction_preference:mother": {"entry_id": 999, "updated_at": prior.isoformat()},
        "term_usage:mother": {"count": 99, "updated_at": prior.isoformat()},
        "term_usage:obsolete": {"count": 1},
    }
    for key, value in values.items():
        steward.put_projection(
            session,
            space_id=space_id,
            account_id=account_id,
            projection_key=key,
            value=value,
            now=prior,
        )
    return {"card_cooldown:household_link", *values}


def _emit_owned_events(
    session: Session, *, space_id: int, account_id: int, now: datetime
) -> dict[str, dict[str, Any]]:
    specs = (
        ("card.dismissed", {"kind": "household_link"}),
        (
            "term.personal_updated",
            {"concept_code": "mother", "entry_id": 101, "account_id": account_id},
        ),
        ("term.usage_recorded", {"concept_code": "mother"}),
        ("term.usage_recorded", {"concept_code": "mother"}),
    )
    for event_type, payload in specs:
        domain_events.emit(
            session,
            event_type=event_type,
            aggregate_type="action_card" if event_type.startswith("card.") else "term_entry",
            aggregate_id=101,
            payload=payload,
            space_id=space_id,
            actor_account_id=account_id,
        )
    return {
        "card_cooldown:household_link": {
            "until": (now + timedelta(days=config.STEWARD_COOLDOWN_DAYS)).isoformat()
        },
        "correction_preference:mother": {"entry_id": 101, "updated_at": now.isoformat()},
        "term_usage:mother": {"count": 2, "updated_at": now.isoformat()},
    }


@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "enabled"])
@pytest.mark.parametrize("scope", ["account", "space"])
def test_rebuild_preserves_real_recommendation_dismissals(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    projection_time: datetime,
    enabled: bool,
    scope: str,
) -> None:
    actor = create_user_with_pin(db_session, "rebuild-actor", "123456", gender="m")
    observer = create_user_with_pin(db_session, "rebuild-observer", "123456")
    parent = create_user_with_pin(db_session, "rebuild-parent", "123456", gender="f")
    space = _space(db_session, actor, observer, parent, name="recommendation-rebuild")
    for child in (actor, observer):
        fact = source_facts.create_source_fact(
            db_session,
            fact_type="biological_parent",
            subject_user_id=parent.id,
            object_user_id=child.id,
            provenance="manual_entry",
            space_id=space.id,
        )
        source_facts.transition_source_fact(db_session, fact, "confirm")
    _seed_owned_cache(
        db_session, space_id=space.id, account_id=actor.account.id, now=projection_time
    )
    expected_owned = _emit_owned_events(
        db_session, space_id=space.id, account_id=actor.account.id, now=projection_time
    )
    db_session.commit()

    # Both cooldowns come from the real producer; the observer has no replayable events.
    for viewer in (actor, observer):
        personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
        payload = family_recommendations.recommendations_payload(
            db_session, account=viewer.account, space_id=space.id
        )
        assert payload["view_status"] == "current"
        assert any(item["target_user_id"] == parent.id for item in payload["items"])
        family_recommendations.dismiss_recommendation(
            db_session,
            account=viewer.account,
            space_id=space.id,
            target_user_id=parent.id,
            category="confirmed_kinship",
        )
    db_session.commit()
    before = _snapshot(db_session)
    dismissal_key = f"{family_recommendations.DISMISS_PREFIX}{parent.id}"
    dismissals = {
        (space.id, viewer.account.id, dismissal_key): before[
            (space.id, viewer.account.id, dismissal_key)
        ]
        for viewer in (actor, observer)
    }
    # Disabled cases represent persisted state created before the flag was turned off.
    monkeypatch.setattr(config, "BEHAVIOR_PROJECTION_ENABLED", enabled)

    for hours in (1, 2):
        rebuild_at = projection_time + timedelta(hours=hours)
        count = steward.rebuild_behavior_projections(
            db_session,
            space_id=space.id,
            account_id=actor.account.id if scope == "account" else None,
            now=rebuild_at,
        )
        db_session.commit()
        after = _snapshot(db_session)
        assert count == (4 if enabled else 0)  # Four events produce three owned rows.
        for viewer in (actor, observer):
            payload = family_recommendations.recommendations_payload(
                db_session, account=viewer.account, space_id=space.id
            )
            assert payload["view_status"] == "current"
            assert all(item["target_user_id"] != parent.id for item in payload["items"])
        assert {key: after.get(key) for key in dismissals} == dismissals
        if enabled:
            owned = {
                key[2]: row[1]
                for key, row in after.items()
                if key[:2] == (space.id, actor.account.id) and key[2] != dismissal_key
            }
            assert owned == expected_owned
        else:
            assert after == before
        assert (
            steward.kind_in_cooldown(
                db_session,
                space_id=space.id,
                account_id=actor.account.id,
                kind="household_link",
                now=rebuild_at,
            )
            is enabled
        )


@pytest.mark.parametrize("scope", ["account", "space"])
def test_rebuild_without_matching_events_preserves_foreign_rows(
    db_session: Session, projection_time: datetime, scope: str
) -> None:
    actor = create_user_with_pin(db_session, "empty-actor", "123456")
    observer = create_user_with_pin(db_session, "empty-observer", "123456")
    space = _space(db_session, actor, observer, name="empty-rebuild")
    other_space = _space(db_session, actor, name="untouched-space")
    foreign_keys = (
        "future_feature:kept",
        "CARD_COOLDOWN:household_link",
        "Term_usage:mother",
        "cardXcooldown:household_link",
        "correctionXpreference:mother",
        "termXusage:mother",
        "card_cooldown",
        "term_usage_extra:mother",
    )
    removed_keys: set[_ProjectionKey] = set()
    for space_id, account_id in (
        (space.id, actor.account.id),
        (space.id, observer.account.id),
        (other_space.id, actor.account.id),
    ):
        owned_keys = _seed_owned_cache(
            db_session, space_id=space_id, account_id=account_id, now=projection_time
        )
        if space_id == space.id and (scope == "space" or account_id == actor.account.id):
            removed_keys.update((space_id, account_id, key) for key in owned_keys)
        # Existing foreign/legacy state is not constrained by this service's write allowlist.
        for key in foreign_keys:
            db_session.add(
                BehaviorProjection(
                    space_id=space_id,
                    account_id=account_id,
                    projection_key=key,
                    value_json={"version": 2, "source": [space_id, account_id, key]},
                    updated_at=projection_time - timedelta(days=5),
                )
            )
    domain_events.emit(
        db_session,
        event_type="card.viewed",
        aggregate_type="action_card",
        aggregate_id=101,
        payload={"kind": "household_link"},
        space_id=space.id,
        actor_account_id=actor.account.id,
    )
    domain_events.emit(
        db_session,
        event_type="term.usage_recorded",
        aggregate_type="term_entry",
        aggregate_id=101,
        payload={"concept_code": "mother"},
        space_id=space.id,
        actor_account_id=None,
    )
    db_session.commit()
    before = _snapshot(db_session)
    expected = {key: row for key, row in before.items() if key not in removed_keys}

    for hours in (1, 2):
        assert (
            steward.rebuild_behavior_projections(
                db_session,
                space_id=space.id,
                account_id=actor.account.id if scope == "account" else None,
                now=projection_time + timedelta(hours=hours),
            )
            == 0
        )
        db_session.commit()
        assert _snapshot(db_session) == expected


@pytest.mark.parametrize("scope", ["account", "space"])
def test_rebuild_replays_scoped_valid_events_idempotently(
    db_session: Session, projection_time: datetime, scope: str
) -> None:
    actor = create_user_with_pin(db_session, "replay-actor", "123456")
    observer = create_user_with_pin(db_session, "replay-observer", "123456")
    space = _space(db_session, actor, observer, name="event-rebuild")
    other_space = _space(db_session, actor, name="other-events")
    rebuilt_scopes = {(space.id, actor.account.id)}
    if scope == "space":
        rebuilt_scopes.add((space.id, observer.account.id))
    expected: dict[_ProjectionKey, dict[str, Any]] = {}
    for space_id, account_id in (
        (space.id, actor.account.id),
        (space.id, observer.account.id),
        (other_space.id, actor.account.id),
    ):
        _seed_owned_cache(db_session, space_id=space_id, account_id=account_id, now=projection_time)
        values = _emit_owned_events(
            db_session, space_id=space_id, account_id=account_id, now=projection_time
        )
        if (space_id, account_id) in rebuilt_scopes:
            expected.update({(space_id, account_id, key): value for key, value in values.items()})
    for event_type, payload, event_space, event_actor in (
        ("card.dismissed", {"kind": "unknown_kind"}, space.id, actor.account.id),
        (
            "term.personal_updated",
            {"concept_code": "mother", "entry_id": "101"},
            space.id,
            actor.account.id,
        ),
        ("term.usage_recorded", {"concept_code": 101}, space.id, actor.account.id),
        ("card.viewed", {"kind": "household_link"}, space.id, actor.account.id),
        ("term.usage_recorded", {"concept_code": "mother"}, space.id, None),
        ("term.usage_recorded", {"concept_code": "mother"}, None, actor.account.id),
    ):
        domain_events.emit(
            db_session,
            event_type=event_type,
            aggregate_type="action_card" if event_type.startswith("card.") else "term_entry",
            aggregate_id=101,
            payload=payload,
            space_id=event_space,
            actor_account_id=event_actor,
        )
    db_session.commit()
    before = _snapshot(db_session)
    untouched = {key: row for key, row in before.items() if key[:2] not in rebuilt_scopes}
    replay_values = []

    for hours in (1, 2):
        rebuild_at = projection_time + timedelta(hours=hours)
        count = steward.rebuild_behavior_projections(
            db_session,
            space_id=space.id,
            account_id=actor.account.id if scope == "account" else None,
            now=rebuild_at,
        )
        db_session.commit()
        after = _snapshot(db_session)
        assert count == 4 * len(rebuilt_scopes)
        assert {
            key: row for key, row in after.items() if key[:2] not in rebuilt_scopes
        } == untouched
        rebuilt = {key: row for key, row in after.items() if key[:2] in rebuilt_scopes}
        values = {key: row[1] for key, row in rebuilt.items()}
        assert values == expected
        assert all(row[2] == rebuild_at for row in rebuilt.values())
        replay_values.append(values)
    assert replay_values[0] == replay_values[1]
