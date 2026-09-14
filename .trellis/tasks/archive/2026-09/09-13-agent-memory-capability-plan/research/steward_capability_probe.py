"""Offline MR-23/MR-26 reproduction; writes only its research output and private temp DB.

Run from this worktree's backend:
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  ../.trellis/tasks/09-13-agent-memory-capability-plan/research/steward_capability_probe.py \
  --source-commit <checkout-sha>
No real provider or service is started. The network is denied in-process.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
import platform
import secrets
import socket
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
RESEARCH = Path(__file__).resolve().parent
ROOT = RESEARCH.parents[3]
BACKEND = ROOT / "backend"
MODEL = "gpt-5.6-sol"
FROZEN_TIME = datetime(2026, 9, 13, 0, 0, 0)


def jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [jsonable(v) for v in value]
    return value


def write_json(path, data):
    path.write_text(
        json.dumps(jsonable(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class Clock:
    def __init__(self):
        self.value = FROZEN_TIME

    def now(self):
        return self.value

    def advance(self):
        self.value += timedelta(seconds=1)


def bootstrap(temp_dir, stack):
    # Explicitly override inherited deployment values before importing any app module.
    flags = {
        "AGENT_RUNTIME_ENABLED": "0",
        "STEWARD_ENABLED": "1",
        "STEWARD_WORKER_ENABLED": "0",
        "STEWARD_ASSIST_CANDIDATE": "1",
        "STEWARD_ASSIST_RANKING": "0",
        "STEWARD_ASSIST_EXPLANATION": "0",
        "BEHAVIOR_PROJECTION_ENABLED": "1",
        "PERSONAL_FAMILY_VIEW_ENABLED": "1",
        "POLICY_GUARD_ENABLED": "1",
        "MEMORY_ENABLED": "0",
        "RAG_ENABLED": "0",
        "DEV_SEED_DEMO_DATA": "0",
        "BCRYPT_ROUNDS": "4",
    }
    os.environ.update(flags)
    os.environ["DATA_DIR"] = str(temp_dir / "data")
    for key in ("SECRET_KEY", "ADMIN_JWT_SECRET", "AGENT_SERVICE_SECRET"):
        os.environ[key] = secrets.token_urlsafe(48)
    os.environ["ADMIN_JWT_ISSUER"] = "offline-steward-eval-issuer"
    os.environ["ADMIN_JWT_AUDIENCE"] = "offline-steward-eval-audience"
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)

    from alembic import command
    from alembic.config import Config
    from app import config

    config.ensure_ready()
    # Same explicit test-only override as backend/tests/conftest.py. No production edit.
    config.AGENT_PROVIDER_STANDARD_PROFILE_ONLY = False
    migration_config = Config(str(BACKEND / "alembic.ini"))
    migration_config.set_main_option("script_location", str(BACKEND / "migrations"))
    with (temp_dir / "migration.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            command.upgrade(migration_config, "head")

    for module_name in (
        "app.services.action_cards",
        "app.services.domain_events",
        "app.services.family_recommendations",
        "app.services.personal_family_view",
        "app.services.source_facts",
        "app.services.steward",
        "app.services.steward_assist",
        "app.services.steward_suggestions",
    ):
        importlib.import_module(module_name)
    from app.utils import timeutil

    clock = Clock()
    original_utcnow = timeutil.utcnow
    # Patch clocks, never business functions. Late imports receive patched timeutil.utcnow.
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and getattr(module, "utcnow", None) is original_utcnow:
            stack.enter_context(patch.object(module, "utcnow", clock.now))
    network_attempts = []

    def deny_network(*_args, **_kwargs):
        network_attempts.append("blocked")
        raise RuntimeError("offline_probe_network_forbidden")

    for symbol in ("getaddrinfo", "create_connection"):
        stack.enter_context(patch.object(socket, symbol, deny_network))
    for symbol in ("connect", "connect_ex"):
        stack.enter_context(patch.object(socket.socket, symbol, deny_network))
    return config, clock, network_attempts


def person(db, name, clock, *, gender="unknown"):
    from app.models.account import Account
    from app.models.user import User
    from app.utils.security import hash_pin

    row = User(
        name=name,
        gender=gender,
        privacy_mode="handover",
        profile_status="identity_confirmed",
        profile_confirmed_at=clock.now(),
        created_at=clock.now(),
    )
    row.account = Account(
        pin_hash=hash_pin("123456"),
        pin_must_change=False,
        token_version=0,
        failed_attempts=0,
        status="claimed",
        claimed_at=clock.now(),
    )
    db.add(row)
    db.flush()
    return row


def space_with_members(db, users, clock, name):
    from app.models.space import FamilySpace, SpaceMember

    row = FamilySpace(name=name, owner_id=users[0].id, kind="household", created_at=clock.now())
    db.add(row)
    db.flush()
    for index, user in enumerate(users):
        db.add(
            SpaceMember(
                space_id=row.id,
                user_id=user.id,
                added_by=users[0].id,
                role="space_admin" if index == 0 else "member",
                status="active",
                created_at=clock.now(),
                updated_at=clock.now(),
            )
        )
    db.flush()
    return row


def source_fact(db, space, subject, obj, *, confirmed):
    from app.services import source_facts

    row = source_facts.create_source_fact(
        db,
        fact_type="biological_parent",
        subject_user_id=subject.id,
        object_user_id=obj.id,
        provenance="manual_entry",
        space_id=space.id,
    )
    if confirmed:
        source_facts.transition_source_fact(db, row, "confirm")
    db.flush()
    return row


def provider_setting(db, space, clock):
    from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
    from app.utils.secretbox import encrypt_secret

    provider = AgentProvider(
        name=f"offline-mr23-{space.id}",
        kind="openai_compatible",
        api="openai-responses",
        base_url="https://offline-eval.invalid/v1",
        secret_ciphertext=encrypt_secret(secrets.token_urlsafe(32)),
        allowed_models_json=[MODEL],
        enabled=True,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    db.add(provider)
    db.flush()
    setting = AgentSpaceProviderSetting(
        space_id=space.id,
        agent_kind="steward",
        provider_id=provider.id,
        model=MODEL,
        cloud_allowed=True,
        enabled=True,
        assist_candidate=True,
        assist_ranking=False,
        assist_explanation=False,
    )
    db.add(setting)
    db.commit()
    return setting


def touch_event(db, space, actor, *, label):
    from app.services.domain_events import emit

    event = emit(
        db,
        event_type="profile.updated",
        aggregate_type="profile",
        aggregate_id=actor.id,
        payload={"profile_id": actor.id, "offline_case": label},
        space_id=space.id,
        actor_account_id=actor.account.id,
    )
    db.commit()
    return event.id


def run_core(db, space, clock):
    from app.services import steward

    clock.advance()
    cursor = steward.current_event_watermark(db)
    db.commit()
    job, _ = steward.enqueue_steward_job(
        db, space_id=space.id, cause="integrity_scan", trigger_cursor=cursor, now=clock.now()
    )
    granted = steward.lease_next_steward_job(
        db, space_id=space.id, leased_by="offline-mr23", now=clock.now()
    )
    require(granted is not None and granted.id == job.id, "core job did not lease")
    summary = steward.execute_steward_job(
        db,
        granted,
        now=clock.now(),
        worker_id="offline-mr23",
        expected_attempt=granted.attempt,
    )
    require(granted.status == "succeeded", "core failed")
    return granted, summary


def snapshots(db, space, subject, job, *, phase, expected_support_fact_ids, summary=None):
    from app import config
    from app.models.relationship_facts import SourceFact
    from app.models.steward import StewardAssistBatch, StewardLlmCandidate, StewardModelCall
    from app.models.steward_suggestion import StewardSuggestion, StewardSuggestionRecipient
    from app.services import steward_assist, steward_suggestions
    from sqlalchemy import select

    candidates = list(
        db.scalars(select(StewardLlmCandidate).where(StewardLlmCandidate.space_id == space.id))
    )
    suggestions = list(
        db.scalars(
            select(StewardSuggestion).where(
                StewardSuggestion.space_id == space.id, StewardSuggestion.origin == "model"
            )
        )
    )
    batch = db.scalar(select(StewardAssistBatch).where(StewardAssistBatch.job_id == job.id))
    attempts = list(db.scalars(select(StewardModelCall).where(StewardModelCall.job_id == job.id)))
    shown = steward_suggestions.list_suggestions_page(
        db, account=subject.account, space_id=space.id, cursor=None, limit=100
    )["items"]
    visible_states = {row["id"]: row["state"] for row in shown}
    recipients = list(
        db.scalars(
            select(StewardSuggestionRecipient).where(
                StewardSuggestionRecipient.suggestion_id.in_([row.id for row in suggestions])
            )
        )
    )
    facts = list(
        db.scalars(
            select(SourceFact)
            .where(SourceFact.space_id == space.id, SourceFact.state == "confirmed")
            .order_by(SourceFact.id)
        )
    )
    return {
        "phase": phase,
        "expected_support_fact_ids": expected_support_fact_ids,
        "current_confirmed_facts": [{"id": f.id, "revision": f.revision} for f in facts],
        "flags": {
            "steward": config.STEWARD_ENABLED,
            "platform_candidate": config.STEWARD_ASSIST_CANDIDATE,
            "space_candidate": True,
            "effective_candidate": steward_assist.assist_enabled(db, space.id, "candidate"),
            "ranking": config.STEWARD_ASSIST_RANKING,
            "explanation": config.STEWARD_ASSIST_EXPLANATION,
        },
        "job": {
            "id": job.id,
            "status": job.status,
            "attempt": job.attempt,
            "trigger_cursor": job.trigger_cursor,
            "last_event_cursor": job.last_event_cursor,
            "summary": summary,
        },
        "batch": None
        if batch is None
        else {
            "id": batch.id,
            "job_id": batch.job_id,
            "status": batch.status,
            "error_code": batch.error_code,
            "evidence_hash": batch.evidence_hash,
        },
        "attempts": [
            {
                "id": call.id,
                "batch_id": call.batch_id,
                "job_id": call.job_id,
                "kind": call.assist_kind,
                "status": call.status,
                "error_code": call.error_code,
                "input_hash": call.input_hash,
                "provider_id": call.provider_id,
                "model": call.model,
                "validated_output": call.output_json,
                "billed_tokens_synthetic_usage": call.billed_tokens,
            }
            for call in attempts
        ],
        "candidates": [
            {
                "id": c.id,
                "job_id": c.job_id,
                "digest": c.candidate_digest,
                "status": c.status,
                "payload": c.payload_json,
            }
            for c in candidates
        ],
        "suggestions": [
            {
                "id": s.id,
                "evidence_hash": s.evidence_hash,
                "evidence": s.evidence_json,
                "status": s.status,
                "subject_list_state": visible_states.get(s.id),
                "revision": s.revision,
                "source_candidate_id": s.source_candidate_id,
                "source_job_id": s.source_job_id,
                "superseded_by_id": s.superseded_by_id,
            }
            for s in suggestions
        ],
        "recipients": [
            {
                "suggestion_id": r.suggestion_id,
                "account_id": r.account_id,
                "dismissed_at": r.dismissed_at,
                "cooldown_until": r.cooldown_until,
                "cooldown_evidence_hash": r.cooldown_evidence_hash,
            }
            for r in recipients
        ],
    }


def mr23_case(db, mode, clock, temp_dir):
    from app.models.steward import StewardAssistBatch, StewardLlmCandidate
    from app.models.steward_suggestion import StewardSuggestion
    from app.services import (
        source_facts,
        steward,
        steward_assist,
        steward_guard,
        steward_suggestions,
    )
    from sqlalchemy import select

    a = person(db, f"{mode}-sibling-a", clock, gender="m")
    b = person(db, f"{mode}-sibling-b", clock, gender="f")
    p = person(db, f"{mode}-parent-p", clock, gender="m")
    q = person(db, f"{mode}-parent-q", clock, gender="f")
    x = person(db, f"{mode}-unrelated-x", clock, gender="m")
    y = person(db, f"{mode}-unrelated-y", clock, gender="f")
    space = space_with_members(db, [a, b, p, q, x, y], clock, f"mr23-{mode}")
    initial = [
        source_fact(db, space, p, a, confirmed=True),
        source_fact(db, space, p, b, confirmed=True),
    ]
    related = [
        source_fact(db, space, q, a, confirmed=False),
        source_fact(db, space, q, b, confirmed=False),
    ]
    unrelated = source_fact(db, space, x, y, confirmed=False)
    db.commit()
    provider_setting(db, space, clock)
    expected_before = [f.id for f in initial]
    expected_after = (
        expected_before + [f.id for f in related] if mode == "related" else expected_before
    )
    fixture = {
        "mode": mode,
        "space_id": space.id,
        "candidate": {"kind": "direct_sibling", "subject_user_id": a.id, "object_user_id": b.id},
        "expected_support_fact_ids_before": expected_before,
        "expected_support_fact_ids_after": expected_after,
        "proposed_related_fact_ids": [f.id for f in related],
        "proposed_unrelated_fact_ids": [unrelated.id],
        "annotation_basis": (
            "shared parent P supports siblings; Q is additional shared parent; X/Y disconnected"
        ),
        "annotation_written_before_first_core": True,
    }
    write_json(temp_dir / f"mr23-{mode}-fixture-before-run.json", fixture)
    ctx = steward_guard.ProjectionContext(steward._space_visible_user_ids(db, space))
    model_output = json.dumps(
        [{"kind": "direct_sibling", "subject": ctx.codename(a.id), "object": ctx.codename(b.id)}]
    )
    calls = []
    phases = []
    current_phase = ""

    def fake_transport(_url, _headers, payload, timeout):
        user_content = payload["input"][-1]["content"]
        parsed = json.loads(user_content)
        require(isinstance(parsed.get("facts"), list), "candidate request was not projected")
        calls.append(
            {
                "phase": current_phase,
                "model": payload["model"],
                "node_count": len(parsed["nodes"]),
                "fact_ids_revisions": [
                    {"id": f["fact_id"], "revision": f["revision"]} for f in parsed["facts"]
                ],
                "timeout_seconds": timeout,
            }
        )
        return {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": model_output}]}
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }

    def cycle(label, expected, *, replay_batch=False):
        nonlocal current_phase
        current_phase = label
        job, summary = run_core(db, space, clock)
        phases.append(
            snapshots(
                db,
                space,
                a,
                job,
                phase=f"{label}:core",
                expected_support_fact_ids=expected,
                summary=summary,
            )
        )
        before_calls = len(calls)
        status = steward_assist.run_due_batch(db, transport=fake_transport, now=clock.now())
        require(status == "applied", f"{label}: assist did not apply: {status}")
        require(len(calls) == before_calls + 1, f"{label}: fake transport did not execute once")
        phases.append(
            snapshots(
                db,
                space,
                a,
                job,
                phase=f"{label}:assist",
                expected_support_fact_ids=expected,
            )
        )
        if replay_batch:
            batch = db.scalar(select(StewardAssistBatch).where(StewardAssistBatch.job_id == job.id))
            before = len(calls)
            repeated_status = steward_assist.execute_batch(db, batch.id, transport=fake_transport)
            require(len(calls) == before, "applied batch replay sent a second request")
            phases.append(
                {
                    "phase": f"{label}:applied_batch_replay",
                    "job_id": job.id,
                    "batch_id": batch.id,
                    "status": repeated_status,
                    "additional_transport_calls": len(calls) - before,
                }
            )
        return job

    cycle("initial", expected_before)
    first_candidates = list(
        db.scalars(select(StewardLlmCandidate).where(StewardLlmCandidate.space_id == space.id))
    )
    require(len(first_candidates) == 1, "fake output did not create initial candidate")
    require(
        not db.scalar(
            select(StewardSuggestion.id).where(
                StewardSuggestion.space_id == space.id, StewardSuggestion.origin == "model"
            )
        ),
        "unexpected same-core model suggestion before next core",
    )
    touch_event(db, space, a, label="next-core")
    projection_job = cycle("first_projection", expected_before)
    suggestion = db.scalar(
        select(StewardSuggestion).where(
            StewardSuggestion.space_id == space.id, StewardSuggestion.origin == "model"
        )
    )
    require(suggestion is not None, "later core did not project model candidate")
    result = steward_suggestions.dismiss_suggestion(
        db,
        account=a.account,
        space_id=space.id,
        suggestion_id=suggestion.id,
        expected_revision=suggestion.revision,
        now=clock.now(),
    )
    require(result["state"] == "dismissed", "real dismiss command failed")
    phases.append(
        snapshots(
            db, space, a, projection_job, phase="dismiss", expected_support_fact_ids=expected_before
        )
    )

    clock.advance()
    if mode == "related":
        for fact in related:
            source_facts.transition_source_fact(db, fact, "confirm", actor_account_id=a.account.id)
        db.commit()
    elif mode == "unrelated":
        source_facts.transition_source_fact(db, unrelated, "confirm", actor_account_id=a.account.id)
        db.commit()
    else:
        touch_event(db, space, a, label="no-new-evidence")
    cycle("after_mutation", expected_after, replay_batch=True)
    touch_event(db, space, a, label="later-core-after-mutation")
    cycle("later_projection", expected_after)
    touch_event(db, space, a, label="same-evidence-replay")
    cycle("same_evidence_replay", expected_after, replay_batch=True)
    final = phases[-2]
    if "candidates" not in final:
        final = next(p for p in reversed(phases) if "candidates" in p)
    return {
        "fixture": fixture,
        "stages": phases,
        "fake_transport_calls": calls,
        "assessment": {
            "initial_candidate_id": first_candidates[0].id,
            "initial_suggestion_id": suggestion.id,
            "final_candidate_count": len(final["candidates"]),
            "final_model_suggestion_count": len(final["suggestions"]),
            "final_evidence_hashes": [s["evidence_hash"] for s in final["suggestions"]],
            "final_subject_states": [s["subject_list_state"] for s in final["suggestions"]],
            "expected_support_changed": expected_before != expected_after,
            "final_suggestion_contains_expected_new_support": all(
                set(expected_after).issubset({f["id"] for f in s["evidence"]["facts"]})
                for s in final["suggestions"]
            ),
            "transport_calls": len(calls),
            "real_provider_calls": 0,
        },
    }


def projection_snapshot(db, space, actor, target, clock):
    from app.models.steward import BehaviorProjection
    from app.services import family_recommendations, steward
    from sqlalchemy import select

    db.expire_all()
    rows = list(
        db.scalars(
            select(BehaviorProjection)
            .where(BehaviorProjection.space_id == space.id)
            .order_by(BehaviorProjection.account_id, BehaviorProjection.projection_key)
        )
    )
    return {
        "rows": [
            {
                "id": r.id,
                "space_id": r.space_id,
                "account_id": r.account_id,
                "key": r.projection_key,
                "value": r.value_json,
                "updated_at": r.updated_at,
            }
            for r in rows
        ],
        "family_cooldown_active": family_recommendations._cooldown_active(
            db,
            space_id=space.id,
            account_id=actor.account.id,
            target_id=target.id,
            now=clock.now(),
        ),
        "card_cooldown_active": steward.kind_in_cooldown(
            db,
            space_id=space.id,
            account_id=actor.account.id,
            kind="household_link",
            now=clock.now(),
        ),
    }


def mr26_case(db, *, enabled, scope, clock):
    from app import config
    from app.services import family_recommendations, personal_family_view, steward
    from app.services.domain_events import emit

    config.BEHAVIOR_PROJECTION_ENABLED = True  # Seed persisted rows before hard-off case.
    actor = person(db, f"mr26-{scope}-{enabled}-actor", clock, gender="m")
    target = person(db, f"mr26-{scope}-{enabled}-parent", clock, gender="f")
    observer = person(db, f"mr26-{scope}-{enabled}-observer", clock)
    space = space_with_members(db, [actor, target, observer], clock, f"mr26-{scope}-{enabled}")
    source_fact(db, space, target, actor, confirmed=True)
    db.commit()

    event_specs = [
        ("card.dismissed", "action_card", {"kind": "household_link"}),
        (
            "term.personal_updated",
            "term_entry",
            {"concept_code": "mother", "entry_id": 101, "account_id": actor.account.id},
        ),
        ("term.usage_recorded", "term_entry", {"concept_code": "mother"}),
        ("term.usage_recorded", "term_entry", {"concept_code": "mother"}),
    ]
    event_ids = []
    for event_type, aggregate_type, payload in event_specs:
        event = emit(
            db,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=101,
            payload=payload,
            space_id=space.id,
            actor_account_id=actor.account.id,
        )
        db.flush()
        event_ids.append(event.id)
    until = clock.now() + timedelta(days=config.STEWARD_COOLDOWN_DAYS)
    steward.set_kind_cooldown(
        db,
        space_id=space.id,
        account_id=actor.account.id,
        kind="household_link",
        now=clock.now(),
    )
    for key, value in (
        ("correction_preference:mother", {"entry_id": 101, "updated_at": clock.now().isoformat()}),
        ("term_usage:mother", {"count": 2, "updated_at": clock.now().isoformat()}),
    ):
        steward.put_projection(
            db,
            space_id=space.id,
            account_id=actor.account.id,
            projection_key=key,
            value=value,
            now=clock.now(),
        )
    db.commit()
    # Exercise the real family recommendation producer after a real PFV rebuild.
    personal_family_view.rebuild_view(db, account=actor.account, space_id=space.id)
    db.commit()
    before_dismiss_payload = family_recommendations.recommendations_payload(
        db, account=actor.account, space_id=space.id
    )
    require(
        any(item["target_user_id"] == target.id for item in before_dismiss_payload["items"]),
        "family recommendation fixture was not visible",
    )
    family_recommendations.dismiss_recommendation(
        db,
        account=actor.account,
        space_id=space.id,
        target_user_id=target.id,
        category="confirmed_kinship",
    )
    # A separate account with no allowed replay events exercises account/scope preservation.
    steward.put_projection(
        db,
        space_id=space.id,
        account_id=observer.account.id,
        projection_key=f"{family_recommendations.DISMISS_PREFIX}{actor.id}",
        value={"until": until.isoformat(), "category": "confirmed_kinship"},
        now=clock.now(),
    )
    db.commit()
    config.BEHAVIOR_PROJECTION_ENABLED = enabled
    before = projection_snapshot(db, space, actor, target, clock)
    counts = []
    outputs = []
    for _ in range(2):
        count = steward.rebuild_behavior_projections(
            db,
            space_id=space.id,
            account_id=actor.account.id if scope == "account" else None,
            now=clock.now(),
        )
        db.commit()
        counts.append(count)
        outputs.append(projection_snapshot(db, space, actor, target, clock))

    def canonical_rows(snapshot, *, nonowned):
        return [
            {k: v for k, v in row.items() if k != "id"}
            for row in snapshot["rows"]
            if row["key"].startswith(family_recommendations.DISMISS_PREFIX) == nonowned
        ]

    # Row IDs are explicitly reported; semantic replay equality ignores implementation cache IDs.
    return {
        "enabled": enabled,
        "scope": scope,
        "space_id": space.id,
        "actor_account_id": actor.account.id,
        "observer_account_id": observer.account.id,
        "events": event_ids,
        "event_producer": "synthetic allow-listed events via real domain_events.emit",
        "family_key_producer": "real personal_family_view.rebuild_view + dismiss_recommendation",
        "before": before,
        "after_first": outputs[0],
        "after_second": outputs[1],
        "replayed_event_counts": counts,
        "nonowned_rows_preserved_first": (
            canonical_rows(before, nonowned=True) == canonical_rows(outputs[0], nonowned=True)
        ),
        "owned_replay_semantics_stable": (
            canonical_rows(outputs[0], nonowned=False) == canonical_rows(outputs[1], nonowned=False)
        ),
        "all_rows_preserved_when_disabled": None if enabled else before == outputs[0] == outputs[1],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, default=RESEARCH / "steward-capability-results.json")
    args = parser.parse_args()
    require(args.output.resolve().is_relative_to(RESEARCH), "output must stay in E/research")
    temp_dir = Path(tempfile.mkdtemp(prefix="fg-steward-capability-eval-", dir="/private/tmp"))
    started = time.monotonic()
    result = {"source_commit_reported_by_main": args.source_commit, "temp_dir": str(temp_dir)}
    with contextlib.ExitStack() as stack:
        config, clock, network_attempts = bootstrap(temp_dir, stack)
        from app.db import SessionLocal, engine
        from sqlalchemy import text

        source_files = [
            "backend/app/services/steward.py",
            "backend/app/services/steward_assist.py",
            "backend/app/services/steward_guard.py",
            "backend/app/services/steward_suggestions.py",
            "backend/app/services/family_recommendations.py",
            "backend/app/services/source_facts.py",
            "backend/app/services/personal_family_view.py",
            "backend/app/services/domain_events.py",
            "backend/app/models/steward.py",
            "backend/app/models/steward_suggestion.py",
        ]
        result["versions"] = {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "packages": {
                name: importlib.metadata.version(name)
                for name in ("SQLAlchemy", "alembic", "fastapi", "httpx")
            },
            "source_sha256": {
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in source_files
            },
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        result["isolation"] = {
            "database_is_within_private_temp": config.DB_PATH.is_relative_to(temp_dir),
            "worker_started": False,
            "network": "all socket connect/DNS attempts denied; candidate transport is fake",
            "production_sources_modified": False,
            "provider_profile_gate": "explicit test-only False, matching conftest",
            "clock_start": FROZEN_TIME,
            "migration": "real Alembic upgrade head from empty temporary database",
        }
        try:
            with SessionLocal() as db:
                result["alembic_heads"] = list(
                    db.scalars(text("SELECT version_num FROM alembic_version"))
                )
                result["mr23"] = []
                for mode in ("related", "unrelated", "no_new_evidence"):
                    result["mr23"].append(mr23_case(db, mode, clock, temp_dir))
                # Real core hard-off control: a new job runs with candidate platform disabled.
                from app.models.space import FamilySpace
                from app.models.steward import StewardAssistBatch, StewardModelCall
                from app.models.user import User
                from app.services import steward_assist

                first_case = result["mr23"][0]["fixture"]
                space = db.get(FamilySpace, first_case["space_id"])
                a = db.get(User, first_case["candidate"]["subject_user_id"])
                config.STEWARD_ASSIST_CANDIDATE = False
                touch_event(db, space, a, label="candidate-platform-off")
                job, summary = run_core(db, space, clock)
                from sqlalchemy import select

                result["mr23_disabled_control"] = {
                    "job_id": job.id,
                    "core_status": job.status,
                    "effective_candidate": steward_assist.assist_enabled(db, space.id, "candidate"),
                    "batch_ids": list(
                        db.scalars(
                            select(StewardAssistBatch.id).where(StewardAssistBatch.job_id == job.id)
                        )
                    ),
                    "model_call_ids": list(
                        db.scalars(
                            select(StewardModelCall.id).where(StewardModelCall.job_id == job.id)
                        )
                    ),
                    "summary": summary,
                }
                result["mr26"] = []
                for enabled in (False, True):
                    for scope in ("account", "space"):
                        result["mr26"].append(
                            mr26_case(db, enabled=enabled, scope=scope, clock=clock)
                        )
                result["network_attempts"] = len(network_attempts)
                result["real_provider_calls"] = 0
                result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                require(len(network_attempts) == 0, "some path attempted forbidden network")
                result["completed"] = True
        except Exception as exc:
            result["completed"] = False
            result["failure_class"] = type(exc).__name__
            write_json(temp_dir / "partial-results.json", result)
            raise
        finally:
            engine.dispose()
    write_json(args.output, result)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "temp_dir": str(temp_dir),
                "completed": result["completed"],
                "alembic_heads": result["alembic_heads"],
                "mr23": [{"mode": r["fixture"]["mode"], **r["assessment"]} for r in result["mr23"]],
                "mr26": [
                    {
                        key: r[key]
                        for key in (
                            "enabled",
                            "scope",
                            "replayed_event_counts",
                            "nonowned_rows_preserved_first",
                            "owned_replay_semantics_stable",
                            "all_rows_preserved_when_disabled",
                        )
                    }
                    for r in result["mr26"]
                ],
                "elapsed_seconds": result["elapsed_seconds"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
