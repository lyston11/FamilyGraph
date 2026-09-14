"""Lexical context planning has bounded, same-session evidence and a real budget."""

import json

import pytest
from sqlalchemy import select
from test_rag_acceptance_contract import _confirm, _context, _enable, _start_run

from app.models.agent import AgentMessage
from app.models.context import ContextBuild
from app.models.rag import RAGChunk, RAGDocument
from app.services import context_builder, memory_rag
from app.services.rag_query import MAX_TERMS, plan_query
from conftest import create_agent_fixture, create_agent_message, create_agent_session


def _anchor_world(db, api, name, history, *, elsewhere=None):
    _enable(db)
    owner, space = create_agent_fixture(db, name=name)
    ids = [
        _confirm(db, owner, text) for text in ("外公周日上午去杭州体检。", "舅舅周六在苏州过生日。")
    ]
    session = create_agent_session(db, account_id=owner.account.id, space_id=space.id)
    for text in history:
        create_agent_message(db, session, content={"text": text})
    if elsewhere is not None:
        other = create_agent_session(db, account_id=owner.account.id, space_id=space.id)
        create_agent_message(db, other, content={"text": elsewhere})
    world = _start_run(db, api, session, "他呢？")
    return world, ids


@pytest.mark.parametrize(
    "history,index,anchor",
    [(["想查外公周日的安排。"], 0, "外公"), (["想查舅舅周六的安排。"], 1, "舅舅")],
)
def test_same_question_uses_only_unique_same_session_anchor(
    db_session, internal_client, monkeypatch, history, index, anchor
):
    world, ids = _anchor_world(db_session, internal_client, f"b-unique-{index}", history)
    seen = []
    original = context_builder.search_rag

    def capture(*args, **kwargs):
        seen.append(kwargs["query_plan"])
        return original(*args, **kwargs)

    monkeypatch.setattr(context_builder, "search_rag", capture)
    response = _context(internal_client, world)
    assert response.status_code == 200, response.text
    blocks = response.json()["context_blocks"]
    assert [block["source_id"] for block in blocks] == [str(ids[index])]
    assert seen[0].anchors == (anchor,)
    assert anchor in (*seen[0].fts_terms, *seen[0].fallback_terms)
    assert seen[0].term_count <= MAX_TERMS
    build = db_session.get(ContextBuild, response.json()["context_build_id"])
    assert build.policy_json["query_plan"]["anchor_count"] == 1
    assert anchor not in json.dumps(build.policy_json, ensure_ascii=False)


@pytest.mark.parametrize(
    "history,elsewhere,reason",
    [
        ([], None, "anchor_missing"),
        ([], "外公周日的安排。", "anchor_missing"),
        (["外公和舅舅的安排。"], None, "anchor_ambiguous"),
        (["外公的安排。", "收到", "你好", "继续", "好"], None, "anchor_missing"),
        (["填" * 500 + "外公的安排。"], None, "anchor_history_truncated"),
    ],
)
def test_ambiguous_absent_or_out_of_window_anchor_never_guessed(
    db_session, internal_client, history, elsewhere, reason
):
    world, _ids = _anchor_world(
        db_session, internal_client, "b-anchor-negative", history, elsewhere=elsewhere
    )
    response = _context(internal_client, world)
    assert response.status_code == 200, response.text
    assert response.json()["context_blocks"] == []
    build = db_session.get(ContextBuild, response.json()["context_build_id"])
    assert build.policy_json["query_plan"]["anchor_count"] == 0
    assert reason in build.policy_json["query_plan"]["degradation"]


def test_planner_requires_an_explicit_reference_and_supports_labelled_places():
    assert plan_query("上海", recent_messages=["外公去杭州"]).anchors == ()
    assert plan_query("那里有什么安排？", recent_messages=["地点：杭州"]).anchors == ("杭州",)
    ambiguous = plan_query("那里呢？", recent_messages=["地点：杭州，地点：苏州"])
    assert ambiguous.anchors == () and "anchor_ambiguous" in ambiguous.degradation
    q16 = plan_query("他在哪里体检？", recent_messages=["想查外公周日的体检安排。"])
    assert q16.anchors == ("外公",)
    assert q16.term_count <= 8
    long_history = plan_query("他呢？", recent_messages=["外公" + "填" * 500 + "舅舅"])
    assert long_history.anchors == ()
    assert "anchor_history_truncated" in long_history.degradation


