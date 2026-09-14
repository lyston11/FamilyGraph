"""Local delivery budgets, crash fencing and metadata-only operator recovery."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from test_steward_staged_pipeline import _family, _run

from app import config
from app.models.admin_access import AdminAccessAudit
from app.models.space import SpaceMember
from app.models.steward import (
    StewardAssistBatch,
    StewardDeliveryIntent,
    StewardGeneration,
    StewardJob,
    StewardModelCall,
    StewardPublication,
    StewardRetryBudget,
)
from app.services import steward, steward_assist, steward_delivery, steward_gc, steward_runtime
from app.services.relationship_resolver import advance_search
from app.utils.timeutil import utcnow
from conftest import admin_session_headers, auth_header, create_system_admin, login


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", False)
    monkeypatch.setattr(config, "STEWARD_STAGE_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(config, "STEWARD_RETRY_BACKOFF_FIRST_SECONDS", 0)
    monkeypatch.setattr(steward_runtime, "run_slice", advance_search)
    monkeypatch.setattr(steward, "_detect_findings", lambda *_args: [])


def _intent(session, generation_id):
    return session.scalar(
        select(StewardDeliveryIntent).where(
            StewardDeliveryIntent.generation_id == generation_id,
            StewardDeliveryIntent.kind == "assist",
        )
    )


def _budget(session, intent):
    return session.scalar(
        select(StewardRetryBudget).where(
            StewardRetryBudget.space_id == intent.space_id,
            StewardRetryBudget.scope == "delivery",
            StewardRetryBudget.fingerprint == intent.effect_fingerprint,
        )
    )


def _poison(monkeypatch):
    calls = []
    original = steward_delivery._apply

    def apply(session, intent, job, space, **kwargs):
        if intent.kind == "assist":
            calls.append(intent.id)
            raise ValueError("synthetic local projection failure")
        return original(session, intent, job, space, **kwargs)

    monkeypatch.setattr(steward_delivery, "_apply", apply)
    return calls, original


def test_same_effect_keeps_failure_budget_across_scan_and_transfers_gc_root(
    db_session, monkeypatch
):
    people, space = _family(db_session, size=2, name="delivery-budget")
    calls, _original = _poison(monkeypatch)
    first = _run(db_session, space.id, deliver=True)
    first_intent = _intent(db_session, first["generation_id"])
    assert first_intent.status == "failed" and len(calls) == 2
    second = _run(db_session, space.id, deliver=True)
    second_intent = _intent(db_session, second["generation_id"])
    assert second["stats"]["fingerprint_short_circuit"] == 1
    assert first_intent.effect_fingerprint == second_intent.effect_fingerprint
    assert second_intent.status == "failed" and len(calls) == 2
    budget = _budget(db_session, second_intent)
    assert budget.attempts == budget.max_attempts == 2 and budget.exhausted
    for _ in range(20):
        assert steward_gc.collect(db_session.get_bind(), batch_size=1) <= 1
    db_session.expire_all()
    assert first_intent.status == "superseded"
    assert second_intent.status == "failed"
    assert steward_delivery.backlog(db_session) == {"failed": 1}

    # A genuine source change permits new work, while the invalid old effect
    # can terminate without retaining a permanent failed generation root.
    people[0].name = "delivery-budget-new-source"
    db_session.commit()
    third = _run(db_session, space.id, deliver=True)
    third_intent = _intent(db_session, third["generation_id"])
    assert third_intent.effect_fingerprint != second_intent.effect_fingerprint
    assert len(calls) == 4
    second_id = second_intent.id
    for _ in range(30):
        steward_gc.collect(db_session.get_bind(), batch_size=2)
    db_session.expire_all()
    old = db_session.get(StewardDeliveryIntent, second_id)
    assert old is None or old.status == "superseded"
    assert steward_delivery.backlog(db_session) == {"failed": 1}


@pytest.mark.parametrize("change", ["presentation", "membership", "policy"])
def test_gc_finds_invalid_failed_delivery_without_successor(db_session, monkeypatch, change):
    people, space = _family(db_session, size=2, name="delivery-invalid-root")
    _poison(monkeypatch)
    result = _run(db_session, space.id, deliver=True)
    intent = _intent(db_session, result["generation_id"])
    assert intent.status == "failed"
    assert steward_gc.collect(db_session.get_bind(), batch_size=1) == 0
    if change == "presentation":
        people[0].name = "delivery-source-changed"
    elif change == "membership":
        member = db_session.scalar(
            select(SpaceMember).where(
                SpaceMember.space_id == space.id, SpaceMember.user_id == people[1].id
            )
        )
        member.status = "removed"
    else:
        monkeypatch.setattr(config, "POLICY_VERSION", "new-policy")
    db_session.commit()
    assert steward_gc.collect(db_session.get_bind(), batch_size=1) == 1
    db_session.expire_all()
    assert intent.status == "superseded"
    assert steward_delivery.backlog(db_session) == {}
    assert db_session.scalar(select(func.count(StewardGeneration.id))) == 1
    assert db_session.get(StewardPublication, space.id).generation_id == result["generation_id"]


def test_durable_delivery_claim_survives_crash_and_rejects_late_failure(db_session, monkeypatch):
    _people, space = _family(db_session, size=2, name="delivery-crash")
    result = _run(db_session, space.id)
    intent = _intent(db_session, result["generation_id"])
    for other in db_session.scalars(select(StewardDeliveryIntent)):
        if other.id != intent.id:
            other.status = "done"
    db_session.commit()
    bind = db_session.get_bind()
    old = steward_delivery._claim_due(
        bind, owner="lost-process", generation_id=result["generation_id"]
    )
    assert isinstance(old, steward_delivery._Claim)
    db_session.refresh(intent)
    assert _budget(db_session, intent).attempts == 1
    intent.lease_until = utcnow() - timedelta(seconds=1)
    db_session.commit()
    resumed = steward_delivery._claim_due(
        bind, owner="new-process", generation_id=result["generation_id"]
    )
    assert isinstance(resumed, steward_delivery._Claim) and resumed.attempt == old.attempt + 1
    steward_delivery._record_failure(bind, old)
    db_session.refresh(intent)
    assert intent.lease_owner == "new-process" and intent.status == "pending"
    # Deliver the already reserved claim through the real result transaction.
    monkeypatch.setattr(steward_delivery, "_claim_due", lambda *_args, **_kwargs: resumed)
    assert steward_delivery.drain(bind=bind, limit=1)["delivery_done"] == 1
    db_session.refresh(intent)
    db_session.expire_all()
    assert intent.status == "done" and intent.lease_owner is None
    assert (
        _budget(db_session, intent).attempts == 1
    )  # Lost process consumed one; success refunded one.


def test_admin_delivery_retry_is_bounded_idempotent_and_does_not_reset_unknown(
    db_session, monkeypatch, admin_client, client
):
    create_system_admin(db_session)
    headers = admin_session_headers(admin_client)
    people, space = _family(db_session, size=2, name="delivery-admin")
    monkeypatch.setattr(config, "STEWARD_STAGE_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(config, "STEWARD_RERUN_COOLDOWN_SECONDS", 60)
    calls, original = _poison(monkeypatch)
    result = _run(db_session, space.id, deliver=True)
    intent = _intent(db_session, result["generation_id"])
    generation = db_session.get(StewardGeneration, result["generation_id"])
    batch = StewardAssistBatch(
        space_id=space.id,
        job_id=generation.job_id,
        policy_version=steward.POLICY_VERSION,
        evidence_hash="0" * 64,
        status="failed",
        attempt=1,
        fence_json={},
        error_code=steward_assist.REASON_NETWORK_UNKNOWN,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db_session.add(batch)
    db_session.flush()
    unknown = StewardModelCall(
        space_id=space.id,
        job_id=generation.job_id,
        batch_id=batch.id,
        policy_version=steward.POLICY_VERSION,
        assist_kind="candidate",
        model="test",
        prompt_digest="0" * 64,
        prompt_chars=1,
        status="unknown",
        billed_tokens=23,
        reserved_input_tokens=18,
        reserved_output_tokens=5,
        created_at=utcnow(),
    )
    db_session.add(unknown)
    db_session.commit()
    job_count = db_session.scalar(select(func.count(StewardJob.id)))
    listed = admin_client.get(
        f"/admin-api/v1/steward/deliveries?space_id={space.id}&page_size=1", headers=headers
    )
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["intent_id"] == intent.id and item["status"] == "failed"
    assert set(item) == {
        "intent_id",
        "generation_id",
        "space_id",
        "kind",
        "status",
        "attempt",
        "available_at",
        "error_code",
    }
    assert (
        admin_client.get(
            "/admin-api/v1/steward/deliveries?page_size=101", headers=headers
        ).status_code
        == 422
    )

    path = f"/admin-api/v1/steward/spaces/{space.id}/deliveries/{intent.id}/retry"
    body = {
        "reason": "token=raw-reason-must-not-persist",
        "expected_policy_version": steward.POLICY_VERSION,
        "expected_attempt": intent.attempt,
    }
    family_headers = auth_header(login(client, people[0].name, "123456").json())
    assert admin_client.post(path, headers=family_headers, json=body).status_code == 401
    assert client.post(path, headers=headers, json=body).status_code == 404
    assert (
        admin_client.post(path, headers=headers, json={**body, "expected_attempt": 99}).status_code
        == 409
    )
    assert (
        admin_client.post(
            path, headers=headers, json={**body, "expected_policy_version": "other"}
        ).status_code
        == 409
    )
    accepted = admin_client.post(path, headers=headers, json=body)
    assert accepted.status_code == 202 and not accepted.json()["coalesced"]
    duplicate = admin_client.post(path, headers=headers, json=body)
    assert duplicate.status_code == 202 and duplicate.json()["coalesced"]
    db_session.expire_all()
    budget = _budget(db_session, intent)
    assert budget.manual_grants == 1 and budget.max_attempts == 2
    steward_delivery.drain(bind=db_session.get_bind(), generation_id=generation.id, limit=64)
    db_session.refresh(intent)
    assert intent.status == "failed" and len(calls) == 2
    assert admin_client.post(path, headers=headers, json=body).status_code == 409
    current_body = {**body, "expected_attempt": intent.attempt}
    assert admin_client.post(path, headers=headers, json=current_body).status_code == 429
    db_session.refresh(budget)
    budget.manual_retry_at = utcnow() - timedelta(seconds=61)
    db_session.commit()
    assert admin_client.post(path, headers=headers, json=current_body).status_code == 202
    monkeypatch.setattr(steward_delivery, "_apply", original)
    http_calls = []
    monkeypatch.setattr(steward_assist, "_post_json", lambda *_args: http_calls.append(True))
    steward_delivery.drain(bind=db_session.get_bind(), generation_id=generation.id, limit=64)
    db_session.expire_all()
    assert intent.status == "done"
    assert batch.status == "failed" and unknown.status == "unknown" and unknown.billed_tokens == 23
    assert http_calls == []
    assert db_session.scalar(select(func.count(StewardModelCall.id))) == 1
    assert db_session.scalar(select(func.count(StewardJob.id))) == job_count
    audits = list(
        db_session.scalars(
            select(AdminAccessAudit).where(AdminAccessAudit.action == "steward.delivery.retry")
        )
    )
    assert audits and all("raw-reason" not in str(row.filters_json) for row in audits)
    assert all(row.filters_json["intent_id"] == intent.id for row in audits)
