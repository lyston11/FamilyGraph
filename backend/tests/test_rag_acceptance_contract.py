"""B acceptance regressions: real APIs, separate writers and exact provenance.

Derived from the frozen audit probes; those original artifacts remain unchanged.
"""

import json
import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import config
from app.db import SessionLocal
from app.models.agent import AgentJob, AgentRun, AgentRunEvent, AgentToolCall
from app.models.context import ContextBuild, ContextBuildItem
from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAGChunk, RAGDocument
from app.services import agent_events, agent_queue, memory_rag
from app.services.agent_tokens import issue_service_token
from app.utils import timeutil
from conftest import (
    auth_header,
    create_agent_fixture,
    create_agent_message,
    create_agent_session,
    login,
)


def _enable(db):
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, updated_at=timeutil.utcnow())
        db.add(row)
    row.memory_enabled = True
    row.rag_enabled = True
    db.commit()


def _confirm(db, owner, summary):
    candidate = memory_rag.propose_candidate(
        db,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote=summary,
        summary=summary,
        suggested_scope="private",
        purpose="independent synthetic B review",
    )
    memory = memory_rag.confirm_candidate(
        db,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope="private",
    )
    db.commit()
    return memory.id


def _start_run(db, internal_client, session, query, *, tools=None):
    message = create_agent_message(db, session, content={"text": query})
    run = agent_queue.enqueue_run(
        db,
        agent_session=session,
        kind="assistant",
        policy_version=config.POLICY_VERSION,
        tool_allowlist=["familygraph.echo"] if tools is None else tools,
        message=message,
    )
    grant_response = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "b-independent"},
        headers={"Authorization": f"Bearer {issue_service_token()}"},
    )
    assert grant_response.status_code == 200, grant_response.text
    grant = grant_response.json()
    assert grant["run_id"] == run.id
    headers = {"Authorization": f"Bearer {grant['run_token']}"}
    started = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={"events": [{"seq": 1, "type": "run.started", "public_payload": {}}]},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    return {"run_id": run.id, "job_id": grant["job_id"], "headers": headers}


def _world(db, internal_client, name, summaries=None, query="上海"):
    _enable(db)
    owner, space = create_agent_fixture(db, name=name)
    memory_ids = [
        _confirm(db, owner, value) for value in (summaries or ["上海聚餐在周日下午三点。"])
    ]
    session = create_agent_session(db, account_id=owner.account.id, space_id=space.id)
    result = _start_run(db, internal_client, session, query)
    result.update(owner=owner, space=space, session=session, memory_ids=memory_ids, name=name)
    return result


def _context(internal_client, world):
    result = internal_client.get(
        f"/internal/agent/runs/{world['run_id']}/context", headers=world["headers"]
    )
    if result.status_code == 200:
        world["context"] = result.json()
    return result


def _answer(internal_client, world, payload):
    context = world.get("context")
    reference = (
        {
            "context_reference": {
                "build_id": context["context_build_id"],
                "attempt": context["attempt"],
                "used_handles": list(
                    dict.fromkeys(re.findall(r"\[(rag:[^\s\]]+)\]", payload["text"]))
                ),
            }
        }
        if context
        else {}
    )
    return internal_client.post(
        f"/internal/agent/runs/{world['run_id']}/events/append",
        json={
            "events": [
                {
                    "seq": 2,
                    "type": "message.assistant_added",
                    "public_payload": payload,
                    **reference,
                }
            ]
        },
        headers=world["headers"],
    )


def _public_headers(client, world):
    response = login(client, world["name"], "123456")
    assert response.status_code == 200, response.text
    return auth_header(response.json())


def _fallback(client, world, headers):
    return client.get(f"/api/agent/runs/{world['run_id']}/events/2/citations", headers=headers)


