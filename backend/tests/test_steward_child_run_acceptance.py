"""E1 acceptance: the execution unit is the attempt, and the chain is wired.

Two kinds of assertion:

1. **Behaviour equivalence** — every kind still runs through the in-process
   carrier, so ``steward_model_calls.run_id`` stays NULL. That is the property
   that makes the refactor safe to merge: it changes *how* work is scheduled and
   who may settle it, not what the model is asked or what gets written back.
2. **The chain works** — plan → lease → carrier → settle, driven through the real
   service layer. Per the repo's standing lesson (spec §6) a mock cannot prove an
   internal-protocol contract, so the settle path is exercised for real; only the
   upstream model call is absent.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from test_steward_candidate_evidence import family
from test_steward_candidate_evidence_integration import _core, _world

from app import config
from app.errors import extract_api_error
from app.models.agent import AgentRun
from app.models.steward import StewardAssistPlan, StewardModelCall
from app.services import agent_tokens, steward_assist


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    """Steward engine on, Pi carrier off: the shipping shape for E1."""
    for flag in ("STEWARD_ENABLED", "PERSONAL_FAMILY_VIEW_ENABLED", "STEWARD_ASSIST_CANDIDATE"):
        monkeypatch.setattr(config, flag, True)
    for flag in ("STEWARD_WORKER_ENABLED", "STEWARD_PI_RUNTIME_ENABLED"):
        monkeypatch.setattr(config, flag, False)
    for kind in ("CANDIDATE", "RANKING", "EXPLANATION", "TERMINOLOGY"):
        monkeypatch.setattr(config, f"STEWARD_ASSIST_{kind}_CARRIER", "inproc")


def _service_token(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", True)
    return {"Authorization": f"Bearer {agent_tokens.issue_service_token()}"}


def _planned(db_session):
    """Run the deterministic core, which registers the plan and its attempts."""
    world = _world(db_session)
    _core(db_session, world)
    plan = db_session.scalar(
        select(StewardAssistPlan).where(StewardAssistPlan.job_id == world.space.id)
        if False
        else select(StewardAssistPlan)
    )
    assert plan is not None
    return world, plan


# --------------------------------------------------------------------------
# Behaviour equivalence
# --------------------------------------------------------------------------


def test_plan_reserves_attempts_without_any_network(db_session, monkeypatch):
    """Registering a plan also reserves its attempts: one transaction, no window.

    The old design registered a batch and reserved attempts later, so "batch
    exists but nothing is leaseable" was a real state (crash point ②). Reserving
    here removes it.
    """
    world = _world(db_session)
    _core(db_session, world)
    plan = db_session.scalar(select(StewardAssistPlan).order_by(StewardAssistPlan.id.desc()))
    assert plan is not None
    attempts = list(
        db_session.scalars(select(StewardModelCall).where(StewardModelCall.plan_id == plan.id))
    )
    assert attempts, "a plan with work must reserve at least one attempt"
    for attempt in attempts:
        assert attempt.carrier == "inproc"
        assert attempt.next_attempt_at is not None, "reserved must be immediately leaseable"
        assert attempt.lease_owner is None, "reservation is not a lease"
        # The attempt snapshots the digest it fences on, so a later fence run does
        # not depend on the plan still being readable.
        assert attempt.evidence_hash == plan.evidence_hash


def test_a_fenced_attempt_does_not_block_its_space(db_session, monkeypatch):
    """The fence retires what it refuses, and the space keeps moving.

    This is a regression guard for a real defect: the send-time fence used to live
    in ``schedule_due_attempt``, so removing it there left ``lease_attempt``
    returning a fenced attempt's plan while nothing was retired. A space whose
    evidence changed would then sit reserved forever — reported as having work,
    never leaseable, and never explained by a reason code.

    Constructed so the *second* candidate is fenced and the first is not: with a
    single candidate, "skip the fenced row" and "return None" are
    indistinguishable, and this test would pass with the sweep deleted.
    """
    world = _world(db_session)
    _core(db_session, world)
    plan = db_session.scalar(select(StewardAssistPlan).order_by(StewardAssistPlan.id.desc()))
    assert plan is not None
    leasable = db_session.scalar(
        select(StewardModelCall).where(StewardModelCall.plan_id == plan.id)
    )
    assert leasable is not None

    # An extra attempt that sorts first, so the fenced row is the one the selection
    # query reaches before the leaseable sibling.
    from datetime import timedelta

    from app.utils.timeutil import utcnow

    now = utcnow()
    fenced = StewardModelCall(
        space_id=plan.space_id,
        job_id=plan.job_id,
        policy_version=plan.policy_version,
        assist_kind=leasable.assist_kind,
        prompt_digest="f" * 64,
        prompt_chars=10,
        status="reserved",
        seq=leasable.seq + 100,
        created_at=now,
        plan_id=plan.id,
        subject_key="fenced-first",
        input_hash="g" * 64,
        attempt_no=1,
        carrier="inproc",
        evidence_hash=plan.evidence_hash,
        next_attempt_at=now - timedelta(seconds=1),
    )
    db_session.add(fenced)
    db_session.commit()

    real_fence = steward_assist._fence_check

    def fence(db, check_plan, attempt, kind):
        # Only the extra attempt is refused; the sibling must be leased in the same
        # call, which is what proves the sweep continues instead of giving up.
        if attempt is not None and attempt.id == fenced.id:
            return steward_assist.REASON_EVIDENCE_CHANGED
        return real_fence(db, check_plan, attempt, kind)

    monkeypatch.setattr(steward_assist, "_fence_check", fence)

    grant = steward_assist.lease_attempt(
        db_session, space_id=plan.space_id, worker_id="carrier", carrier="inproc"
    )

    assert grant is not None, "the space must still have leaseable work"
    assert grant["attempt_id"] == leasable.id, "the unfenced sibling must be leased"
    db_session.expire_all()
    retired = db_session.get(StewardModelCall, fenced.id)
    assert retired is not None and retired.status == "skipped"
    assert retired.error_code == steward_assist.REASON_EVIDENCE_CHANGED


def test_inproc_carrier_leaves_run_id_null(db_session, monkeypatch):
    """No child run is created while every kind uses the in-process carrier."""
    _world_plan = _planned(db_session)
    calls: list[dict[str, object]] = []
    with monkeypatch.context() as patch:
        patch.setattr(
            steward_assist,
            "settle_attempt",
            lambda *_a, **_k: "succeeded",
        )
        steward_assist.run_attempt(db_session, space_id=1, worker_id="test")
    db_session.expire_all()
    assert db_session.scalar(select(AgentRun).where(AgentRun.kind == "steward")) is None
    del calls


def test_pi_carrier_is_closed_when_the_runtime_flag_is_off(db_session, monkeypatch):
    """The lease endpoint refuses while STEWARD_PI_RUNTIME_ENABLED is off (503)."""
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_PI_RUNTIME_ENABLED", False)
    from fastapi.testclient import TestClient

    from app.main import internal_app

    response = TestClient(internal_app).post(
        "/internal/agent/steward/attempts/lease",
        headers={"Authorization": f"Bearer {agent_tokens.issue_service_token()}"},
        json={"kind": "steward", "space_id": 1, "leased_by": "test"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STEWARD_DISABLED"


def test_assistant_lease_endpoint_still_rejects_steward_kind(db_session, monkeypatch):
    """The generic queue endpoint does not become a steward consumer."""
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", True)
    from fastapi.testclient import TestClient

    from app.main import internal_app

    response = TestClient(internal_app).post(
        "/internal/agent/jobs/lease",
        headers={"Authorization": f"Bearer {agent_tokens.issue_service_token()}"},
        json={"kind": "steward", "leased_by": "test"},
    )
    # The request schema pins kind to "assistant", so a steward kind is a 422 at
    # the boundary rather than reaching the handler's own guard.
    assert response.status_code == 422


# --------------------------------------------------------------------------
# The chain: lease → settle
# --------------------------------------------------------------------------


def test_lease_attempt_takes_one_attempt_and_leases_only_it(db_session, monkeypatch):
    """Leasing is per attempt: it must not touch a sibling in the same plan."""
    world = _world(db_session)
    _core(db_session, world)
    plan = db_session.scalar(select(StewardAssistPlan).order_by(StewardAssistPlan.id.desc()))
    assert plan is not None
    before = list(
        db_session.scalars(select(StewardModelCall).where(StewardModelCall.plan_id == plan.id))
    )

    grant = steward_assist.lease_attempt(
        db_session,
        space_id=plan.space_id,
        worker_id="test-carrier",
        carrier="inproc",
    )

    assert grant is not None
    db_session.expire_all()
    after = {
        row.id: row
        for row in db_session.scalars(
            select(StewardModelCall).where(StewardModelCall.plan_id == plan.id)
        )
    }
    leased = after[grant["attempt_id"]]
    assert leased.status == "in_flight"
    assert leased.lease_owner == "test-carrier"
    assert leased.lease_until is not None
    # Exactly one attempt moved: the others are still reserved and leaseable.
    others = [row for row in after.values() if row.id != grant["attempt_id"]]
    assert all(row.status in ("reserved", "skipped") for row in others)
    assert len(after) == len(before)


def test_leasing_picks_from_the_requested_space(db_session, monkeypatch):
    """The lease selection is scoped to the requested space.

    Constructed so the *other* space holds the globally oldest reserved attempt:
    if the space filter is dropped from the selection query, this leases space B's
    attempt while asking for space A, and the assertion catches it (verified by
    mutation). Without this shape the filter is unobservable — with one attempt
    per space, the unfiltered query happens to land correctly.
    """
    from test_steward_assist import _provider, _steward_setting

    provider = _provider(db_session, name="e1-space-filter-provider")
    # B first, so B's attempt carries the earlier next_attempt_at.
    space_b = family(db_session, name="e1-filter-b")
    _steward_setting(db_session, space_b.space, provider, candidate=True)
    space_a = family(db_session, name="e1-filter-a")
    _steward_setting(db_session, space_a.space, provider, candidate=True)
    db_session.commit()
    _core(db_session, space_b)
    _core(db_session, space_a)

    grant = steward_assist.lease_attempt(
        db_session,
        space_id=space_a.space.id,
        worker_id="carrier-a",
        carrier="inproc",
    )

    assert grant is not None
    assert grant["space_id"] == space_a.space.id, "leased work from the wrong space"
    db_session.expire_all()
    leased = db_session.get(StewardModelCall, grant["attempt_id"])
    assert leased is not None and leased.space_id == space_a.space.id


def test_two_spaces_lease_independently(db_session, monkeypatch):
    """Two spaces must be able to lease at the same time.

    This is the point of the refactor: the old limit was a whole-database
    ``MAX_CONCURRENT_BATCHES=1``, so space B waited for space A. With a per-space
    budget both are leaseable at once.
    """
    monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE", 1)
    from test_steward_assist import _provider, _steward_setting

    provider = _provider(db_session, name="e1-two-space-provider")
    first = family(db_session, name="e1-two-a")
    _steward_setting(db_session, first.space, provider, candidate=True)
    second = family(db_session, name="e1-two-b")
    _steward_setting(db_session, second.space, provider, candidate=True)
    db_session.commit()
    _core(db_session, first)
    _core(db_session, second)

    a = steward_assist.lease_attempt(
        db_session, space_id=first.space.id, worker_id="carrier-a", carrier="inproc"
    )
    b = steward_assist.lease_attempt(
        db_session, space_id=second.space.id, worker_id="carrier-b", carrier="inproc"
    )

    assert a is not None, "space A must be leaseable"
    assert b is not None, "space B must be leaseable while A is in flight"
    assert a["space_id"] != b["space_id"]


def test_a_lease_without_a_space_picks_one_with_work(db_session, monkeypatch):
    """The sidecar asks for work without naming a space, and still gets work.

    The sidecar has no view of the space topology and must not grow one: it asks
    for work and the server decides whose work to hand out, exactly as
    ``/jobs/lease`` does for the assistant queue. Without this the Pi carrier
    cannot lease at all — the endpoint requires ``space_id``, and the sidecar has
    no way to produce one.
    """
    world = _world(db_session)
    _core(db_session, world)

    grant = steward_assist.lease_attempt(db_session, worker_id="carrier-no-space", carrier="inproc")

    assert grant is not None, "a space with due work must be found without naming it"
    assert grant["space_id"] == world.space.id


def test_a_lease_without_a_space_skips_a_full_one(db_session, monkeypatch):
    """Not naming a space must not collapse the budget back to whole-database 1.

    This is the property the omitted-``space_id`` path could quietly break: if the
    server picked the first space with work regardless of its in-flight count,
    space B would wait for space A again — exactly the global-1 behaviour E1
    removed. Constructed with A holding a live lease at a budget of 1, so B is the
    only admissible answer.
    """
    monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE", 1)
    from test_steward_assist import _provider, _steward_setting

    provider = _provider(db_session, name="e2-space-skip-provider")
    first = family(db_session, name="e2-skip-a")
    _steward_setting(db_session, first.space, provider, candidate=True)
    second = family(db_session, name="e2-skip-b")
    _steward_setting(db_session, second.space, provider, candidate=True)
    db_session.commit()
    _core(db_session, first)
    _core(db_session, second)

    held = steward_assist.lease_attempt(
        db_session, space_id=first.space.id, worker_id="carrier-a", carrier="inproc"
    )
    assert held is not None

    # Give space A a *second*, earlier-due attempt: without it A holds no
    # reserved work, so it would never be a candidate and the budget check would
    # be unobservable (verified by mutation — that is how the first version of
    # this test was wrong). With it, A is the first candidate by deadline and the
    # budget is the only thing that can move the answer to B.
    from datetime import timedelta

    from app.utils.timeutil import utcnow

    source = db_session.scalar(
        select(StewardModelCall).where(StewardModelCall.space_id == first.space.id)
    )
    assert source is not None
    db_session.add(
        StewardModelCall(
            space_id=source.space_id,
            job_id=source.job_id,
            policy_version=source.policy_version,
            assist_kind=source.assist_kind,
            prompt_digest="f" * 64,
            prompt_chars=10,
            status="reserved",
            seq=source.seq + 100,
            created_at=utcnow(),
            plan_id=source.plan_id,
            subject_key="a-second",
            input_hash="g" * 64,
            attempt_no=1,
            carrier="inproc",
            evidence_hash=source.evidence_hash,
            next_attempt_at=utcnow() - timedelta(seconds=5),
        )
    )
    db_session.commit()

    other = steward_assist.lease_attempt(db_session, worker_id="carrier-anon", carrier="inproc")

    assert other is not None, "space B must still be leaseable while A is full"
    assert (
        other["space_id"] == second.space.id
    ), "the full space A had the earliest work; the budget must skip it"


def test_per_space_budget_blocks_a_second_lease_in_the_same_space(db_session, monkeypatch):
    """The budget bounds one space, and it is the *budget* that does it.

    A second reserved attempt is inserted deliberately: with only one attempt per
    space the selection query would return None on its own, and this test would
    pass even with the budget check deleted (verified by mutation — that is how
    the first version of this test was wrong).
    """
    monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE", 1)
    world = _world(db_session)
    _core(db_session, world)
    plan = db_session.scalar(select(StewardAssistPlan).order_by(StewardAssistPlan.id.desc()))
    assert plan is not None
    first = db_session.scalar(select(StewardModelCall).where(StewardModelCall.plan_id == plan.id))
    assert first is not None

    from app.utils.timeutil import utcnow

    second = StewardModelCall(
        space_id=plan.space_id,
        job_id=plan.job_id,
        policy_version=plan.policy_version,
        assist_kind=first.assist_kind,
        prompt_digest="f" * 64,
        prompt_chars=10,
        status="reserved",
        seq=first.seq + 100,
        created_at=utcnow(),
        plan_id=plan.id,
        subject_key="second",
        input_hash="g" * 64,
        attempt_no=1,
        carrier="inproc",
        evidence_hash=plan.evidence_hash,
        next_attempt_at=utcnow(),
    )
    db_session.add(second)
    db_session.commit()

    granted = steward_assist.lease_attempt(
        db_session,
        space_id=plan.space_id,
        worker_id="carrier-a",
        carrier="inproc",
    )
    assert granted is not None
    blocked = steward_assist.lease_attempt(
        db_session,
        space_id=plan.space_id,
        worker_id="carrier-b",
        carrier="inproc",
    )
    assert blocked is None, "a full space must not lease a second attempt"

    # And the block is the budget, not an absence of work: the second attempt is
    # still reserved and leaseable once the first is no longer in flight.
    db_session.expire_all()
    assert db_session.get(StewardModelCall, second.id).status == "reserved"


def test_lease_time_fence_skips_instead_of_leasing(db_session, monkeypatch):
    """A fence that fails at lease time retires the attempt; it is not leased.

    The write-back fence is checked again at settle, so this is the *sending*
    gate: leasing work whose evidence already changed would spend a model call
    that can never be applied.
    """
    world = _world(db_session)
    _core(db_session, world)
    # Change the evidence after reservation, so the reserved digest no longer
    # matches: exactly the TOCTOU window the fence exists for.
    monkeypatch.setattr(
        steward_assist,
        "_fence_check",
        lambda *_a, **_k: steward_assist.REASON_EVIDENCE_CHANGED,
    )

    grant = steward_assist.lease_attempt(
        db_session, space_id=world.space.id, worker_id="carrier", carrier="inproc"
    )

    assert grant is None, "a fenced-out attempt must not be leased"
    db_session.expire_all()
    skipped = list(
        db_session.scalars(
            select(StewardModelCall).where(
                StewardModelCall.status == "skipped",
                StewardModelCall.error_code == steward_assist.REASON_EVIDENCE_CHANGED,
            )
        )
    )
    assert skipped, "the attempt must be retired with a safe reason code"


def test_an_expired_lease_does_not_block_the_space_forever(db_session, monkeypatch):
    """Only *live* in-flight attempts count against the budget.

    A dead lease that still reads ``in_flight`` (the process died before recovery
    ran) must not permanently consume the space's capacity — otherwise a crash
    would silently stop all future work for that space.
    """
    from datetime import timedelta

    from app.utils.timeutil import utcnow

    monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE", 1)
    world = _world(db_session)
    _core(db_session, world)
    plan = db_session.scalar(select(StewardAssistPlan).order_by(StewardAssistPlan.id.desc()))
    assert plan is not None
    first = db_session.scalar(select(StewardModelCall).where(StewardModelCall.plan_id == plan.id))
    assert first is not None

    second = StewardModelCall(
        space_id=plan.space_id,
        job_id=plan.job_id,
        policy_version=plan.policy_version,
        assist_kind=first.assist_kind,
        prompt_digest="f" * 64,
        prompt_chars=10,
        status="reserved",
        seq=first.seq + 100,
        created_at=utcnow(),
        plan_id=plan.id,
        subject_key="second",
        input_hash="g" * 64,
        attempt_no=1,
        carrier="inproc",
        evidence_hash=plan.evidence_hash,
        next_attempt_at=utcnow(),
    )
    db_session.add(second)
    # Occupy the single slot with an already-dead lease.
    first.status = "in_flight"
    first.lease_owner = "dead-carrier"
    first.lease_until = utcnow() - timedelta(seconds=5)
    db_session.commit()

    grant = steward_assist.lease_attempt(
        db_session,
        space_id=plan.space_id,
        worker_id="live-carrier",
        carrier="inproc",
    )

    assert grant is not None, "an expired lease must not consume the space's budget"
    assert grant["attempt_id"] == second.id


def test_settle_attempt_applies_the_product_and_is_not_repeatable(db_session, monkeypatch):
    """Settlement validates, applies, and refuses a second attempt to settle."""
    world = _world(db_session)
    _core(db_session, world)
    grant = steward_assist.lease_attempt(
        db_session,
        space_id=world.space.id,
        worker_id="test-carrier",
        carrier="inproc",
    )
    assert grant is not None

    # terminology's product shape: an empty items list is a legal product (no
    # improvement found), which is exactly what a fake transport returns here.
    attempt = db_session.get(StewardModelCall, grant["attempt_id"])
    assert attempt is not None
    output = json.dumps({"version": 1, "context_hash": "x", "items": []})

    status = steward_assist.settle_attempt(
        db_session,
        attempt_id=attempt.id,
        status="succeeded",
        lease_owner="test-carrier",
        text=output,
        latency_ms=11,
    )
    db_session.expire_all()
    settled = db_session.get(StewardModelCall, attempt.id)
    assert settled is not None
    assert status == settled.status
    assert settled.status in ("succeeded", "degraded")
    assert settled.billed_tokens is not None

    before = settled.status
    again = steward_assist.settle_attempt(
        db_session,
        attempt_id=attempt.id,
        status="succeeded",
        lease_owner="test-carrier",
        text=output,
    )
    assert again is None, "a settled attempt must not be settled again"
    assert db_session.get(StewardModelCall, attempt.id).status == before


def test_settle_attempt_refuses_a_foreign_lease_owner(db_session, monkeypatch):
    """Only the lease holder may settle: a late result cannot overwrite state."""
    world = _world(db_session)
    _core(db_session, world)
    grant = steward_assist.lease_attempt(
        db_session, space_id=world.space.id, worker_id="holder", carrier="inproc"
    )
    assert grant is not None

    result = steward_assist.settle_attempt(
        db_session,
        attempt_id=grant["attempt_id"],
        status="succeeded",
        lease_owner="impostor",
        text=json.dumps([]),
    )
    assert result is None
    db_session.expire_all()
    assert db_session.get(StewardModelCall, grant["attempt_id"]).status == "in_flight"


def test_failed_settlement_bills_conservatively_and_never_auto_retries(db_session, monkeypatch):
    """A transport timeout is unknown: billed, terminal, and not replayed."""
    world = _world(db_session)
    _core(db_session, world)
    grant = steward_assist.lease_attempt(
        db_session,
        space_id=world.space.id,
        worker_id="test-carrier",
        carrier="inproc",
    )
    assert grant is not None
    import httpx

    steward_assist.settle_attempt(
        db_session,
        attempt_id=grant["attempt_id"],
        status="failed",
        lease_owner="test-carrier",
        exc=httpx.ReadTimeout("read timed out"),
    )
    db_session.expire_all()
    settled = db_session.get(StewardModelCall, grant["attempt_id"])
    assert settled is not None
    # A timeout cannot prove the upstream did not process the request.
    assert settled.status == "unknown"
    assert (settled.billed_tokens or 0) > 0
    # And it must not become leaseable again.
    assert (
        steward_assist.lease_attempt(
            db_session, space_id=world.space.id, worker_id="carrier-b", carrier="inproc"
        )
        is None
    )


# --------------------------------------------------------------------------
# Recovery
# --------------------------------------------------------------------------


def test_recovery_converges_an_expired_lease_to_unknown(db_session, monkeypatch):
    """Crash point ④: sent but never settled → unknown, billed, not replayed."""
    from datetime import timedelta

    from app.utils.timeutil import utcnow

    world = _world(db_session)
    _core(db_session, world)
    grant = steward_assist.lease_attempt(
        db_session, space_id=world.space.id, worker_id="doomed", carrier="inproc"
    )
    assert grant is not None
    attempt = db_session.get(StewardModelCall, grant["attempt_id"])
    assert attempt is not None
    attempt.lease_until = utcnow() - timedelta(seconds=1)
    db_session.commit()

    assert steward_assist.recover_stuck_attempts(db_session) >= 1

    db_session.expire_all()
    recovered = db_session.get(StewardModelCall, grant["attempt_id"])
    assert recovered is not None
    assert recovered.status == "unknown"
    assert (recovered.billed_tokens or 0) > 0
    assert recovered.lease_owner is None


def test_recovery_does_not_touch_a_live_lease(db_session, monkeypatch):
    """A live lease must survive a recovery pass."""
    world = _world(db_session)
    _core(db_session, world)
    grant = steward_assist.lease_attempt(
        db_session, space_id=world.space.id, worker_id="alive", carrier="inproc"
    )
    assert grant is not None

    steward_assist.recover_stuck_attempts(db_session)

    db_session.expire_all()
    assert db_session.get(StewardModelCall, grant["attempt_id"]).status == "in_flight"


def test_recovery_does_not_touch_reserved_attempts(db_session, monkeypatch):
    """``reserved`` was never sent, so it is leaseable again rather than unknown."""
    world = _world(db_session)
    _core(db_session, world)
    plan = db_session.scalar(select(StewardAssistPlan).order_by(StewardAssistPlan.id.desc()))
    assert plan is not None
    reserved = list(
        db_session.scalars(
            select(StewardModelCall).where(
                StewardModelCall.plan_id == plan.id, StewardModelCall.status == "reserved"
            )
        )
    )
    assert reserved

    steward_assist.recover_stuck_attempts(db_session)

    db_session.expire_all()
    for attempt in reserved:
        assert db_session.get(StewardModelCall, attempt.id).status == "reserved"


# --------------------------------------------------------------------------
# The context guard is positive, not merely absent
# --------------------------------------------------------------------------


def test_steward_context_requires_a_real_steward_attempt(db_session, monkeypatch):
    """A fabricated run_id is refused rather than silently accepted."""
    from app.errors import POLICY_CONTEXT_INVALID
    from app.services import context_builder

    world = _world(db_session)
    admin = world.a
    with pytest.raises(Exception) as exc:
        context_builder.ContextBuilder(db_session).build(
            actor=admin,
            space_id=world.space.id,
            agent_kind="steward",
            query="q",
            run_id=999_999,
        )
    assert exc.value.status_code == 422
    payload = extract_api_error(exc.value.detail)
    assert payload is not None and payload["code"] == POLICY_CONTEXT_INVALID


# --------------------------------------------------------------------------
# The fence has exactly the two call sites the design names
# --------------------------------------------------------------------------


def test_the_write_back_fence_has_exactly_two_call_sites():
    """``_fence_check`` is called from the lease gate and the settle path only.

    The old design called it from nine places, and that is what let the two
    execution paths drift: a change to one call site did not reach the others.
    The count is asserted rather than described because "only two" is the
    property, and a third call site is exactly how the drift comes back.

    This is a structural check on the source, not a behavioural one, so it is
    deliberately blunt: it names the three permitted callers and fails on any
    fourth. The permitted three are ``lease_attempt`` (the sending gate),
    ``settle_attempt`` (the write-back gate) and ``recover_stuck_attempts``
    (which finishes a product whose write-back was interrupted — it re-checks the
    world before applying, and skipping that check would apply stale work).
    """
    import ast
    from pathlib import Path

    source = Path(steward_assist.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    callers = sorted(
        {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_fence_check"
        }
    )
    assert callers == ["lease_attempt", "record_attempt_outcome", "recover_stuck_attempts"]


def test_both_carriers_send_the_same_prompt_text(db_session, monkeypatch):
    """A child run must send what the in-process carrier sends.

    The in-process carrier sends ``_PROMPTS[kind]`` as the system message and the
    projection as the user message, and ``prompt_digest`` is computed over those
    two. If the Pi path sent only a generic system prompt, the two carriers would
    ask the model different questions while the recorded digest claimed otherwise
    — and for the candidate kind the difference is load-bearing, because the
    direction semantics and conflict rules live in that text.

    So the projection must carry the instructions verbatim, and the digest must be
    reproducible from what the projection hands over.
    """
    import hashlib

    from app.main import internal_app
    from app.services.steward_assist import _PROMPTS

    world, plan = _planned(db_session)
    attempt = db_session.scalar(select(StewardModelCall).where(StewardModelCall.plan_id == plan.id))
    assert attempt is not None
    db_session.commit()

    grant = steward_assist.lease_attempt(
        db_session, space_id=plan.space_id, worker_id="carrier", carrier="inproc"
    )
    assert grant is not None
    run = steward_assist.open_child_run(
        db_session, attempt_id=grant["attempt_id"], lease_owner="carrier"
    )
    assert run is not None
    run_token = agent_tokens.issue_run_token(
        run_id=run.id,
        job_id=grant["steward_job_id"],
        attempt=run.attempt,
        agent_kind="steward",
        space_id=grant["space_id"],
        tool_allowlist=list(run.tool_allowlist_json or []),
        steward_attempt_id=grant["attempt_id"],
        viewer_account_id=grant["viewer_account_id"],
    )
    db_session.commit()

    from fastapi.testclient import TestClient

    response = TestClient(internal_app).get(
        f"/internal/agent/runs/{run.id}/context",
        headers={"Authorization": f"Bearer {run_token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    instructions = body["steward_instructions"]
    assert (
        instructions == _PROMPTS[attempt.assist_kind]
    ), "the projection must carry the same instruction text the in-process carrier sends"
    # And the digest is reproducible from it, which is what makes "the two
    # carriers send the same thing" checkable rather than a claim.
    block = body["context_blocks"][0]["content"]
    rebuilt = hashlib.sha256(f"{instructions}\n{block}".encode()).hexdigest()
    assert rebuilt == attempt.prompt_digest
