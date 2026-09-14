"""Public read consumers follow atomic publication, never legacy materialization."""

from __future__ import annotations

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from app import config
from app.models.personal_family_view import PersonalFamilyView
from app.models.relationship_facts import SourceFact
from app.models.space import SpaceMember
from app.models.steward import StewardPublication
from app.services import (
    family_recommendations,
    household_card,
    kinship_presentation,
    personal_family_view,
    space_stats,
    steward,
    steward_pipeline,
    steward_runtime,
    steward_terminology_snapshot,
    terms,
)
from app.services.relationship_resolver import advance_search
from app.services.source_facts import create_source_fact, transition_source_fact
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture, create_space_member, create_user_with_pin


@pytest.fixture
def staged_family(db_session, monkeypatch):
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", False)
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    monkeypatch.setattr(steward_runtime, "run_slice", advance_search)
    terms.seed_builtin_packs(db_session)
    viewer, space = create_agent_fixture(db_session, name="publication-consumer")
    people = [viewer]
    facts = []
    for index in range(2):
        parent = create_user_with_pin(
            db_session, f"publication-parent-{index}", "123456", gender="m"
        )
        create_space_member(db_session, space.id, parent.id)
        fact = create_source_fact(
            db_session,
            fact_type="biological_parent",
            subject_user_id=parent.id,
            object_user_id=people[-1].id,
            provenance="manual_entry",
            space_id=space.id,
        )
        transition_source_fact(db_session, fact, "confirm")
        facts.append(fact)
        people.append(parent)
    db_session.commit()
    job = steward.lease_next_steward_job(
        db_session, leased_by="consumer-test", space_id=space.id, ttl_seconds=120
    )
    assert job is not None
    binding = steward_pipeline.binding_for(job)
    job = steward_pipeline.begin_job(db_session, binding)
    summary = steward_pipeline.execute(db_session, job, now=utcnow(), upper=job.trigger_cursor)
    return people, space, facts, binding, job.trigger_cursor, summary


def read_consumers(session, people, space):
    viewer = people[0]
    return (
        family_recommendations.recommendations_payload(
            session, account=viewer.account, space_id=space.id
        ),
        space_stats.space_stats_payload(session, account=viewer.account, space_id=space.id),
        household_card.household_card_payload(session, account=viewer.account, space_id=space.id),
        kinship_presentation.build_relation_presentation(
            session,
            viewer=viewer,
            account=viewer.account,
            space_id=space.id,
            subject_user_id=viewer.id,
            object_user_id=people[1].id,
            relation_state="confirmed",
        ),
    )


def test_consumers_switch_at_publication_without_synchronous_get_rebuild(
    db_session, staged_family, monkeypatch
):
    people, space, _facts, binding, upper, summary = staged_family

    def no_rebuild(*args, **kwargs):
        raise AssertionError("read consumer attempted synchronous recompute")

    monkeypatch.setattr(personal_family_view, "rebuild_view", no_rebuild)
    monkeypatch.setattr(steward_terminology_snapshot, "resolve_graph", no_rebuild)
    recommendations, stats, household, presentation = read_consumers(db_session, people, space)
    assert db_session.get(StewardPublication, space.id) is None
    assert recommendations["view_status"] != "current" and recommendations["items"] == []
    assert stats["status"] != "current" and stats["node_count"] == stats["edge_count"] == 0
    assert stats["member_count"] == 3
    assert len(household["members"]) == 2 and household["computed_at"] is None
    assert presentation["term"] is None

    steward_pipeline.publish(db_session, binding, summary=summary, upper=upper)
    writes = []

    def observe(_connection, _cursor, statement, *_args):
        if statement.lstrip().split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            writes.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", observe)
    try:
        recommendations, stats, household, presentation = read_consumers(db_session, people, space)
    finally:
        event.remove(bind, "before_cursor_execute", observe)
    assert writes == []
    assert recommendations["view_status"] == stats["status"] == "current"
    assert {item["target_user_id"] for item in recommendations["items"]} == {
        people[1].id,
        people[2].id,
    }
    assert stats["node_count"] == 3 and stats["edge_count"] == 2
    assert stats["relation_distribution"] == [{"dir_class": "elder", "count": 2}]
    assert household["view_version"] == stats["view_version"] == summary["generation_id"]
    assert household["computed_at"] is not None
    assert presentation["term"] == "爸爸"
    assert presentation["term_source_level"] == "locale"
    assert presentation["term_source_label"] == "地区用词"
    # The old live table intentionally remains unmaterialized.
    assert all(row.status != "current" for row in db_session.scalars(select(PersonalFamilyView)))


@pytest.mark.parametrize("change", ["fact", "membership"])
def test_consumer_publication_fence_hides_revoked_terms_and_counts(
    db_session, staged_family, change, monkeypatch
):
    people, space, facts, binding, upper, summary = staged_family
    steward_pipeline.publish(db_session, binding, summary=summary, upper=upper)
    with Session(bind=db_session.get_bind()) as writer:
        if change == "fact":
            writer.execute(
                update(SourceFact)
                .where(SourceFact.id == facts[0].id)
                .values(revision=SourceFact.revision + 1)
            )
        else:
            writer.execute(
                update(SpaceMember)
                .where(
                    SpaceMember.space_id == space.id,
                    SpaceMember.user_id == people[1].id,
                )
                .values(status="removed")
            )
        writer.commit()
    db_session.expire_all()
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    recommendations, stats, household, presentation = read_consumers(db_session, people, space)
    assert recommendations["view_status"] == stats["status"] == "stale"
    assert recommendations["items"] == []
    assert stats["node_count"] == stats["edge_count"] == 0
    assert stats["stale_reason"] == "input_changed"
    assert household["computed_at"] is None
    assert presentation["term"] is None
    if change == "membership":
        assert stats["member_count"] == 2
        assert {row["user_id"] for row in household["members"]} == {people[2].id}


def test_uncomputed_stats_and_household_reads_do_not_create_or_rebuild_views(
    db_session, monkeypatch
):
    viewer, space = create_agent_fixture(db_session, name="uncomputed-consumer")

    def no_rebuild(*args, **kwargs):
        raise AssertionError("GET attempted synchronous recompute")

    monkeypatch.setattr(personal_family_view, "rebuild_view", no_rebuild)
    stats = space_stats.space_stats_payload(db_session, account=viewer.account, space_id=space.id)
    household = household_card.household_card_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    assert stats["status"] == "never_computed"
    assert stats["node_count"] == stats["edge_count"] == 0
    assert stats["member_count"] == 1
    assert household["view_version"] == 0 and household["computed_at"] is None
    assert db_session.scalar(select(PersonalFamilyView.id)) is None
