"""Additional independent-writer, private-wire and historical read regressions."""

import json

import pytest
from sqlalchemy import select, text
from test_rag_acceptance_contract import (
    _answer,
    _context,
    _fallback,
    _public_headers,
    _replace_lease,
    _start_run,
    _world,
)

from app.db import SessionLocal
from app.models.agent import AgentJob, AgentMessage, AgentRun, AgentRunEvent, AgentToolCall
from app.models.audit_log import AuditLog
from app.models.context import ContextBuild
from app.models.rag import RAGChunk, RAGDocument
from app.services import memory_rag
from conftest import create_agent_session


@pytest.mark.parametrize("boundary", ["authorization", "admission"])
def test_provider_fences_original_attempt_before_upstream(
    db_session, internal_client, monkeypatch, boundary
):
    from test_provider_proxy import _FakeAsyncClient, _install_fake, _seed_provider

    from app.api import internal_agent
    from app.services import provider_proxy

    owner, space, _provider = _seed_provider(db_session, name=f"b-provider-{boundary}")
    session = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    world = _start_run(db_session, internal_client, session, "synthetic provider request")
    _install_fake(monkeypatch, [b"{}"])
    _FakeAsyncClient.last = None
    switched = False
    if boundary == "authorization":
        original = internal_agent._authorize_run

        def replace_after_auth(*args, **kwargs):
            nonlocal switched
            result = original(*args, **kwargs)
            _replace_lease(world["run_id"])
            switched = True
            return result

        monkeypatch.setattr(internal_agent, "_authorize_run", replace_after_auth)
    else:
        original = provider_proxy._admit_upstream_request

        def replace_before_admission(*args, **kwargs):
            nonlocal switched
            _replace_lease(world["run_id"])
            switched = True
            return original(*args, **kwargs)

        monkeypatch.setattr(provider_proxy, "_admit_upstream_request", replace_before_admission)
    response = internal_client.post(
        f"/internal/agent/runs/{world['run_id']}/provider/chat/completions",
        headers=world["headers"],
        json={"model": "model-x", "stream": True, "messages": []},
    )
    assert switched and response.status_code in (403, 409), response.text
    assert _FakeAsyncClient.last is None, "stale identity must send zero upstream requests"
    with SessionLocal() as check:
        assert check.get(AgentRun, world["run_id"]).attempt == 2
        assert check.get(AgentRun, world["run_id"]).status == "leased"


def test_job_attempt_mismatch_is_not_hidden_by_run_attempt(db_session, internal_client):
    world = _world(db_session, internal_client, "b-job-attempt")
    job = db_session.get(AgentJob, world["job_id"])
    job.attempt += 1
    db_session.commit()
    assert _context(internal_client, world).status_code == 403


def test_tool_rechecks_signed_attempt_at_final_admission(db_session, internal_client, monkeypatch):
    from app.services import agent_tools

    world = _world(db_session, internal_client, "b-tool-admission")
    original = agent_tools.fence_execution
    reached = False

    def replace_before_admission(db, identity, **kwargs):
        nonlocal reached
        assert identity.expected_attempt == 1
        _replace_lease(world["run_id"], running=True)
        reached = True
        return original(db, identity, **kwargs)

    monkeypatch.setattr(agent_tools, "fence_execution", replace_before_admission)
    monkeypatch.setattr(agent_tools, "_dispatch", lambda *a, **k: pytest.fail("stale dispatch"))
    response = internal_client.post(
        f"/internal/agent/runs/{world['run_id']}/tools/familygraph.echo/execute",
        headers=world["headers"],
        json={"version": 1, "input": {"text": "stale"}, "tool_call_id": "stale-admission"},
    )
    assert reached and response.status_code == 403, response.text
    with SessionLocal() as check:
        assert check.get(AgentRun, world["run_id"]).attempt == 2
        assert check.scalar(select(AgentToolCall.id)) is None
        assert (
            check.scalar(select(AuditLog.id).where(AuditLog.action == "agent_tool_executed"))
            is None
        )


