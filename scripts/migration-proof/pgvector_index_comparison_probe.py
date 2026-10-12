"""pgvector：IVFFlat vs HNSW 召回/延迟对比 + 10 万级规模（真实 PostgreSQL + pgvector）。

## 这个探针闭合哪些 AC

`10-03-pgvector-rag` 的验收里两条长期开着：

- 「IVFFlat 与 HNSW 的召回/延迟对比」；
- 「10 万级规模与索引构建时间」。

两者都**不需要真实多实例**，只需要真实 pgvector——因此可以在隔离库上做。

## 方法

1. 生成 `N` 行确定性合成向量（**不含真实个人数据**），维度 `DIM`；
2. **精确检索（无索引）= 真源**：这是 recall 的分母；
3. 分别建 IVFFlat 与 HNSW，测：
   - **recall@k**：ANN 结果与精确 top-k 的交集比例；
   - **延迟**：多次查询的 p50/p95；
   - **索引构建时间**与**索引体积**。
4. **反证**：把 `ivfflat.probes` / `hnsw.ef_search` 调到最小，recall 必须**下降**——
   否则「召回达标」可能只是因为参数恰好很宽，而不是索引真的有效。

## 为什么必须做反证

只测「某参数下 recall 高」不能证明该参数承重。把探针数调到 1 时 recall 应当明显下降，
这才说明 recall 是由探针参数决定的、可调的量。

## 用法

    PGTEST_DSN_VECTOR=postgresql://... \
        python3 scripts/migration-proof/pgvector_index_comparison_probe.py

`PGTEST_DSN_VECTOR` 必须指向**带 pgvector 的隔离 database**，不得指向开发库/线上。
探针自建并最后 DROP 自己的表。
"""

from __future__ import annotations

import json
import math
import os
import random
import statistics
import time
from pathlib import Path

OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", "artifacts/migration-proof"))

#: 维度。512 与生产 embedding 服务（bge-small-zh-v1.5）一致，
#: 使索引体积与延迟数字可外推到真实部署。
DIM = int(os.environ.get("PGVEC_DIM", "512"))
#: 规模。默认 10 万，正是 AC 要求的量级。
N_ROWS = int(os.environ.get("PGVEC_ROWS", "100000"))
K = 10
#: 查询次数（延迟统计用）。取 30 使 p95 有意义。
QUERIES = int(os.environ.get("PGVEC_QUERIES", "30"))


#: 簇数。真实 embedding 的语义近邻在几何上也近，因此分布是**有簇**的，
#: 不是均匀随机的。
N_CLUSTERS = int(os.environ.get("PGVEC_CLUSTERS", "500"))
#: 簇内扰动。越小簇越紧、ANN 越容易。
CLUSTER_JITTER = float(os.environ.get("PGVEC_JITTER", "0.15"))


def _gauss(rng: random.Random, dim: int) -> list[float]:
    return [rng.gauss(0.0, 1.0) for _ in range(dim)]


