"""把 golden set 灌入隔离 PostgreSQL 并用**真实 embedding 服务**建向量索引。

## 为什么需要它

`vector_similarity_distribution.py` 的前置是「库里已有分段向量」。测试套件通过
`conftest` 的 fixture 建库，但那是 SQLite 且进程内；本脚本在**隔离 PG** 上
复现同一份语料，使相似度分布可以在真实数据库 + 真实模型上重测。

## 用法

    PGTEST_DSN=postgresql+psycopg://... EMBEDDING_BASE_URL=http://... \
        python3 scripts/migration-proof/seed_golden_embeddings.py

只写隔离库；脚本拒绝在 `sqlite` DSN 上运行（那没有 pgvector）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 允许从仓库内（`scripts/migration-proof/`）或任意临时路径运行：
# 后者没有 `parents[2]`，因此允许用 `BACKEND_DIR` 显式指定。
ROOT = (
    Path(os.environ["REPO_ROOT"])
    if os.environ.get("REPO_ROOT")
    else Path(__file__).resolve().parents[2]
)


def main() -> int:
    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL")
        return 2
    if "sqlite" in dsn:
        print("SKIP: 本脚本需要 PostgreSQL + pgvector")
        return 2

    sys.path.insert(0, str(ROOT / "backend"))
    os.environ.setdefault("DATA_DIR", "/tmp/fg-seed-golden")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.services import embedding_client, embedding_index, memory_eval, memory_rag
    from app.utils import timeutil

    if not embedding_client.enabled():
        print("SKIP: 需要 EMBEDDING_BASE_URL 指向可用的 embedding 服务")
        return 2

    engine = create_engine(dsn, future=True)
    golden = memory_eval.load_golden_set()

    with Session(engine) as db:
        # ---- 建 owner：user + account + space（与 conftest 的 fixture 等价）----
        from app.models.account import Account
        from app.models.space import FamilySpace, SpaceMember
        from app.models.user import User
        from app.utils import security

        # 与 `tests/conftest.py::create_user_with_pin` 同构：
        # `claim_status` 不是 User 的字段，而是 **Account.status**。
        # 直传 `User(claim_status=...)` 会 TypeError。
        now = timeutil.utcnow()
        user = User(
            name="golden-seed-owner",
            created_at=now,
            profile_status="identity_confirmed",
            profile_confirmed_at=now,
        )
        user.account = Account(
            pin_hash=security.hash_pin("123456"),
            pin_must_change=False,
            token_version=0,
            failed_attempts=0,
            locked_until=None,
            status="claimed",
            claimed_at=now,
        )
        db.add(user)
        db.flush()
        account = user.account
        space = FamilySpace(
            name="golden-seed-space", kind="household", owner_id=user.id, created_at=now
        )
        db.add(space)
        db.flush()
        db.add(
            SpaceMember(
                space_id=space.id,
                user_id=user.id,
                added_by=user.id,
                role="space_admin",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()

        # ---- 灌 golden memories ----
        labels: dict[str, int] = {}
        supersedes: list[tuple[int, int]] = []
        for entry in golden["memories"]:
            candidate = memory_rag.propose_candidate(
                db,
                author_account_id=account.id,
                source={"kind": "manual"},
                source_quote=entry["text"],
                summary=entry["text"],
                suggested_scope="private",
                purpose="golden set",
                sensitivity=entry.get("sensitivity", "normal"),
            )
            memory = memory_rag.confirm_candidate(
                db,
                candidate_id=candidate.id,
                confirmer=user,
                confirmer_account=account,
                scope="private",
                space_id=None,
            )
            labels[entry["label"]] = memory.id
            if "supersedes" in entry:
                supersedes.append((labels[entry["supersedes"]], memory.id))
        db.flush()
        for old_id, new_id in supersedes:
            memory_rag.supersede_memory(
                db, memory_id=old_id, account_id=account.id, by_memory_id=new_id
            )
        db.commit()

        # ---- 建向量索引（真实 embedding）----
        from app.services import rag_embeddings

        report = asyncio.run(
            embedding_index.run_index_pass(
                db,
                model=rag_embeddings.configured_model(),
                max_batches=50,
            )
        )
        db.commit()
        print(f"索引报告：{report}")

    with engine.connect() as conn:
        from sqlalchemy import text

        n_docs = conn.execute(
            text("SELECT count(*) FROM rag_documents WHERE status='active'")
        ).scalar_one()
        n_chunks = conn.execute(
            text("SELECT count(*) FROM rag_chunks WHERE status='active'")
        ).scalar_one()
        n_seg = conn.execute(text("SELECT count(*) FROM rag_embedding_segments")).scalar_one()
    engine.dispose()
    print(f"active 文档={n_docs} chunk={n_chunks} 分段向量={n_seg}")
    if n_seg == 0:
        print("FAIL: 未产生分段向量")
        return 1
    print("PASS: golden set 已用真实 embedding 建索引")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
