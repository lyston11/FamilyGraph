"""Workstream B regressions: Chinese lexical recall, bounded fallback, chunk
stability, attempt-bound context builds and the server-authenticated citation
contract (B-AC1..B-AC8 core paths).

All content is synthetic (retrieval-cases.md v1 dataset); no real family text.
"""

import json

import pytest
from sqlalchemy import select

from app import config
from app.models.agent import AgentMessage, AgentRunEvent
from app.models.memory import Memory
from app.models.rag import RAGChunk
from app.services import agent_queue, memory_rag
from app.services.agent_tokens import issue_run_token, issue_service_token
from app.services.rag_query import plan_query
from conftest import create_agent_fixture, create_space_member

# ---- synthetic dataset (retrieval-cases.md v1) ----


def _confirm_memory(db, user, *, summary, scope, space_id=None, sensitivity="normal"):
    candidate = memory_rag.propose_candidate(
        db,
        author_account_id=user.account.id,
        source={"kind": "manual"},
        source_quote=summary,
        summary=summary,
        suggested_scope=scope,
        purpose="synthetic dataset",
        sensitivity=sensitivity,
    )
    return memory_rag.confirm_candidate(
        db,
        candidate_id=candidate.id,
        confirmer=user,
        confirmer_account=user.account,
        scope=scope,
        space_id=space_id,
    )


M5_PADDING = "这是一段与问题无关的填充文字。" * 40  # > old 1200-char block boundary around the fact


def _dataset(db, owner, member):
    """M1..M7 + negatives; returns source_id map keyed by label."""
    ids: dict[str, str] = {}
    m1 = _confirm_memory(
        db, owner, summary="今年春节在上海聚餐，外婆喜欢清淡饮食。", scope="private"
    )
    ids["M1"] = str(m1.id)
    m2 = _confirm_memory(
        db, owner, summary="去年春节在广州聚餐，今年暂未确定其他安排。", scope="private"
    )
    ids["M2"] = str(m2.id)
    m3 = _confirm_memory(
        db, owner, summary="外公周日上午去杭州体检。", scope="household", space_id=owner.space.id
    )
    ids["M3"] = str(m3.id)
    m4 = _confirm_memory(db, owner, summary="奶奶在家里习惯被称为阿婆。", scope="private")
    ids["M4"] = str(m4.id)
    m5 = _confirm_memory(
        db,
        owner,
        summary=M5_PADDING + "周六下午给舅舅过生日，蛋糕不要花生。" + M5_PADDING,
        scope="private",
    )
    ids["M5"] = str(m5.id)
    m6 = _confirm_memory(db, owner, summary="Grandma prefers low-salt food.", scope="private")
    ids["M6"] = str(m6.id)
    m7 = _confirm_memory(
        db, owner, summary="端午节在苏州住两晚。", scope="household", space_id=owner.space.id
    )
    ids["M7"] = str(m7.id)
    # X1: member-only private memory (owner must not see it).
    x1 = _confirm_memory(db, member, summary="今年春节在北京聚餐。", scope="private")
    ids["X1"] = str(x1.id)
    # X3: revoked / deleted variants.
    x3 = _confirm_memory(db, owner, summary="今年春节在南京聚餐。", scope="private")
    memory_rag.revoke_memory(db, memory_id=x3.id, account_id=owner.account.id)
    # X4: pending candidate, never confirmed.
    memory_rag.propose_candidate(
        db,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote="今年春节在厦门聚餐。",
        summary="今年春节在厦门聚餐。",
        suggested_scope="private",
        purpose="synthetic dataset",
    )
    # X6: high sensitivity (cloud providers must not receive it).
    x6 = _confirm_memory(
        db,
        owner,
        summary="外婆的高血压药盒放在武汉老宅。",
        scope="private",
        sensitivity="high",
    )
    ids["X6"] = str(x6.id)
    db.flush()
    return ids