def test_admitted_tool_remains_attributed_to_original_attempt(
    db_session, internal_client, monkeypatch
):
    from app.services import agent_tools
    from conftest import create_agent_fixture

    owner, space = create_agent_fixture(db_session, name="b-tool-admitted")
    session = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    world = _start_run(
        db_session, internal_client, session, "scope", tools=[agent_tools.TOOL_PROBE_SCOPE]
    )
    original = agent_tools._dispatch
    reached = False

    def replace_after_admission(db, spec, **kwargs):
        nonlocal reached
        assert not db.in_transaction(), "admission must commit before dispatch"
        with SessionLocal() as check:
            claim = check.scalar(
                select(AgentToolCall).where(AgentToolCall.run_id == world["run_id"])
            )
            assert claim is not None and claim.result_json == {}
        _replace_lease(world["run_id"], running=True)
        # Expire/reload the ORM Run to model any later access after replacement.
        db.expire_all()
        assert db.get(AgentRun, world["run_id"]).attempt == 2
        assert kwargs["run"].attempt == 1
        reached = True
        return original(db, spec, **kwargs)

    monkeypatch.setattr(agent_tools, "_dispatch", replace_after_admission)
    endpoint = (
        f"/internal/agent/runs/{world['run_id']}/tools/{agent_tools.TOOL_PROBE_SCOPE}/execute"
    )
    body = {"version": 1, "input": {}, "tool_call_id": "admitted-attempt-one"}
    response = internal_client.post(endpoint, headers=world["headers"], json=body)
    assert reached and response.status_code == 200, response.text
    assert response.json()["output"]["attempt"] == 1
    with SessionLocal() as check:
        assert check.get(AgentRun, world["run_id"]).attempt == 2
        claim = check.scalar(select(AgentToolCall).where(AgentToolCall.run_id == world["run_id"]))
        assert claim.result_json["attempt"] == 1
        audit_row = check.scalar(select(AuditLog).where(AuditLog.action == "agent_tool_executed"))
        assert audit_row.detail["attempt"] == 1
    # Admission does not give the old worker permission to start another call.
    assert internal_client.post(endpoint, headers=world["headers"], json=body).status_code == 403


@pytest.mark.parametrize("refuse", [False, True])
def test_claimed_web_tool_releases_writer_before_real_gateway_network_boundary(
    db_session, internal_client, monkeypatch, refuse
):
    from test_controlled_web import _enable_platform, _enable_space, _patch_dns

    from app import config
    from app.services import agent_tools, controlled_web
    from conftest import create_agent_fixture

    owner, space = create_agent_fixture(db_session, name=f"b-web-writer-{refuse}")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    _patch_dns(monkeypatch)
    session = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    world = _start_run(
        db_session, internal_client, session, "public research", tools=[agent_tools.TOOL_SEARCH_WEB]
    )
    calls = 0

    def provider_boundary(endpoint, secret, query, limit, *, allowed_addresses):
        nonlocal calls
        calls += 1
        assert endpoint and secret and query and limit and allowed_addresses
        # This is the actual controlled_web search I/O seam, after its policy,
        # quota and DNS checks. A different SQLite connection must be writable.
        with SessionLocal() as contender:
            contender.execute(text("PRAGMA busy_timeout = 100"))
            claim = contender.scalar(
                select(AgentToolCall).where(AgentToolCall.run_id == world["run_id"])
            )
            assert claim is not None and claim.result_json == {}
            contender.execute(
                text("UPDATE agent_runs SET updated_at = updated_at WHERE id = :run_id"),
                {"run_id": world["run_id"]},
            )
            contender.commit()
        if refuse:
            raise controlled_web.WebGatewayError(
                503, "WEB_PROVIDER_UNAVAILABLE", "synthetic unavailable"
            )
        return [
            {
                "title": "Synthetic public page",
                "url": "https://www.example.com/page",
                "snippet": "synthetic",
            }
        ]

    monkeypatch.setattr(controlled_web, "_provider_search", provider_boundary)
    endpoint = f"/internal/agent/runs/{world['run_id']}/tools/{agent_tools.TOOL_SEARCH_WEB}/execute"
    body = {
        "version": 1,
        "input": {"query": "public research", "use_case": "research"},
        "tool_call_id": "claimed-web",
    }
    response = internal_client.post(endpoint, headers=world["headers"], json=body)
    assert calls == 1 and response.status_code == (503 if refuse else 200), response.text
    with SessionLocal() as check:
        claim = check.scalar(select(AgentToolCall).where(AgentToolCall.run_id == world["run_id"]))
        if refuse:
            assert claim is None, "known refusal releases only its empty reservation"
        else:
            assert claim.result_json["results"][0]["title"] == "Synthetic public page"
    if not refuse:
        repeated = internal_client.post(endpoint, headers=world["headers"], json=body)
        assert repeated.status_code == 200 and repeated.json() == response.json()
        assert calls == 1, "durable claim replays the first result without another search"


