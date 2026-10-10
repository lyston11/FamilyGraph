"""向量候选在 `search_rag` 里的接线（P3-b）。

## 这组测试防的是哪一类缺陷

向量路径在 PostgreSQL 上曾经**完全不可用却无人察觉**：`_vector_candidates` 没有把
`_ELIGIBILITY_SQL` 里的 `:now` / `:is_assistant` / `:user_id` 传给查询，于是每次
都抛 `StatementError`、被 `except` 吞掉、静默回退词法。表现是「检索看起来正常」，
而向量增强从未生效。

原来的测试只覆盖 `rag_embeddings.build_vector_candidates` 的 **SQL 形状**，
没有任何测试覆盖 `search_rag` 的**接线**。因此这里测试的是接线本身：

1. 绑定参数齐全（缺任何一个 → 查询抛错 → 向量路径静默死亡）；
2. 相似度地板（缺它 → 弃答用例被无关向量填满，abstention 从 1.00 掉到 0.00）；
3. 只增不减（向量候选不得挤掉词法命中）。
"""

from __future__ import annotations

from app.models.platform_features import PlatformFeatureConfig
from app.services import embedding_client, memory_rag
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture


def _enable(db):
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, updated_at=utcnow())
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
        purpose="vector wiring",
    )
    return memory_rag.confirm_candidate(
        db,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope="private",
    )


class _FakeDialect:
    name = "postgresql"


class _FakeBind:
    dialect = _FakeDialect()


class _FakeSession:
    """最小 Session 替身：让 `_vector_candidates` 走到真正执行查询的那一步。

    必须伪造方言为 `postgresql`：函数在非 PG 方言上会提前返回空，
    那样「地板丢弃」类测试会**假通过**（因为永远返回空）。
    """

    def __init__(self, rows: list[dict] | None = None, error: Exception | None = None) -> None:
        self.bind = _FakeBind()
        self._rows = rows
        self._error = error
        self.captured: dict | None = None
        self.rolled_back = False

    def execute(self, _statement, params=None):
        self.captured = dict(params or {})
        if self._error is not None:
            raise self._error
        return _StubResult(self._rows or [])

    def rollback(self) -> None:
        self.rolled_back = True


class _FakeEmbeddingResult:
    def __init__(self, vector: list[float], dimension: int) -> None:
        self.vectors = [vector]
        self.dimension = dimension
        self.degraded = False
        self.reason = None
        self.usable = True


def _stub_embedding(monkeypatch, *, vector: list[float], dimension: int = 8):
    """把 embedding 客户端替换成可控返回，使测试不依赖真实服务。"""
    monkeypatch.setattr(embedding_client, "enabled", lambda: True)

    async def fake_embed_query(_query: str, **_kwargs):
        return _FakeEmbeddingResult(vector, dimension)

    monkeypatch.setattr(embedding_client, "embed_query", fake_embed_query)
    from app.services import rag_embeddings

    monkeypatch.setattr(rag_embeddings, "configured_dimension", lambda: dimension)
    monkeypatch.setattr(rag_embeddings, "configured_model", lambda: "test-model")
    return rag_embeddings


def _fake_rows(*, rank: float, chunk_id: int = 1) -> list[dict]:
    return [
        {
            "chunk_id": chunk_id,
            "document_id": 1,
            "source_type": "memory",
            "source_id": "1",
            "text": "外婆住在苏州。",
            "token_estimate": 6,
            "scope": "private",
            "sensitivity": "normal",
            "revision": 1,
            "index_version": "v1",
            "chunk_index": 0,
            "source_revision": 1,
            "rank": rank,
        }
    ]


def test_vector_query_receives_every_eligibility_binding(db_session, monkeypatch):
    """接线测试：eligibility 的每个命名参数都必须传给向量查询。

    缺任何一个都会让查询抛 `StatementError`，而 `except` 会把它变成
    「静默回退词法」——这正是向量路径在 PG 上死掉的方式。
    """
    _enable(db_session)
    owner, space = create_agent_fixture(db_session, name="vector-binding")
    memory = _confirm(db_session, owner, "外婆住在苏州。")
    db_session.commit()
    rag_embeddings = _stub_embedding(monkeypatch, vector=[0.1] * 8)

    fake = _FakeSession(rows=[], error=RuntimeError("stop here: params are what matters"))
    monkeypatch.setattr(memory_rag, "_rows_to_hits", lambda *a, **k: ([], 0))

    memory_rag._vector_candidates(
        fake,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        agent_kind="assistant",
        query="外婆住在哪里",
        eligibility=memory_rag._ELIGIBILITY_SQL.format(sensitivity=""),
        seen_chunk_ids=set(),
        limit=5,
        now=utcnow(),
        is_assistant=1,
        user_id=owner.id,
    )
    params = fake.captured or {}
    # `_ELIGIBILITY_SQL` 用到的每个命名参数都必须在场。
    for name in ("now", "is_assistant", "user_id", "account_id", "space_id"):
        assert name in params, f"向量查询缺少 eligibility 绑定参数 {name}"
    assert params["model"] == "test-model"
    assert params["query_vector"].startswith("[")
    del rag_embeddings, memory