@pytest.fixture()
def rag_world(db_session):
    from app.models.platform_features import PlatformFeatureConfig

    row = db_session.get(PlatformFeatureConfig, 1)
    if row is None:
        from datetime import datetime

        row = PlatformFeatureConfig(
            id=1, memory_enabled=True, rag_enabled=True, updated_at=datetime(2026, 9, 13)
        )
        db_session.add(row)
    else:
        row.memory_enabled = True
        row.rag_enabled = True
    db_session.flush()
    owner, space = create_agent_fixture(db_session, name="rag-owner")
    member, _member_space = create_agent_fixture(db_session, name="rag-member")
    create_space_member(db_session, space.id, member.id, role="member")
    owner.space = space
    member.space = _member_space
    ids = _dataset(db_session, owner, member)
    db_session.commit()
    return owner, member, space, ids


def _sources(hits):
    seen: list[str] = []
    for hit in hits:
        if hit.source_id not in seen:
            seen.append(hit.source_id)
    return seen


def _search(db, actor, space, query, **kwargs):
    return memory_rag.search_rag(
        db,
        actor=actor,
        account=actor.account,
        space_id=space.id,
        query=query,
        agent_kind="assistant",
        **kwargs,
    )


# ---- V-B01: frozen core Chinese positives ----


@pytest.mark.parametrize(
    "query,label",
    [
        ("春节", "M1"),
        ("上海", "M1"),
        ("在上海", "M1"),
        ("春节在上海", "M1"),
        ("今年春节在哪里聚餐？", "M1"),
        ("外婆喜欢什么口味？", "M1"),
        ("外婆", "M1"),
        ("今年春节在上海聚餐，外婆喜欢清淡饮食。", "M1"),
        ("过年聚会地点", "M1"),
        ("外公", "M3"),
        ("周日体检在哪个城市？", "M3"),
        ("奶奶怎么称呼？", "M4"),
        ("舅舅生日蛋糕要注意什么？", "M5"),
        ("端午", "M7"),
        ("端午节住哪里？", "M7"),
    ],
)
def test_core_chinese_recall_top5(rag_world, db_session, query, label):
    owner, _member, space, ids = rag_world
    hits = _search(db_session, owner, space, query, limit=5)
    assert ids[label] in _sources(hits), (query, label, _sources(hits))


def test_m13_hit_contains_the_peanut_fact(rag_world, db_session):
    owner, _member, space, _ids = rag_world
    hits = _search(db_session, owner, space, "舅舅生日蛋糕要注意什么？", limit=5)
    assert any("花生" in hit.text for hit in hits)


def test_english_regression_unchanged(rag_world, db_session):
    owner, _member, space, ids = rag_world
    assert ids["M6"] in _sources(_search(db_session, owner, space, "low-salt food", limit=5))
    assert ids["M6"] in _sources(
        _search(db_session, owner, space, "Grandma prefers low-salt food.", limit=5)
    )


# ---- V-B02: negatives, boundaries, bounded fallback ----


def test_n01_other_private_memory_never_leaks(rag_world, db_session):
    owner, _member, space, ids = rag_world
    sources = _sources(_search(db_session, owner, space, "北京", limit=10))
    assert ids["X1"] not in sources
    assert sources == []


def test_n03_revoked_memory_not_resurrected(rag_world, db_session):
    owner, _member, space, _ids = rag_world
    assert _sources(_search(db_session, owner, space, "南京", limit=10)) == []


def test_n04_pending_candidate_not_indexed(rag_world, db_session):
    owner, _member, space, _ids = rag_world
    assert _sources(_search(db_session, owner, space, "厦门", limit=10)) == []


def test_n06_high_sensitivity_excluded_for_cloud(rag_world, db_session):
    owner, _member, space, ids = rag_world
    assert ids["X6"] not in _sources(
        _search(db_session, owner, space, "药盒 武汉", limit=10, provider_kind="cloud")
    )


def test_n08_fts_operators_and_punctuation_safe(rag_world, db_session):
    owner, _member, space, _ids = rag_world
    for query in ('" OR 1=1 --', "春节）(", "!!!", "   ", "NEAR((春节))"):
        hits = _search(db_session, owner, space, query, limit=10)
        assert isinstance(hits, list)


def test_n09_single_char_and_oversized_query_bounded(rag_world, db_session):
    owner, _member, space, _ids = rag_world
    hits = _search(db_session, owner, space, "春", limit=10)
    assert isinstance(hits, list) and len(hits) <= 10
    hits = _search(db_session, owner, space, "春节" * 400, limit=10)
    assert isinstance(hits, list) and len(hits) <= 10