def test_real_context_obeys_its_documented_utf8_sub_budget(db_session, internal_client):
    world = _world(
        db_session,
        internal_client,
        "b-budget",
        summaries=[f"预算{i}。" + "合成预算说明。" * 90 for i in range(6)],
        query="预算",
    )
    response = _context(internal_client, world)
    assert response.status_code == 200, response.text
    blocks = response.json()["context_blocks"]
    # Six legal candidates are the input, not the required included output.
    candidates = memory_rag.search_rag(
        db_session,
        actor=world["owner"],
        account=world["owner"].account,
        space_id=world["space"].id,
        query="预算",
        for_model=False,
    )
    assert len(candidates) >= 6
    assert 0 < len(blocks) < len(candidates)
    build = db_session.get(ContextBuild, response.json()["context_build_id"])
    from app.services.rag_budget import estimate_context

    estimated = estimate_context(blocks)
    items = db_session.scalars(
        select(ContextBuildItem).where(ContextBuildItem.build_id == build.id)
    ).all()
    assert sum(item.token_estimate for item in items if item.included) == estimated
    assert any(item.exclusion_reason == "token_budget" for item in items)
    print(
        "B_BUDGET",
        {
            "blocks": len(blocks),
            "stored_budget": build.token_budget,
            "wrapped_utf8_estimate": estimated,
        },
    )
    assert estimated <= build.token_budget


@pytest.mark.parametrize("mutation", ["delete", "hash", "version", "revision"])
def test_citation_requires_exact_original_chunk(db_session, internal_client, client, mutation):
    world = _world(db_session, internal_client, f"b-exact-{mutation}")
    context = _context(internal_client, world)
    assert context.status_code == 200, context.text
    block = context.json()["context_blocks"][0]
    chunk = db_session.get(RAGChunk, int(block["citation"].rsplit(":c", 1)[1]))
    if mutation == "delete":
        db_session.delete(chunk)
    elif mutation == "hash":
        chunk.text = "已替换的合成片段，不再是模型所见内容。"
    elif mutation == "version":
        chunk.index_version = "synthetic-other-version"
    else:
        chunk.source_revision += 1
    db_session.commit()
    appended = _answer(
        internal_client, world, {"role": "assistant", "text": f"合成回答 [{block['citation']}]"}
    )
    assert appended.status_code == 200, appended.text
    fallback = _fallback(client, world, _public_headers(client, world))
    assert fallback.status_code == 200, fallback.text
    print("B_EXACT", {"mutation": mutation, "response": fallback.json()})
    assert fallback.json()["citations"] == []


def test_repeated_context_invalidates_when_rag_is_disabled(db_session, internal_client):
    world = _world(db_session, internal_client, "b-rag-disable")
    first = _context(internal_client, world)
    assert first.status_code == 200 and first.json()["context_blocks"]
    flags = db_session.get(PlatformFeatureConfig, 1)
    flags.rag_enabled = False
    db_session.commit()
    second = _context(internal_client, world)
    print(
        "B_RAG_DISABLED",
        {"status": second.status_code, "blocks": len(second.json().get("context_blocks", []))},
    )
    assert second.status_code == 409
    build_id = first.json()["context_build_id"]
    with SessionLocal() as check:
        assert check.get(ContextBuild, build_id).invalidated_at is not None
    flags.rag_enabled = True
    db_session.commit()
    assert _context(internal_client, world).status_code == 409


def test_internal_history_drops_revoked_citation_identifiers(db_session, internal_client, client):
    world = _world(db_session, internal_client, "b-internal-history")
    context = _context(internal_client, world)
    assert context.status_code == 200, context.text
    handle = context.json()["context_blocks"][0]["citation"]
    assert (
        _answer(
            internal_client, world, {"role": "assistant", "text": f"合成回答 [{handle}]"}
        ).status_code
        == 200
    )
    settled = internal_client.post(
        f"/internal/agent/runs/{world['run_id']}/settle",
        json={"status": "succeeded"},
        headers=world["headers"],
    )
    assert settled.status_code == 200, settled.text
    memory_rag.revoke_memory(
        db_session, memory_id=world["memory_ids"][0], account_id=world["owner"].account.id
    )
    db_session.commit()
    public_history = client.get(
        f"/api/agent/sessions/{world['session'].id}/messages",
        headers=_public_headers(client, world),
    ).json()
    public_assistant = next(item for item in public_history if item["role"] == "assistant")
    assert (
        public_assistant["citations"] == [] and public_assistant["unavailable_citation_count"] == 1
    )
    later = _start_run(db_session, internal_client, world["session"], "继续")
    restored = _context(internal_client, later)
    assert restored.status_code == 200, restored.text
    historical = next(item for item in restored.json()["messages"] if item["role"] == "assistant")
    print(
        "B_INTERNAL_HISTORY",
        {
            "public_unavailable": public_assistant["unavailable_citation_count"],
            "internal_stored_citations": historical["content_json"].get("citations"),
        },
    )
    assert historical["content_json"].get("citations", []) == []


