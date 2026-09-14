"""In-place legacy upgrade, lossless refusal and actual SQLite FK contracts."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session
from test_rag_lifecycle_migrations import migrate, migration_engine
from test_steward_candidate_evidence import candidate, fact, family, job, record, versions

from app.models.notification import Notification
from app.models.steward import StewardCandidateEvidenceVersion, StewardLlmCandidate
from app.models.steward_inferred import StewardInferredEdge
from app.models.steward_suggestion import StewardSuggestion, StewardSuggestionRecipient
from app.services.steward_guard import candidate_digest
from app.utils.timeutil import utcnow

PARENT = "0048_steward_terminology_publication"
HEAD = "0049_steward_candidate_evidence"
_HISTORY_TABLES = (
    "steward_suggestions",
    "steward_suggestion_recipients",
    "steward_inferred_edges",
    "notifications",
    "source_facts",
)


def _snapshot(engine, *, schema=False):
    with engine.connect() as connection:
        snapshot = {
            table: connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
            for table in _HISTORY_TABLES
        }
        snapshot["candidates"] = connection.execute(
            text(
                "SELECT id,space_id,job_id,candidate_kind,payload_json,candidate_digest,"
                "status,created_at FROM steward_llm_candidates ORDER BY id"
            )
        ).all()
        snapshot["versions"] = (
            connection.execute(
                text("SELECT * FROM steward_candidate_evidence_versions ORDER BY id")
            ).all()
            if inspect(connection).has_table("steward_candidate_evidence_versions")
            else []
        )
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        if schema:
            snapshot["attribution"] = connection.execute(
                text("SELECT id,attribution_status FROM steward_llm_candidates ORDER BY id")
            ).all()
            snapshot["schema"] = connection.execute(
                text("SELECT type,name,sql FROM sqlite_master ORDER BY type,name")
            ).all()
            snapshot["head"] = connection.execute(text("SELECT * FROM alembic_version")).all()
        return snapshot


def _seed_legacy(engine):
    with Session(engine, expire_on_commit=False) as session:
        world = family(session, name="migration-evidence")
        origin = job(session, world.space.id)
        confirmed = fact(session, world.a.id, world.b.id, world.space.id, kind="direct_sibling")
        for candidate_id, (subject, object_, status) in enumerate(
            ((world.a.id, world.b.id, "proposed"), (world.b.id, world.a.id, "dismissed")),
            start=41,
        ):
            # The pre-0049 table has no attribution column. Real legacy IDs,
            # directed digests and even old free text must survive unchanged.
            payload = {
                "kind": "direct_sibling",
                "subject_user_id": subject,
                "object_user_id": object_,
                "rationale": "retained historical payload",
            }
            session.execute(
                text(
                    "INSERT INTO steward_llm_candidates "
                    "(id,space_id,job_id,candidate_kind,payload_json,"
                    "candidate_digest,status,created_at) "
                    "VALUES (:id,:space,:job,'direct_sibling',:payload,:digest,:status,:now)"
                ),
                {
                    "id": candidate_id,
                    "space": world.space.id,
                    "job": origin.id,
                    "payload": json.dumps(payload),
                    "digest": candidate_digest("direct_sibling", subject, object_),
                    "status": status,
                    "now": utcnow().isoformat(sep=" "),
                },
            )
            suggestion = StewardSuggestion(
                space_id=world.space.id,
                origin="model",
                kind="relation_proposal",
                subject_user_id=subject,
                object_user_id=object_,
                value_json={"fact_type": "direct_sibling"},
                evidence_json={"facts": [[world.pa.id, 2], [world.pb.id, 2]]},
                evidence_hash=f"legacy-evidence-{candidate_id}",
                dedupe_key=f"legacy-{candidate_id}",
                policy_version="historical",
                status="proposed" if candidate_id == 41 else "resolved",
                revision=4,
                source_candidate_id=candidate_id,
                source_job_id=origin.id,
                linked_fact_id=confirmed.id if candidate_id == 42 else None,
                submit_key="historical-confirmation" if candidate_id == 42 else None,
                submit_result_json={"linked_fact_id": confirmed.id} if candidate_id == 42 else None,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(suggestion)
            session.flush()
            session.add(
                StewardSuggestionRecipient(
                    suggestion_id=suggestion.id,
                    account_id=world.a.account.id,
                    read_at=utcnow(),
                    dismissed_at=utcnow(),
                    cooldown_until=utcnow() + timedelta(days=90),
                    cooldown_evidence_hash=suggestion.evidence_hash,
                    created_at=utcnow(),
                )
            )
            session.add(
                Notification(
                    space_id=world.space.id,
                    recipient_account_id=world.a.account.id,
                    kind="steward_suggestion",
                    suggestion_id=suggestion.id,
                    title="Historical suggestion",
                    created_at=utcnow(),
                    read_at=utcnow(),
                )
            )
            session.add(
                StewardInferredEdge(
                    space_id=world.space.id,
                    subject_user_id=subject,
                    object_user_id=object_,
                    relation_kind="direct_sibling",
                    status="rejected" if candidate_id == 41 else "confirmed",
                    origin="llm",
                    source_candidate_id=candidate_id,
                    evidence_hash=suggestion.evidence_hash,
                    evidence_json=suggestion.evidence_json,
                    revision=6,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                    resolved_at=utcnow(),
                )
            )
        session.commit()


@pytest.mark.parametrize("foreign_keys", [False, True])
def test_in_place_upgrade_preserves_legacy_identity_and_all_confirmation_dismissal_history(
    tmp_path, foreign_keys
):
    # Ancestors predate the FK-state contract. Only 0049's actual connection is
    # under this test's ON/OFF matrix, as in the existing publication tests.
    result = migrate(tmp_path, "upgrade", PARENT, foreign_keys=False)
    assert result.returncode == 0, result.stderr
    engine = migration_engine(tmp_path)
    try:
        _seed_legacy(engine)
        before = _snapshot(engine)
        old_indexes = inspect(engine).get_indexes("steward_llm_candidates")
        old_unique = inspect(engine).get_unique_constraints("steward_llm_candidates")
        old_foreign_keys = inspect(engine).get_foreign_keys("steward_llm_candidates")
        for direction, target in (("upgrade", "head"), ("downgrade", "-1"), ("upgrade", "head")):
            result = migrate(tmp_path, direction, target, foreign_keys=foreign_keys)
            assert result.returncode == 0, result.stderr
            assert _snapshot(engine) == before
            assert inspect(engine).get_indexes("steward_llm_candidates") == old_indexes
            assert inspect(engine).get_unique_constraints("steward_llm_candidates") == old_unique
            assert inspect(engine).get_foreign_keys("steward_llm_candidates") == old_foreign_keys
            with engine.connect() as connection:
                expected_head = PARENT if direction == "downgrade" else HEAD
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == expected_head
                )
                if direction == "upgrade":
                    assert connection.execute(
                        text("SELECT id,attribution_status FROM steward_llm_candidates ORDER BY id")
                    ).all() == [(41, "legacy"), (42, "legacy")]
                    assert (
                        connection.scalar(
                            text("SELECT count(*) FROM steward_candidate_evidence_versions")
                        )
                        == 0
                    )
        deletes = {
            fk["constrained_columns"][0]: fk["options"]["ondelete"]
            for fk in inspect(engine).get_foreign_keys("steward_candidate_evidence_versions")
        }
        assert deletes == {
            "candidate_id": "CASCADE",
            "space_id": "CASCADE",
            "source_job_id": "SET NULL",
            "source_batch_id": "SET NULL",
            "source_model_call_id": "SET NULL",
            "projection_job_id": "SET NULL",
        }
    finally:
        engine.dispose()


@pytest.mark.parametrize("foreign_keys", [False, True])
@pytest.mark.parametrize("adoption", ["unsupported", "versioned", "certificate"])
def test_adopted_attribution_or_versions_refuse_downgrade_without_first_ddl(
    tmp_path, foreign_keys, adoption
):
    result = migrate(tmp_path, "upgrade", PARENT, foreign_keys=False)
    assert result.returncode == 0, result.stderr
    result = migrate(tmp_path, "upgrade", "head", foreign_keys=foreign_keys)
    assert result.returncode == 0, result.stderr
    engine = migration_engine(tmp_path)
    try:
        with Session(engine, expire_on_commit=False) as session:
            world = family(session, name="adopted-attribution")
            row = candidate(session, world)
            if adoption == "certificate":
                assert record(session, row) is not None
                stored = versions(session)[0].support_facts_json
            else:
                row.attribution_status = adoption
                session.commit()
                stored = None
        before = _snapshot(engine, schema=True)
        result = migrate(tmp_path, "downgrade", "-1", foreign_keys=foreign_keys)
        assert result.returncode != 0 and "retain data and roll forward" in result.stderr
        assert "ACTUAL_ALEMBIC_DDL_COUNT=0" in result.stdout
        assert _snapshot(engine, schema=True) == before
        with Session(engine) as session:
            candidate_row = session.scalar(select(StewardLlmCandidate))
            assert candidate_row.attribution_status == (
                "versioned" if adoption == "certificate" else adoption
            )
            if stored is not None:
                assert (
                    session.scalar(select(StewardCandidateEvidenceVersion)).support_facts_json
                    == stored
                )
    finally:
        engine.dispose()


@pytest.mark.parametrize("foreign_keys", [False, True])
def test_ambiguous_relative_deep_downgrade_preserves_entire_schema(tmp_path, foreign_keys):
    result = migrate(tmp_path, "upgrade", PARENT, foreign_keys=False)
    assert result.returncode == 0, result.stderr
    result = migrate(tmp_path, "upgrade", "head", foreign_keys=foreign_keys)
    assert result.returncode == 0, result.stderr
    engine = migration_engine(tmp_path)
    try:
        before = _snapshot(engine, schema=True)
        result = migrate(tmp_path, "downgrade", "-2", foreign_keys=foreign_keys)
        assert result.returncode != 0 and "ambiguous" in result.stderr.lower()
        assert "ACTUAL_ALEMBIC_DDL_COUNT=0" in result.stdout
        assert _snapshot(engine, schema=True) == before
    finally:
        engine.dispose()