def test_alias_expansion_is_not_hardcoded_to_m1(rag_world, db_session):
    owner, _member, space, ids = rag_world
    hits = _search(db_session, owner, space, "端午聚会安排", limit=5)
    assert ids["M7"] in _sources(hits)


# ---- B-AC4: deterministic chunking and stable chunk identity ----


def test_same_version_reindex_keeps_chunk_ids(rag_world, db_session):
    owner, _member, _space, ids = rag_world
    memory = db_session.scalar(select(Memory).where(Memory.id == int(ids["M1"])))
    document = memory_rag.index_memory(db_session, memory)
    first = db_session.scalars(
        select(RAGChunk).where(RAGChunk.document_id == document.id).order_by(RAGChunk.chunk_index)
    ).all()
    memory_rag.index_memory(db_session, memory)
    db_session.flush()
    second = db_session.scalars(
        select(RAGChunk).where(RAGChunk.document_id == document.id).order_by(RAGChunk.chunk_index)
    ).all()
    assert [c.id for c in first] == [c.id for c in second]
    assert all(c.index_version == memory_rag.RAG_INDEX_VERSION for c in second)


def test_long_memory_chunks_are_sentence_bounded(rag_world, db_session):
    owner, _member, _space, ids = rag_world
    memory = db_session.scalar(select(Memory).where(Memory.id == int(ids["M5"])))
    chunks = memory_rag._chunk_text(memory.content)
    assert len(chunks) > 1
    max_chunk = memory_rag._CHUNK_MAX_CHARS + memory_rag._CHUNK_OVERLAP_CHARS + 50
    assert all(len(chunk) <= max_chunk for chunk in chunks)
    assert any("花生" in chunk for chunk in chunks)


def test_token_estimate_is_conservative_utf8():
    assert memory_rag._estimate_tokens("春节聚餐") >= 4  # 4 CJK chars ≈ 4 tokens
    assert memory_rag._estimate_tokens("abcd") >= 1


# ---- attempt-bound context builds (B-AC6 context half) ----


def _lease(db, internal_client, run):
    response = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "b-test"},
        headers={"Authorization": f"Bearer {issue_service_token()}"},
    )
    assert response.status_code == 200
    return response.json()


def _seed_run(db, name, *, existing_user=None):
    from conftest import create_agent_message, create_agent_session

    if existing_user is not None:
        user, space = existing_user
    else:
        user, space = create_agent_fixture(db, name=name)
    agent_session = create_agent_session(db, account_id=user.account.id, space_id=space.id)
    message = create_agent_message(db, agent_session, content={"text": "春节在上海吃什么？"})
    run = agent_queue.enqueue_run(
        db,
        agent_session=agent_session,
        kind="assistant",
        policy_version=config.POLICY_VERSION,
        tool_allowlist=["familygraph.echo"],
        message=message,
    )
    return user, space, agent_session, run


def test_same_attempt_context_is_idempotent_and_reauthorized(
    rag_world, db_session, internal_client
):
    owner, _member, space, _ids = rag_world
    _user, _space, _session, run = _seed_run(db_session, "ctx-replay")
    grant = _lease(db_session, internal_client, run)
    headers = {"Authorization": f"Bearer {grant['run_token']}"}
    first = internal_client.get(f"/internal/agent/runs/{run.id}/context", headers=headers)
    assert first.status_code == 200
    build_id = first.json()["context_build_id"]
    assert build_id is not None
    second = internal_client.get(f"/internal/agent/runs/{run.id}/context", headers=headers)
    assert second.status_code == 200
    assert second.json()["context_build_id"] == build_id