def test_sse_does_not_trust_citations_supplied_in_public_payload(
    db_session, internal_client, client
):
    world = _world(db_session, internal_client, "b-sse-forged")
    fake = {
        "source_type": "memory",
        "source_id": "not-a-source",
        "scope": "private",
        "sensitivity": "normal",
        "revision": 99,
        "citation_handle": "forged-handle",
        "text": "synthetic unverified excerpt",
    }
    appended = _answer(
        internal_client, world, {"role": "assistant", "text": "普通回答", "citations": [fake]}
    )
    assert appended.status_code == 200, appended.text
    settled = internal_client.post(
        f"/internal/agent/runs/{world['run_id']}/settle",
        json={"status": "succeeded"},
        headers=world["headers"],
    )
    assert settled.status_code == 200, settled.text
    headers = _public_headers(client, world)
    sse = client.get(f"/api/agent/runs/{world['run_id']}/events", headers=headers)
    assert sse.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in sse.text.splitlines()
        if line.startswith("data: ")
    ]
    answer = next(event for event in events if event["type"] == "message.assistant_added")
    fallback = _fallback(client, world, headers)
    assert fallback.status_code == 200 and fallback.json()["citations"] == []
    print("B_SSE_FORGED", {"fallback": fallback.json(), "sse_payload": answer["payload"]})
    assert answer["payload"].get("citations", []) == []


def test_fallback_is_scoped_to_the_run_session_and_assistant_role(
    db_session, internal_client, client
):
    other_owner, other_space = create_agent_fixture(db_session, name="b-shadow-earlier")
    other = create_agent_session(
        db_session, account_id=other_owner.account.id, space_id=other_space.id
    )
    shadow = create_agent_message(db_session, other, content={"text": "无关会话的用户消息"})
    world = _world(db_session, internal_client, "b-fallback-key")
    context = _context(internal_client, world)
    assert context.status_code == 200, context.text
    handle = context.json()["context_blocks"][0]["citation"]
    # This is a legal user message shape: message keys are unique per session,
    # and the browser endpoint accepts caller-chosen Idempotency-Key values.
    shadow.idempotency_key = f"run:{world['run_id']}:event:2"
    db_session.commit()
    assert (
        _answer(
            internal_client, world, {"role": "assistant", "text": f"合成回答 [{handle}]"}
        ).status_code
        == 200
    )
    headers = _public_headers(client, world)
    history = client.get(
        f"/api/agent/sessions/{world['session'].id}/messages", headers=headers
    ).json()
    assistant = next(item for item in history if item["role"] == "assistant")
    assert len(assistant["citations"]) == 1
    fallback = _fallback(client, world, headers)
    assert fallback.status_code == 200, fallback.text
    print(
        "B_FALLBACK_SCOPE",
        {"history_count": len(assistant["citations"]), "fallback": fallback.json()},
    )
    assert fallback.json()["citations"] == assistant["citations"]


def _replace_lease(run_id, *, running=False):
    # A deterministic synchronization point, using a separate DB session and
    # real reaper/lease services after the old token has passed authorization.
    with SessionLocal() as other:
        run = other.get(AgentRun, run_id)
        job = other.get(AgentJob, run.job_id)
        past = timeutil.utcnow() - timedelta(seconds=1)
        job.lease_expires_at = past
        run.lease_expires_at = past
        other.commit()
        assert agent_queue.reaper_pass(other) == 1
        grant = agent_queue.lease_next(
            other, kind="assistant", leased_by="replacement", ttl_seconds=60
        )
        assert grant is not None and grant.run.id == run_id and grant.job.attempt == 2
        if running:
            agent_events.append_events(
                other,
                grant.run,
                [agent_events.EventEntry(seq=2, type="run.started", public_payload={})],
            )
            other.commit()


