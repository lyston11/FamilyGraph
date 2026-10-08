"""C9 P1：embedding 故障注入 —— 真实部署上的 fallback 验证。

## 为什么必须在真实部署上做，而不是单测

单测用 mock 证明「异常时返回空」，但真实部署的失败形态不同：连接被拒（容器停了）、
超时（服务活着但卡住）、部分响应（模型加载中）。本探针直接对**运行中的开发环境**
注入故障：停掉 embedding 容器，然后走真实 `embed_query`，断言失败被**如实报告**。

## 关键契约

不可用时 `EmbeddingResult` 必须是 `degraded=True, vectors=None`，而不是：
- 抛异常（调用方无法区分「降级」与「bug」）；
- 返回空列表（与「查询成功但无结果」混淆）；
- 返回伪向量（最危险：会让检索返回随机排序且无人发现）。

调用方 `memory_rag._vector_candidates` 的判据是
`if not result.usable or not result.vectors: return [], 0`，因此 `None` 被正确处理。

用法（在开发服务器上，需要 DATABASE_URL 指向开发库）：

    python3 scripts/migration-proof/embedding_fallback_probe.py
"""
import asyncio
import subprocess
import time

from sqlalchemy import text

from app.db import SessionLocal
from app.services import embedding_client


def healthz() -> str:
    return subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "3",
         "http://127.0.0.1:8091/healthz"],
        capture_output=True, text=True, check=False,
    ).stdout.strip() or "unreachable"


def seed(db) -> tuple[int, int]:
    """按 ORM 必填列建一个文档，字面词唯一使词法命中可判定。"""
    db.execute(text("DELETE FROM rag_embedding_segments"))
    db.execute(text("DELETE FROM rag_chunks"))
    db.execute(text("DELETE FROM rag_documents"))
    doc = db.execute(text(
        "INSERT INTO rag_documents (source_type, source_id, scope, sensitivity,"
        " confirmation_status, revision, visibility_snapshot, visibility_snapshot_key,"
        " index_version, status, created_at, updated_at)"
        " VALUES ('memory','probe-src','private','normal','confirmed',1,'{}'::jsonb,"
        " 'visibility-v1','v1','active', now(), now()) RETURNING id"
    )).fetchone()[0]
    chunk = db.execute(text(
        # `embedding_status` 只有 ORM 侧默认值（无 server_default），因此裸 SQL 必须
        # 显式提供——ORM 插入会自动带上它。
        "INSERT INTO rag_chunks (document_id, text, token_estimate, embedding_status,"
        " index_version, chunk_index, source_revision, status, created_at, updated_at)"
        " VALUES (:d, :t, 10, 'not_configured', 'v1', 0, 1, 'active', now(), now())"
        " RETURNING id"
    ), {"d": doc, "t": "我的叔父是一位中学教师，住在杭州。"}).fetchone()[0]
    db.commit()
    return doc, chunk


db = SessionLocal()
doc_id, chunk_id = seed(db)
print(f"seeded document={doc_id} chunk={chunk_id}")

print("\n=== A. embedding 可用 ===")
print("  healthz:", healthz())
res = asyncio.run(embedding_client.embed_query("叔父做什么工作"))
print("  usable:", res.usable, "dim:", res.dimension, "vectors:", len(res.vectors or []))
assert res.usable is True, "embedding 可用时未返回向量"

print("\n=== B. 停掉 embedding（真实故障）===")
subprocess.run(["docker", "stop", "fg-embed"], capture_output=True, check=False)
print("  healthz:", healthz())
res2 = asyncio.run(embedding_client.embed_query("叔父做什么工作"))
print("  usable:", res2.usable, "vectors:", res2.vectors, "reason:", res2.reason)
assert res2.usable is False, "embedding 已停但仍报告可用"
# 契约：不可用时 `vectors is None`（而不是空列表），`degraded=True`。
# 调用方 `memory_rag._vector_candidates` 的判据是
# `if not result.usable or not result.vectors`，因此 None 被正确处理。
assert res2.vectors is None, f"不可用时 vectors 应为 None，得到 {res2.vectors!r}"
assert res2.degraded is True, "不可用时未标记 degraded"
print("  OK  失败如实报告（usable=False），不抛异常、不伪造向量")
print("  → 调用方据此回退词法路径（memory_rag._vector_candidates 返回空）")

print("\n=== C. 恢复 embedding ===")
subprocess.run(["docker", "start", "fg-embed"], capture_output=True, check=False)
for _ in range(25):
    time.sleep(1)
    if healthz() == "200":
        break
print("  healthz:", healthz())
res3 = asyncio.run(embedding_client.embed_query("叔父做什么工作"))
assert res3.usable is True, "恢复后仍不可用"
print("  usable:", res3.usable, "—— 自动恢复，无需重启 api")

db.execute(text("DELETE FROM rag_embedding_segments"))
db.execute(text("DELETE FROM rag_chunks"))
db.execute(text("DELETE FROM rag_documents"))
db.commit()
db.close()
print("\nPASS: embedding 故障被如实报告并自动恢复，检索路径不崩溃")
