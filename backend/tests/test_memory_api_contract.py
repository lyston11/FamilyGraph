"""Real FastAPI/migrated-DB memory contract, permission and retry regressions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app import config
from app.db import SessionLocal
from app.models.account import Account
from app.models.memory import Memory, MemoryCandidate
from app.models.rag import RAGChunk, RAGDocument
from app.models.space import SpaceMember
from app.models.user import User
from app.models.v2_foundation import DomainEvent
from app.services import context_builder, memory_rag, memory_sources
from app.utils.timeutil import utcnow
from conftest import (
    auth_header,
    create_agent_fixture,
    create_agent_message,
    create_agent_session,
    create_space_member,
    create_user_with_pin,
    login,
    seed_space_with_owner,
)


def _identity(client, db, name="memory-api-owner"):
    actor, space = create_agent_fixture(db, name=name)
    response = login(client, name, "123456")
    assert response.status_code == 200
    return actor, space, auth_header(response.json())


def _payload(**changes):
    return {
        "source": {"kind": "manual"},
        "idempotency_key": str(uuid4()),
        "raw_quote": "The cobalt walnut archive is upstairs.",
        "summary": "The cobalt walnut archive is upstairs.",
        "purpose": "family reference",
        "suggested_scope": "private",
        "sensitivity": "normal",
        **changes,
    }


def _create(client, headers, payload=None):
    response = client.post("/api/memory-candidates", headers=headers, json=payload or _payload())
    assert response.status_code == 201, response.text
    return response.json()


def _confirm(client, headers, candidate, **changes):
    response = client.post(
        f"/api/memory-candidates/{candidate['id']}/confirm",
        headers=headers,
        json={"scope": "private", **changes},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _search(client, headers, space_id, query="cobalt walnut"):
    response = client.get(
        "/api/rag/search", headers=headers, params={"space_id": space_id, "q": query}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _rag_payload(hit, space_id, **changes):
    payload = _payload(
        source={
            "kind": "rag_chunk",
            "document_id": hit["document_id"],
            "chunk_id": hit["chunk_id"],
            "revision": hit["revision"],
            "index_version": hit["index_version"],
            "space_id": space_id,
        },
        sensitivity=hit["sensitivity"],
        **changes,
    )
    payload.pop("raw_quote")
    return payload


def _assert_redacted(value, status):
    assert value["source_status"] == status
    assert value["raw_quote"] is None
    assert value.get("summary", value.get("content")) is None
    assert value["purpose"] is None
    assert value["source_message_id"] is None
    assert value["source_document_ref"] is None
    assert value["source_span_json"] == {}
    assert value["allowed_scopes"] == []


def test_real_manual_create_confirm_retry_and_search(client, db_session):
    actor, space, headers = _identity(client, db_session)
    payload = _payload()
    missing = {key: value for key, value in payload.items() if key != "source"}
    response = client.post("/api/memory-candidates", headers=headers, json=missing)
    assert response.status_code == 422
    assert "刷新客户端" in response.json()["error"]["message"]
    assert db_session.scalar(select(func.count()).select_from(MemoryCandidate)) == 0

    created = _create(client, headers, payload)
    assert created["raw_quote"] == payload["raw_quote"]
    assert created["source_kind"] == "manual" and created["source_status"] == "available"
    assert created["allowed_scopes"] == [f"household:{space.id}", "private"]
    assert client.get("/api/memory-candidates", headers=headers).json() == [created]
    assert _search(client, headers, space.id) == []

    # A new HTTP request after a lost response reuses the committed operation.
    assert _create(client, headers, payload) == created
    conflict = client.post(
        "/api/memory-candidates", headers=headers, json={**payload, "summary": "changed"}
    )
    assert conflict.status_code == 409
    memory = _confirm(client, headers, created, retention_days=30)
    assert _confirm(client, headers, created, retention_days=30) == memory
    conflict = client.post(
        f"/api/memory-candidates/{created['id']}/confirm",
        headers=headers,
        json={"scope": "private", "retention_days": 31},
    )
    assert conflict.status_code == 409
    assert db_session.scalar(select(func.count()).select_from(Memory)) == 1
    assert _search(client, headers, space.id)[0]["source_id"] == str(memory["id"])
    assert client.get("/api/memories", headers=headers).json() == [memory]
    other = _create(client, headers)
    dismissed = client.post(f"/api/memory-candidates/{other['id']}/dismiss", headers=headers)
    assert dismissed.status_code == 200 and dismissed.json()["raw_quote"] == payload["raw_quote"]
    assert (
        client.get(
            "/api/memory-candidates", headers=headers, params={"include_decided": True}
        ).status_code
        == 200
    )
    assert actor.account.id == db_session.scalar(select(Memory.author_account_id))


@pytest.mark.parametrize("operation", ["create", "confirm", "dismiss", "revoke"])
def test_response_serialization_failure_rolls_back_before_commit(
    client, db_session, monkeypatch, operation
):
    from app.api import memory as api

    _, _, headers = _identity(client, db_session)
    candidate = _create(client, headers) if operation != "create" else None
    memory = _confirm(client, headers, candidate) if operation == "revoke" else None
    candidate_mapper = operation in ("create", "dismiss")
    name = "_candidate_out" if candidate_mapper else "_memory_out"
    original = getattr(api, name)

    def unserializable(*args, **kwargs):
        value = original(*args, **kwargs)
        value.source_span_json = {"unserializable": object()}
        return value

    monkeypatch.setattr(api, name, unserializable)
    with TestClient(client.app, raise_server_exceptions=False) as transport:
        if operation == "create":
            response = transport.post("/api/memory-candidates", headers=headers, json=_payload())
        elif operation == "confirm":
            response = transport.post(
                f"/api/memory-candidates/{candidate['id']}/confirm",
                headers=headers,
                json={"scope": "private"},
            )
        elif operation == "dismiss":
            response = transport.post(
                f"/api/memory-candidates/{candidate['id']}/dismiss", headers=headers
            )
        else:
            response = transport.post(f"/api/memories/{memory['id']}/revoke", headers=headers)
    assert response.status_code == 500
    db_session.expire_all()
    if operation == "create":
        assert db_session.scalar(select(func.count()).select_from(MemoryCandidate)) == 0
    elif operation in ("confirm", "dismiss"):
        assert db_session.get(MemoryCandidate, candidate["id"]).status == "pending"
        assert db_session.scalar(select(func.count()).select_from(Memory)) == 0
    else:
        row = db_session.get(Memory, memory["id"])
        assert row.status == "active" and row.revision == 1


@pytest.mark.parametrize("field", ["source_document_ref", "source_span", "source_message_id"])
def test_fake_or_conflicting_legacy_source_never_authorizes(client, db_session, field):
    _, _, headers = _identity(client, db_session)
    payload = _payload()
    payload[field] = (
        {"kind": "manual", "verified": True}
        if field == "source_span"
        else (123 if field == "source_message_id" else "authorized-by-me")
    )
    response = client.post("/api/memory-candidates", headers=headers, json=payload)
    assert response.status_code == 422
    if field == "source_document_ref":
        payload.pop("source")
        assert (
            client.post("/api/memory-candidates", headers=headers, json=payload).status_code == 422
        )
    assert db_session.scalar(select(func.count()).select_from(MemoryCandidate)) == 0


@pytest.mark.parametrize(
    "variant", ["other_author", "assistant", "system", "derived", "altered_quote"]
)
def test_message_snapshot_requires_exact_original_user_input(client, db_session, variant):
    actor, space, headers = _identity(client, db_session)
    author = actor
    if variant == "other_author":
        author = create_user_with_pin(db_session, "other-message-author", "123456")
    session = create_agent_session(db_session, account_id=author.account.id, space_id=space.id)
    message = create_agent_message(
        db_session,
        session,
        role=variant if variant in ("assistant", "system") else "user",
        content={
            "text": "An original user memory.",
            **({"citations": ["fake"]} if variant == "derived" else {}),
        },
    )
    payload = _payload(source={"kind": "agent_message", "message_id": message.id})
    payload["raw_quote"] = "forged" if variant == "altered_quote" else "An original user memory."
    response = client.post("/api/memory-candidates", headers=headers, json=payload)
    assert response.status_code in (403, 422)
    assert db_session.scalar(select(func.count()).select_from(MemoryCandidate)) == 0


@pytest.mark.parametrize("delete_session", [False, True])
def test_confirmed_original_user_snapshot_survives_chat_fk_deletion(
    client, db_session, delete_session
):
    actor, space, headers = _identity(client, db_session)
    session = create_agent_session(db_session, account_id=actor.account.id, space_id=space.id)
    message = create_agent_message(
        db_session, session, content={"text": "An original cobalt walnut story."}
    )
    payload = _payload(
        source={"kind": "agent_message", "message_id": message.id},
        raw_quote="An original cobalt walnut story.",
    )
    candidate = _create(client, headers, payload)
    memory = _confirm(client, headers, candidate)
    # Legacy message locator is safe only under the exact same original-user rule.
    pending_payload = {**payload, "idempotency_key": str(uuid4()), "source_message_id": message.id}
    pending_payload.pop("source")
    pending = _create(client, headers, pending_payload)
    original_message_id = message.id
    db_session.delete(session if delete_session else message)
    db_session.commit()
    db_session.expire_all()
    row = db_session.get(Memory, memory["id"])
    assert row.source_message_id is None and row.source_kind == "agent_message"
    assert row.source_span_json["message_id"] == original_message_id
    listed = client.get("/api/memories", headers=headers).json()[0]
    assert listed["raw_quote"] == payload["raw_quote"]
    assert listed["source_status"] == "deleted_snapshot"
    assert _search(client, headers, space.id)
    pending_view = client.get("/api/memory-candidates", headers=headers).json()[0]
    _assert_redacted(pending_view, "unavailable")
    response = client.post(
        f"/api/memory-candidates/{pending['id']}/confirm",
        headers=headers,
        json={"scope": "private"},
    )
    assert response.status_code == 403


def test_rag_source_locator_scope_and_sensitivity_are_reauthorized(client, db_session):
    actor, space, headers = _identity(client, db_session)
    other_space = seed_space_with_owner(db_session, actor.id, name="other-rag-space")
    root = _confirm(client, headers, _create(client, headers), scope=f"household:{space.id}")
    hit = _search(client, headers, space.id)[0]
    source_payload = _rag_payload(hit, space.id)
    for field, value in {
        "document_id": hit["document_id"] + 100,
        "chunk_id": hit["chunk_id"] + 100,
        "revision": hit["revision"] + 1,
        "index_version": "wrong-version",
        "space_id": other_space.id,
    }.items():
        forged = {**source_payload, "source": {**source_payload["source"], field: value}}
        response = client.post("/api/memory-candidates", headers=headers, json=forged)
        assert response.status_code == 403, (field, response.text)
    copy = _create(client, headers, source_payload)
    assert copy["raw_quote"] == hit["text"]
    assert copy["allowed_scopes"] == [f"household:{space.id}", "private"]
    denied = client.post(
        f"/api/memory-candidates/{copy['id']}/confirm",
        headers=headers,
        json={"scope": f"household:{other_space.id}"},
    )
    assert denied.status_code == 422
    private = _confirm(client, headers, copy)
    private_hit = next(
        item
        for item in _search(client, headers, space.id)
        if item["source_id"] == str(private["id"])
    )
    recopy = _create(client, headers, _rag_payload(private_hit, space.id))
    assert recopy["allowed_scopes"] == ["private"]
    denied = client.post(
        f"/api/memory-candidates/{recopy['id']}/confirm",
        headers=headers,
        json={"scope": f"household:{space.id}"},
    )
    assert denied.status_code == 422
    # A private copy still cannot be retrieved from a different shared-source space.
    assert _search(client, headers, other_space.id) == []
    assert root["scope"] == "household"


def test_human_rag_save_is_separate_from_model_local_provider_gate(client, db_session):
    actor, space, headers = _identity(client, db_session)
    candidate = _create(client, headers, _payload(sensitivity="high"))
    _confirm(client, headers, candidate)
    hit = _search(client, headers, space.id)[0]
    payload = _rag_payload(hit, space.id)
    response = client.post(
        "/api/memory-candidates", headers=headers, json={**payload, "sensitivity": "normal"}
    )
    assert response.status_code == 422
    copy = _create(client, headers, payload)
    assert copy["sensitivity"] == "high" and copy["allowed_scopes"] == ["private"]
    assert (
        memory_rag.search_rag(
            db_session, actor=actor, account=actor.account, space_id=space.id, query="cobalt walnut"
        )
        == []
    )


@pytest.mark.parametrize(
    "invalidation", ["revoke", "expire", "reader_membership", "author_visibility"]
)
def test_rag_copy_dependencies_guard_every_read_and_replay_surface(
    client, db_session, invalidation
):
    actor, space, headers = _identity(client, db_session)
    member = create_user_with_pin(db_session, "copy-reader", "123456")
    create_space_member(db_session, space.id, member.id)
    member_headers = auth_header(login(client, "copy-reader", "123456").json())
    root = _confirm(client, headers, _create(client, headers), scope=f"household:{space.id}")
    hit = _search(client, member_headers, space.id)[0]
    first_payload = _rag_payload(hit, space.id)
    first = _create(client, member_headers, first_payload)
    private = _confirm(client, member_headers, first)
    pending_payload = _rag_payload(hit, space.id)
    pending = _create(client, member_headers, pending_payload)
    copy_hit = next(
        item
        for item in _search(client, member_headers, space.id)
        if item["source_id"] == str(private["id"])
    )
    nested = _confirm(
        client, member_headers, _create(client, member_headers, _rag_payload(copy_hit, space.id))
    )
    if invalidation == "revoke":
        assert client.post(f"/api/memories/{root['id']}/revoke", headers=headers).status_code == 200
    elif invalidation == "expire":
        from datetime import timedelta

        db_session.get(Memory, root["id"]).retention_until = utcnow() - timedelta(seconds=1)
        db_session.commit()
    else:
        subject_id = member.id if invalidation == "reader_membership" else actor.id
        create_space_member(db_session, space.id, subject_id, status="removed")
    views = client.get(
        "/api/memory-candidates", headers=member_headers, params={"include_decided": True}
    ).json()
    for view in views:
        _assert_redacted(view, "unavailable")
    listed = client.get("/api/memories", headers=member_headers).json()
    assert {item["id"] for item in listed} == {private["id"], nested["id"]}
    for view in listed:
        _assert_redacted(view, "unavailable")
    assert _search(client, member_headers, space.id) == []
    built = context_builder.ContextBuilder(db_session).build(
        actor=member,
        space_id=space.id,
        agent_kind="assistant",
        query="cobalt walnut",
        run_id=None,
        provider_kind="openai_compatible",
    )
    assert built.sources == ()
    for candidate, payload in ((first, first_payload), (pending, pending_payload)):
        assert (
            client.post("/api/memory-candidates", headers=member_headers, json=payload).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/memory-candidates/{candidate['id']}/confirm",
                headers=member_headers,
                json={"scope": "private"},
            ).status_code
            == 403
        )
    row = db_session.get(Memory, private["id"], populate_existing=True)
    if invalidation in ("revoke", "expire"):
        assert not memory_sources.memory_materializable(db_session, row)
        with pytest.raises(HTTPException) as error:
            memory_rag.index_memory(db_session, row)
        assert error.value.status_code == 409
        memory_rag.rebuild_index(db_session)
        db_session.commit()
        assert _search(client, member_headers, space.id) == []
    else:
        # A reader-specific loss must not globally invalidate everyone else's data.
        assert memory_sources.memory_materializable(db_session, row)
        create_space_member(db_session, space.id, subject_id, status="active")
        assert _search(client, member_headers, space.id)


def test_legacy_unverified_content_is_quarantined_and_can_only_recover_from_real_evidence(
    client, db_session
):
    actor, space, headers = _identity(client, db_session)
    session = create_agent_session(db_session, account_id=actor.account.id, space_id=space.id)
    message = create_agent_message(
        db_session, session, content={"text": "Legacy cobalt walnut record."}
    )
    now = utcnow()
    candidate = MemoryCandidate(
        author_account_id=actor.account.id,
        source_message_id=message.id,
        source_span_json={"raw_legacy": "sensitive source location"},
        source_quote="Legacy cobalt walnut record.",
        summary="Legacy cobalt walnut record.",
        suggested_scope="private",
        purpose="private purpose",
        sensitivity="normal",
        extractor_version="old",
        status="confirmed",
        confirmed_by_account_id=actor.account.id,
        confirmed_at=now,
        decided_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(candidate)
    db_session.flush()
    memory = Memory(
        author_account_id=actor.account.id,
        source_candidate_id=candidate.id,
        source_message_id=message.id,
        raw_quote=candidate.source_quote,
        content=candidate.summary,
        scope="private",
        sensitivity="normal",
        purpose="private purpose",
        confirmed_by_account_id=actor.account.id,
        confirmed_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(memory)
    db_session.flush()
    candidate.memory_id = memory.id
    document = RAGDocument(
        source_type="memory",
        source_id=str(memory.id),
        author_account_id=actor.account.id,
        owner_user_id=actor.id,
        scope="private",
        space_id=None,
        sensitivity="normal",
        confirmation_status="confirmed",
        index_version=memory_rag.RAG_INDEX_VERSION,
        created_at=now,
        updated_at=now,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        RAGChunk(
            document_id=document.id,
            chunk_index=0,
            text=memory.content,
            token_estimate=memory_rag._estimate_tokens(memory.content),
            index_version=memory_rag.RAG_INDEX_VERSION,
            created_at=now,
        )
    )
    db_session.commit()
    _assert_redacted(
        client.get(
            "/api/memory-candidates", headers=headers, params={"include_decided": True}
        ).json()[0],
        "unverified",
    )
    _assert_redacted(client.get("/api/memories", headers=headers).json()[0], "unverified")
    assert _search(client, headers, space.id) == []
    assert not memory_sources.memory_materializable(db_session, memory)
    assert memory_sources.verify_legacy_source(db_session, candidate, account=actor.account)
    assert memory_sources.verify_legacy_source(db_session, memory, account=actor.account)
    db_session.commit()
    restored = client.get("/api/memories", headers=headers).json()[0]
    assert restored["source_status"] == "available"
    assert restored["content"] == "Legacy cobalt walnut record."
    assert restored["source_span_json"].get("legacy_source_span") is None
    assert _search(client, headers, space.id)
    assert candidate.status == "confirmed" and candidate.memory_id == memory.id


@pytest.mark.parametrize(
    "memory_enabled,rag_enabled", [(False, False), (False, True), (True, False), (True, True)]
)
def test_all_feature_combinations_have_real_server_gates(
    client, db_session, monkeypatch, memory_enabled, rag_enabled
):
    _, space, headers = _identity(client, db_session)
    memory = _confirm(client, headers, _create(client, headers))
    pending = _create(client, headers)
    dismissed = _create(client, headers)
    monkeypatch.setattr(config, "MEMORY_ENABLED", memory_enabled)
    monkeypatch.setattr(config, "RAG_ENABLED", rag_enabled)
    for path in ("/api/memory-candidates", "/api/memories"):
        assert client.get(path, headers=headers).status_code == (200 if memory_enabled else 503)
    response = client.get(
        "/api/rag/search", headers=headers, params={"space_id": space.id, "q": "cobalt walnut"}
    )
    assert response.status_code == (200 if rag_enabled else 503)
    if rag_enabled:
        assert response.json()
        save = client.post(
            "/api/memory-candidates",
            headers=headers,
            json=_rag_payload(response.json()[0], space.id),
        )
        assert save.status_code == (201 if memory_enabled else 503)
    assert client.post("/api/memory-candidates", headers=headers, json=_payload()).status_code == (
        201 if memory_enabled else 503
    )
    for path, body in (
        (f"/api/memory-candidates/{pending['id']}/confirm", {"scope": "private"}),
        (f"/api/memory-candidates/{dismissed['id']}/dismiss", None),
        (f"/api/memories/{memory['id']}/revoke", None),
    ):
        response = client.post(path, headers=headers, json=body)
        assert response.status_code == (200 if memory_enabled else 503)
    assert client.delete(f"/api/memories/{memory['id']}", headers=headers).status_code == (
        204 if memory_enabled else 503
    )


def test_parallel_create_and_confirmation_have_single_durable_results(
    client, db_session, monkeypatch
):
    actor, _, _ = _identity(client, db_session)
    account_id = actor.account.id
    key = str(uuid4())
    gate = Barrier(2)
    original_resolve = memory_sources.resolve_source

    def resolve_then_wait(*args, **kwargs):
        resolved = original_resolve(*args, **kwargs)
        # Both requests have already observed no existing idempotency key.
        gate.wait(timeout=5)
        return resolved

    monkeypatch.setattr(memory_sources, "resolve_source", resolve_then_wait)

    def create():
        with SessionLocal() as db:
            row = memory_rag.propose_candidate(
                db,
                author_account_id=account_id,
                source={"kind": "manual"},
                source_quote="Parallel cobalt walnut.",
                summary="Parallel cobalt walnut.",
                suggested_scope="private",
                purpose="parallel retry",
                idempotency_key=key,
            )
            db.commit()
            return row.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        candidates = list(pool.map(lambda _: create(), range(2)))
    assert candidates[0] == candidates[1]
    monkeypatch.setattr(memory_sources, "resolve_source", original_resolve)
    gate = Barrier(2)
    original_access = memory_sources.source_access
    synchronized = local()

    def authorize_pending_then_wait(*args, **kwargs):
        access = original_access(*args, **kwargs)
        if kwargs.get("require_live_message") and not getattr(synchronized, "done", False):
            synchronized.done = True
            # Both requests have read pending and completed the first auth check.
            gate.wait(timeout=5)
        return access

    monkeypatch.setattr(memory_sources, "source_access", authorize_pending_then_wait)

    def confirm():
        with SessionLocal() as db:
            account = db.get(Account, account_id)
            user = db.get(User, account.user_id)
            row = memory_rag.confirm_candidate(
                db,
                candidate_id=candidates[0],
                confirmer=user,
                confirmer_account=account,
                scope="private",
                retention_days=30,
            )
            db.commit()
            return row.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        memories = list(pool.map(lambda _: confirm(), range(2)))
    assert memories[0] == memories[1]
    assert db_session.scalar(select(func.count()).select_from(Memory)) == 1
    for kind in ("memory.candidate.proposed", "memory.confirmed"):
        assert (
            db_session.scalar(
                select(func.count()).select_from(DomainEvent).where(DomainEvent.type == kind)
            )
            == 1
        )


@pytest.mark.parametrize(
    "case", ["message_cross_space", "message_high_shared", "rag_private_shared"]
)
def test_legacy_adapter_cannot_validate_an_existing_memory_with_wider_scope(
    client, db_session, case
):
    actor, space, headers = _identity(client, db_session)
    target = (
        seed_space_with_owner(db_session, actor.id, name="legacy-other-space")
        if case == "message_cross_space"
        else space
    )
    session = create_agent_session(db_session, account_id=actor.account.id, space_id=space.id)
    quote = "Legacy forbidden cobalt walnut scope."
    message = create_agent_message(db_session, session, content={"text": quote})
    message_id, document_ref = message.id, None
    if case == "rag_private_shared":
        _confirm(client, headers, _create(client, headers))
        hit = _search(client, headers, space.id)[0]
        message_id = None
        document_ref = (
            f"rag-chunk:{hit['document_id']}:{hit['chunk_id']}:{hit['revision']}:"
            f"{hit['index_version']}:{space.id}"
        )
        quote = hit["text"]
    now = utcnow()
    row = Memory(
        author_account_id=actor.account.id,
        source_message_id=message_id,
        source_document_ref=document_ref,
        source_span_json={"original": "preserve"},
        raw_quote=quote,
        content=quote,
        scope="household",
        space_id=target.id,
        sensitivity="high" if case == "message_high_shared" else "normal",
        purpose="old scope",
        confirmed_by_account_id=actor.account.id,
        confirmed_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(row)
    db_session.commit()
    with pytest.raises(HTTPException) as error:
        memory_sources.verify_legacy_source(db_session, row, account=actor.account)
    assert error.value.status_code == 403
    assert row.source_verification == "unverified" and row.source_kind == "legacy"
    assert row.source_span_json == {"original": "preserve"}
    assert row.scope == "household" and row.space_id == target.id
    assert not memory_sources.memory_materializable(db_session, row)


def test_private_rag_cannot_be_saved_by_another_member_and_shared_list_rechecks_author(
    client, db_session
):
    actor, space, headers = _identity(client, db_session)
    private = _confirm(client, headers, _create(client, headers))
    private_hit = _search(client, headers, space.id)[0]
    member = create_user_with_pin(db_session, "rag-private-outsider", "123456")
    create_space_member(db_session, space.id, member.id)
    member_headers = auth_header(login(client, "rag-private-outsider", "123456").json())
    assert _search(client, member_headers, space.id) == []
    response = client.post(
        "/api/memory-candidates", headers=member_headers, json=_rag_payload(private_hit, space.id)
    )
    assert response.status_code == 403
    shared = _confirm(client, headers, _create(client, headers), scope=f"household:{space.id}")
    visible = client.get(
        "/api/memories", headers=member_headers, params={"space_id": space.id}
    ).json()
    assert [item["id"] for item in visible] == [shared["id"]]
    create_space_member(db_session, space.id, actor.id, status="removed")
    assert (
        client.get("/api/memories", headers=member_headers, params={"space_id": space.id}).json()
        == []
    )
    assert private["scope"] == "private"


@pytest.mark.parametrize("race", ["membership", "memory_flag", "rag_flag"])
def test_confirmation_rechecks_target_and_flags_after_acquiring_writer(
    client, db_session, monkeypatch, race
):
    actor, space, headers = _identity(client, db_session)
    candidate = _create(client, headers)
    real_access = memory_sources.source_access
    injected = False

    def race_after_authorization(*args, **kwargs):
        nonlocal injected
        access = real_access(*args, **kwargs)
        if kwargs.get("require_live_message") and not injected:
            injected = True
            if race == "membership":
                with SessionLocal() as other:
                    other.execute(
                        update(SpaceMember)
                        .where(SpaceMember.user_id == actor.id, SpaceMember.space_id == space.id)
                        .values(status="removed")
                    )
                    other.commit()
            else:
                monkeypatch.setattr(
                    config, "MEMORY_ENABLED" if race == "memory_flag" else "RAG_ENABLED", False
                )
        return access

    monkeypatch.setattr(memory_sources, "source_access", race_after_authorization)
    response = client.post(
        f"/api/memory-candidates/{candidate['id']}/confirm",
        headers=headers,
        json={"scope": f"household:{space.id}"},
    )
    assert injected
    if race == "rag_flag":
        assert response.status_code == 200
        assert db_session.scalar(select(func.count()).select_from(RAGDocument)) == 0
    else:
        assert response.status_code == (403 if race == "membership" else 503)
        assert db_session.scalar(select(func.count()).select_from(Memory)) == 0
        assert (
            db_session.get(MemoryCandidate, candidate["id"], populate_existing=True).status
            == "pending"
        )