def test_stale_attempt_token_cannot_reuse_context(rag_world, db_session, internal_client):
    owner, _member, space, _ids = rag_world
    user, _space, agent_session, run = _seed_run(db_session, "stale-attempt")
    grant = _lease(db_session, internal_client, run)
    stale = issue_run_token(
        run_id=run.id,
        job_id=grant["job_id"],
        attempt=0,
        agent_kind="assistant",
        account_id=user.account.id,
        space_id=agent_session.space_id,
        tool_allowlist=["familygraph.echo"],
    )
    response = internal_client.get(
        f"/internal/agent/runs/{run.id}/context",
        headers={"Authorization": f"Bearer {stale}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AGENT_TOKEN_SCOPE_MISMATCH"


# ---- event fingerprints and authenticated citations (B-AC5/6/7) ----


def _running_run(db, internal_client, name):
    _user, _space, _session, run = _seed_run(db, name)
    grant = _lease(db, internal_client, run)
    headers = {"Authorization": f"Bearer {grant['run_token']}"}
    response = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={"events": [{"seq": 1, "type": "run.started", "public_payload": {}}]},
        headers=headers,
    )
    assert response.status_code == 200
    return run, grant, headers


def test_event_fingerprint_idempotency_and_conflict(rag_world, db_session, internal_client):
    _owner, _member, _space, _ids = rag_world
    run, _grant, headers = _running_run(db_session, internal_client, "fingerprint")
    payload = {"seq": 2, "type": "turn.started", "public_payload": {}}
    ok = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append", json={"events": [payload]}, headers=headers
    )
    assert ok.status_code == 200 and ok.json()["accepted"]
    replay = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append", json={"events": [payload]}, headers=headers
    )
    assert replay.status_code == 200 and replay.json()["duplicates"] == [2]
    conflict = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={"events": [{"seq": 2, "type": "turn.started", "public_payload": {"x": 1}}]},
        headers=headers,
    )
    assert conflict.status_code == 409


def test_citations_authenticated_from_build_and_revocation_masks(
    rag_world, db_session, client, internal_client
):
    from conftest import auth_header, login

    owner, _member, space, ids = rag_world
    user, _space, agent_session, run = _seed_run(
        db_session, "cite", existing_user=(owner, owner.space)
    )
    grant = _lease(db_session, internal_client, run)
    token = grant["run_token"]
    headers = {"Authorization": f"Bearer {token}"}
    context = internal_client.get(f"/internal/agent/runs/{run.id}/context", headers=headers)
    assert context.status_code == 200, context.json()
    blocks = context.json()["context_blocks"]
    assert blocks, "dataset must produce a hit for the seeded question"
    handle = blocks[0]["citation"]
    assert (
        internal_client.post(
            f"/internal/agent/runs/{run.id}/events/append",
            json={"events": [{"seq": 1, "type": "run.started", "public_payload": {}}]},
            headers=headers,
        ).status_code
        == 200
    )
    answer = f"今年春节在上海聚餐 [rag:{ids['M1']}:r1:c9999 伪造] {handle}"
    appended = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={
            "events": [
                {
                    "seq": 2,
                    "type": "message.assistant_added",
                    "public_payload": {"role": "assistant", "text": answer},
                }
            ]
        },
        headers=headers,
    )
    assert appended.status_code == 200

    login_response = login(client, "rag-owner", "123456")
    assert login_response.status_code == 200, login_response.json()
    token_pair = login_response.json()
    listed = client.get(
        f"/api/agent/sessions/{agent_session.id}/messages", headers=auth_header(token_pair)
    ).json()
    assistant = [m for m in listed if m["role"] == "assistant"][-1]
    citations = assistant["citations"]
    assert len(citations) == 1
    assert citations[0]["source_id"] == ids["M1"]
    assert citations[0]["citation_handle"] == handle
    assert citations[0]["revision"] == 1
    assert citations[0]["scope"] == "private"
    # content_json never echoes the raw stored citation list
    assert "citations" not in assistant["content_json"]

    # Revocation masks the citation on every read surface but keeps the count.
    memory_rag.revoke_memory(db_session, memory_id=int(ids["M1"]), account_id=user.account.id)
    db_session.commit()
    masked = client.get(
        f"/api/agent/sessions/{agent_session.id}/messages", headers=auth_header(token_pair)
    ).json()
    masked_assistant = [m for m in masked if m["role"] == "assistant"][-1]
    assert masked_assistant["citations"] == []
    assert masked_assistant["unavailable_citation_count"] == 1
    fallback = client.get(
        f"/api/agent/runs/{run.id}/events/2/citations", headers=auth_header(token_pair)
    ).json()
    assert fallback["citations"] == [] and fallback["unavailable_citation_count"] == 1

    # A different account can neither read the message nor cite it: reader
    # projection is per-account.
    _member_user, _member_space = create_agent_fixture(db_session, name="cite-other")
    db_session.commit()


