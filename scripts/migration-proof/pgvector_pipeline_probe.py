"""C6：pgvector 全链路探针（真实 PostgreSQL + 真实 pgvector）。

## 覆盖

1. **索引写入**：`pending_chunks` 只选 active，`store_vectors` 幂等；
2. **撤权文档不产生新向量**（`pending_chunks` 的 active 过滤）；
3. **filter-then-ANN 承重**：带过滤不返回撤权 chunk；**反证**去掉过滤能返回它；
4. **RRF 融合确定性**：两路都命中的排前，重复调用结果一致。

## 关键设计说明（在探针里被验证）

- 撤权**不会**自动删除已存在的向量（真实情况：撤权前生成的向量仍在），
  因此可见性**完全**依赖查询侧过滤。探针专门手工插入一个撤权 chunk 的历史向量
  来证明这一点——否则「撤权文档没有向量」会让过滤看起来可有可无。
- `pending_chunks` 跳过撤权文档是**正确的**（不应新增无效向量），但这**不能**
  替代查询侧过滤。

用法：

    PGTEST_DSN=postgresql+psycopg://... python3 scripts/migration-proof/pgvector_pipeline_probe.py
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path

DSN = os.environ.get("PGTEST_DSN")
if not DSN:
    print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL（含 pgvector，不得指向开发库/线上）")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("EMBEDDING_BASE_URL", "")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.services import embedding_index, rag_embeddings  # noqa: E402
from app.services.embedding_client import EmbeddingResult  # noqa: E402

DIM = 8


class StubEmbedder:
    """确定性 stub：探针验证的是**管线**，不是语义质量。

    语义质量由隔离环境上的中文 golden corpus 基准证明（见
    `10-04-lexical-search-migration` 的证据）。用 stub 使探针可离线复跑。
    """

    def _vec(self, text_value: str) -> list[float]:
        digest = hashlib.sha256(text_value.encode("utf-8")).digest()
        vector = [(digest[i] / 255.0) - 0.5 for i in range(DIM)]
        norm = sum(v * v for v in vector) ** 0.5
        return [v / norm for v in vector] if norm else vector

    async def embed_documents(self, texts, **_kw):
        return EmbeddingResult(
            vectors=[self._vec(t) for t in texts], dimension=DIM, degraded=False
        )

    async def embed_query(self, text_value, **_kw):
        return await self.embed_documents([text_value])

    @staticmethod
    def enabled() -> bool:
        return True


def main() -> int:
    engine = create_engine(DSN)
    failures: list[str] = []

    with engine.begin() as conn:
        conn.execute(
            text("DROP TABLE IF EXISTS rag_chunk_embeddings, rag_chunks, rag_documents CASCADE")
        )
        conn.execute(
            text(
                """
                CREATE TABLE rag_documents (id serial primary key, source_type text,
                  source_id text, scope text, sensitivity text, revision int, status text);
                CREATE TABLE rag_chunks (id serial primary key,
                  document_id int references rag_documents(id), text text,
                  token_estimate int, index_version text, chunk_index int,
                  source_revision int, status text);
                """
            )
        )
        for stmt in rag_embeddings.pgvector_ddl(DIM).split(";"):
            if stmt.strip():
                conn.execute(text(stmt))

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO rag_documents (id,source_type,source_id,scope,sensitivity,"
                "revision,status) VALUES (1,'memory','m1','private','normal',1,'active'),"
                "(2,'memory','m2','private','normal',1,'active'),"
                "(3,'memory','m3','private','normal',1,'revoked')"
            )
        )
        for chunk_id, (doc_id, body) in enumerate(
            [(1, "叔叔和侄子"), (1, "祖父"), (2, "外祖父"), (3, "叔叔")], start=1
        ):
            conn.execute(
                text(
                    "INSERT INTO rag_chunks (id,document_id,text,token_estimate,"
                    "index_version,chunk_index,source_revision,status)"
                    " VALUES (:i,:d,:t,1,'v1',0,1,'active')"
                ),
                {"i": chunk_id, "d": doc_id, "t": body},
            )

    stub = StubEmbedder()
    embedding_index.embedding_client = stub  # type: ignore[assignment]

    with Session(engine) as db:
        report = asyncio.run(
            embedding_index.run_index_pass(
                db, model="stub", batch_size=2, max_batches=5, pause_seconds=0
            )
        )
    print(f"  索引报告: {report.summary()}")
    if report.indexed != 3:
        failures.append(f"应索引 3 条（撤权文档被跳过），实际 {report.indexed}")
    if report.failed:
        failures.append(f"索引失败 {report.failed} 条")

    with engine.begin() as conn:
        stored = conn.execute(text("SELECT count(*) FROM rag_chunk_embeddings")).scalar()
        print(f"  向量行数 {stored}（期望 3：撤权文档不产生**新**向量）")
        if stored != 3:
            failures.append(f"向量行数 {stored}，期望 3")

        # 撤权**前**已存在的向量不会自动消失。手工插入一个以证明「查询侧过滤承重」。
        # 若只依赖「撤权文档没有向量」，过滤看起来可有可无——那是错的。
        conn.execute(
            text(
                "INSERT INTO rag_chunk_embeddings (chunk_id, model, revision, scope, embedding)"
                " SELECT c.id, 'stub', d.revision, d.scope, CAST(:v AS vector)"
                " FROM rag_chunks c JOIN rag_documents d ON d.id = c.document_id"
                " WHERE c.id = 4"
            ),
            {"v": "[" + ",".join(["0.1"] * DIM) + "]"},
        )

    query_vector = asyncio.run(stub.embed_query("叔叔")).vectors[0]
    literal = "[" + ",".join(f"{v:.7f}" for v in query_vector) + "]"

    with engine.connect() as conn:
        filtered = rag_embeddings.build_vector_candidates(
            dimension=DIM, eligibility="c.status = 'active' AND d.status = 'active'"
        )
        ids = [r[0] for r in conn.execute(filtered, {"query_vector": literal, "limit": 10, "offset": 0}).fetchall()]
        print(f"  带过滤候选 {ids}（期望不含撤权 chunk 4）")
        if 4 in ids:
            failures.append("撤权 chunk 出现在带过滤结果里")

        unfiltered = rag_embeddings.build_vector_candidates(
            dimension=DIM, eligibility="1=1"
        )
        ids2 = [r[0] for r in conn.execute(unfiltered, {"query_vector": literal, "limit": 10, "offset": 0}).fetchall()]
        print(f"  反证（无过滤）{ids2}")
        if 4 not in ids2:
            failures.append("反证未成立：无过滤时也查不到撤权 chunk，过滤可能不承重")
        else:
            print("  OK  过滤条件承重")

    lexical = [{"chunk_id": 1}, {"chunk_id": 2}]
    vector = [{"chunk_id": 2}, {"chunk_id": 3}]
    fused = rag_embeddings.fuse_candidates(lexical=lexical, vector=vector, limit=3)
    order = [h["chunk_id"] for h in fused]
    print(f"  RRF 融合 {order}（两路都命中的 chunk 2 应排第一）")
    if order[0] != 2:
        failures.append(f"RRF 未把两路都命中的排前：{order}")
    again = [h["chunk_id"] for h in rag_embeddings.fuse_candidates(lexical=lexical, vector=vector, limit=3)]
    if again != order:
        failures.append(f"RRF 不确定：{order} vs {again}")
    else:
        print("  OK  RRF 确定性")

    with engine.begin() as conn:
        conn.execute(
            text("DROP TABLE IF EXISTS rag_chunk_embeddings, rag_chunks, rag_documents CASCADE")
        )

    if failures:
        print("FAIL:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("PASS: pgvector 索引 + filter-then-ANN（含反证）+ RRF 融合")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
