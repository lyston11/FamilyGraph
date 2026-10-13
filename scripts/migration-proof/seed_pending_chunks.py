"""造出大量待索引 chunk，使维护循环真正忙于 embedding。

## 用途

`background_budget_probe.py` 需要「embedding 忙」的采样才能做对比。开发库上
`gate.active` 恒为 0（没有待索引 chunk），因此该对比做不出来。

本脚本在隔离库上灌入 N 条 memory（每条产生 1 个文档 + 若干 chunk），
使维护循环的 `run_index_pass` 有活干，从而产生可观测的 embedding 负载。

## 安全护栏

要求 `PGTEST_DSN` 指向隔离库；拒绝含 `sqlite` 的 DSN。
不接触开发库/生产——调用方负责把 DSN 指向隔离实例。

## 用法

    PGTEST_DSN=postgresql+psycopg://postgres:acc@127.0.0.1:55460/familygraph \
        python3 scripts/migration-proof/seed_pending_chunks.py --count 2000
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = (
    Path(os.environ["REPO_ROOT"])
    if os.environ.get("REPO_ROOT")
    else Path(__file__).resolve().parents[2]
)

#: 合成语料模板（不含真实个人数据）。
_TEMPLATES = [
    "家里的老照片放在书房抽屉里，是外婆年轻时候拍的",
    "每年清明全家一起去山上扫墓，爷爷会讲很多过去的事情",
    "叔叔家的孩子今年考上了大学，学的是计算机专业",
    "舅妈擅长做红烧肉，逢年过节都会做一大锅",
    "外婆喜欢在院子里种花，最喜欢的是月季和桂花",
    "表哥在南方工作，每年只有春节才回来一次",
    "奶奶留下的手写菜谱，记录了三十多道家常菜",
    "小姨家的女儿学钢琴，已经考过了八级",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=500, help="灌入的 memory 条数")
    parser.add_argument("--batch", type=int, default=200, help="每批提交条数")
    args = parser.parse_args()

    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL")
        return 2
    if "sqlite" in dsn:
        print("SKIP: 需要 PostgreSQL")
        return 2

    sys.path.insert(0, str(ROOT / "backend"))
    os.environ.setdefault("DATA_DIR", "/tmp/fg-seed-pending")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.services import memory_rag

    engine = create_engine(dsn, future=True)

    with Session(engine) as db:
        # 复用已有的 owner（若存在），否则报错提示先跑 seed_golden_embeddings。
        from sqlalchemy import select

        from app.models.account import Account
        from app.models.user import User

        row = db.execute(
            select(User, Account).join(Account, Account.user_id == User.id).limit(1)
        ).first()
        if row is None:
            print(
                "FAIL: 库中没有 user+account；先跑 seed_golden_embeddings.py 或 baseline"
            )
            return 1
        owner, account = row

        started = time.perf_counter()
        created = 0
        for i in range(args.count):
            text = f"{_TEMPLATES[i % len(_TEMPLATES)]}（编号 {i}）"
            candidate = memory_rag.propose_candidate(
                db,
                author_account_id=account.id,
                source={"kind": "manual"},
                source_quote=text,
                summary=text,
                suggested_scope="private",
                purpose="pending-chunk seed",
                sensitivity="normal",
            )
            memory_rag.confirm_candidate(
                db,
                candidate_id=candidate.id,
                confirmer=owner,
                confirmer_account=account,
                scope="private",
                space_id=None,
            )
            created += 1
            if created % args.batch == 0:
                db.commit()
                print(f"  {created}/{args.count} …", flush=True)
        db.commit()
        elapsed = time.perf_counter() - started

    with engine.connect() as conn:
        from sqlalchemy import text

        pending = conn.execute(
            text(
                "SELECT count(*) FROM rag_chunks c JOIN rag_documents d ON d.id=c.document_id"
                " WHERE c.status='active' AND d.status='active'"
            )
        ).scalar_one()
        indexed = conn.execute(
            text("SELECT count(*) FROM rag_embedding_segments")
        ).scalar_one()
    engine.dispose()

    print(f"\n已灌入 {created} 条 memory，耗时 {elapsed:.1f}s")
    print(
        f"active chunk={pending}，已索引分段={indexed}，待索引 chunk≈{pending - indexed}"
    )
    print("PASS: 待索引语料已建立，维护循环将产生 embedding 负载")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
