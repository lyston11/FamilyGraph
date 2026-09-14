"""Terminology keeps publication, bounded writers and assist recovery separate."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_steward import _confirm, _person
from test_steward_staged_pipeline import _family, _run
from test_steward_terminology import _account_id, _enable_provider, _grandchild_family

from app import config
from app.models.account import Account
from app.models.notification import Notification
from app.models.space import SpaceMember
from app.models.steward import (
    StewardAssistBatch,
    StewardDeliveryIntent,
    StewardGeneration,
    StewardJob,
    StewardPublication,
    StewardRetryBudget,
    StewardTermProjection,
)
from app.models.steward_suggestion import StewardSuggestion
from app.models.term_registry import TermEntry
from app.models.v2_foundation import DomainEvent
from app.services import (
    maintenance,
    personal_family_view,
    steward,
    steward_assist,
    steward_delivery,
    steward_gc,
    steward_pipeline,
    steward_runtime,
    steward_suggestions,
    steward_terminology,
    terms,
)
from app.services.relationship_resolver import advance_search
from app.utils.timeutil import utcnow


@pytest.fixture(autouse=True)
def _enabled(db_session, monkeypatch):
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", False)
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_ASSIST_CANDIDATE", False)
    monkeypatch.setattr(config, "STEWARD_ASSIST_RANKING", False)
    monkeypatch.setattr(config, "STEWARD_ASSIST_EXPLANATION", False)
    monkeypatch.setattr(config, "STEWARD_ASSIST_TERMINOLOGY", True)
    monkeypatch.setattr(config, "STEWARD_STAGE_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(config, "STEWARD_RETRY_BACKOFF_FIRST_SECONDS", 0)
    monkeypatch.setattr(steward_runtime, "run_slice", advance_search)
    terms.seed_builtin_packs(db_session)
    db_session.commit()


def _intents(session, generation_id, *, phase=None):
    query = select(StewardDeliveryIntent).where(
        StewardDeliveryIntent.generation_id == generation_id,
        StewardDeliveryIntent.kind == "terminology",
    )
    if phase is not None:
        query = query.where(StewardDeliveryIntent.payload_json["phase"].as_string() == phase)
    return list(session.scalars(query.order_by(StewardDeliveryIntent.id)))


def _drain(session, generation_id):
    result = steward_delivery.drain(bind=session.get_bind(), generation_id=generation_id, limit=128)
    session.expire_all()
    return result


def _queue(session, space_id):
    return steward.enqueue_steward_job(
        session,
        space_id=space_id,
        cause="integrity_scan",
        trigger_cursor=steward.current_event_watermark(session),
    )[0]


def _two_preferred_targets(session):
    space, viewer, mother, grandmother = _grandchild_family(session)
    grandfather = _person(session, space.id, "delivery-grandfather", gender="m")
    _confirm(session, "biological_parent", grandfather.id, mother.id, space_id=space.id)
    # A second verified synonym exercises two changed targets for one viewer.
    session.add(
        TermEntry(
            concept_code="Uf-Um",
            level="locale",
            locale="zh-CN",
            term="姥爷",
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    account_id = _account_id(session, viewer)
    for concept_code, term in (("Uf-Uf", "姥姥"), ("Uf-Um", "姥爷")):
        terms.record_usage_and_promote(
            session,
            space_id=space.id,
            concept_code=concept_code,
            term=term,
            account_id=account_id,
            profile_id=viewer.id,
            source_event="manual_select",
        )
    session.commit()
    return space, viewer, grandmother, grandfather


def _long_chain(session):
    space, viewer, _mother, grandmother = _grandchild_family(session)
    target = grandmother
    for index in range(2):
        ancestor = _person(session, space.id, f"delivery-ancestor-{index}", gender="f")
        _confirm(session, "biological_parent", ancestor.id, target.id, space_id=space.id)
        target = ancestor
    session.commit()
    return space, viewer, target


def test_core_publishes_before_detached_terminology_and_assist(db_session, monkeypatch):
    space, viewer, ancestor = _long_chain(db_session)
    _enable_provider(db_session, space)
    result = _run(db_session, space.id)
    generation_id = result["generation_id"]
    assert db_session.get(StewardPublication, space.id).generation_id == generation_id
    assert db_session.get(StewardGeneration, generation_id).status == "published"
    assert db_session.scalar(select(func.count(StewardTermProjection.id))) == 0
    assert db_session.scalar(select(func.count(StewardAssistBatch.id))) == 0

    prepare = steward_terminology.prepare_delivery_item
    collect = steward_terminology.collect_model_groups
    register = steward_assist.register_batch_for_job
    prepared_targets = []
    prepared_groups = []
    registered = []
    account_id = _account_id(db_session, viewer)

    def writer_can_commit(bind):
        # A real, independent connection can take the writer during preparation.
        with Session(bind=bind) as writer:
            writer.connection().exec_driver_sql("BEGIN IMMEDIATE")
            account = writer.get(Account, account_id)
            account.failed_attempts += 1
            writer.commit()

    def outside_writer(bind, *, item):
        writer_can_commit(bind)
        prepared_targets.extend(target["target_user_id"] for target in item["targets"])
        return prepare(bind, item=item)

    def collect_outside_writer(session, **kwargs):
        writer_can_commit(session.get_bind())
        prepared_groups.append(kwargs["space_id"])
        return collect(session, **kwargs)

    def after_terminology(session, **kwargs):
        rows = _intents(session, generation_id)
        assert rows and all(row.status == "done" for row in rows)
        assert session.scalar(select(func.count(StewardTermProjection.id))) > 0
        registered.append(kwargs["job"].id)
        return register(session, **kwargs)

    def old_scan(*_args, **_kwargs):
        raise AssertionError("whole-space terminology scan must not run in a writer")

    monkeypatch.setattr(steward_terminology, "prepare_delivery_item", outside_writer)
    monkeypatch.setattr(steward_terminology, "collect_model_groups", collect_outside_writer)
    monkeypatch.setattr(steward_terminology, "run_deterministic_scan", old_scan)
    monkeypatch.setattr(steward_assist, "register_batch_for_job", after_terminology)
    delivery = _drain(db_session, generation_id)
    assert delivery["delivery_failed"] == 0
    assert delivery["terminology_projections"] > 0
    assert prepared_groups == [space.id]
    assert prepared_targets and registered == [
        db_session.get(StewardGeneration, generation_id).job_id
    ]
    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == account_id,
            StewardTermProjection.target_user_id == ancestor.id,
        )
    )
    assert projection.baseline_source == "derived" and projection.term is None
    batch = db_session.scalar(select(StewardAssistBatch))
    assert batch is not None and batch.fence_json["kinds"] == ["terminology"]


def test_multiple_changed_targets_refresh_once_and_hot_scans_skip_preparation(
    db_session, monkeypatch
):
    space, viewer, grandmother, grandfather = _two_preferred_targets(db_session)
    first = _run(db_session, space.id)
    generation_id = first["generation_id"]
    queued = _queue(db_session, space.id)
    assert queued.attempt == 0
    observed_partial_change = False
    for _ in range(128):
        steward_delivery.drain(bind=db_session.get_bind(), generation_id=generation_id, limit=1)
        db_session.expire_all()
        targets = _intents(db_session, generation_id, phase="target")
        changed = [row for row in targets if row.payload_json.get("changed")]
        if changed and not observed_partial_change:
            observed_partial_change = True
            assert (
                steward.lease_next_steward_job(db_session, leased_by="too-early", space_id=space.id)
                is None
            )
            assert db_session.get(StewardJob, queued.id).attempt == 0
            assert (
                db_session.scalar(
                    select(func.count(DomainEvent.id)).where(
                        DomainEvent.type == "term.steward_updated"
                    )
                )
                == 0
            )
            steward_gc.collect(db_session.get_bind(), batch_size=4)
            db_session.expire_all()
            assert all(
                row.status in ("pending", "done") for row in _intents(db_session, generation_id)
            )
        if all(row.status == "done" for row in _intents(db_session, generation_id)):
            break
    else:
        pytest.fail("terminology did not finish within its finite fixture workset")
    assert observed_partial_change
    assert (
        db_session.scalar(
            select(func.count(StewardTermProjection.id)).where(
                StewardTermProjection.space_id == space.id,
                StewardTermProjection.term.is_not(None),
                StewardTermProjection.status == "active",
            )
        )
        == 2
    )
    assert (
        db_session.scalar(
            select(func.count(DomainEvent.id)).where(DomainEvent.type == "term.steward_updated")
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count(Notification.id)).where(Notification.kind == "steward_suggestion")
        )
        == 0
    )

    def must_reuse(*_args, **_kwargs):
        raise AssertionError("completed terminology/structure must be reused")

    monkeypatch.setattr(steward_terminology, "delivery_items_for_generation", must_reuse)
    monkeypatch.setattr(steward_terminology, "prepare_delivery_item", must_reuse)
    monkeypatch.setattr(steward_pipeline, "start_search", must_reuse)
    second = _run(db_session, space.id, deliver=True)
    assert second["stats"]["derived_recomputed"] == 0
    assert [row.payload_json["phase"] for row in _intents(db_session, second["generation_id"])] == [
        "complete"
    ]
    account = db_session.get(Account, _account_id(db_session, viewer))
    payload = personal_family_view.current_view_payload(
        db_session, account=account, space_id=space.id
    )
    assert payload is not None and payload["status"] == "current"
    labels = {edge["to_user_id"]: edge["term"] for edge in payload["edges"]}
    assert labels[grandmother.id] == "姥姥" and labels[grandfather.id] == "姥爷"
    assert (
        steward.lease_next_steward_job(db_session, leased_by="settled", space_id=space.id) is None
    )
    third = _run(db_session, space.id, deliver=True)
    assert third["stats"]["fingerprint_short_circuit"] == 1
    assert third["stats"]["derived_recomputed"] == 0


def test_unchanged_hot_generation_carries_only_unfinished_terminology(db_session, monkeypatch):
    space, _viewer, _ancestor = _long_chain(db_session)
    first = _run(db_session, space.id)
    original = _intents(db_session, first["generation_id"], phase="target")
    assert len(original) >= 2
    for _ in range(64):
        steward_delivery.drain(
            bind=db_session.get_bind(), generation_id=first["generation_id"], limit=1
        )
        db_session.expire_all()
        completed = [row for row in original if row.status == "done"]
        if completed:
            break
    else:
        pytest.fail("fixture did not complete a terminology batch")
    assert len(completed) < len(original)
    assert not any(row.payload_json.get("changed") for row in completed)
    completed_keys = {row.intent_key for row in completed}
    pending_effects = {
        row.intent_key: row.effect_fingerprint for row in original if row.status == "pending"
    }

    # No display effect has invalidated the current input: the hot core can
    # proceed without waiting for independent optional suggestions to finish.
    second = _run(db_session, space.id)
    assert second["stats"]["fingerprint_short_circuit"] == 1
    remaining = _intents(db_session, second["generation_id"], phase="target")
    assert {row.intent_key for row in remaining} == set(pending_effects)
    assert all(row.intent_key not in completed_keys for row in remaining)
    assert all(row.effect_fingerprint == pending_effects[row.intent_key] for row in remaining)
    prepare = steward_terminology.prepare_delivery_item
    seen = []

    def only_remaining(bind, *, item):
        seen.append((item["viewer_account_id"], [t["target_user_id"] for t in item["targets"]]))
        return prepare(bind, item=item)

    monkeypatch.setattr(steward_terminology, "prepare_delivery_item", only_remaining)
    assert _drain(db_session, second["generation_id"])["delivery_failed"] == 0
    assert len(seen) == len(remaining)


@pytest.mark.parametrize("release", ["membership", "config"])
def test_pending_terminology_yields_to_other_space_and_invalid_source_releases_gate(
    db_session, monkeypatch, release
):
    space, _viewer, _grandmother, grandfather = _two_preferred_targets(db_session)
    _others, other_space = _family(db_session, size=2, name="term-other")
    monkeypatch.setattr(config, "STEWARD_MAX_CONCURRENT_JOBS", 2)
    result = _run(db_session, space.id)
    for _ in range(32):
        steward_delivery.drain(
            bind=db_session.get_bind(), generation_id=result["generation_id"], limit=1
        )
        db_session.expire_all()
        if any(
            row.payload_json.get("changed") for row in _intents(db_session, result["generation_id"])
        ):
            break
    else:
        pytest.fail("fixture did not produce the presentation change")
    queued = _queue(db_session, space.id)
    assert (
        steward.lease_next_steward_job(db_session, leased_by="too-early", space_id=space.id) is None
    )
    other = steward.lease_next_steward_job(db_session, leased_by="other-space")
    assert other is not None and other.space_id == other_space.id
    assert queued.attempt == 0
    if release == "membership":
        member = db_session.scalar(
            select(SpaceMember).where(
                SpaceMember.space_id == space.id, SpaceMember.user_id == grandfather.id
            )
        )
        member.status = "removed"
        db_session.commit()
    else:
        monkeypatch.setattr(config, "STEWARD_ASSIST_TERMINOLOGY", False)
    released = steward.lease_next_steward_job(db_session, leased_by="new-input", space_id=space.id)
    assert released is not None and released.id == queued.id and released.attempt == 1


def test_terminology_failure_is_independent_and_budget_survives_next_generation(
    db_session, monkeypatch
):
    space, viewer, ancestor = _long_chain(db_session)
    account_id = _account_id(db_session, viewer)
    prepare = steward_terminology.prepare_delivery_item
    calls = []

    def poison(bind, *, item):
        if item["viewer_account_id"] == account_id and any(
            target["target_user_id"] == ancestor.id for target in item["targets"]
        ):
            calls.append(item["generation_id"])
            raise RuntimeError("synthetic terminology failure")
        return prepare(bind, item=item)

    monkeypatch.setattr(steward_terminology, "prepare_delivery_item", poison)
    first = _run(db_session, space.id, deliver=True)
    first_failed = [
        row for row in _intents(db_session, first["generation_id"]) if row.status == "failed"
    ]
    assert len(first_failed) == 1 and len(calls) == 2
    assert db_session.get(StewardGeneration, first["generation_id"]).status == "published"
    first_job_id = db_session.get(StewardGeneration, first["generation_id"]).job_id
    assert db_session.get(StewardJob, first_job_id).last_event_cursor == first["trigger_cursor"]
    second = _run(db_session, space.id, deliver=True)
    second_failed = [
        row for row in _intents(db_session, second["generation_id"]) if row.status == "failed"
    ]
    assert len(second_failed) == 1 and len(calls) == 2
    assert first_failed[0].effect_fingerprint == second_failed[0].effect_fingerprint
    failed = second_failed[0]
    budget = db_session.scalar(
        select(StewardRetryBudget).where(
            StewardRetryBudget.fingerprint == failed.effect_fingerprint
        )
    )
    assert budget.exhausted and budget.attempts == 2
    monkeypatch.setattr(steward_terminology, "prepare_delivery_item", prepare)
    with steward._immediate_tx(db_session):
        retry = steward_delivery.retry_intent(
            db_session,
            space_id=space.id,
            intent_id=failed.id,
            expected_attempt=failed.attempt,
            expected_policy_version=steward.POLICY_VERSION,
        )
    assert retry["coalesced"] is False
    assert _drain(db_session, second["generation_id"])["terminology_projections"] >= 1
    assert db_session.get(StewardDeliveryIntent, failed.id).status == "done"


@pytest.mark.parametrize("race", ["presentation", "lease"])
def test_detached_terminology_rechecks_current_inputs_and_exact_claim(
    db_session, monkeypatch, race
):
    space, _viewer, _ancestor = _long_chain(db_session)
    result = _run(db_session, space.id)
    generation_id = result["generation_id"]
    prepare = steward_terminology.prepare_delivery_item
    raced = []

    def mutate_after_prepare(bind, *, item):
        prepared = prepare(bind, item=item)
        if prepared is None or raced:
            return prepared
        with Session(bind=bind) as writer:
            intent = writer.scalar(
                select(StewardDeliveryIntent).where(
                    StewardDeliveryIntent.generation_id == generation_id,
                    StewardDeliveryIntent.kind == "terminology",
                    StewardDeliveryIntent.payload_json["phase"].as_string() == "target",
                    StewardDeliveryIntent.payload_json["viewer_account_id"].as_integer()
                    == item["viewer_account_id"],
                    StewardDeliveryIntent.lease_owner.is_not(None),
                )
            )
            raced.append(
                (
                    intent.id,
                    item["viewer_account_id"],
                    [target["target_user_id"] for target in item["targets"]],
                )
            )
            if race == "lease":
                intent.lease_owner = "replacement-worker"
                intent.attempt += 1
                intent.lease_until = utcnow() + timedelta(seconds=60)
            else:
                # A genuine display producer changes between snapshot and apply.
                entry = writer.scalar(select(TermEntry).where(TermEntry.level == "locale"))
                entry.revision += 1
            writer.commit()
        return prepared

    monkeypatch.setattr(steward_terminology, "prepare_delivery_item", mutate_after_prepare)
    for _ in range(32):
        steward_delivery.drain(bind=db_session.get_bind(), generation_id=generation_id, limit=1)
        if raced:
            break
    assert raced
    db_session.expire_all()
    intent_id, account_id, target_ids = raced[0]
    intent = db_session.get(StewardDeliveryIntent, intent_id)
    assert intent.status == "pending"
    assert (
        db_session.scalar(
            select(StewardTermProjection.id).where(
                StewardTermProjection.viewer_account_id == account_id,
                StewardTermProjection.target_user_id.in_(target_ids),
            )
        )
        is None
    )
    if race == "lease":
        assert intent.lease_owner == "replacement-worker" and intent.attempt == 2
    else:
        assert intent.lease_owner is None
        _drain(db_session, generation_id)
        receipt = _intents(db_session, generation_id, phase="complete")[0]
        assert receipt.status == "done" and receipt.payload_json["inputs_stable"] is False
    assert db_session.get(StewardGeneration, generation_id).status == "published"


def test_notify_false_also_covers_replayed_suggestion(db_session):
    space, viewer, _mother, grandmother = _grandchild_family(db_session)
    account_id = _account_id(db_session, viewer)
    ids = []
    for _ in range(2):
        row, _created = steward_suggestions.upsert_suggestion(
            db_session,
            space_id=space.id,
            origin="deterministic",
            kind="term_preference",
            subject_user_id=viewer.id,
            object_user_id=grandmother.id,
            value_json={"concept_code": "Uf-Uf", "term": "姥姥"},
            evidence_json={"facts": []},
            policy_version=steward.POLICY_VERSION,
            recipient_account_ids=[account_id],
            viewer_account_id=account_id,
            notify=False,
        )
        ids.append(row.id)
        db_session.commit()
    assert ids[0] == ids[1]
    assert db_session.scalar(select(func.count(StewardSuggestion.id))) == 1
    assert (
        db_session.scalar(
            select(func.count(Notification.id)).where(Notification.kind == "steward_suggestion")
        )
        == 0
    )


def test_rag_failure_uses_separate_session_after_committed_core(db_session, monkeypatch):
    people, _space = _family(db_session, size=2, name="delivery-rag")
    account_id = _account_id(db_session, people[0])
    core_sessions = []
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_ENABLED", False)
    monkeypatch.setattr(config, "RAG_ENABLED", True)

    def core(session):
        core_sessions.append(session)
        session.get(Account, account_id).failed_attempts = 1
        return 1

    def rag(session, **_kwargs):
        assert session is not core_sessions[0]
        with Session(bind=session.get_bind()) as observer:
            assert observer.get(Account, account_id).failed_attempts == 1
        session.get(Account, account_id).failed_attempts = 2
        session.flush()
        raise RuntimeError("synthetic RAG failure")

    monkeypatch.setattr(maintenance.agent_queue, "reaper_pass", core)
    monkeypatch.setattr(maintenance.rag_maintenance, "run_maintenance_batch", rag)
    result = maintenance.run_maintenance_tick()
    db_session.expire_all()
    assert result["agent_reaped"] == 1 and result["rag_index_materialized"] == 0
    assert db_session.get(Account, account_id).failed_attempts == 1