@pytest.mark.parametrize("mutation", ["delete", "hash", "version", "revision"])
def test_repeated_context_detects_precise_drift_permanently(db_session, internal_client, mutation):
    world = _world(db_session, internal_client, f"b-context-drift-{mutation}")
    first = _context(internal_client, world)
    assert first.status_code == 200
    chunk_id = int(first.json()["context_blocks"][0]["citation"].rsplit(":c", 1)[1])
    chunk = db_session.get(RAGChunk, chunk_id)
    original = (chunk.text, chunk.index_version, chunk.source_revision)
    if mutation == "delete":
        db_session.delete(chunk)
    elif mutation == "hash":
        chunk.text += " changed"
    elif mutation == "version":
        chunk.index_version = "synthetic-drift"
    else:
        chunk.source_revision += 1
    db_session.commit()
    second = _context(internal_client, world)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "AGENT_CONTEXT_INVALIDATED"
    if mutation != "delete":
        chunk.text, chunk.index_version, chunk.source_revision = original
        db_session.commit()
        assert _context(internal_client, world).status_code == 409
    with SessionLocal() as check:
        builds = check.scalars(
            select(ContextBuild).where(ContextBuild.run_id == world["run_id"])
        ).all()
        assert len(builds) == 1 and builds[0].invalidated_at is not None


def test_retained_old_index_chunk_is_still_exact_and_readable(db_session, internal_client, client):
    world = _world(db_session, internal_client, "b-old-index")
    context = _context(internal_client, world).json()
    handle = context["context_blocks"][0]["citation"]
    chunk = db_session.get(RAGChunk, int(handle.rsplit(":c", 1)[1]))
    document = db_session.get(RAGDocument, chunk.document_id)
    # Pointer only: D owns supported-algorithm staging/materialization tests.
    document.index_version = "synthetic-v-next"
    db_session.commit()
    repeated = _context(internal_client, world)
    assert (
        repeated.status_code == 200
        and repeated.json()["context_blocks"] == context["context_blocks"]
    )
    assert (
        _answer(
            internal_client, world, {"role": "assistant", "text": f"回答 [{handle}]"}
        ).status_code
        == 200
    )
    fallback = _fallback(client, world, _public_headers(client, world)).json()
    assert len(fallback["citations"]) == 1
    assert fallback["citations"][0]["index_version"] == chunk.index_version
    assert "_source_ref" not in fallback["citations"][0]


def test_context_reference_binds_wire_and_fingerprint(db_session, internal_client, client):
    world = _world(db_session, internal_client, "b-wire-binding")
    context = _context(internal_client, world).json()
    handle = context["context_blocks"][0]["citation"]
    payload = {"role": "assistant", "text": f"回答 [{handle}]"}
    reference = {"build_id": context["context_build_id"], "attempt": 1, "used_handles": [handle]}
    endpoint = f"/internal/agent/runs/{world['run_id']}/events/append"

    def submit(ref):
        return internal_client.post(
            endpoint,
            headers=world["headers"],
            json={
                "events": [
                    {
                        "seq": 2,
                        "type": "message.assistant_added",
                        "public_payload": payload,
                        "context_reference": ref,
                    }
                ]
            },
        )

    for invalid in [
        {**reference, "build_id": reference["build_id"] + 1000},
        {**reference, "attempt": 2},
        {**reference, "used_handles": ["rag:forged:r1:c42"]},
    ]:
        assert submit(invalid).status_code == 409
    assert submit(reference).status_code == 200
    assert submit({**reference, "used_handles": []}).status_code == 409
    assert submit(reference).json()["duplicates"] == [2]
    assert len(_fallback(client, world, _public_headers(client, world)).json()["citations"]) == 1