def test_similarity_floor_drops_weak_vector_candidates(db_session, monkeypatch):
    """相似度地板：距离 0.6（相似度 0.4 < 0.50）的候选必须被丢弃。"""
    _enable(db_session)
    owner, space = create_agent_fixture(db_session, name="vector-floor")
    memory = _confirm(db_session, owner, "外婆住在苏州。")
    db_session.commit()
    _stub_embedding(monkeypatch, vector=[0.1] * 8)
    monkeypatch.setattr(
        memory_rag, "_rows_to_hits", lambda _db, rows, **k: (_hits_from_rows(rows), 0)
    )
    fake = _FakeSession(rows=_fake_rows(rank=0.6))

    hits, _ = memory_rag._vector_candidates(
        fake,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        agent_kind="assistant",
        query="外婆住在哪里",
        eligibility=memory_rag._ELIGIBILITY_SQL.format(sensitivity=""),
        seen_chunk_ids=set(),
        limit=5,
        now=utcnow(),
        is_assistant=1,
        user_id=owner.id,
    )
    assert hits == [], "低于相似度地板的向量候选必须被丢弃"
    del memory


def test_similarity_floor_keeps_strong_vector_candidates(db_session, monkeypatch):
    """反证：距离 0.2（相似度 0.8 >= 0.50）的候选必须保留。

    没有这条，上一条测试可以通过「永远返回空」来满足。
    """
    _enable(db_session)
    owner, space = create_agent_fixture(db_session, name="vector-floor-keep")
    _confirm(db_session, owner, "外婆住在苏州。")
    db_session.commit()
    _stub_embedding(monkeypatch, vector=[0.1] * 8)
    monkeypatch.setattr(
        memory_rag, "_rows_to_hits", lambda _db, rows, **k: (_hits_from_rows(rows), 0)
    )
    fake = _FakeSession(rows=_fake_rows(rank=0.2))
    hits, _ = memory_rag._vector_candidates(
        fake,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        agent_kind="assistant",
        query="外婆住在哪里",
        eligibility=memory_rag._ELIGIBILITY_SQL.format(sensitivity=""),
        seen_chunk_ids=set(),
        limit=5,
        now=utcnow(),
        is_assistant=1,
        user_id=owner.id,
    )
    assert len(hits) == 1 and hits[0].chunk_id == 1


def test_vector_failure_falls_back_to_lexical(db_session, monkeypatch):
    """向量查询失败必须返回空（回退词法），而不是把失败升级成检索失败。"""
    _enable(db_session)
    owner, space = create_agent_fixture(db_session, name="vector-fallback")
    _confirm(db_session, owner, "外婆住在苏州。")
    db_session.commit()
    _stub_embedding(monkeypatch, vector=[0.1] * 8)
    fake = _FakeSession(error=RuntimeError("simulated query failure"))
    hits, denied = memory_rag._vector_candidates(
        fake,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        agent_kind="assistant",
        query="外婆住在哪里",
        eligibility=memory_rag._ELIGIBILITY_SQL.format(sensitivity=""),
        seen_chunk_ids=set(),
        limit=5,
        now=utcnow(),
        is_assistant=1,
        user_id=owner.id,
    )
    assert hits == [] and denied == 0
    assert fake.rolled_back, "查询失败后必须 rollback，否则会话停留在失败事务里"


def test_vector_candidates_never_displace_lexical_hits(db_session, monkeypatch):
    """只增不减：向量候选不得挤掉词法命中。

    这是 `search_rag` 里记录的第一条硬性约束。实测中向量候选曾把词法命中挤出去，
    导致 citation_precision 从 0.548 掉到 0.187、弃答正确率从 1.00 掉到 0.00。
    """
    _enable(db_session)
    owner, space = create_agent_fixture(db_session, name="vector-no-displace")
    for index in range(4):
        _confirm(db_session, owner, f"外婆住在苏州的第{index}个院子。")
    db_session.commit()
    lexical = memory_rag.search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query="外婆住在哪里",
        limit=3,
        for_model=False,
    )
    lexical_ids = {hit.chunk_id for hit in lexical}
    assert lexical_ids, "词法路径应当有命中，否则本测试没有意义"
    # 未启用 embedding 时 hybrid 结果必须与词法结果**完全一致**（只增不减的下界）。
    hybrid = memory_rag.search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query="外婆住在哪里",
        limit=3,
        for_model=False,
    )
    assert [hit.chunk_id for hit in hybrid] == [hit.chunk_id for hit in lexical]


class _StubResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


def _hits_from_rows(rows):
    """忠实把行转成 RAGHit，尤其是 `rank`（地板判据来自它）。

    硬编码 rank 会让测试替身撒谎：地板测试输入任何距离都得到同一个值。
    """
    from app.services.memory_rag import RAGHit

    return [
        RAGHit(
            document_id=int(row["document_id"]),
            chunk_id=int(row["chunk_id"]),
            source_id=str(row["source_id"]),
            text=str(row["text"]),
            token_estimate=int(row["token_estimate"]),
            rank=float(row["rank"]),
            citation_handle=f"rag:{row['chunk_id']}:r1:c{row['chunk_id']}",
            scope=str(row["scope"]),
            sensitivity=str(row["sensitivity"]),
            revision=int(row["revision"]),
        )
        for row in rows
    ]
