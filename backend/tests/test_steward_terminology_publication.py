"""Detached terminology delivery through real, immutable publication targets."""

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import event, select
from test_steward import _confirm, _person
from test_steward_staged_pipeline import _run
from test_steward_terminology import _account_id, _grandchild_family

from app import config
from app.models.account import Account
from app.models.steward import (
    StewardGeneration,
    StewardGenerationView,
    StewardJob,
    StewardPublication,
    StewardTermProjection,
    StewardTermSuppression,
)
from app.models.term_registry import TermUsage
from app.services import (
    personal_family_view,
    steward_runtime,
    steward_snapshot,
    steward_terminology,
    steward_terminology_snapshot,
    terms,
)
from app.services.relationship_resolver import advance_search
from app.services.steward_pipeline import write_transaction
from app.utils.timeutil import utcnow


@pytest.fixture(autouse=True)
def _setup(db_session, monkeypatch):
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", False)
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    for kind in ("CANDIDATE", "RANKING", "EXPLANATION"):
        monkeypatch.setattr(config, f"STEWARD_ASSIST_{kind}", False)
    monkeypatch.setattr(steward_runtime, "run_slice", advance_search)
    steward_terminology._DELIVERY_SNAPSHOTS.clear()
    terms.seed_builtin_packs(db_session)
    db_session.commit()
    yield
    steward_terminology._DELIVERY_SNAPSHOTS.clear()


def _publication(session, *, extra_targets=0, usage=True):
    space, viewer, mother, grandmother = _grandchild_family(session)
    account_id = _account_id(session, viewer)
    targets = [grandmother]
    for index in range(extra_targets):
        target = _person(session, space.id, f"term-extra-{index}", gender="f")
        _confirm(session, "biological_parent", target.id, mother.id, space_id=space.id)
        targets.append(target)
    if usage:
        terms.record_usage_and_promote(
            session,
            account_id=account_id,
            profile_id=viewer.id,
            space_id=space.id,
            concept_code="Uf-Uf",
            term="姥姥",
            source_event="manual_select",
        )
    session.commit()
    _run(session, space.id, deliver=False)
    publication = session.get(StewardPublication, space.id)
    assert publication is not None
    generation = session.get(StewardGeneration, publication.generation_id)
    assert generation is not None and generation.status == "published"
    items = [
        item
        for item in steward_terminology.delivery_items_for_generation(
            session, generation_id=generation.id
        )
        if item["viewer_account_id"] == account_id
    ]
    session.commit()
    return space, viewer, targets, generation, items


def test_unchanged_locale_targets_need_no_delivery_but_model_alias_remains_available(db_session):
    space, viewer, targets, _generation, items = _publication(db_session, usage=False)
    assert items == []
    groups = steward_terminology.collect_model_groups(
        db_session, space_id=space.id, max_groups=8, max_targets=8
    )
    group = next(group for group in groups if group["root_user_id"] == viewer.id)
    target = next(row for row in group["targets"] if row["target_user_id"] == targets[0].id)
    assert target["projection_id"] is None
    assert target["baseline_term"] == "外婆"
    assert "姥姥" in target["allowed_terms"]