def test_legacy_no_reference_never_guesses_citations(db_session, internal_client, client):
    world = _world(db_session, internal_client, "b-legacy-wire")
    context = _context(internal_client, world).json()
    handle = context["context_blocks"][0]["citation"]
    world.pop("context")
    assert (
        _answer(
            internal_client, world, {"role": "assistant", "text": f"旧客户端 [{handle}]"}
        ).status_code
        == 200
    )
    assert _fallback(client, world, _public_headers(client, world)).json()["citations"] == []


def test_sse_revocation_and_reconnect_share_history_projection(db_session, internal_client, client):
    world = _world(db_session, internal_client, "b-sse-revoke")
    context = _context(internal_client, world).json()
    handle = context["context_blocks"][0]["citation"]
    assert (
        _answer(
            internal_client, world, {"role": "assistant", "text": f"回答 [{handle}]"}
        ).status_code
        == 200
    )
    assert (
        internal_client.post(
            f"/internal/agent/runs/{world['run_id']}/settle",
            headers=world["headers"],
            json={"status": "succeeded"},
        ).status_code
        == 200
    )
    headers = _public_headers(client, world)

    def stream_answer():
        response = client.get(
            f"/api/agent/runs/{world['run_id']}/events", headers={**headers, "Last-Event-ID": "1"}
        )
        assert response.status_code == 200
        values = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        return next(row["payload"] for row in values if row["type"] == "message.assistant_added")

    first = stream_answer()
    assert len(first["citations"]) == 1 and first["citations_complete"]
    memory_rag.revoke_memory(
        db_session, memory_id=world["memory_ids"][0], account_id=world["owner"].account.id
    )
    db_session.commit()
    masked = stream_answer()
    assert masked["citations"] == [] and masked["unavailable_citation_count"] == 1
    assert "_source_ref" not in json.dumps(masked)
    fallback = _fallback(client, world, headers).json()
    assert (fallback["citations"], fallback["unavailable_citation_count"]) == ([], 1)


def test_public_reserved_input_is_fingerprinted_before_stripping(db_session, internal_client):
    world = _world(db_session, internal_client, "b-reserved-fingerprint")
    payload = {
        "role": "assistant",
        "text": "unchanged",
        "citations": [{"source_id": "fake"}],
        "unavailable_citation_count": 99,
        "_source_ref": {"secret": "synthetic"},
    }
    first = _answer(internal_client, world, payload)
    assert first.status_code == 200
    assert _answer(internal_client, world, payload).json()["duplicates"] == [2]
    assert _answer(internal_client, world, {**payload, "citations": []}).status_code == 409
    row = db_session.scalar(
        select(AgentRunEvent).where(AgentRunEvent.run_id == world["run_id"], AgentRunEvent.seq == 2)
    )
    assert row.public_payload["text"] == payload["text"]
    assert row.public_payload["citations"] == []
    assert row.public_payload["unavailable_citation_count"] == 0
    assert "_source_ref" not in row.public_payload


@pytest.mark.parametrize("changed", ["deployment_policy", "provider"])
def test_context_policy_change_is_persistently_invalidated(
    db_session, internal_client, monkeypatch, changed
):
    from test_provider_proxy import _seed_provider
    from test_rag_acceptance_contract import _confirm, _enable

    from app import config

    _enable(db_session)
    owner, space, provider = _seed_provider(db_session, name=f"b-policy-{changed}")
    _confirm(db_session, owner, "上海合成政策资料。")
    session = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    world = _start_run(db_session, internal_client, session, "上海")
    context = _context(internal_client, world)
    assert context.status_code == 200 and context.json()["context_blocks"]
    original_version = config.POLICY_VERSION
    if changed == "deployment_policy":
        monkeypatch.setattr(config, "POLICY_VERSION", "synthetic-policy-new")
    else:
        provider.enabled = False
        db_session.commit()
    assert _context(internal_client, world).status_code == 409
    if changed == "deployment_policy":
        monkeypatch.setattr(config, "POLICY_VERSION", original_version)
    else:
        provider.enabled = True
        db_session.commit()
    assert _context(internal_client, world).status_code == 409