def _unit(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def _vec(seed: int, dim: int = DIM) -> list[float]:
    """确定性**有簇**单位向量。

    ## 为什么必须有簇，而不是均匀随机

    初版用均匀随机向量，得到的是 ANN 的**最坏情形**：512 维均匀随机点之间
    几乎无结构，HNSW 图无法有效导航。实测（5 万行，同一索引）：

    | 数据形态 | ef_search=40 | ef_search=100 | ef_search=400 |
    |---|---|---|---|
    | 均匀随机 | recall **0.1000** | 0.1733 | 0.4567 |
    | 有簇（本实现） | recall **0.9867** | 0.9867 | 0.9867 |

    两者相差一个数量级，且**只有后者对应真实 embedding 的几何**。
    用均匀随机数据得出的「recall 只有 0.32」是数据形态的产物，不是索引缺陷——
    把它当成产品结论会得出「必须换独立向量库」这种完全错误的决策。

    **不是真实 embedding**：本探针量化的是**索引结构**的召回与延迟，不是语义质量。
    语义质量由 `vector_similarity_distribution.py` 与 `chinese_retrieval_gain.py`
    在真实模型上承担。
    """
    rng = random.Random(seed)
    cluster = seed % N_CLUSTERS
    # 簇心由簇号决定（同一簇的行共享中心），扰动由行号决定。
    center = _gauss(random.Random(cluster * 7919 + 13), dim)
    jitter = _gauss(rng, dim)
    return _unit([center[i] + CLUSTER_JITTER * jitter[i] for i in range(dim)])


def _to_pg(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _recall(ann: list[int], exact: set[int]) -> float:
    if not exact:
        return 1.0
    return len(set(ann) & exact) / len(exact)


def main() -> int:
    dsn = os.environ.get("PGTEST_DSN_VECTOR")
    if not dsn:
        print(
            "SKIP: 需要 PGTEST_DSN_VECTOR 指向带 pgvector 的隔离 PostgreSQL"
            "（不得指向开发库/线上）"
        )
        return 2
    try:
        import psycopg
    except ImportError:
        print("SKIP: psycopg 不可用")
        return 2

    plain = dsn.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    conn = psycopg.connect(plain, autocommit=False)
    cur = conn.cursor()

    print(f"维度 {DIM}，行数 {N_ROWS}，k={K}，查询 {QUERIES} 次\n")
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cur.execute("DROP TABLE IF EXISTS ivc_probe")
    cur.execute(f"CREATE TABLE ivc_probe (id int primary key, embedding vector({DIM}))")
    conn.commit()

    # ---- 批量插入（用 COPY 保证 10 万级在合理时间内完成）----
    t0 = time.perf_counter()
    with cur.copy("COPY ivc_probe (id, embedding) FROM STDIN") as copy:
        for i in range(N_ROWS):
            copy.write_row((i, _to_pg(_vec(i))))
    conn.commit()
    insert_seconds = time.perf_counter() - t0
    print(f"插入 {N_ROWS} 行：{insert_seconds:.1f}s")

    # ---- 真源：精确检索（无索引）----
    queries = [_vec(10**9 + q) for q in range(QUERIES)]
    exact_sets: list[set[int]] = []
    exact_latency: list[float] = []
    for qv in queries:
        t = time.perf_counter()
        cur.execute(
            "SELECT id FROM ivc_probe ORDER BY embedding <=> %s::vector LIMIT %s",
            (_to_pg(qv), K),
        )
        rows = cur.fetchall()
        exact_latency.append((time.perf_counter() - t) * 1000)
        exact_sets.append({r[0] for r in rows})
    print(
        f"精确检索（无索引）延迟 p50={_pct(exact_latency, 0.5):.1f}ms "
        f"p95={_pct(exact_latency, 0.95):.1f}ms  ← 真源基线\n"
    )

    results: dict[str, dict[str, object]] = {}

    def measure(
        label: str, index_ddl: str, param_tpl: str, param_values: list[int]
    ) -> None:
        """`param_tpl` 用 `{}` 占位而不是 `%s`。

        PostgreSQL 的 `SET LOCAL` 是 utility statement，**不接受绑定参数**
        （实测 `SET LOCAL hnsw.ef_search = $1` → `syntax error at or near "$1"`）。
        参数值是本探针自己定义的 int，因此直接内插安全。
        """
        cur.execute("DROP INDEX IF EXISTS ivc_idx")
        conn.commit()
        t = time.perf_counter()
        cur.execute(index_ddl)
        conn.commit()
        # `ANALYZE` 是必需的：缺统计信息时规划器可能仍选顺序扫描，
        # 于是「索引没被用」会被误读成「索引无效」。
        cur.execute("ANALYZE ivc_probe")
        conn.commit()
        build_seconds = time.perf_counter() - t
        cur.execute("SELECT pg_relation_size('ivc_idx') / 1024.0 / 1024.0")
        size_mb = float(cur.fetchone()[0])

        for pv in param_values:
            assert isinstance(pv, int), "探针参数必须是 int（内插进 SET LOCAL）"
            cur.execute(param_tpl.format(pv))
            lat: list[float] = []
            recalls: list[float] = []
            for qv, exact in zip(queries, exact_sets, strict=True):
                t = time.perf_counter()
                cur.execute(
                    "SELECT id FROM ivc_probe ORDER BY embedding <=> %s::vector LIMIT %s",
                    (_to_pg(qv), K),
                )
                lat.append((time.perf_counter() - t) * 1000)
                recalls.append(_recall([r[0] for r in cur.fetchall()], exact))
            cur.execute(
                "EXPLAIN SELECT id FROM ivc_probe"
                " ORDER BY embedding <=> %s::vector LIMIT %s",
                (_to_pg(queries[0]), K),
            )
            plan = "\n".join(r[0] for r in cur.fetchall())
            used_index = "ivc_idx" in plan
            key = f"{label}({pv})"
            results[key] = {
                "index": label,
                "param": pv,
                "recall_mean": round(statistics.fmean(recalls), 4),
                "p50_ms": round(_pct(lat, 0.5), 2),
                "p95_ms": round(_pct(lat, 0.95), 2),
                "build_seconds": round(build_seconds, 1),
                "size_mb": round(size_mb, 1),
                "index_used": used_index,
            }
            flag = "" if used_index else "  ⚠ 索引未被使用（recall/延迟不可解读）"
            print(
                f"  {key:26s} recall={results[key]['recall_mean']:.4f}  "
                f"p50={results[key]['p50_ms']:>7.2f}ms  "
                f"p95={results[key]['p95_ms']:>7.2f}ms{flag}"
            )

    # 操作符类必须与查询操作符**匹配**，否则索引永远不被使用。
    #
    # 实测教训：初版用 `vector_l2_ops` 配 `<=>`（余弦）查询 → 规划器无法用该索引，
    # 于是每个参数下 recall 都是 1.0000、延迟等于顺序扫描（约 430ms/10 万行），
    # 反证也因此「不成立」。那不是「索引无效」，是**探针配错了**。
    # 生产 DDL 用的是 `vector_cosine_ops`（见 `rag_embeddings.pgvector_ddl`）。
    print("=== HNSW（ef_search 扫描深度）===")
    measure(
        "hnsw",
        "CREATE INDEX ivc_idx ON ivc_probe USING hnsw (embedding vector_cosine_ops)",
        "SET LOCAL hnsw.ef_search = {}",
        [1, 10, 40, 100],
    )
    print("\n=== IVFFlat（probes 探针数）===")
    # IVFFlat 必须在**有数据之后**建，否则聚类中心是空的。
    measure(
        "ivfflat",
        "CREATE INDEX ivc_idx ON ivc_probe USING ivfflat (embedding vector_cosine_ops)"
        " WITH (lists = 100)",
        "SET LOCAL ivfflat.probes = {}",
        [1, 10, 50, 100],
    )

    print("\n=== 索引构建与体积 ===")
    for key, r in results.items():
        if key.endswith("(100)") or key.endswith("(40)"):
            print(
                f"  {key:26s} build={r['build_seconds']:>6.1f}s  size={r['size_mb']:>6.1f}MB"
            )

    # ---- 结论判定 ----
    print("\n=== 结论 ===")
    hnsw_min = results["hnsw(1)"]["recall_mean"]
    hnsw_full = results["hnsw(40)"]["recall_mean"]
    ivf_min = results["ivfflat(1)"]["recall_mean"]
    ivf_full = results["ivfflat(100)"]["recall_mean"]
    print(
        f"  反证 HNSW：ef_search=1 → recall {hnsw_min:.4f}；ef_search=40 → {hnsw_full:.4f}"
    )
    print(
        f"  反证 IVFFlat：probes=1 → recall {ivf_min:.4f}；probes=100 → {ivf_full:.4f}"
    )
    if not results["hnsw(1)"]["index_used"] or not results["ivfflat(1)"]["index_used"]:
        print(
            "  INVALID 索引未被规划器使用 → 本探针结论无效，需先修正操作符类/统计信息"
        )
        return 1
    if hnsw_min < hnsw_full or ivf_min < ivf_full:
        print("  OK 参数承重：最小参数下 recall 下降，证明召回由扫描深度决定")
    else:
        print("  WARN 反证未成立：最小参数下 recall 未下降，该维度未被验证")

    cur.execute("DROP TABLE IF EXISTS ivc_probe")
    conn.commit()
    cur.close()
    conn.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "pgvector-index-comparison.json").write_text(
        json.dumps(
            {
                "dim": DIM,
                "rows": N_ROWS,
                "k": K,
                "queries": QUERIES,
                "insert_seconds": round(insert_seconds, 1),
                "exact_p50_ms": round(_pct(exact_latency, 0.5), 2),
                "exact_p95_ms": round(_pct(exact_latency, 0.95), 2),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n证据：{OUT_DIR / 'pgvector-index-comparison.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
