"""P3-b 决策探针：向量相似度能否区分「相关」与「不相关」？

## 这个探针决定什么

它给出 `_VECTOR_MIN_SIMILARITY`（相似度地板）的**取值依据**，并回答一个更根本的
问题：**余弦相似度能不能当作相关性判据？**

## 结论（2026-10-10，`bge-small-zh-v1.5`，golden set）

```text
相关命中     min = 0.5091   p50 = 0.6580   max = 0.7716
不相关命中   min = 0.3634   p50 = 0.5000   max = 0.7712   ← 与相关重叠
弃答用例的虚假 top-1：0.4859 / 0.3811 / 0.3634
```

两个分布**重叠**（不相关的最高 0.7712 高于相关的最低 0.5091），因此余弦相似度
**不能**作为相关性判据——这是「向量不做重排依据」的实测根据。

但存在一个 0.023 宽的可用间隙：弃答用例的虚假命中最高 **0.4859**，相关命中最低
**0.5091**。地板取 **0.50** 落在其中，只做一件事：丢掉明显无关的向量补充，
从而让「库里没有」仍表现为空结果（否则向量候选填满 limit，弃答正确率从 1.00
掉到 0.00——实测过）。

## 换模型必须重跑本探针

0.50 是**这个模型在这个语料上**的值。换 embedding 模型或换 chunking 算法后相似度
尺度会变，地板必须重测后再定，不能沿用。

## 为什么地板窄是可以接受的

地板**只作用于向量新增候选**，不作用于词法命中。因此误伤一个「弱相关」向量候选的
代价是零——词法路径本来就已经提供了那条命中。它换来的是弃答语义。

用法：

    PGTEST_DSN=postgresql://... EMBEDDING_BASE_URL=http://... \\
        python scripts/migration-proof/vector_similarity_distribution.py
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import sys
from pathlib import Path

OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", "artifacts/migration-proof"))


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def main() -> int:
    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向已建立索引的隔离 PostgreSQL")
        return 2
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        print("SKIP: 需要 sqlalchemy")
        return 2

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.services import embedding_client, memory_eval, rag_embeddings

    if not embedding_client.enabled():
        print("SKIP: 需要 EMBEDDING_BASE_URL 指向可用的 embedding 服务")
        return 2

    engine = create_engine(dsn)
    golden = memory_eval.load_golden_set()
    label_by_text = {entry["text"]: entry["label"] for entry in golden["memories"]}

    with engine.connect() as conn:
        chunk_rows = conn.execute(
            text(
                "SELECT c.id, c.text FROM rag_chunks c JOIN rag_documents d ON d.id = c.document_id"
                " WHERE c.status = 'active' AND d.status = 'active'"
            )
        ).fetchall()
        segment_rows = conn.execute(
            text("SELECT chunk_id, embedding::text FROM rag_embedding_segments WHERE model = :m"),
            {"m": rag_embeddings.configured_model()},
        ).fetchall()
    texts = {row[0]: row[1] for row in chunk_rows}
    segments = [
        (row[0], [float(value) for value in row[1].strip("[]").split(",")]) for row in segment_rows
    ]
    if not segments:
        print("FAIL: 没有分段向量。请先跑 embedding_index.run_index_pass 建索引。")
        return 1

    relevant: list[float] = []
    irrelevant: list[float] = []
    abstention_top: list[float] = []
    print(f"{'case':26} {'mode':16} best_relevant  best_irrelevant  gap")
    for case in golden["cases"]:
        query_vector = asyncio.run(embedding_client.embed_query(case["question"])).vectors[0]
        scored = sorted(
            ((_cosine(query_vector, vector), chunk_id) for chunk_id, vector in segments),
            reverse=True,
        )[:20]
        expected = set(case.get("expected_sources", ()))
        rel = [score for score, cid in scored if label_by_text.get(texts.get(cid, "")) in expected]
        irr = [
            score for score, cid in scored if label_by_text.get(texts.get(cid, "")) not in expected
        ]
        best_rel = max(rel) if rel else None
        best_irr = max(irr) if irr else None
        mode = "abstention" if case.get("expect_empty") else "answerable"
        rel_text = f"{best_rel:.4f}" if best_rel else "-"
        irr_text = f"{best_irr:.4f}" if best_irr else "-"
        gap_text = f"{best_rel - best_irr:.4f}" if best_rel and best_irr else "-"
        print(f"{case['id']:26} {mode:16} {rel_text:>12} {irr_text:>16} {gap_text:>8}")
        if best_rel:
            relevant.append(best_rel)
        if best_irr:
            irrelevant.append(best_irr)
        if case.get("expect_empty") and best_irr:
            abstention_top.append(best_irr)

    def summarize(name: str, values: list[float]) -> str:
        if not values:
            return f"{name}: (无样本)"
        return (
            f"{name}: min={min(values):.4f} p50={statistics.median(values):.4f} "
            f"max={max(values):.4f}"
        )

    print()
    print(summarize("相关 top-1", relevant))
    print(summarize("不相关 top-1", irrelevant))
    print(summarize("弃答虚假 top-1", abstention_top))

    failures: list[str] = []
    if not relevant or not irrelevant:
        failures.append("样本不足，无法判定分布是否重叠")
    else:
        # 结论 1：分布必须重叠——否则「不能用余弦相似度做相关性判据」这条结论不成立。
        if max(irrelevant) <= min(relevant):
            failures.append(
                "不相关与相关分布**不重叠**：余弦相似度在本语料上可用作判据，"
                "本探针的结论（不可用）需要修订，地板也应重新论证"
            )
        # 结论 2：必须存在可用间隙（弃答虚假 < 相关最低），否则地板无法同时满足
        # 「保住弃答」与「保住相关」。
        if abstention_top and min(relevant) <= max(abstention_top):
            failures.append(
                f"不存在可用间隙：弃答虚假最高 {max(abstention_top):.4f} >= "
                f"相关最低 {min(relevant):.4f}，当前地板取值失去依据"
            )
        else:
            print(
                f"  OK  存在可用间隙：弃答虚假最高 {max(abstention_top):.4f} < "
                f"相关最低 {min(relevant):.4f}（地板 0.50 落在其中）"
            )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "vector-similarity-distribution.json").write_text(
        json.dumps(
            {
                "model": rag_embeddings.configured_model(),
                "relevant": relevant,
                "irrelevant": irrelevant,
                "abstention_top": abstention_top,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    if failures:
        print("\nFAIL:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nPASS: 分布重叠（余弦相似度不可作判据）且存在可用地板间隙")
    return 0


if __name__ == "__main__":
    sys.exit(main())
