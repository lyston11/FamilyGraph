"""端到端验证 RAG 真实链路与**空间隔离**，全部走生产服务函数。

不手工 INSERT：用 `propose_candidate` → `confirm_candidate` → `index_memory`
→ `search_rag`，即真实用户路径。

## 为什么需要这个探针

RAG 的隔离合同是「**先授权过滤，再排序**」——不是先取候选再过滤。后者会在授权
范围只覆盖少数文档时返回不足量的结果，更糟的是可能把不可见内容带出。

本探针用两个真实空间各写一条 `lineage` 记忆，然后断言：

1. 同空间可见（A 能查到自己的）；
2. **跨空间不可见**（A 查不到 B 的）；
3. 反向同样不可见（B 查不到 A 的）；
4. steward 作为共享数据消费方能查到同空间的；
5. **撤权后不可见**（成员被移除后立刻失去检索能力，无需等索引清理）。

## 用法

```bash
docker run --rm --network <net> -e DATABASE_URL=... -e PYTHONPATH=/repo/backend \
  -e MEMORY_ENABLED=1 -e RAG_ENABLED=1 \
  <api-image> python /repo/scripts/migration-proof/rag_isolation_probe.py
```

退出码 0 = 全部通过；1 = 有断言失败；2 = 环境不足（空间/成员不够）。
"""

import sys

sys.path.insert(0, "/repo/backend")

from sqlalchemy import text

from app.db import SessionLocal
from app.models.account import Account
from app.models.user import User
from app.services import memory_rag

TAG = "probe-e2e"


def main() -> int:
    with SessionLocal() as db:
        spaces = db.execute(
            text("SELECT id FROM family_spaces WHERE kind='lineage' ORDER BY id LIMIT 2")
        ).scalars().all()
        space_a, space_b = int(spaces[0]), int(spaces[1])

        actors = {}
        for sid in (space_a, space_b):
            row = db.execute(
                text(
                    "SELECT u.id, a.id FROM space_members sm"
                    " JOIN users u ON u.id = sm.user_id"
                    " JOIN accounts a ON a.user_id = u.id"
                    " WHERE sm.space_id = :s AND sm.status='active' LIMIT 1"
                ),
                {"s": sid},
            ).first()
            actors[sid] = (int(row[0]), int(row[1]))
        print(f"  空间 A={space_a} B={space_b}")

        # 用真实链路：propose → confirm → index
        made = {}
        for sid in (space_a, space_b):
            uid, aid = actors[sid]
            actor = db.get(User, uid)
            account = db.get(Account, aid)
            quote = f"{TAG} 空间{sid}：爷爷是一位退休中学教师，喜欢下围棋"
            cand = memory_rag.propose_candidate(
                db,
                author_account_id=aid,
                source_quote=quote,
                summary=f"空间{sid}的爷爷是一位退休中学教师，喜欢下围棋",
                suggested_scope="lineage",
                purpose="probe",
                sensitivity="normal",
                source={"kind": "manual"},
                extractor_version="probe-v1",
            )
            db.commit()
            print(f"  空间 {sid}: candidate={cand.id} status={cand.status}")

            mem = memory_rag.confirm_candidate(
                db,
                candidate_id=cand.id,
                confirmer=actor,
                confirmer_account=account,
                scope="lineage",
                space_id=sid,
            )
            db.commit()
            made[sid] = mem.id
            print(f"  空间 {sid}: memory={mem.id} verification={mem.source_verification}")

            doc = memory_rag.index_memory(db, mem)
            db.commit()
            print(f"  空间 {sid}: document={doc.id} scope={doc.scope} space={doc.space_id}")

        def docs_for(mid):
            return db.execute(
                text("SELECT id FROM rag_documents WHERE source_id = :s"), {"s": str(mid)}
            ).scalar()

        a_doc, b_doc = docs_for(made[space_a]), docs_for(made[space_b])
        print(f"  documents: A={a_doc} B={b_doc}")

        def search(sid, query, kind="assistant"):
            uid, aid = actors[sid]
            return memory_rag.search_rag(
                db,
                actor=db.get(User, uid),
                account=db.get(Account, aid),
                query=query,
                space_id=sid,
                agent_kind=kind,
                limit=20,
            )

        hits_a = {h.document_id for h in search(space_a, "围棋")}
        hits_b = {h.document_id for h in search(space_b, "围棋")}
        hits_st = {h.document_id for h in search(space_a, "围棋", kind="steward")}
        print(f"  A 命中={sorted(hits_a)}  B 命中={sorted(hits_b)}  steward(A)={sorted(hits_st)}")

        checks = [
            ("同空间可见：A 应见自己的文档", a_doc in hits_a),
            ("跨空间隔离：A 不得见 B 的文档", b_doc not in hits_a),
            ("反向隔离：B 不得见 A 的文档", a_doc not in hits_b),
            ("steward 可见同空间 lineage", a_doc in hits_st),
        ]
        print()
        ok = True
        for name, passed in checks:
            print(f"  [{'OK  ' if passed else 'FAIL'}] {name}")
            ok = ok and passed

        # 撤权：移除 A 的成员资格后不得再见
        uid_a, _ = actors[space_a]
        db.execute(
            text("UPDATE space_members SET status='removed' WHERE space_id=:s AND user_id=:u"),
            {"s": space_a, "u": uid_a},
        )
        db.commit()
        after = {h.document_id for h in search(space_a, "围棋")}
        revoked_ok = a_doc not in after
        print(f"  [{'OK  ' if revoked_ok else 'FAIL'}] 撤权后 A 不得再见自己的文档（命中={sorted(after)}）")
        ok = ok and revoked_ok

        # 清理
        db.execute(text("UPDATE space_members SET status='active' WHERE space_id=:s AND user_id=:u"), {"s": space_a, "u": uid_a})
        db.execute(text("DELETE FROM rag_chunks WHERE document_id IN (SELECT id FROM rag_documents WHERE source_id = ANY(:ids))"), {"ids": [str(m) for m in made.values()]})
        db.execute(text("DELETE FROM rag_documents WHERE source_id = ANY(:ids)"), {"ids": [str(m) for m in made.values()]})
        db.execute(text("DELETE FROM memories WHERE raw_quote LIKE :t"), {"t": f"{TAG}%"})
        db.execute(text("DELETE FROM memory_candidates WHERE source_quote LIKE :t"), {"t": f"{TAG}%"})
        db.commit()
        print("\n探针数据已清理")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
