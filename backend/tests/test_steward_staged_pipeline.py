"""Behavioral regressions for staged publication, demand, delivery and overlay."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.models.account import Account
from app.models.agent_provider import AgentSpaceProviderSetting
from app.models.notification import Notification
from app.models.relationship_facts import SourceFact
from app.models.space import SpaceMember
from app.models.steward import (
    StewardDeliveryIntent,
    StewardFindingDelivery,
    StewardGeneration,
    StewardGenerationView,
    StewardJob,
    StewardPublication,
    StewardRetryBudget,
    StewardViewDemand,
    StewardViewTarget,
)
from app.models.steward_inferred import StewardInferredEdge
from app.models.steward_suggestion import StewardSuggestion
from app.models.v2_foundation import DomainEvent
from app.services import (
    personal_family_view,
    steward,
    steward_assist,
    steward_delivery,
    steward_demand,
    steward_gc,
    steward_inferred,
    steward_overlay,
    steward_pipeline,
    steward_runtime,
    steward_snapshot,
    steward_terminology,
    steward_views,
    terms,
)
from app.services.relationship_resolver import (
    RelationshipResolution,
    SearchBudgetExceeded,
    advance_search,
    resolve_graph,
)
from app.services.source_facts import create_source_fact, transition_source_fact
from app.utils.timeutil import utcnow
from conftest import (
    auth_header,
    create_agent_fixture,
    create_space_member,
    create_user_with_pin,
    login,
)


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", False)
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    # These tests verify transaction and scheduling boundaries. Real spawn CPU
    # execution has separate resolver/runtime and concurrent endpoint coverage.
    monkeypatch.setattr(steward_runtime, "run_slice", advance_search)


def _family(session, *, size=3, name="staged"):
    viewer, space = create_agent_fixture(session, name=name)
    people = [viewer]
    for index in range(1, size):
        person = create_user_with_pin(session, f"{name}-{index}", "123456", gender="m")
        create_space_member(session, space.id, person.id)
        fact = create_source_fact(
            session,
            fact_type="biological_parent",
            subject_user_id=person.id,
            object_user_id=people[-1].id,
            provenance="manual_entry",
            space_id=space.id,
        )
        transition_source_fact(session, fact, "confirm")
        people.append(person)
    session.commit()
    return people, space


def _run(session, space_id, *, deliver=False):
    job = steward.lease_next_steward_job(session, leased_by="staged-test", space_id=space_id)
    if job is None:
        steward.enqueue_steward_job(
            session,
            space_id=space_id,
            cause="integrity_scan",
            trigger_cursor=steward.current_event_watermark(session),
        )
        job = steward.lease_next_steward_job(session, leased_by="staged-test", space_id=space_id)
    assert job is not None
    return steward.execute_steward_job(
        session, job, worker_id="staged-test", expected_attempt=job.attempt, drain_delivery=deliver
    )


def test_skeleton_precedes_search_and_independent_writer_can_commit(db_session, monkeypatch):
    people, space = _family(db_session)
    bind = db_session.get_bind()
    observed = []

    def search(state, **kwargs):
        with Session(bind=bind) as independent:
            view = independent.scalar(
                select(StewardGenerationView).where(
                    StewardGenerationView.root_user_id == state.graph.viewer_user_id,
                )
            )
            assert view is not None and len(view.skeleton_json["nodes"]) == 3
            if not observed:
                assert view.completed_count == 0
                assert independent.scalar(select(func.count(StewardPublication.space_id))) == 0
            account = independent.get(Account, people[0].account.id)
            account.failed_attempts += 1  # Login metadata is not a structural input.
            independent.commit()
            observed.append(view.id)
        return advance_search(state, **kwargs)

    monkeypatch.setattr(steward_runtime, "run_slice", search)
    result = _run(db_session, space.id)
    assert observed and result["stats"]["derived_recomputed"] == 6


@pytest.mark.parametrize("found", [True, False])
def test_target_save_keeps_result_and_skeleton_json_codecs_outside_writer(
    db_session, monkeypatch, found
):
    from test_steward_snapshot_fences import pending_view, world

    fixture = world(db_session)
    generation, work, snapshot = pending_view(db_session, fixture)
    resolution = resolve_graph(snapshot.graph, target_user_id=fixture.target.id)
    if not found:
        # The storage contract also accepts a completed conservative no_path.
        resolution = RelationshipResolution(
            viewer_user_id=fixture.viewer.id,
            target_user_id=fixture.target.id,
            space_id=fixture.space.id,
            found=False,
            path_class="none",
            concept_code=None,
            snapshot_hash=snapshot.graph.snapshot_hash,
            node_genders=snapshot.graph.node_genders,
        )
    expected_raw = steward_pipeline.encode_resolution(resolution)
    expected_edge = steward_pipeline._edge_for(snapshot, resolution)
    expected_raw_json, expected_edge_json = json.dumps(expected_raw), json.dumps(expected_edge)
    expected_raw = json.loads(expected_raw_json)  # JSON object keys round-trip as strings.
    bind = db_session.get_bind()
    assert steward_pipeline._reserve_search(
        bind,
        fixture.binding,
        generation.id,
        account_id=fixture.viewer.account.id,
        target_id=fixture.target.id,
        fingerprint=steward_pipeline.search_fingerprint(snapshot.graph.snapshot_hash),
    )
    original_write = steward_pipeline.write_transaction
    original_encode, original_decode = json.JSONEncoder.encode, json.JSONDecoder.decode
    writing = False
    encoded = []

    @contextmanager
    def observe_writer(database):
        nonlocal writing
        try:
            with original_write(database) as session:
                writing = True
                yield session
                # Keep the flag set through the real context's commit/flush.
        finally:
            writing = False

    def encode(encoder, value):
        is_result = (
            (isinstance(value, dict) and ("viewer_user_id" in value or "from_user_id" in value))
            or value is None
            or (isinstance(value, str) and value in (expected_raw_json, expected_edge_json))
        )
        if is_result:
            assert not writing, "target JSON must not be encoded while holding the writer"
            encoded.append(value)
        return original_encode(encoder, value)

    def decode(decoder, value, **kwargs):
        if writing:
            assert not any(
                key in value for key in ('"topology_edges"', '"target_user_id"', '"from_user_id"')
            ), "target completion must not decode the family skeleton or an old result"
        return original_decode(decoder, value, **kwargs)

    monkeypatch.setattr(steward_pipeline, "write_transaction", observe_writer)
    monkeypatch.setattr(json.JSONEncoder, "encode", encode)
    monkeypatch.setattr(json.JSONDecoder, "decode", decode)
    # Replaying an acknowledged target must avoid loading its previous JSON,
    # increment progress only once, and refund the reservation only once.
    for _ in range(2):
        steward_pipeline.save_target(
            bind,
            fixture.binding,
            generation.id,
            work.view_id,
            snapshot=snapshot,
            resolution=resolution,
            reserved=True,
        )
    assert len(encoded) == 4  # One resolution and one edge/null per invocation, before BEGIN.
    with Session(bind=bind) as reader:
        target = reader.scalar(
            select(StewardViewTarget).where(StewardViewTarget.view_id == work.view_id)
        )
        assert isinstance(target.resolution_json, dict)
        assert target.resolution_json == expected_raw and target.edge_json == expected_edge
        assert target.status == ("ready" if found else "unavailable")
        assert target.reason_code == (None if found else "no_path")
        assert (
            reader.scalar(
                select(func.json_type(StewardViewTarget.resolution_json)).where(
                    StewardViewTarget.id == target.id
                )
            )
            == "object"
        )
        view = reader.get(StewardGenerationView, work.view_id)
        assert view.completed_count == view.total_count == 1 and view.revision == 2
        assert view.status == "ready"
        assert reader.get(StewardGeneration, generation.id).ready_views == 1
        budget = reader.scalar(select(StewardRetryBudget))
        assert budget.attempts == 0 and not budget.exhausted


def test_demand_is_detached_and_new_viewer_takes_next_preparation_slot(db_session, monkeypatch):
    people, space = _family(db_session, size=4, name="demand-order")
    account_ids = [person.account.id for person in people]
    db_session.add(
        StewardViewDemand(
            space_id=space.id,
            viewer_account_id=account_ids[1],
            revision=1,
            fulfilled_revision=0,
            focus_user_id=people[3].id,
            requested_at=utcnow(),
        )
    )
    db_session.commit()
    prepared = []
    original = steward_snapshot.read_viewer
    bind = db_session.get_bind()

    def snapshot(*args, **kwargs):
        prepared.append(kwargs["account_id"])
        return original(*args, **kwargs)

    def search(state, **kwargs):
        if len(prepared) == 1:
            with Session(bind=bind) as independent:
                if (
                    independent.scalar(
                        select(StewardViewDemand.id).where(
                            StewardViewDemand.viewer_account_id == account_ids[3],
                        )
                    )
                    is None
                ):
                    independent.add(
                        StewardViewDemand(
                            space_id=space.id,
                            viewer_account_id=account_ids[3],
                            revision=1,
                            fulfilled_revision=0,
                            requested_at=utcnow(),
                        )
                    )
                    independent.commit()
        return advance_search(state, **kwargs)

    monkeypatch.setattr(steward_snapshot, "read_viewer", snapshot)
    monkeypatch.setattr(steward_runtime, "run_slice", search)
    _run(db_session, space.id)
    assert prepared[:2] == [account_ids[1], account_ids[3]]
    assert set(prepared) == set(account_ids)
    db_session.expire_all()
    demands = list(db_session.scalars(select(StewardViewDemand)))
    assert all(demand.fulfilled_revision == demand.revision for demand in demands)
    _run(db_session, space.id)
    assert db_session.scalar(select(StewardJob.id).where(StewardJob.status == "queued")) is None


def test_late_same_cursor_demand_survives_publication(db_session, monkeypatch):
    people, space = _family(db_session, size=2, name="late-demand")
    original = steward_pipeline._prepare_delivery
    viewer_account_id = people[0].account.id

    def prepare(bind, binding, generation_id):
        with Session(bind=bind) as independent:
            independent.add(
                StewardViewDemand(
                    space_id=space.id,
                    viewer_account_id=viewer_account_id,
                    revision=1,
                    fulfilled_revision=0,
                    requested_at=utcnow(),
                )
            )
            independent.commit()
        return original(bind, binding, generation_id)

    monkeypatch.setattr(steward_pipeline, "_prepare_delivery", prepare)
    result = _run(db_session, space.id)
    current = db_session.get(StewardGeneration, result["generation_id"])
    assert current.status == "published"
    queued = db_session.scalar(select(StewardJob).where(StewardJob.status == "queued"))
    assert queued is not None
    demand = db_session.scalar(select(StewardViewDemand))
    assert demand.fulfilled_revision < demand.revision


def test_planned_finding_is_not_a_delivery_receipt(db_session, monkeypatch):
    people, space = _family(db_session, size=2, name="finding-receipt")
    finding = steward._finding(
        "gap", {"code": "sibling_missing_parents", "pair": [person.id for person in people]}
    )
    present = [finding, finding]  # Duplicate detector witnesses share one signature.
    monkeypatch.setattr(steward, "_detect_findings", lambda *_: list(present))
    first = _run(db_session, space.id)
    people[0].name = "finding-renamed"
    db_session.commit()  # Invalidate generation 1 before its delivery runs.
    second = _run(db_session, space.id)
    delivered = steward_delivery.drain(
        bind=db_session.get_bind(), generation_id=second["generation_id"], limit=64
    )
    assert delivered["findings_emitted"] == 1
    steward_delivery.drain(
        bind=db_session.get_bind(), generation_id=first["generation_id"], limit=64
    )
    _run(db_session, space.id, deliver=True)
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(StewardFindingDelivery)) == 1
    present.clear()
    _run(db_session, space.id, deliver=True)
    present.append(finding)
    result = _run(db_session, space.id, deliver=True)
    assert result["stats"]["findings_emitted"] == 1  # Disappear/reappear is a new occurrence.
    assert db_session.scalar(select(func.count()).select_from(StewardFindingDelivery)) == 2
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(DomainEvent)
            .where(DomainEvent.type == "steward.gap_detected")
        )
        == 2
    )


def test_assist_orphan_recovery_cannot_bypass_staged_intent(db_session, monkeypatch):
    people, space = _family(db_session, size=2, name="assist-gate")
    result = _run(db_session, space.id)
    registrations = []
    monkeypatch.setattr(
        steward_assist,
        "register_batch_for_job",
        lambda session, **kwargs: registrations.append(kwargs["job"].id),
    )
    assert steward_assist.recover_stuck_batches(db_session) == 0
    people[0].name = "assist-invalidated"
    db_session.commit()
    assert steward_assist.recover_stuck_batches(db_session) == 0
    assert registrations == []
    generation = db_session.get(StewardGeneration, result["generation_id"])
    assert generation.status == "published"


@pytest.mark.parametrize("change", ["name", "terminology_rule", "model_enabled"])
def test_required_failure_stops_publication_and_budget_survives_presentation_changes(
    db_session, monkeypatch, change
):
    people, space = _family(db_session, size=2, name="budget-persist")
    monkeypatch.setattr(config, "STEWARD_STAGE_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(config, "STEWARD_RETRY_BACKOFF_FIRST_SECONDS", 0)
    attempts = []

    def broken(state, **kwargs):
        attempts.append(1)
        raise SearchBudgetExceeded("expansions", 8, 8)

    monkeypatch.setattr(steward_runtime, "run_slice", broken)
    for _ in range(2):
        with pytest.raises(steward_pipeline.RequiredTargetFailed):
            _run(db_session, space.id)
        db_session.rollback()
    if change == "name":
        people[0].name = "only-presentation-changed"
    elif change == "terminology_rule":
        monkeypatch.setattr(steward_terminology, "RULE_VERSION", "test-next-terminology")
    else:
        monkeypatch.setattr(
            config, "STEWARD_ASSIST_TERMINOLOGY", not config.STEWARD_ASSIST_TERMINOLOGY
        )
    db_session.commit()
    with pytest.raises(steward_pipeline.RequiredTargetFailed):
        _run(db_session, space.id)
    assert len(attempts) == 2
    db_session.expire_all()
    assert db_session.scalar(select(StewardPublication.space_id)) is None
    assert (
        db_session.scalar(select(StewardJob.id).where(StewardJob.last_event_cursor.is_not(None)))
        is None
    )
    assert db_session.scalar(select(StewardRetryBudget.exhausted)) is True
    assert (
        steward_demand.register(account=people[0].account, space_id=space.id, retry=True)
        == "queued"
    )
    with pytest.raises(steward_pipeline.RequiredTargetFailed):
        _run(db_session, space.id)
    assert len(attempts) == 3
    budget = db_session.scalar(select(StewardRetryBudget))
    db_session.refresh(budget)
    assert budget.manual_grants == 1 and budget.max_attempts == 3
    steward_demand.register(account=people[0].account, space_id=space.id, retry=True)
    db_session.refresh(budget)
    assert budget.manual_grants == 1


@pytest.mark.parametrize("change", ["terminology_rule", "presentation_rule", "model_enabled"])
def test_presentation_config_rebuilds_terms_without_searching_published_paths(
    db_session, monkeypatch, change
):
    _people, space = _family(db_session, name="config-reuse")
    first = _run(db_session, space.id)
    previous = {
        view.viewer_account_id: (view.structural_hash, view.presentation_hash)
        for view in db_session.scalars(
            select(StewardGenerationView).where(
                StewardGenerationView.generation_id == first["generation_id"]
            )
        )
    }

    def no_search(*args, **kwargs):
        raise AssertionError("presentation configuration must reuse the published paths")

    monkeypatch.setattr(steward_runtime, "run_slice", no_search)
    if change == "terminology_rule":
        monkeypatch.setattr(steward_terminology, "RULE_VERSION", "test-next-terminology")
    elif change == "presentation_rule":
        monkeypatch.setattr(terms, "PRESENTATION_RULE_VERSION", "test-next-presentation")
    else:
        monkeypatch.setattr(
            config, "STEWARD_ASSIST_TERMINOLOGY", not config.STEWARD_ASSIST_TERMINOLOGY
        )
    second = _run(db_session, space.id)
    assert second["stats"]["derived_recomputed"] == 0
    assert second["stats"]["personal_family_views_rebuilt"] == len(previous)
    current = list(
        db_session.scalars(
            select(StewardGenerationView).where(
                StewardGenerationView.generation_id == second["generation_id"]
            )
        )
    )
    assert len(current) == len(previous)
    for view in current:
        graph_hash, display_hash = previous[view.viewer_account_id]
        assert view.structural_hash == graph_hash and view.presentation_hash != display_hash
        assert view.status == "ready" and view.completed_count == view.total_count == 2
        assert view.result_view_id is None  # Rendered terms were rebuilt for the new rules.


def test_optional_overlay_preserves_multi_hop_and_revokes_independently(db_session, monkeypatch):
    from test_steward_inferred import _candidate, _make_job

    people, space = _family(db_session, size=2, name="overlay-path")
    extra = create_user_with_pin(db_session, "overlay-extra", "123456", gender="f")
    create_space_member(db_session, space.id, extra.id)
    db_session.add(
        AgentSpaceProviderSetting(
            space_id=space.id,
            agent_kind="steward",
            provider_id=None,
            model=None,
            enabled=True,
            inferred_tree=True,
        )
    )
    db_session.commit()
    monkeypatch.setattr(config, "STEWARD_INFERRED_TREE_ENABLED", True)
    job = _make_job(db_session, space)
    _candidate(db_session, job, kind="spouse", subject_id=people[1].id, object_id=extra.id)
    facts = list(db_session.scalars(select(SourceFact)))
    assert (
        steward_inferred.project_for_job(
            db_session, job, facts=facts, visible={person.id for person in people} | {extra.id}
        )
        == 1
    )
    db_session.commit()
    result = _run(db_session, space.id, deliver=True)
    binding = steward_overlay.claim_due(db_session.get_bind(), owner="overlay-test")
    assert binding is not None and binding.account_id == people[0].account.id
    steward_overlay.execute(db_session.get_bind(), binding)
    with steward_snapshot.read_transaction(db_session.get_bind()) as read:
        payload, _ = steward_views.payload_for(
            read, account=people[0].account, space_id=space.id, progressive=True
        )
    assert payload["status"] == "current"
    inferred = payload["inferred_edges"]
    assert len(inferred) == 1 and len(inferred[0]["viewer_path"]) == 2
    assert inferred[0]["viewer_term"] and inferred[0]["presentation"]
    assert inferred[0]["viewer_path"][0]["from"] == people[0].id
    assert inferred[0]["viewer_path"][-1]["to"] == extra.id
    # Successful optional work must refund its in-flight reservation. A
    # presentation edit keeps the same structural failure budget; four valid
    # generations must not exhaust the default three-attempt crash budget.
    for index in range(3):
        terms.set_personal_term(
            db_session,
            account_id=people[0].account.id,
            space_id=space.id,
            concept_code="Um",
            term=f"父亲称谓{index}",
        )
        db_session.commit()
        result = _run(db_session, space.id, deliver=True)
        binding = steward_overlay.claim_due(db_session.get_bind(), owner="overlay-test")
        assert binding is not None and binding.account_id == people[0].account.id
        steward_overlay.execute(db_session.get_bind(), binding)
        db_session.expire_all()
        budgets = list(db_session.scalars(select(StewardRetryBudget)))
        assert all(budget.attempts == 0 and not budget.exhausted for budget in budgets)
    with steward_snapshot.read_transaction(db_session.get_bind()) as read:
        payload, _ = steward_views.payload_for(
            read, account=people[0].account, space_id=space.id, progressive=True
        )
    assert len(payload["inferred_edges"][0]["viewer_path"]) == 2
    before_revision = payload["progress"]["revision"]
    edge = db_session.scalar(select(StewardInferredEdge))
    edge.status, edge.revision = "rejected", edge.revision + 1
    db_session.commit()
    with steward_snapshot.read_transaction(db_session.get_bind()) as read:
        after, _ = steward_views.payload_for(
            read, account=people[0].account, space_id=space.id, progressive=True
        )
    assert after["status"] == "current" and after["inferred_edges"] == []
    assert after["edges"] == payload["edges"]
    assert after["progress"]["revision"] > before_revision
    assert db_session.get(StewardGeneration, result["generation_id"]).status == "published"


def test_gc_keeps_current_and_shared_result_sources(db_session, monkeypatch):
    _people, space = _family(db_session, size=2, name="gc-shared")
    results = [_run(db_session, space.id, deliver=True) for _ in range(3)]
    counts = [steward_gc.collect(db_session.get_bind(), batch_size=1) for _ in range(30)]
    assert all(count <= 1 for count in counts)
    db_session.expire_all()
    first, middle, current = [result["generation_id"] for result in results]
    assert db_session.get(StewardGeneration, first) is not None
    assert db_session.get(StewardGeneration, middle) is None
    assert db_session.get(StewardGeneration, current) is not None
    assert db_session.scalar(select(func.count()).select_from(StewardViewTarget)) == 2
    # The source generation can disappear while its succeeded job remains.
    # Durable checkpoint identity must prevent legacy orphan recovery from
    # registering assist a second time after GC.
    registrations = []
    monkeypatch.setattr(
        steward_assist,
        "register_batch_for_job",
        lambda session, **kwargs: registrations.append(kwargs["job"].id),
    )
    assert steward_assist.recover_stuck_batches(db_session) == 0
    assert registrations == []


@pytest.mark.parametrize("initially_fulfilled", [False, True])
@pytest.mark.parametrize("revocation", ["membership", "deleted_user"])
def test_revoked_demand_terminates_and_rejoin_gets_new_revision(
    db_session, initially_fulfilled, revocation
):
    people, space = _family(db_session, size=3, name="demand-revoked")
    requester = people[2]
    demand = StewardViewDemand(
        space_id=space.id,
        viewer_account_id=requester.account.id,
        revision=1,
        fulfilled_revision=0,
        focus_user_id=people[0].id,
        requested_at=utcnow(),
    )
    db_session.add(demand)
    db_session.commit()
    if initially_fulfilled:
        _run(db_session, space.id)
    member = db_session.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == space.id, SpaceMember.user_id == requester.id
        )
    )
    if revocation == "membership":
        member.status = "removed"
    else:
        requester.deleted_at = utcnow()
    db_session.commit()
    for _ in range(2):
        _run(db_session, space.id)
        assert db_session.scalar(select(StewardJob.id).where(StewardJob.status == "queued")) is None
        db_session.refresh(demand)
        assert demand.revision == demand.fulfilled_revision == 1
    member.status, requester.deleted_at = "active", None
    db_session.commit()
    assert steward_demand.register(account=requester.account, space_id=space.id) == "queued"
    db_session.refresh(demand)
    assert demand.revision == 2 and demand.fulfilled_revision == 1
    _run(db_session, space.id)
    db_session.refresh(demand)
    assert demand.fulfilled_revision == 2
    assert db_session.scalar(select(StewardJob.id).where(StewardJob.status == "queued")) is None


def test_finding_notification_batches_resume_without_duplicates_or_revoked_recipient(
    db_session, monkeypatch
):
    batch_size = steward_delivery.SUGGESTION_RECIPIENT_BATCH_SIZE
    people, space = _family(db_session, size=batch_size * 2 + 1, name="fanout")
    finding = steward._finding(
        "gap", {"code": "sibling_missing_parents", "pair": [people[0].id, people[1].id]}
    )
    monkeypatch.setattr(steward, "_detect_findings", lambda *_: [finding])
    first = _run(db_session, space.id)
    intents = list(
        db_session.scalars(
            select(StewardDeliveryIntent)
            .where(
                StewardDeliveryIntent.generation_id == first["generation_id"],
                StewardDeliveryIntent.kind == "suggestion_finding",
            )
            .order_by(StewardDeliveryIntent.id)
        )
    )
    assert len(intents) == 3
    assert all(len(row.payload_json["recipient_account_ids"]) <= batch_size for row in intents)
    steward_delivery.drain(
        bind=db_session.get_bind(), generation_id=first["generation_id"], limit=2
    )
    assert db_session.scalar(select(func.count(Notification.id))) == batch_size
    db_session.refresh(intents[0])
    assert intents[0].status == "done"
    intents[0].status = "pending"  # Replay after an acknowledgement was lost.
    db_session.commit()
    steward_delivery.drain(
        bind=db_session.get_bind(), generation_id=first["generation_id"], limit=1
    )
    assert db_session.scalar(select(func.count(Notification.id))) == batch_size

    removed = people[-1]
    member = db_session.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == space.id, SpaceMember.user_id == removed.id
        )
    )
    member.status = "removed"
    db_session.commit()
    steward_delivery.drain(
        bind=db_session.get_bind(), generation_id=first["generation_id"], limit=64
    )
    assert db_session.scalar(select(func.count(Notification.id))) == batch_size
    _run(db_session, space.id, deliver=True)
    db_session.expire_all()
    assert db_session.scalar(select(func.count(StewardSuggestion.id))) == 1
    recipients = list(db_session.scalars(select(Notification.recipient_account_id)))
    assert len(recipients) == len(set(recipients)) == len(people) - 1
    assert set(recipients) == {person.account.id for person in people[:-1]}


def test_latest_failed_preview_kept_but_unpublished_delivery_terminates(db_session, monkeypatch):
    _people, space = _family(db_session, size=2, name="gc-failed")

    def fail_publication(*args, **kwargs):
        raise ValueError("publication unavailable")

    monkeypatch.setattr(steward, "_publish_success", fail_publication)
    with pytest.raises(ValueError):
        _run(db_session, space.id)
    db_session.rollback()
    generation = db_session.scalar(select(StewardGeneration))
    assert generation.status == "failed"
    assert db_session.scalar(select(StewardPublication)) is None
    intents = list(db_session.scalars(select(StewardDeliveryIntent)))
    assert intents and any(row.status == "pending" for row in intents)
    counts = [
        steward_gc.collect(db_session.get_bind(), batch_size=1) for _ in range(len(intents) + 2)
    ]
    assert all(count <= 1 for count in counts)
    db_session.expire_all()
    assert db_session.get(StewardGeneration, generation.id) is not None
    assert db_session.scalar(select(func.count(StewardGenerationView.id))) == 2
    assert all(
        row.status == "superseded" for row in db_session.scalars(select(StewardDeliveryIntent))
    )


@pytest.mark.parametrize("new_root", ["publication", "reference"])
def test_gc_rechecks_new_root_after_lock_free_discovery(db_session, monkeypatch, new_root):
    people, space = _family(db_session, size=2, name="gc-race")
    first = _run(db_session, space.id, deliver=True)
    people[0].name = "gc-race-new-name"
    db_session.commit()
    latest = _run(db_session, space.id, deliver=True)
    original = steward_gc._discover
    bind = db_session.get_bind()
    observed = []

    def discover(session, *, cap):
        candidate = original(session, cap=cap)
        assert candidate is not None and candidate.generation_id == first["generation_id"]
        assert candidate.kind == "targets"
        with Session(bind=bind) as other:
            if new_root == "publication":
                other.get(StewardPublication, space.id).generation_id = candidate.generation_id
            else:
                source = other.scalar(
                    select(StewardGenerationView).where(
                        StewardGenerationView.generation_id == first["generation_id"]
                    )
                )
                target = other.scalar(
                    select(StewardGenerationView).where(
                        StewardGenerationView.generation_id == latest["generation_id"]
                    )
                )
                target.result_view_id = source.id
            other.commit()  # Would time out if candidate discovery held the writer.
        observed.append(candidate)
        return candidate

    monkeypatch.setattr(steward_gc, "_discover", discover)
    assert steward_gc.collect(bind, batch_size=1) == 0
    assert observed
    assert db_session.get(StewardViewTarget, observed[0].ids[0]) is not None


def test_canonical_reader_uses_real_snapshot_and_preserves_caller_work(db_session, monkeypatch):
    people, space = _family(db_session, size=2, name="canonical-read")
    _run(db_session, space.id)
    account, bind = people[0].account, db_session.get_bind()
    account.failed_attempts = 7  # Remains unflushed caller work.
    before = people[0].name
    original = steward_views.valid_generation
    changed = []

    def validate(session, generation):
        result = original(session, generation)
        if not changed:
            with Session(bind=bind) as other:
                other.get(type(people[0]), people[0].id).name = "canonical-read-renamed"
                other.commit()
            changed.append(True)
        return result

    monkeypatch.setattr(steward_views, "valid_generation", validate)
    payload = personal_family_view.current_view_payload(
        db_session, account=account, space_id=space.id
    )
    assert payload["status"] == "current"
    assert (
        next(node for node in payload["nodes"] if node["user_id"] == people[0].id)["display"][
            "name"
        ]
        == before
    )
    assert "term_source_level" in payload["edges"][0]
    assert account.failed_attempts == 7
    assert not db_session.connection().connection.driver_connection.in_transaction
    with Session(bind=bind) as other:
        assert other.get(Account, account.id).failed_attempts == 0
    later = personal_family_view.current_view_payload(
        db_session, account=account, space_id=space.id
    )
    assert later["nodes"] == []
    # An explicit caller write transaction is equally untouched.
    db_session.flush()
    assert db_session.connection().connection.driver_connection.in_transaction
    personal_family_view.current_view_payload(db_session, account=account, space_id=space.id)
    assert db_session.connection().connection.driver_connection.in_transaction
    with Session(bind=bind) as other:
        assert other.get(Account, account.id).failed_attempts == 0


def test_worker_stopped_empty_progress_is_terminal_but_manual_lease_can_advance(db_session, client):
    _people, space = _family(db_session, size=2, name="worker-stopped")
    headers = auth_header(login(client, "worker-stopped", "123456").json())
    url = f"/api/personal-family-view?space_id={space.id}&progressive=true"
    first = client.get(url, headers=headers)
    assert first.status_code == 200
    progress = first.json()["progress"]
    assert progress["phase"] == "failed" and progress["reason_code"] == "worker_stopped"
    assert progress["next_poll_ms"] == 0
    grant = steward.lease_next_steward_job(db_session, leased_by="manual", space_id=space.id)
    assert grant is not None
    active = client.get(url, headers=headers)
    assert active.json()["progress"]["phase"] in ("queued", "preparing")
    grant.lease_expires_at = utcnow()
    db_session.commit()
    expired = client.get(url, headers=headers)
    assert expired.json()["progress"]["reason_code"] == "worker_stopped"


def test_shutdown_reports_live_coordinator_until_it_has_stopped(monkeypatch):
    gate = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)
    started = threading.Event()

    def pending():
        started.set()
        gate.wait(2)

    future = executor.submit(pending)
    assert started.wait(1)
    monkeypatch.setattr(steward_runtime, "_coordinators", executor)
    monkeypatch.setattr(steward_runtime, "_active", {123: future})
    started_at = time.monotonic()
    assert not steward_runtime.shutdown_runtime(timeout_seconds=0.01)
    assert time.monotonic() - started_at < 0.5
    gate.set()
    future.result(timeout=1)
    assert steward_runtime.shutdown_runtime(timeout_seconds=1)


def test_validated_clock_header_on_empty_current_and_304(db_session, client):
    _people, space = _family(db_session, size=2, name="clock-contract")
    headers = auth_header(login(client, "clock-contract", "123456").json())
    url = f"/api/personal-family-view?space_id={space.id}&progressive=true"
    responses = [client.get(url, headers=headers)]
    _run(db_session, space.id)
    current = client.get(url, headers=headers)
    responses.append(current)
    responses.append(client.get(url, headers={**headers, "If-None-Match": current.headers["etag"]}))
    assert [response.status_code for response in responses] == [200, 200, 304]
    for response in responses:
        validated = int(response.headers["x-pfv-validated-at"])
        expires = int(response.headers["x-pfv-display-until"])
        assert 0 < expires - validated <= config.PERSONAL_FAMILY_VIEW_DISPLAY_TTL_SECONDS
        assert "X-PFV-Validated-At" in response.headers["access-control-expose-headers"]
        assert "date" not in response.headers  # Uvicorn supplies its own single HTTP Date.
