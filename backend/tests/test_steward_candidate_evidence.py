"""Deterministic support, immutable history and scoped one-shot revalidation."""

from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.models.space import SpaceMember, SpaceProfileRef
from app.models.steward import (
    StewardAssistBatch,
    StewardCandidateEvidenceVersion,
    StewardJob,
    StewardLlmCandidate,
    StewardModelCall,
)
from app.services import source_facts, steward
from app.services import steward_candidate_evidence as evidence
from app.services.steward_guard import candidate_digest
from app.utils.timeutil import utcnow
from conftest import (
    create_agent_fixture,
    create_space_member,
    create_user_with_pin,
    seed_space_with_owner,
)


def family(session, *, name="candidate-evidence", complete=True):
    a, space = create_agent_fixture(session, name=f"{name}-a")
    people = []
    for suffix in ("b", "p", "q"):
        person = create_user_with_pin(session, f"{name}-{suffix}", "123456")
        create_space_member(session, space.id, person.id)
        people.append(person)
    b, p, q = people
    pa = fact(session, p.id, a.id, space.id)
    pb = fact(session, p.id, b.id, space.id) if complete else None
    session.commit()
    return SimpleNamespace(a=a, b=b, p=p, q=q, space=space, pa=pa, pb=pb)


def fact(session, parent_id, child_id, space_id, *, kind="biological_parent", state="confirmed"):
    row = source_facts.create_source_fact(
        session,
        fact_type=kind,
        subject_user_id=parent_id,
        object_user_id=child_id,
        provenance="manual_entry",
        space_id=space_id,
    )
    if state == "confirmed":
        source_facts.transition_source_fact(session, row, "confirm")
    elif state == "disputed":
        source_facts.transition_source_fact(session, row, "dispute")
    return row


