"""Compose real D version switching with the real B context/citation routes."""

from test_rag_acceptance_contract import (
    _answer, _context, _fallback, _public_headers, _world,
)

from app.models.rag import RAGChunk, RAGDocument
from app.services import memory_rag, rag_maintenance


def test_real_context_and_public_citation_survive_stage_repair_and_next_batch(
    db_session, internal_client, client,
):
    world = _world(
        db_session, internal_client, "D-B-composed-review",
        summaries=["上海聚餐在周日下午三点。" + "完整原始材料。" * 200],
    )
    context_response = _context(internal_client, world)
    assert context_response.status_code == 200, context_response.text
    original = context_response.json()
    assert original["context_blocks"]
    handle = original["context_blocks"][0]["citation"]
    chunk = db_session.get(RAGChunk, int(handle.rsplit(":c", 1)[1]))
    document = db_session.get(RAGDocument, chunk.document_id)
    old_identity = (chunk.id, chunk.text, chunk.index_version, chunk.source_revision)
    assert old_identity[2] == "fts5-trigram-v2"
    switched = rag_maintenance.stage_index_version(
        db_session, target_version="fts5-trigram-v1", worker_id="independent-review"
    )
    assert switched["flipped"] == 1
    db_session.commit()
    assert memory_rag.repair_fts(db_session) > 0
    db_session.commit()
    rag_maintenance.run_maintenance_batch(db_session, worker_id="independent-review")
    db_session.commit()
    assert db_session.get(RAGDocument, document.id, populate_existing=True).index_version == (
        "fts5-trigram-v1"
    )
    hits = memory_rag.search_rag(
        db_session, actor=world["owner"], account=world["owner"].account,
        space_id=world["space"].id, query="上海", for_model=False,
    )
    assert hits and all(hit.index_version == "fts5-trigram-v1" for hit in hits)
    repeated = _context(internal_client, world)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["context_build_id"] == original["context_build_id"]
    assert repeated.json()["context_blocks"] == original["context_blocks"]
    accepted = _answer(
        internal_client, world, {"role": "assistant", "text": f"保留原始引用 [{handle}]"}
    )
    assert accepted.status_code == 200, accepted.text
    result = _fallback(client, world, _public_headers(client, world))
    assert result.status_code == 200, result.text
    citations = result.json()["citations"]
    assert len(citations) == 1
    assert citations[0]["index_version"] == "fts5-trigram-v2"
    assert citations[0]["citation_handle"] == handle
    assert "_source_ref" not in citations[0]
    old = db_session.get(RAGChunk, old_identity[0], populate_existing=True)
    assert (old.id, old.text, old.index_version, old.source_revision) == old_identity