@pytest.mark.parametrize("copies,expect_hit", [(37, True), (205, False)])
def test_refill_continues_across_pages_but_stops_at_shared_scan_cap(
    db_session, internal_client, copies, expect_hit
):
    _enable(db_session)
    owner, space = create_agent_fixture(db_session, name=f"b-refill-{copies}")
    root_id = _confirm(db_session, owner, "needlexyz预算 synthetic note")
    root_doc = db_session.scalar(select(RAGDocument).where(RAGDocument.source_id == str(root_id)))
    root_chunk = db_session.scalar(select(RAGChunk).where(RAGChunk.document_id == root_doc.id))
    for _ in range(copies):
        candidate = memory_rag.propose_candidate(
            db_session,
            author_account_id=owner.account.id,
            source={
                "kind": "rag_chunk",
                "document_id": root_doc.id,
                "chunk_id": root_chunk.id,
                "revision": 1,
                "index_version": root_chunk.index_version,
                "space_id": space.id,
            },
            source_quote=root_chunk.text,
            summary="needlexyz预算 synthetic note",
            suggested_scope="private",
            purpose="bounded refill regression",
        )
        memory_rag.confirm_candidate(
            db_session,
            candidate_id=candidate.id,
            confirmer=owner,
            confirmer_account=owner.account,
            scope="private",
        )
    db_session.commit()
    valid = _confirm(db_session, owner, "needlexyz预算 synthetic note")
    memory_rag.revoke_memory(db_session, memory_id=root_id, account_id=owner.account.id)
    db_session.commit()
    for query in ("needlexyz", "预算", "needlexyz 预算"):
        trace = {}
        hits = memory_rag.search_rag(
            db_session,
            actor=owner,
            account=owner.account,
            space_id=space.id,
            query=query,
            for_model=False,
            limit=1,
            trace=trace,
        )
        assert [hit.source_id for hit in hits] == ([str(valid)] if expect_hit else [])
        assert trace["scanned"] <= 200
        assert trace["denied"] >= min(copies, 200)
        assert trace["stop_reason"] == ("limit" if expect_hit else "scan_limit")


def test_context_reread_does_not_research_or_absorb_its_new_answer(
    db_session, internal_client, monkeypatch
):
    world, _ids = _anchor_world(db_session, internal_client, "b-exact-replay", ["外公的安排"])
    first = _context(internal_client, world)
    assert first.status_code == 200
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            context_builder, "search_rag", lambda *a, **k: pytest.fail("replay must not search")
        )
        # An appended reply does not change the query's preceding history.
        with_context = world["context"]
        run_id = world["run_id"]
        message = db_session.get(
            AgentMessage,
            next(
                m["id"] for m in with_context["messages"] if m["content_json"]["text"] == "他呢？"
            ),
        )
        db_session.add(
            AgentMessage(
                session_id=message.session_id,
                role="assistant",
                content_json={"text": "舅舅是另一个人"},
                created_at=message.created_at,
            )
        )
        db_session.commit()
        second = internal_client.get(
            f"/internal/agent/runs/{run_id}/context", headers=world["headers"]
        )
        assert (
            second.status_code == 200
            and second.json()["context_blocks"] == first.json()["context_blocks"]
        )
        assert second.json()["context_build_id"] == first.json()["context_build_id"]


def test_backend_uses_the_frozen_sidecar_envelope():
    from pathlib import Path

    from app.services.rag_budget import ESTIMATOR_VERSION, estimate_context, render_context_appendix

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/rag_context_envelope_v1.json").read_text()
    )
    assert render_context_appendix(fixture["blocks"]) == fixture["appendix"]
    assert estimate_context(fixture["blocks"]) == fixture["estimated_tokens"]
    assert ESTIMATOR_VERSION == fixture["estimator_version"]


@pytest.mark.parametrize("query", ["__", "a_"])
def test_short_word_fallback_treats_pattern_characters_as_literals(db_session, query):
    _enable(db_session)
    owner, space = create_agent_fixture(db_session, name=f"b-like-literal-{query}")
    _confirm(db_session, owner, "上海聚餐在周日下午三点，abc 是普通文字。")
    match_id = _confirm(db_session, owner, f"合成字面标识 {query}。")

    hits = memory_rag.search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query=query,
    )

    assert [hit.source_id for hit in hits] == [str(match_id)]
    assert all(query in hit.text for hit in hits)