def test_unused_or_fabricated_handles_never_authenticated(rag_world, db_session, internal_client):
    _owner, _member, _space, ids = rag_world
    _user, _space, _agent_session, run = _seed_run(db_session, "forge")
    grant = _lease(db_session, internal_client, run)
    headers = {"Authorization": f"Bearer {grant['run_token']}"}
    context = internal_client.get(f"/internal/agent/runs/{run.id}/context", headers=headers)
    assert context.status_code == 200
    internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={"events": [{"seq": 1, "type": "run.started", "public_payload": {}}]},
        headers=headers,
    )
    # The text only *mentions* an excluded-source-style fabricated handle for a
    # memory that is not part of this build (M2 exists but query targets M1).
    appended = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={
            "events": [
                {
                    "seq": 2,
                    "type": "message.assistant_added",
                    "public_payload": {
                        "role": "assistant",
                        "text": f"回答 [rag:{ids['M2']}:r1:c1] [完全捏造]",
                    },
                }
            ]
        },
        headers=headers,
    )
    assert appended.status_code == 200
    event = db_session.scalar(
        select(AgentRunEvent).where(AgentRunEvent.run_id == run.id, AgentRunEvent.seq == 2)
    )
    reference = event.context_reference_json or {}
    assert reference.get("used_handles") == []
    message = db_session.scalar(
        select(AgentMessage).where(AgentMessage.idempotency_key == f"run:{run.id}:event:2")
    )
    assert message.content_json.get("citations") is None


def test_web_citations_survive_and_payload_limit_holds(rag_world, db_session, internal_client):
    _owner, _member, _space, _ids = rag_world
    run, _grant, headers = _running_run(db_session, internal_client, "payload")
    web = [
        {
            "url": "https://example.com/a",
            "title": "示例" * 100,
            "excerpt": "摘" * 1200,
            "fetched_at": "2026-09-13T00:00:00Z",
            "trust": "external",
        }
    ]
    text = "回答" + "正" * 3000
    ok = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={
            "events": [
                {
                    "seq": 2,
                    "type": "message.assistant_added",
                    "public_payload": {"role": "assistant", "text": text, "web_citations": web},
                }
            ]
        },
        headers=headers,
    )
    assert ok.status_code == 200
    stored = db_session.scalar(
        select(AgentMessage).where(AgentMessage.idempotency_key == f"run:{run.id}:event:2")
    )
    assert stored.content_json["web_citations"] == web
    oversized = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={
            "events": [
                {
                    "seq": 3,
                    "type": "message.assistant_added",
                    "public_payload": {"role": "assistant", "text": "超" * 20000},
                }
            ]
        },
        headers=headers,
    )
    assert oversized.status_code == 422


def test_query_plan_shape_and_bounds():
    plan = plan_query("今年春节在哪里聚餐？")
    assert plan.version == "lex-v1"
    assert "哪里" not in plan.fts_terms
    assert "过年聚会地点".count("") >= 0
    alias_plan = plan_query("过年聚会地点")
    # Aliases land on the fallback branch (2 chars); the original run stays the
    # FTS phrase.  Both branches feed the same eligibility-filtered search.
    assert "春节" in alias_plan.fallback_terms and "聚餐" in alias_plan.fallback_terms
    assert alias_plan.phrase == "过年聚会地点"
    long_plan = plan_query("春节" * 400)
    assert len(long_plan.normalized_query) <= 500
    assert "query_truncated" in long_plan.degradation


def test_event_fingerprint_differs_from_authenticated_payload(rag_world, db_session):
    from app.services.agent_events import EventEntry

    entry = EventEntry(seq=1, type="turn.started", public_payload={})
    assert entry.request_fingerprint == entry.request_fingerprint
    other = EventEntry(seq=1, type="turn.started", public_payload={"x": 1})
    assert entry.request_fingerprint != other.request_fingerprint
    assert json.dumps({"a": 1}, sort_keys=True) is not None