def job(session, space_id):
    row = StewardJob(
        space_id=space_id,
        cause="integrity_scan",
        trigger_cursor=steward.current_event_watermark(session),
        status="succeeded",
        policy_version=config.POLICY_VERSION,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def candidate(session, world, *, reverse=False, kind="direct_sibling", status="proposed"):
    origin = job(session, world.space.id)
    subject, object_ = (world.b.id, world.a.id) if reverse else (world.a.id, world.b.id)
    row = StewardLlmCandidate(
        space_id=world.space.id,
        job_id=origin.id,
        candidate_kind=kind,
        payload_json={"kind": kind, "subject_user_id": subject, "object_user_id": object_},
        candidate_digest=candidate_digest(kind, subject, object_),
        status=status,
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def provenance(session, space_id):
    origin = job(session, space_id)
    batch = StewardAssistBatch(
        space_id=space_id,
        job_id=origin.id,
        evidence_hash="0" * 64,
        policy_version=config.POLICY_VERSION,
        status="applying",
        attempt=1,
        lease_owner="evidence-test",
        lease_until=utcnow() + timedelta(minutes=5),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(batch)
    session.flush()
    call = StewardModelCall(
        space_id=space_id,
        job_id=origin.id,
        batch_id=batch.id,
        policy_version=config.POLICY_VERSION,
        assist_kind="candidate",
        prompt_digest="0" * 64,
        prompt_chars=0,
        status="succeeded",
        seq=1,
        created_at=utcnow(),
    )
    session.add(call)
    session.flush()
    return batch, call


def record(session, row):
    batch, call = provenance(session, row.space_id)
    version = evidence.record_for_candidate(
        session, row, batch=batch, model_call=call, now=utcnow()
    )
    session.commit()
    return version


def versions(session, candidate_id=None):
    query = select(StewardCandidateEvidenceVersion).order_by(StewardCandidateEvidenceVersion.id)
    if candidate_id is not None:
        query = query.where(StewardCandidateEvidenceVersion.candidate_id == candidate_id)
    return list(session.scalars(query))


def test_all_complete_parent_pairs_are_canonical_and_unrelated_facts_do_not_version(db_session):
    world = family(db_session)
    row = candidate(db_session, world)
    row.payload_json = {**row.payload_json, "rationale": "MODEL-FREE-TEXT-MUST-NOT-BE-EVIDENCE"}
    original_candidate = (row.id, row.job_id, row.candidate_digest, copy.deepcopy(row.payload_json))
    first = record(db_session, row)
    assert first is not None
    first_snapshot = copy.deepcopy(first.support_facts_json)
    assert [item["id"] for item in first_snapshot] == [world.pa.id, world.pb.id]
    assert all(set(item) == evidence._FACT_KEYS for item in first_snapshot)
    assert "MODEL-FREE-TEXT" not in str(first_snapshot)
    assert first.validation_contract_version == evidence.VALIDATION_CONTRACT_VERSION
    assert row.attribution_status == "versioned"

    # Single-sided support and other parent kinds are not a sibling certificate.
    qa = fact(db_session, world.q.id, world.a.id, world.space.id)
    adoptive = fact(db_session, world.q.id, world.b.id, world.space.id, kind="adoptive_parent")
    fact(db_session, world.a.id, world.b.id, world.space.id, kind="partner")
    assert record(db_session, row).id == first.id
    source_facts.transition_source_fact(db_session, adoptive, "revoke")
    qb = fact(db_session, world.q.id, world.b.id, None)
    second = record(db_session, row)
    assert second.id != first.id and second.evidence_digest != first.evidence_digest
    assert [item["id"] for item in second.support_facts_json] == sorted(
        [world.pa.id, world.pb.id, qa.id, qb.id]
    )
    assert second.support_facts_json[-1]["space_id"] is None
    assert record(db_session, row).id == second.id
    assert first.support_facts_json == first_snapshot
    assert (row.id, row.job_id, row.candidate_digest, row.payload_json) == original_candidate
    assert len(versions(db_session)) == 2


@pytest.mark.parametrize("kind", ["spouse", "adoptive_parent", "guardian", "direct_sibling"])
def test_unsupported_type_or_incomplete_support_stays_explicit(db_session, kind):
    world = family(db_session, complete=False)
    row = candidate(db_session, world, kind=kind)
    assert row.attribution_status == "legacy"
    assert record(db_session, row) is None
    assert row.attribution_status == "unsupported"
    assert versions(db_session) == []


@pytest.mark.parametrize("change", ["foreign_space", "proposed", "disputed", "parent_removed"])
def test_only_current_scoped_confirmed_support_can_be_adopted(db_session, change):
    world = family(db_session, complete=False)
    if change == "foreign_space":
        other = seed_space_with_owner(db_session, world.a.id, name="foreign-evidence")
        fact(db_session, world.p.id, world.b.id, other.id)
    elif change in ("proposed", "disputed"):
        fact(db_session, world.p.id, world.b.id, world.space.id, state=change)
    else:
        fact(db_session, world.p.id, world.b.id, world.space.id)
        member = db_session.scalar(
            select(SpaceMember).where(
                SpaceMember.space_id == world.space.id, SpaceMember.user_id == world.p.id
            )
        )
        member.status = "removed"
    row = candidate(db_session, world)
    assert record(db_session, row) is None
    assert row.attribution_status == "unsupported"


def test_active_reference_and_owner_match_steward_input_scope(db_session):
    world = family(db_session)
    member = db_session.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == world.space.id, SpaceMember.user_id == world.p.id
        )
    )
    member.status = "removed"
    db_session.add(
        SpaceProfileRef(
            space_id=world.space.id,
            user_id=world.p.id,
            status="active",
            created_at=utcnow(),
        )
    )
    row = candidate(db_session, world)
    first = record(db_session, row)
    assert first is not None
    needed = {world.a.id, world.b.id, world.p.id}
    assert evidence._scoped_user_ids(db_session, world.space, needed) == (
        steward._space_visible_user_ids(db_session, world.space) & needed
    )


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("revoked", "source_not_confirmed"),
        ("disputed", "source_not_confirmed"),
        ("revision", "source_revision_changed"),
        ("deleted", "source_missing"),
        ("foreign_space", "source_scope_changed"),
        ("type", "source_structure_changed"),
        ("parent_removed", "source_out_of_scope"),
        ("endpoint_removed", "source_out_of_scope"),
    ],
)
def test_projection_checks_original_sources_and_cannot_substitute_new_support(
    db_session, change, reason
):
    world = family(db_session)
    row = candidate(db_session, world, status="dismissed")
    version = record(db_session, row)
    snapshot = copy.deepcopy(version.support_facts_json)
    # A newer complete certificate cannot rescue a changed saved source.
    fact(db_session, world.q.id, world.a.id, world.space.id)
    fact(db_session, world.q.id, world.b.id, world.space.id)
    if change == "revoked":
        source_facts.transition_source_fact(db_session, world.pa, "revoke")
    elif change == "disputed":
        world.pa.state = "disputed"  # Historical/admin state, no confirmed -> dispute command.
    elif change == "revision":
        world.pa.revision += 1
    elif change == "deleted":
        db_session.delete(world.pa)
    elif change == "foreign_space":
        other = seed_space_with_owner(db_session, world.a.id, name="moved-evidence")
        world.pa.space_id = other.id
    elif change == "type":
        world.pa.fact_type = "guardian"
    else:
        user_id = world.p.id if change == "parent_removed" else world.b.id
        member = db_session.scalar(
            select(SpaceMember).where(
                SpaceMember.space_id == world.space.id, SpaceMember.user_id == user_id
            )
        )
        member.status = "removed"
    db_session.commit()
    projection_job = job(db_session, world.space.id)
    assert evidence.project_version(
        db_session, candidate_id=row.id, version_id=version.id, job=projection_job
    )
    db_session.commit()
    assert version.status == "invalidated" and version.invalidation_reason == reason
    assert version.support_facts_json == snapshot
    assert version.projection_job_id == projection_job.id
    assert version.projection_checked_at is not None
    assert row.attribution_status == "versioned" and row.status == "dismissed"
    assert not evidence.project_version(
        db_session, candidate_id=row.id, version_id=version.id, job=projection_job
    )