@pytest.mark.parametrize("endpoint", ["context", "events", "heartbeat", "settle", "tool"])
def test_signed_attempt_is_fenced_after_authorization(
    db_session, internal_client, monkeypatch, endpoint
):
    from app.api import internal_agent

    world = _world(db_session, internal_client, f"b-fence-{endpoint}")
    authorize = internal_agent._authorize_run
    switched = False

    def authorize_then_replace(db, request, run_id):
        nonlocal switched
        result = authorize(db, request, run_id)
        if not switched and run_id == world["run_id"]:
            assert result[2]["attempt"] == 1
            switched = True
            _replace_lease(run_id, running=endpoint == "tool")
        return result

    monkeypatch.setattr(internal_agent, "_authorize_run", authorize_then_replace)
    run_id = world["run_id"]
    if endpoint == "context":
        response = _context(internal_client, world)
    elif endpoint == "events":
        response = _answer(
            internal_client, world, {"role": "assistant", "text": "stale attempt wrote this"}
        )
    elif endpoint == "heartbeat":
        response = internal_client.post(
            f"/internal/agent/jobs/{world['job_id']}/heartbeat",
            json={"lease_ttl_seconds": 3600},
            headers=world["headers"],
        )
    elif endpoint == "settle":
        response = internal_client.post(
            f"/internal/agent/runs/{run_id}/settle",
            json={"status": "succeeded"},
            headers=world["headers"],
        )
    else:
        response = internal_client.post(
            f"/internal/agent/runs/{run_id}/tools/familygraph.echo/execute",
            json={"version": 1, "input": {"text": "stale tool"}, "tool_call_id": "b-stale-tool"},
            headers=world["headers"],
        )
    assert switched
    with SessionLocal() as inspect_db:
        current = inspect_db.get(AgentRun, run_id)
        stored_tool = inspect_db.scalar(select(AgentToolCall).where(AgentToolCall.run_id == run_id))
        stored_answer = inspect_db.scalar(
            select(AgentRunEvent).where(
                AgentRunEvent.run_id == run_id, AgentRunEvent.type == "message.assistant_added"
            )
        )
        observed = {
            "endpoint": endpoint,
            "http": response.status_code,
            "current_attempt": current.attempt,
            "current_status": current.status,
            "stale_answer_persisted": stored_answer is not None,
            "tool_result_persisted": stored_tool is not None,
        }
    print("B_STALE_ATTEMPT", observed)
    assert response.status_code in (403, 409), observed
    assert not observed["stale_answer_persisted"] and not observed["tool_result_persisted"]
    assert observed["current_attempt"] == 2
    assert observed["current_status"] == ("running" if endpoint == "tool" else "leased")


def test_source_revocation_retry_keeps_original_committed_event(
    db_session, internal_client, client
):
    world = _world(db_session, internal_client, "b-retry-control")
    context = _context(internal_client, world)
    assert context.status_code == 200, context.text
    handle = context.json()["context_blocks"][0]["citation"]
    payload = {"role": "assistant", "text": f"合成回答 [{handle}]", "web_citations": []}
    first = _answer(internal_client, world, payload)
    assert first.status_code == 200, first.text
    row = db_session.scalar(
        select(AgentRunEvent).where(AgentRunEvent.run_id == world["run_id"], AgentRunEvent.seq == 2)
    )
    original = (row.id, row.public_payload, row.request_fingerprint, row.context_reference_json)
    memory_rag.revoke_memory(
        db_session, memory_id=world["memory_ids"][0], account_id=world["owner"].account.id
    )
    db_session.commit()
    retried = _answer(internal_client, world, payload)
    assert retried.status_code == 200 and retried.json()["duplicates"] == [2]
    db_session.refresh(row)
    assert (
        row.id,
        row.public_payload,
        row.request_fingerprint,
        row.context_reference_json,
    ) == original
    fallback = _fallback(client, world, _public_headers(client, world))
    assert (
        fallback.status_code == 200
        and fallback.json()["citations"] == []
        and fallback.json()["unavailable_citation_count"] == 1
    )
    print(
        "B_RETRY_CONTROL", {"duplicates": retried.json()["duplicates"], "fallback_unavailable": 1}
    )


def test_concurrent_same_attempt_context_returns_one_reused_build(
    db_session, internal_client, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi.testclient import TestClient

    from app.api import internal_agent
    from app.main import internal_app

    world = _world(db_session, internal_client, "b-concurrent-context")
    barrier = Barrier(2, timeout=5)
    original_authorize = internal_agent._authorize_run

    def synchronized_authorize(*args, **kwargs):
        result = original_authorize(*args, **kwargs)
        barrier.wait()
        return result

    monkeypatch.setattr(internal_agent, "_authorize_run", synchronized_authorize)

    def fetch():
        api = TestClient(internal_app, raise_server_exceptions=False)
        try:
            return _context(api, world)
        finally:
            api.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: fetch(), range(2)))
    codes = sorted(response.status_code for response in responses)
    builds = db_session.scalars(
        select(ContextBuild).where(ContextBuild.run_id == world["run_id"])
    ).all()
    print("B_CONCURRENT_CONTEXT", {"codes": codes, "persisted_builds": len(builds)})
    assert codes == [200, 200]
    assert responses[0].json()["context_build_id"] == responses[1].json()["context_build_id"]


