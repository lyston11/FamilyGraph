"""Source changes without events still fence published and optional PFV output.

Local facts/membership/name/birth/gender and structural cache reuse are covered
by the snapshot-fence and relationship-compute suites. These cases exercise the
remaining producer boundaries with separately committed database connections.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app import config
from app.models.account import Account
from app.models.agent_provider import AgentSpaceProviderSetting
from app.models.personal_family_view import PersonalFamilyBridge
from app.models.relationship_facts import SourceFact
from app.models.space import SpaceMember, SpaceProfileRef
from app.models.steward import StewardGeneration, StewardInferredOverlay, StewardPublication
from app.models.steward_inferred import StewardInferredEdge
from app.models.term_registry import TermEntry
from app.models.user import User
from app.models.v2_foundation import DisclosurePreference, DomainEvent
from app.services import (
    steward,
    steward_delivery,
    steward_inferred,
    steward_overlay,
    steward_pipeline,
    steward_runtime,
    steward_snapshot,
    steward_views,
    visibility,
)
from app.services.relationship_resolver import advance_search
from app.services.source_facts import create_source_fact, transition_source_fact
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture, create_space_member, create_user_with_pin


@dataclass(frozen=True)
class Family:
    viewer_id: int
    account_id: int
    space_id: int
    target_id: int
    ref_id: int
    disclosure_id: int
    term_id: int


def _ref(session: Session, *, space_id: int, user_id: int) -> SpaceProfileRef:
    row = SpaceProfileRef(space_id=space_id, user_id=user_id, status="active", created_at=utcnow())
    session.add(row)
    session.flush()
    return row


def _parent(session: Session, *, space_id: int, parent_id: int, child_id: int) -> SourceFact:
    row = create_source_fact(
        session,
        fact_type="biological_parent",
        subject_user_id=parent_id,
        object_user_id=child_id,
        provenance="manual_entry",
        space_id=space_id,
    )
    transition_source_fact(session, row, "confirm")
    session.commit()
    return row


@pytest.fixture()
def family(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Family:
    # These tests exercise database fences, not process scheduling. Keep the
    # actual search slices while avoiding an unrelated child process per case.
    monkeypatch.setattr(
        steward_runtime, "run_slice", lambda state: advance_search(state, max_expansions=64)
    )
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    # Jobs are driven directly, but the read-side liveness contract must allow
    # retrying invalid input instead of reporting a stopped worker terminally.
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    viewer, space = create_agent_fixture(db_session, name="input-versions-viewer")
    target = create_user_with_pin(
        db_session, "input-versions-child", "123456", gender="f", bio="Disclosed biography"
    )
    space.kind = "lineage"
    ref = _ref(db_session, space_id=space.id, user_id=target.id)
    disclosure = DisclosurePreference(
        profile_id=target.id,
        category="bio",
        scope="space",
        space_id=space.id,
        allowed=True,
        updated_at=utcnow(),
    )
    term = TermEntry(
        concept_code="Df",
        level="personal",
        owner_account_id=viewer.account.id,
        term="闺女",
        status="active",
        revision=1,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db_session.add_all([disclosure, term])
    db_session.commit()
    _parent(db_session, space_id=space.id, parent_id=viewer.id, child_id=target.id)
    return Family(viewer.id, viewer.account.id, space.id, target.id, ref.id, disclosure.id, term.id)


def _versions(bind: Engine | Connection, family: Family) -> dict[str, Any]:
    with steward_snapshot.read_transaction(bind) as reader:
        return steward_snapshot.input_versions(reader, family.space_id)


def _event_count(bind: Engine | Connection) -> int:
    with Session(bind=bind) as reader:
        return int(reader.scalar(select(func.count()).select_from(DomainEvent)) or 0)


def _payload(bind: Engine | Connection, family: Family) -> tuple[dict[str, Any], datetime | None]:
    with steward_snapshot.read_transaction(bind) as reader:
        account = reader.get(Account, family.account_id)
        assert account is not None
        payload, valid_until = steward_views.payload_for(
            reader, account=account, space_id=family.space_id, progressive=True
        )
        assert payload is not None
        return payload, valid_until


def _publish(session: Session, family: Family) -> int:
    job = steward.lease_next_steward_job(
        session, leased_by="input-versions-worker", space_id=family.space_id, ttl_seconds=120
    )
    assert job is not None
    summary = steward.run_steward_job(
        session,
        job,
        worker_id="input-versions-worker",
        expected_attempt=job.attempt,
        drain_delivery=False,
    )
    payload, valid_until = _payload(session.get_bind(), family)
    assert payload["status"] == "current" and valid_until is not None
    assert family.target_id in {node["user_id"] for node in payload["nodes"]}
    return int(summary["generation_id"])


def _assert_old_input_rejected(
    bind: Engine | Connection, family: Family, before: dict[str, Any], events: int
) -> None:
    assert _event_count(bind) == events
    with steward_snapshot.read_transaction(bind) as reader:
        assert not steward_snapshot.versions_match(
            reader, space_id=family.space_id, expected=before
        )
    with pytest.raises(steward_snapshot.SnapshotChanged):
        steward_snapshot.read_viewer(
            bind,
            space_id=family.space_id,
            account_id=family.account_id,
            expected_versions=before,
        )
    payload, valid_until = _payload(bind, family)
    assert payload["status"] == "stale" and valid_until is None
    assert payload["stale_reason"] == payload["progress"]["reason_code"] == "input_changed"
    assert payload["progress"]["phase"] == "retrying"
    assert payload["progress"]["next_poll_ms"] > 0
    assert (
        payload["nodes"]
        == payload["edges"]
        == payload["inferred_edges"]
        == payload["topology_edges"]
        == []
    )


@pytest.mark.parametrize(
    "change", ["biography", "disclosure", "profile_ref", "token_version", "term", "soft_delete"]
)
def test_source_commit_without_domain_event_invalidates_published_output(
    db_session: Session, family: Family, change: str
) -> None:
    _publish(db_session, family)
    bind = db_session.get_bind()
    before, events = _versions(bind, family), _event_count(bind)
    # Raw writes intentionally bypass domain-event scheduling and ORM hooks.
    with Session(bind=bind) as writer:
        if change == "biography":
            writer.execute(
                update(User).where(User.id == family.target_id).values(bio="Updated biography")
            )
        elif change == "disclosure":
            writer.execute(
                delete(DisclosurePreference).where(DisclosurePreference.id == family.disclosure_id)
            )
        elif change == "profile_ref":
            writer.execute(
                update(SpaceProfileRef)
                .where(SpaceProfileRef.id == family.ref_id)
                .values(status="removed")
            )
        elif change == "token_version":
            writer.execute(
                update(Account)
                .where(Account.id == family.account_id)
                .values(token_version=Account.token_version + 1)
            )
        elif change == "term":
            # Content is an input even if a non-event producer omits revision++.
            writer.execute(
                update(TermEntry).where(TermEntry.id == family.term_id).values(term="女儿")
            )
        else:
            writer.execute(
                update(User).where(User.id == family.target_id).values(deleted_at=utcnow())
            )
        writer.commit()
    _assert_old_input_rejected(bind, family, before, events)
    if change == "disclosure":
        current = steward_snapshot.read_viewer(
            bind,
            space_id=family.space_id,
            account_id=family.account_id,
            expected_versions=_versions(bind, family),
        )
        node = next(
            node for node in json.loads(current.nodes_json) if node["user_id"] == family.target_id
        )
        assert node["display"]["bio"] == visibility.MASKED


def _bridge(session: Session, family: Family) -> tuple[int, int, int, datetime]:
    anchor, space = create_agent_fixture(session, name="input-versions-remote")
    relative = create_user_with_pin(session, "input-versions-remote-child", "123456")
    create_space_member(session, space.id, relative.id)
    space.kind = "lineage"
    fact = _parent(session, space_id=space.id, parent_id=anchor.id, child_id=relative.id)
    now = utcnow()
    expires = now + timedelta(minutes=1)
    bridge = PersonalFamilyBridge(
        lineage_space_a_id=family.space_id,
        lineage_space_b_id=space.id,
        anchor_a_user_id=family.viewer_id,
        anchor_b_user_id=anchor.id,
        normalized_pair_key=f"input-versions:{family.viewer_id}:{anchor.id}",
        initiated_by_account_id=family.account_id,
        consent_a_account_id=family.account_id,
        consent_b_account_id=anchor.account.id,
        consent_a_at=now,
        consent_b_at=now,
        scope_json={"mode": "anchor_paths"},
        status="active",
        revision=1,
        expires_at=expires,
        created_at=now,
        updated_at=now,
    )
    session.add(bridge)
    session.commit()
    return space.id, relative.id, fact.id, expires


@pytest.mark.parametrize("change", ["remote_fact", "remote_membership"])
def test_remote_bridge_source_commit_fences_the_local_publication(
    db_session: Session, family: Family, change: str
) -> None:
    remote_space, relative, fact, _expires = _bridge(db_session, family)
    _publish(db_session, family)
    bind = db_session.get_bind()
    payload, _ = _payload(bind, family)
    assert relative in {node["user_id"] for node in payload["nodes"]}
    before, events = _versions(bind, family), _event_count(bind)
    with Session(bind=bind) as writer:
        if change == "remote_fact":
            writer.execute(
                update(SourceFact)
                .where(SourceFact.id == fact)
                .values(revision=SourceFact.revision + 1)
            )
        else:
            writer.execute(
                update(SpaceMember)
                .where(SpaceMember.space_id == remote_space, SpaceMember.user_id == relative)
                .values(status="removed")
            )
        writer.commit()
    _assert_old_input_rejected(bind, family, before, events)


def test_bridge_time_expiry_rejects_published_output_without_any_write(
    db_session: Session, family: Family, monkeypatch: pytest.MonkeyPatch
) -> None:
    _remote_space, relative, _fact, expires = _bridge(db_session, family)
    generation_id = _publish(db_session, family)
    bind = db_session.get_bind()
    before, events = _versions(bind, family), _event_count(bind)
    payload, valid_until = _payload(bind, family)
    assert relative in {node["user_id"] for node in payload["nodes"]}
    assert valid_until is not None and valid_until <= expires
    with steward_snapshot.read_transaction(bind) as reader:
        generation = reader.get(StewardGeneration, generation_id)
        assert generation is not None
        assert steward_pipeline.valid_generation(reader, generation)
        assert not steward_pipeline.valid_generation(reader, generation, now=expires)
    # Advance only the fence clock: no expiry command, new event or new generation.
    monkeypatch.setattr(steward_pipeline, "utcnow", lambda: expires)
    expired, semantic_until = _payload(bind, family)
    assert _versions(bind, family) == before and _event_count(bind) == events
    assert expired["status"] == "stale" and semantic_until is None
    assert expired["stale_reason"] == expired["progress"]["reason_code"] == "input_changed"
    assert expired["progress"]["phase"] == "retrying"
    assert expired["progress"]["next_poll_ms"] > 0
    assert (
        expired["nodes"]
        == expired["edges"]
        == expired["inferred_edges"]
        == expired["topology_edges"]
        == []
    )


def test_noop_login_counters_and_real_publication_preserve_input_versions(
    db_session: Session, family: Family
) -> None:
    bind = db_session.get_bind()
    before, events = _versions(bind, family), _event_count(bind)
    with Session(bind=bind) as writer:
        writer.execute(
            update(User).where(User.id == family.target_id).values(name=User.name, bio=User.bio)
        )
        writer.execute(
            update(SpaceProfileRef)
            .where(SpaceProfileRef.id == family.ref_id)
            .values(status="active")
        )
        writer.execute(
            update(TermEntry).where(TermEntry.id == family.term_id).values(term=TermEntry.term)
        )
        writer.commit()
    assert _versions(bind, family) == before
    with Session(bind=bind) as writer:
        writer.execute(
            update(Account)
            .where(Account.id == family.account_id)
            .values(
                failed_attempts=Account.failed_attempts + 1,
                locked_until=utcnow() + timedelta(seconds=1),
            )
        )
        writer.commit()
    assert _versions(bind, family) == before and _event_count(bind) == events
    generation_id = _publish(db_session, family)
    assert _versions(bind, family) == before
    assert _event_count(bind) > events  # Completion events are outputs, not new inputs.
    with Session(bind=bind) as reader:
        publication = reader.get(StewardPublication, family.space_id)
        assert publication is not None and publication.generation_id == generation_id


def _published_overlay(
    session: Session, family: Family, monkeypatch: pytest.MonkeyPatch
) -> tuple[int, int]:
    monkeypatch.setattr(config, "STEWARD_INFERRED_TREE_ENABLED", True)
    extra = create_user_with_pin(session, "input-versions-inferred-child", "123456", gender="m")
    _ref(session, space_id=family.space_id, user_id=extra.id)
    session.add(
        AgentSpaceProviderSetting(
            space_id=family.space_id, agent_kind="steward", enabled=True, inferred_tree=True
        )
    )
    session.flush()
    evidence = steward_inferred._evidence_snapshot(session, family.space_id)
    edge = StewardInferredEdge(
        space_id=family.space_id,
        subject_user_id=family.target_id,
        object_user_id=extra.id,
        relation_kind="biological_parent",
        status="proposed",
        origin="llm",
        evidence_hash=evidence["evidence_hash"],
        evidence_json={"facts": evidence["facts"]},
        revision=1,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(edge)
    session.commit()
    generation_id = _publish(session, family)
    bind = session.get_bind()
    steward_delivery.drain(bind=bind, generation_id=generation_id, limit=64)
    binding = steward_overlay.claim_due(
        bind, owner="input-versions-overlay", space_id=family.space_id
    )
    assert binding is not None

    def unexpected_overlay_failure(*_args: Any, **_kwargs: Any) -> None:
        # A swallowed optional failure would otherwise hide why setup failed.
        raise

    with monkeypatch.context() as setup:
        setup.setattr(steward_overlay, "_fail", unexpected_overlay_failure)
        steward_overlay.execute(bind, binding)
    with Session(bind=bind) as reader:
        assert reader.get(StewardInferredOverlay, (family.space_id, family.account_id)) is not None
    return extra.id, edge.id


@pytest.mark.parametrize("change", ["space_switch", "evidence", "dismiss", "platform_switch"])
def test_optional_source_change_hides_old_overlay_and_preserves_confirmed_results(
    db_session: Session, family: Family, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    extra_id, edge_id = _published_overlay(db_session, family, monkeypatch)
    bind = db_session.get_bind()
    before, events = _versions(bind, family), _event_count(bind)
    published, _ = _payload(bind, family)
    assert extra_id in {node["user_id"] for node in published["nodes"]}
    inferred = next(edge for edge in published["inferred_edges"] if edge["id"] == edge_id)
    assert len(inferred["viewer_path"]) == 2
    if change == "platform_switch":
        # A configuration switch has no database event/revision at all; readers
        # must still independently recheck the effective optional-layer flag.
        monkeypatch.setattr(config, "STEWARD_INFERRED_TREE_ENABLED", False)
    else:
        with Session(bind=bind) as writer:
            if change == "space_switch":
                writer.execute(
                    update(AgentSpaceProviderSetting)
                    .where(
                        AgentSpaceProviderSetting.space_id == family.space_id,
                        AgentSpaceProviderSetting.agent_kind == "steward",
                    )
                    .values(inferred_tree=False)
                )
            elif change == "evidence":
                writer.execute(
                    update(StewardInferredEdge)
                    .where(StewardInferredEdge.id == edge_id)
                    .values(evidence_json={"facts": []})
                )
            else:
                writer.execute(
                    update(StewardInferredEdge)
                    .where(StewardInferredEdge.id == edge_id)
                    .values(status="rejected", revision=StewardInferredEdge.revision + 1)
                )
            writer.commit()
    after = _versions(bind, family)
    assert _event_count(bind) == events
    assert steward_snapshot.core_versions(after) == steward_snapshot.core_versions(before)
    assert (after == before) is (change == "platform_switch")
    current, valid_until = _payload(bind, family)
    assert current["status"] == "current" and valid_until is not None
    assert current["edges"] == published["edges"]
    assert current["topology_edges"] == published["topology_edges"]
    assert current["progress"]["total_count"] == published["progress"]["total_count"]
    assert current["progress"]["phase"] == "ready"
    assert current["inferred_edges"] == []
    assert extra_id not in {node["user_id"] for node in current["nodes"]}
