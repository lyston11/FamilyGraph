"""C6：中文检索增益对照 —— 向量路径是否**真的**比词法更好。

## 为什么必须有这一步

「向量检索已接入」不等于「检索变好了」。若不同措辞的查询在词法下也能命中，
向量就是纯粹的额外成本与故障面。因此必须用**同义改写**的查询对照：

- 词法（PGroonga）擅长字面重合；
- 向量擅长**不同措辞的同一含义**（"爸爸的弟弟" vs "叔叔"）。

语料设计原则：每个查询的**目标文档不含查询字面**，但**存在语义等价表述**。
这样词法必然漏，向量应当补上。

## 判定

| 结果 | 结论 |
|---|---|
| 向量提升召回且不引入无关命中 | 值得启用 |
| 向量无提升 | **不应启用**（纯成本） |
| 向量提升但引入大量无关命中 | 需调参或只用词法 |

本探针**如实报告**，不为了「证明向量有用」而挑语料。

用法：

    PGTEST_DSN=postgresql+psycopg://... EMBEDDING_BASE_URL=http://127.0.0.1:8091 \\
      python3 scripts/migration-proof/chinese_retrieval_gain.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

DSN = os.environ.get("PGTEST_DSN")
if not DSN:
    print("SKIP: 需要 PGTEST_DSN（含 pgvector 与 pgroonga）")
    raise SystemExit(2)
if not os.environ.get("EMBEDDING_BASE_URL"):
    print("SKIP: 需要 EMBEDDING_BASE_URL 指向 embedding 服务")
    raise SystemExit(2)

from sqlalchemy import create_engine, text  # noqa: E402

from app.services import embedding_chunking, embedding_client, rag_embeddings  # noqa: E402

DIM = 512

# (查询, 目标文档文本, 说明)
# 目标文档**不含**查询字面，但语义等价 —— 词法必然漏，向量应补。
CORPUS: list[tuple[str, str, str]] = [
    ("爸爸的弟弟", "我的叔父是一位中学教师，住在杭州。", "叔父/叔叔 同义"),
    ("妈妈的姐姐", "姨妈常年在北京工作，每年春节回来。", "姨妈/姨母 同义"),
    ("爷爷的爸爸", "曾祖父年轻时在乡村行医。", "曾祖父 辈分"),
    ("我妻子的母亲", "岳母喜欢种花，院子里有很多月季。", "岳母 姻亲"),
    ("儿子的妻子", "儿媳在医院做护士。", "儿媳 姻亲"),
    ("哥哥的女儿", "侄女今年考上了大学。", "侄女 辈分"),
    ("父亲的姐妹", "姑姑住在上海，做外贸生意。", "姑姑/姑母 同义"),
    ("女儿的儿子", "外孙刚满三岁，很爱笑。", "外孙 辈分"),
]

# 无关文档（干扰项）：验证向量不会把不相关内容拉进来。
NOISE = [
    "今天天气不错，适合出门散步。",
    "公司第三季度的营收同比增长百分之十二。",
    "这道菜需要先腌制半小时再下锅。",
    "地铁六号线早高峰非常拥挤。",
]


def main() -> int:
    engine = create_engine(DSN)
    failures: list[str] = []

    with engine.begin() as conn:
        conn.execute(
            text(
                "DROP TABLE IF EXISTS rag_embedding_segments, rag_chunks, rag_documents CASCADE"
            )
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

    # PGroonga 探测必须用**独立事务**。
    #
    # ## 为什么不能在同一事务里 try/except
    #
    # PostgreSQL 中一条语句失败会把**整个事务标记为 aborted**，后续语句全部失败直到
    # 回滚。因此若在创建表的同一事务里尝试 `CREATE INDEX ... USING pgroonga`
    # （本实例是 pgvector 镜像，未装 pgroonga），捕获异常后事务仍处于 aborted 状态，
    # `with engine.begin()` 退出时**回滚全部 CREATE TABLE**——实测报
    # `relation "rag_documents" does not exist`。
    #
    # 用 savepoint（`begin_nested`）或独立事务才能隔离该失败。
    has_pgroonga = False
    try:
        with engine.begin() as conn:
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_gain_pgroonga ON rag_chunks USING pgroonga (text)")
            )
        has_pgroonga = True
    except Exception:  # noqa: BLE001
        has_pgroonga = False

    docs = [(t, "target", label) for _q, t, label in CORPUS] + [
        (n, "noise", "") for n in NOISE
    ]
    with engine.begin() as conn:
        for i, (body, _kind, _label) in enumerate(docs, start=1):
            conn.execute(
                text(
                    "INSERT INTO rag_documents (id,source_type,source_id,scope,sensitivity,"
                    "revision,status) VALUES (:i,'memory',:sid,'private','normal',1,'active')"
                ),
                {"i": i, "sid": f"m{i}"},
            )
            conn.execute(
                text(
                    "INSERT INTO rag_chunks (id,document_id,text,token_estimate,index_version,"
                    "chunk_index,source_revision,status)"
                    " VALUES (:i,:i,:t,1,'v1',0,1,'active')"
                ),
                {"i": i, "t": body},
            )

    # 向量索引：用真实 embedding 服务（不是 stub）。
    model = "local:bge-small-zh-v1.5"

    async def _index() -> int:
        written = 0
        for i, (body, _kind, _label) in enumerate(docs, start=1):
            segments = embedding_chunking.segment_text(body)
            result = await embedding_client.embed_documents([s.text for s in segments])
            if not result.usable:
                raise RuntimeError(f"embedding 不可用：{result.reason}")
            assert result.vectors is not None
            with engine.begin() as conn:
                for segment, vector in zip(segments, result.vectors, strict=True):
                    literal = "[" + ",".join(f"{v:.7f}" for v in vector) + "]"
                    conn.execute(
                        text(
                            "INSERT INTO rag_embedding_segments (chunk_id, model, segment_index,"
                            " algorithm, char_start, char_end, revision, scope, embedding)"
                            " VALUES (:c,:m,:si,:a,:cs,:ce,1,'private',CAST(:v AS vector))"
                        ),
                        {
                            "c": i, "m": model, "si": segment.index,
                            "a": embedding_chunking.SEGMENT_ALGORITHM,
                            "cs": segment.char_start, "ce": segment.char_end, "v": literal,
                        },
                    )
                    written += 1
        return written

    written = asyncio.run(_index())
    print(f"已索引 {written} 个分段向量（{len(docs)} 个 chunk）\n")

    eligibility = "c.status = 'active' AND d.status = 'active'"
    vector_sql = rag_embeddings.build_vector_candidates(
        dimension=DIM, eligibility=eligibility
    )

    lexical_hits = 0
    vector_hits = 0
    vector_only = 0
    noise_leaked = 0
    rows_out: list[dict[str, object]] = []

    for idx, (query, _target, label) in enumerate(CORPUS):
        target_chunk = idx + 1  # 前 len(CORPUS) 个文档是目标
        result = asyncio.run(embedding_client.embed_query(query))
        if not result.usable or not result.vectors:
            failures.append(f"embedding 查询失败：{result.reason}")
            break
        literal = "[" + ",".join(f"{v:.7f}" for v in result.vectors[0]) + "]"

        with engine.connect() as conn:
            # 词法：PGroonga（若可用），否则 LIKE
            if has_pgroonga:
                lex_rows = conn.execute(
                    text(
                        "SELECT c.id FROM rag_chunks c JOIN rag_documents d ON d.id=c.document_id"
                        f" WHERE c.text &@~ :q AND {eligibility} LIMIT 3"
                    ),
                    {"q": query},
                ).fetchall()
            else:
                lex_rows = conn.execute(
                    text(
                        "SELECT c.id FROM rag_chunks c JOIN rag_documents d ON d.id=c.document_id"
                        f" WHERE c.text LIKE :q AND {eligibility} LIMIT 3"
                    ),
                    {"q": f"%{query}%"},
                ).fetchall()
            vec_rows = conn.execute(
                vector_sql, {"query_vector": literal, "limit": 3, "offset": 0}
            ).fetchall()

        lex_ids = [r[0] for r in lex_rows]
        vec_ids = [r[0] for r in vec_rows]
        lex_ok = target_chunk in lex_ids
        vec_ok = target_chunk in vec_ids
        leaked = [i for i in vec_ids if i > len(CORPUS)]  # 命中干扰项

        lexical_hits += int(lex_ok)
        vector_hits += int(vec_ok)
        vector_only += int(vec_ok and not lex_ok)
        noise_leaked += len(leaked)

        print(
            f"  {query:<12s} 目标文档={target_chunk:>2d} "
            f"词法{'命中' if lex_ok else '漏掉'} 向量{'命中' if vec_ok else '漏掉'} "
            f"| {label}"
        )
        rows_out.append(
            {"query": query, "target": target_chunk, "lexical_hit": lex_ok,
             "vector_hit": vec_ok, "noise_hits": leaked}
        )

    n = len(CORPUS)
    print(f"\n召回：词法 {lexical_hits}/{n}  向量 {vector_hits}/{n}  仅向量命中 {vector_only}")
    print(f"向量引入的无关命中：{noise_leaked}")
    if not has_pgroonga:
        # 不能声称「结论保守」——那是不准确的。本语料的目标文档与查询**零字面重合**
        # （"爸爸的弟弟" vs "我的叔父是一位中学教师"），因此 LIKE 与 PGroonga 都会漏。
        # 真实缺口是：**尚未在有 PGroonga 的实例上做同一对照**，以确认词法在
        # 「部分字面重合」的场景下不会补上这些召回。
        print("注意：本实例无 PGroonga，词法基线为 LIKE。本语料目标文档与查询零字面重合，")
        print("      因此 PGroonga 预计同样漏掉；但在有 PGroonga 的实例上复跑仍属未完成。")

    with engine.begin() as conn:
        conn.execute(
            text(
                "DROP TABLE IF EXISTS rag_embedding_segments, rag_chunks, rag_documents CASCADE"
            )
        )

    report = {
        "corpus_size": n,
        "lexical_recall": lexical_hits,
        "vector_recall": vector_hits,
        "vector_only_recall": vector_only,
        "noise_leaked": noise_leaked,
        "pgroonga_available": has_pgroonga,
        "rows": rows_out,
        "failures": failures,
    }
    out_dir = Path(os.environ.get("MIGRATION_PROOF_OUT", str(ROOT / "artifacts/migration-proof")))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "chinese-retrieval-gain.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    if vector_only == 0:
        print("\n结论：向量**未带来额外召回** —— 不应启用（纯成本与故障面）")
        return 0
    print(f"\n结论：向量带来 {vector_only} 个额外召回；启用前需人工确认这些命中确实相关")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