def test_fts_refills_after_source_dependency_denies_top_hit(db_session, internal_client):
    world = _world(
        db_session,
        internal_client,
        "b-fts-refill",
        summaries=["needlexyz shared note"],
        query="needlexyz",
    )
    root_document = db_session.scalar(
        select(RAGDocument).where(
            RAGDocument.source_type == "memory",
            RAGDocument.source_id == str(world["memory_ids"][0]),
        )
    )
    root_chunk = db_session.scalar(select(RAGChunk).where(RAGChunk.document_id == root_document.id))
    candidate = memory_rag.propose_candidate(
        db_session,
        author_account_id=world["owner"].account.id,
        source={
            "kind": "rag_chunk",
            "document_id": root_document.id,
            "chunk_id": root_chunk.id,
            "revision": root_document.revision,
            "index_version": root_chunk.index_version,
            "space_id": world["space"].id,
        },
        source_quote=root_chunk.text,
        summary="needlexyz shared note",
        suggested_scope="private",
        purpose="synthetic dependent source",
    )
    copied = memory_rag.confirm_candidate(
        db_session,
        candidate_id=candidate.id,
        confirmer=world["owner"],
        confirmer_account=world["owner"].account,
        scope="private",
    )
    db_session.commit()
    valid_id = _confirm(db_session, world["owner"], "needlexyz shared note")
    memory_rag.revoke_memory(
        db_session, memory_id=world["memory_ids"][0], account_id=world["owner"].account.id
    )
    db_session.commit()
    common = dict(
        actor=world["owner"],
        account=world["owner"].account,
        space_id=world["space"].id,
        query="needlexyz",
        agent_kind="assistant",
        for_model=False,
    )
    limited = memory_rag.search_rag(db_session, limit=1, **common)
    control = memory_rag.search_rag(db_session, limit=20, **common)
    limited_ids = [hit.source_id for hit in limited]
    control_ids = [hit.source_id for hit in control]
    print(
        "B_FTS_REFILL",
        {
            "denied_copy_id": str(copied.id),
            "valid_id": str(valid_id),
            "limit1": limited_ids,
            "limit20": control_ids,
        },
    )
    assert str(valid_id) in control_ids
    assert limited_ids == [str(valid_id)]


def test_exact_16k_public_payload_preserves_text_web_and_citation_fallback(
    db_session, internal_client, client
):
    world = _world(db_session, internal_client, "b-exact-byte-control")
    context = _context(internal_client, world)
    assert context.status_code == 200, context.text
    handle = context.json()["context_blocks"][0]["citation"]
    payload = {
        "role": "assistant",
        "text": f"[{handle}]\n" + '界🙂"' * 1000,
        "web_citations": [
            {
                "url": "https://example.com/synthetic",
                "title": "合成引用",
                "excerpt": "合成摘要",
                "trust": "external",
            }
        ],
    }

    def size(value):
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))

    padding = agent_events.MAX_PAYLOAD_BYTES - size(payload)
    assert padding > 0
    payload["text"] += "x" * padding
    assert size(payload) == 16384
    appended = _answer(internal_client, world, payload)
    assert appended.status_code == 200, appended.text
    event = db_session.scalar(
        select(AgentRunEvent).where(AgentRunEvent.run_id == world["run_id"], AgentRunEvent.seq == 2)
    )
    assert event.public_payload == payload and size(event.public_payload) == 16384
    fallback = _fallback(client, world, _public_headers(client, world))
    assert fallback.status_code == 200 and len(fallback.json()["citations"]) == 1
    oversized = {**payload, "text": payload["text"] + "x"}
    rejected = internal_client.post(
        f"/internal/agent/runs/{world['run_id']}/events/append",
        headers=world["headers"],
        json={
            "events": [{"seq": 3, "type": "message.assistant_added", "public_payload": oversized}]
        },
    )
    assert rejected.status_code == 422, rejected.text
    print(
        "B_16K_CONTROL",
        {
            "accepted_utf8_bytes": 16384,
            "web_preserved": True,
            "fallback_citations": 1,
            "oversize_status": rejected.status_code,
        },
    )