def test_rag_off_build_stays_empty_after_late_enable(db_session, internal_client):
    from app.models.platform_features import PlatformFeatureConfig

    world = _world(db_session, internal_client, "b-empty-build")
    flags = db_session.get(PlatformFeatureConfig, 1)
    flags.rag_enabled = False
    db_session.commit()
    first = _context(internal_client, world)
    assert first.status_code == 200 and first.json()["context_blocks"] == []
    # The new sidecar submits an explicit empty reference on an ordinary
    # completed answer. Unchanged off policy must not invalidate this build.
    answered = _answer(internal_client, world, {"role": "assistant", "text": "ordinary reply"})
    assert answered.status_code == 200, answered.text
    still_off = _context(internal_client, world)
    assert still_off.status_code == 200 and still_off.json()["context_blocks"] == []
    assert still_off.json()["context_build_id"] == first.json()["context_build_id"]
    flags.rag_enabled = True
    db_session.commit()
    second = _context(internal_client, world)
    assert second.status_code == 200 and second.json()["context_blocks"] == []
    assert second.json()["context_build_id"] == first.json()["context_build_id"]
    with SessionLocal() as check:
        assert check.get(ContextBuild, first.json()["context_build_id"]).invalidated_at is None


def _cited_event(context, *, seq):
    handle = context["context_blocks"][0]["citation"]
    return {
        "seq": seq,
        "type": "message.assistant_added",
        "public_payload": {"role": "assistant", "text": f"synthetic [{handle}]"},
        "context_reference": {
            "build_id": context["context_build_id"],
            "attempt": context["attempt"],
            "used_handles": [handle],
        },
    }