def test_captured_version_ignores_added_facts_and_terminal_check_is_historical(db_session):
    world = family(db_session)
    row = candidate(db_session, world)
    version = record(db_session, row)
    fact(db_session, world.q.id, world.a.id, world.space.id)
    fact(db_session, world.q.id, world.b.id, world.space.id)
    db_session.commit()
    projection_job = job(db_session, world.space.id)
    moment = utcnow()
    assert evidence.project_version(
        db_session, candidate_id=row.id, version_id=version.id, job=projection_job, now=moment
    )
    db_session.commit()
    source_facts.transition_source_fact(db_session, world.pa, "revoke")
    db_session.commit()
    assert not evidence.project_version(
        db_session, candidate_id=row.id, version_id=version.id, job=projection_job
    )
    assert version.status == "projected" and version.invalidation_reason is None
    assert version.projection_checked_at == moment
    assert [item["id"] for item in version.support_facts_json] == [world.pa.id, world.pb.id]


def test_version_and_candidate_scope_must_match_captured_intent(db_session):
    world = family(db_session)
    row = candidate(db_session, world)
    version = record(db_session, row)
    other = seed_space_with_owner(db_session, world.a.id, name="other-evidence-scope")
    wrong_job = job(db_session, other.id)
    assert not evidence.project_version(
        db_session, candidate_id=row.id, version_id=version.id, job=wrong_job
    )
    right_job = job(db_session, world.space.id)
    assert not evidence.project_version(
        db_session, candidate_id=row.id + 1000, version_id=version.id, job=right_job
    )
    assert version.status == "pending"


def test_database_prevents_snapshot_and_terminal_rewrite_but_allows_nullable_fk_cleanup(db_session):
    world = family(db_session)
    row = candidate(db_session, world)
    version = record(db_session, row)
    projection_job = job(db_session, world.space.id)
    evidence.project_version(
        db_session, candidate_id=row.id, version_id=version.id, job=projection_job
    )
    db_session.commit()
    original = copy.deepcopy(version.support_facts_json)
    source_job, projection_id, candidate_id = (
        version.source_job_id,
        projection_job.id,
        row.id,
    )
    for statement in (
        "UPDATE steward_candidate_evidence_versions SET support_facts_json='[]'",
        "UPDATE steward_candidate_evidence_versions SET evidence_digest='rewritten'",
        "UPDATE steward_candidate_evidence_versions SET status='pending', "
        "projection_checked_at=NULL,projection_job_id=NULL",
        "UPDATE steward_candidate_evidence_versions SET projection_checked_at='2000-01-01'",
        "UPDATE steward_llm_candidates SET attribution_status='legacy'",
    ):
        with pytest.raises(IntegrityError):
            db_session.execute(text(statement))
        db_session.rollback()
    db_session.delete(db_session.get(StewardJob, source_job))
    db_session.delete(db_session.get(StewardJob, projection_id))
    db_session.commit()
    db_session.expire_all()
    retained = versions(db_session)[0]
    assert retained.support_facts_json == original and retained.status == "projected"
    assert retained.source_job_id is None and retained.source_batch_id is None
    assert retained.source_model_call_id is None and retained.projection_job_id is None
    assert retained.projection_checked_at is not None
    # Preserve the existing original candidate -> first job CASCADE contract.
    original_job_id = db_session.get(StewardLlmCandidate, candidate_id).job_id
    db_session.delete(db_session.get(StewardJob, original_job_id))
    db_session.commit()
    assert versions(db_session) == []
    db_session.expire_all()
    assert db_session.get(StewardLlmCandidate, candidate_id) is None


def test_two_writers_keep_one_version_and_its_first_provenance(db_session):
    world = family(db_session)
    row = candidate(db_session, world)
    sources = [provenance(db_session, world.space.id) for _ in range(2)]
    candidate_id = row.id
    source_ids = [(batch.id, call.id) for batch, call in sources]
    db_session.commit()
    bind = db_session.get_bind()
    barrier = threading.Barrier(2)

    def writer(index):
        barrier.wait(timeout=5)
        with Session(bind) as session, steward._immediate_tx(session):
            batch_id, call_id = source_ids[index]
            version = evidence.record_for_candidate(
                session,
                session.get(StewardLlmCandidate, candidate_id),
                batch=session.get(StewardAssistBatch, batch_id),
                model_call=session.get(StewardModelCall, call_id),
                now=utcnow(),
            )
            return version.id, version.source_batch_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(writer, index) for index in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0] == results[1]
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(StewardCandidateEvidenceVersion)) == 1
    assert versions(db_session)[0].source_batch_id in {item[0] for item in source_ids}