def test_delivery_planning_uses_referenced_ready_targets_without_loading_graphs(
    db_session, monkeypatch
):
    space, viewer, targets, generation, _items = _publication(db_session)
    source = db_session.scalar(
        select(StewardGenerationView).where(
            StewardGenerationView.generation_id == generation.id,
            StewardGenerationView.root_user_id == viewer.id,
        )
    )
    successor = StewardGeneration(
        space_id=space.id,
        status="running",
        execution_cursor=generation.execution_cursor,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db_session.add(successor)
    db_session.flush()
    db_session.add(
        StewardGenerationView(
            generation_id=successor.id,
            space_id=space.id,
            viewer_account_id=source.viewer_account_id,
            root_user_id=viewer.id,
            status="ready",
            result_view_id=source.id,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    db_session.commit()

    def no_snapshot(*args, **kwargs):
        raise AssertionError("delivery planning must not load a graph")

    monkeypatch.setattr(steward_snapshot, "viewer_from_session", no_snapshot)
    items = steward_terminology.delivery_items_for_generation(
        db_session, generation_id=successor.id
    )
    assert [target["target_user_id"] for item in items for target in item["targets"]] == [
        targets[0].id
    ]


def test_batch_calculates_after_read_snapshot_and_applies_all_targets_before_revision_fence(
    db_session, monkeypatch
):
    space, viewer, targets, generation, items = _publication(db_session, extra_targets=1)
    assert len(items) == 1 and len(items[0]["targets"]) == 2
    bind = db_session.get_bind()
    active_read = False
    observed = []
    original_read = steward_snapshot.read_transaction
    original_context = steward_terminology_snapshot.current_target_context

    @contextmanager
    def tracked_read(bind):
        nonlocal active_read
        active_read = True
        try:
            with original_read(bind) as reader:
                yield reader
        finally:
            active_read = False

    def calculate(snapshot, **kwargs):
        assert not active_read
        observed.append(kwargs["target_user_id"])
        return original_context(snapshot, **kwargs)

    monkeypatch.setattr(steward_snapshot, "read_transaction", tracked_read)
    monkeypatch.setattr(steward_terminology_snapshot, "current_target_context", calculate)
    prepared = steward_terminology.prepare_delivery_item(bind, item=items[0])
    assert observed == [target.id for target in targets]
    with write_transaction(bind) as writer:
        result = steward_terminology.apply_delivery_item(
            writer, job=writer.get(StewardJob, generation.job_id), prepared=prepared
        )
    assert result["projections"] == 2 and result["changed"] is True
    steward_terminology.confirm_delivery_item(bind, prepared=prepared)
    replay = steward_terminology.prepare_delivery_item(bind, item=items[0])
    with write_transaction(bind) as writer:
        result = steward_terminology.apply_delivery_item(
            writer, job=writer.get(StewardJob, generation.job_id), prepared=replay
        )
    assert result["changed"] is False and result["suggestions"] == 0
    db_session.expire_all()
    projections = list(
        db_session.scalars(
            select(StewardTermProjection).where(
                StewardTermProjection.space_id == space.id,
                StewardTermProjection.viewer_account_id == _account_id(db_session, viewer),
            )
        )
    )
    assert len(projections) == 2 and all(row.term == "姥姥" for row in projections)


def test_preference_change_between_prepare_and_apply_discards_the_whole_batch(db_session):
    space, _viewer, _targets, generation, items = _publication(db_session, extra_targets=1)
    bind = db_session.get_bind()
    prepared = steward_terminology.prepare_delivery_item(bind, item=items[0])
    usage = db_session.scalar(select(TermUsage).where(TermUsage.space_id == space.id))
    usage.created_at += timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(steward_snapshot.SnapshotChanged), write_transaction(bind) as writer:
        steward_terminology.apply_delivery_item(
            writer, job=writer.get(StewardJob, generation.job_id), prepared=prepared
        )
    assert (
        db_session.scalar(
            select(StewardTermProjection.id).where(StewardTermProjection.space_id == space.id)
        )
        is None
    )


def test_ten_targets_use_bounded_writes_and_one_snapshot_across_confirmed_commits(
    db_session, monkeypatch
):
    _space, _viewer, targets, generation, items = _publication(db_session, extra_targets=9)
    assert [len(item["targets"]) for item in items] == [8, 2]
    bind = db_session.get_bind()
    original = steward_snapshot.viewer_from_session
    loaded = []

    def load(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        loaded.append(snapshot)
        return snapshot

    monkeypatch.setattr(steward_snapshot, "viewer_from_session", load)
    applied = 0
    for item in items:
        prepared = steward_terminology.prepare_delivery_item(bind, item=item)
        with write_transaction(bind) as writer:
            result = steward_terminology.apply_delivery_item(
                writer, job=writer.get(StewardJob, generation.job_id), prepared=prepared
            )
        steward_terminology.confirm_delivery_item(bind, prepared=prepared)
        applied += result["projections"]
        assert result["changed"]
    assert applied == len(targets) == 10
    assert len(loaded) == 1


def test_rolled_back_apply_cannot_advance_snapshot_cache_to_an_unrelated_input_version(
    db_session, monkeypatch
):
    space, viewer, targets, generation, items = _publication(db_session)
    bind = db_session.get_bind()
    loads = []
    original = steward_snapshot.viewer_from_session

    def load(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        loads.append(snapshot)
        return snapshot

    monkeypatch.setattr(steward_snapshot, "viewer_from_session", load)
    prepared = steward_terminology.prepare_delivery_item(bind, item=items[0])
    with pytest.raises(RuntimeError, match="receipt failed"), write_transaction(bind) as writer:
        steward_terminology.apply_delivery_item(
            writer, job=writer.get(StewardJob, generation.job_id), prepared=prepared
        )
        raise RuntimeError("receipt failed")
    account_id = _account_id(db_session, viewer)
    db_session.add(
        StewardTermSuppression(
            viewer_account_id=account_id,
            space_id=space.id,
            target_user_id=targets[0].id,
            suppression_key=steward_terminology.suppression_key_for(
                viewer_account_id=account_id,
                space_id=space.id,
                target_user_id=targets[0].id,
                concept_code="Uf-Uf",
                term="姥姥",
            ),
            created_at=utcnow(),
        )
    )
    db_session.commit()
    # The unrelated write has exactly the rolled-back output's revision value.
    assert steward_snapshot.input_versions(db_session, space.id) == prepared["_applied_versions"]
    db_session.commit()
    fresh = steward_terminology.prepare_delivery_item(bind, item=items[0])
    assert len(loads) == 2
    assert fresh["targets"][0]["preferred_term"] is None


def test_pure_display_needs_no_database_and_deterministic_term_survives_model_disabled(db_session):
    space, viewer, targets, _generation, items = _publication(db_session)
    bind = db_session.get_bind()
    prepared = steward_terminology.prepare_delivery_item(bind, item=items[0])
    context = prepared["targets"][0]
    steward_terminology.upsert_projection(
        db_session,
        space_id=space.id,
        viewer_account_id=_account_id(db_session, viewer),
        root_user_id=viewer.id,
        target_user_id=targets[0].id,
        concept_code="Uf-Uf",
        semantic_hash=context["semantic_hash"],
        baseline_term="外婆",
        baseline_source="locale",
        term="姥姥",
        origin="deterministic",
    )
    db_session.commit()
    with steward_snapshot.read_transaction(bind) as reader:
        snapshot = steward_snapshot.viewer_from_session(
            reader, space_id=space.id, account_id=_account_id(db_session, viewer)
        )
    assert snapshot.terminology.model_enabled is False

    def no_sql(*args, **kwargs):
        raise AssertionError("detached display must issue no SQL")

    event.listen(bind, "before_cursor_execute", no_sql)
    try:
        value = steward_terminology_snapshot.resolve_display_term(
            snapshot,
            target_user_id=targets[0].id,
            concept_code="Uf-Uf",
            structural_description="母亲的母亲",
            path=context["path"],
        )
        assert value["term"] == "姥姥" and value["source_level"] == "steward"
    finally:
        event.remove(bind, "before_cursor_execute", no_sql)
    payload = personal_family_view.current_view_payload(
        db_session,
        account=db_session.get(Account, snapshot.account_id),
        space_id=space.id,
    )
    # Publication is fenced after an output change until the successor is ready.
    assert payload["status"] != "current"