@pytest.mark.parametrize(
    "change,reason",
    [
        ("rag_disabled", "policy_changed"),
        ("deployment_policy", "policy_changed"),
        ("source_changed", "source_changed"),
        ("unchanged", None),
    ],
)
def test_rejected_event_batch_retains_only_observed_context_invalidation(
    db_session, internal_client, monkeypatch, change, reason
):
    from app import config
    from app.models.platform_features import PlatformFeatureConfig

    world = _world(db_session, internal_client, f"b-rejected-batch-{change}")
    first = _context(internal_client, world)
    assert first.status_code == 200 and first.json()["context_blocks"]
    context = first.json()
    flags = db_session.get(PlatformFeatureConfig, 1)
    original_policy = config.POLICY_VERSION
    handle = context["context_blocks"][0]["citation"]
    chunk = db_session.get(RAGChunk, int(handle.rsplit(":c", 1)[1]))
    original_text = chunk.text
    if change == "rag_disabled":
        flags.rag_enabled = False
        db_session.commit()
    elif change == "deployment_policy":
        monkeypatch.setattr(config, "POLICY_VERSION", "synthetic-changed-policy")
    elif change == "source_changed":
        chunk.text += " synthetic source drift"
        db_session.commit()
    with SessionLocal() as check:
        original_events = check.execute(
            select(
                AgentRunEvent.id,
                AgentRunEvent.seq,
                AgentRunEvent.public_payload,
                AgentRunEvent.request_fingerprint,
                AgentRunEvent.context_reference_json,
            ).where(AgentRunEvent.run_id == world["run_id"])
        ).all()
    cited = _cited_event(context, seq=2)
    rejected = internal_client.post(
        f"/internal/agent/runs/{world['run_id']}/events/append",
        headers=world["headers"],
        json={
            "events": [
                cited,
                {"seq": 4, "type": "message.assistant_added", "public_payload": {"text": "gap"}},
            ]
        },
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "AGENT_EVENT_SEQ_CONFLICT"
    with SessionLocal() as check:
        build = check.get(ContextBuild, context["context_build_id"])
        assert (build.invalidated_at is not None) == (reason is not None)
        assert build.invalidation_reason == reason
        audit_row = check.scalar(
            select(AuditLog).where(
                AuditLog.action == "agent_event_rejected", AuditLog.target_id == world["run_id"]
            )
        )
        assert audit_row.detail["reason"] == "AGENT_EVENT_SEQ_CONFLICT"
        assert (
            check.execute(
                select(
                    AgentRunEvent.id,
                    AgentRunEvent.seq,
                    AgentRunEvent.public_payload,
                    AgentRunEvent.request_fingerprint,
                    AgentRunEvent.context_reference_json,
                ).where(AgentRunEvent.run_id == world["run_id"])
            ).all()
            == original_events
        )
        assert (
            check.scalar(
                select(AgentMessage.id).where(
                    AgentMessage.session_id == world["session"].id, AgentMessage.role == "assistant"
                )
            )
            is None
        )
    if change == "rag_disabled":
        flags.rag_enabled = True
        db_session.commit()
    elif change == "deployment_policy":
        monkeypatch.setattr(config, "POLICY_VERSION", original_policy)
    elif change == "source_changed":
        chunk.text = original_text
        db_session.commit()
    reread = _context(internal_client, world)
    assert reread.status_code == (409 if reason is not None else 200), reread.text
    if reason is None:
        assert reread.json()["context_blocks"] == context["context_blocks"]
        retried = internal_client.post(
            f"/internal/agent/runs/{world['run_id']}/events/append",
            headers=world["headers"],
            json={"events": [cited]},
        )
        assert retried.status_code == 200 and retried.json()["duplicates"] == []


def test_rejected_batch_cannot_invalidate_a_different_signed_attempt(db_session, internal_client):
    from datetime import timedelta

    from app.models.platform_features import PlatformFeatureConfig
    from app.services import agent_queue
    from app.services.agent_tokens import issue_service_token
    from app.utils.timeutil import utcnow

    world = _world(db_session, internal_client, "b-rejected-batch-attempt")
    old_context = _context(internal_client, world).json()
    with SessionLocal() as reaper:
        run = reaper.get(AgentRun, world["run_id"])
        job = reaper.get(AgentJob, world["job_id"])
        run.lease_expires_at = job.lease_expires_at = utcnow() - timedelta(seconds=1)
        reaper.commit()
        assert agent_queue.reaper_pass(reaper) == 1
    lease = internal_client.post(
        "/internal/agent/jobs/lease",
        headers={"Authorization": f"Bearer {issue_service_token()}"},
        json={"kind": "assistant", "leased_by": "b-rejected-batch-replacement"},
    )
    assert lease.status_code == 200 and lease.json()["attempt"] == 2
    fresh_world = {
        "run_id": world["run_id"],
        "headers": {"Authorization": f"Bearer {lease.json()['run_token']}"},
    }
    endpoint = f"/internal/agent/runs/{world['run_id']}/events/append"
    started = internal_client.post(
        endpoint,
        headers=fresh_world["headers"],
        json={"events": [{"seq": 2, "type": "run.started", "public_payload": {}}]},
    )
    assert started.status_code == 200
    fresh_context = _context(internal_client, fresh_world).json()
    assert fresh_context["context_build_id"] != old_context["context_build_id"]
    flags = db_session.get(PlatformFeatureConfig, 1)
    flags.rag_enabled = False
    db_session.commit()
    forged = _cited_event(old_context, seq=3)
    forged["context_reference"]["attempt"] = 2
    refused = internal_client.post(
        endpoint, headers=fresh_world["headers"], json={"events": [forged]}
    )
    assert refused.status_code == 409
    with SessionLocal() as check:
        for build_id in (old_context["context_build_id"], fresh_context["context_build_id"]):
            assert check.get(ContextBuild, build_id).invalidated_at is None
    rejected = internal_client.post(
        endpoint,
        headers=fresh_world["headers"],
        json={
            "events": [
                _cited_event(fresh_context, seq=3),
                {"seq": 5, "type": "message.assistant_added", "public_payload": {"text": "gap"}},
            ]
        },
    )
    assert rejected.status_code == 409
    with SessionLocal() as check:
        assert check.get(ContextBuild, old_context["context_build_id"]).invalidated_at is None
        assert check.get(ContextBuild, fresh_context["context_build_id"]).invalidated_at is not None
        assert list(
            check.scalars(
                select(AgentRunEvent.seq)
                .where(AgentRunEvent.run_id == world["run_id"])
                .order_by(AgentRunEvent.seq)
            )
        ) == [0, 1, 2]
        assert (
            check.scalar(
                select(AgentMessage.id).where(
                    AgentMessage.session_id == world["session"].id, AgentMessage.role == "assistant"
                )
            )
            is None
        )
    flags.rag_enabled = True
    db_session.commit()
    assert _context(internal_client, fresh_world).status_code == 409
    assert _context(internal_client, world).status_code == 403
