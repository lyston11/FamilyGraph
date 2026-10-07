"""后台 embedding 索引（增量、限速、可恢复）。

## 为什么文档向量必须**提前算**，不能每次提问现算

```text
资料确认 / revision 变化  ->  后台增量编码
用户提问                  ->  只编码这次问题
```

若每次提问都现算文档向量，成本随「候选文档数 × 提问次数」增长——每次提问都要
把整个候选集编码一遍。而文档集是**相对稳定**的，提问是**高频**的。分离后每次
提问只编码一个短查询。

## 三条安全性质

### 1. 后台工作让位给在线查询

通过 `embedding_client.embed_documents`（`priority="background"`）让服务端在排队时
优先服务在线请求。没有这个区分，一次全库重建会让所有在线查询排队到超时。

### 2. 小批次 + 让出时间

每批默认 4 条（与服务端 `EMBEDDING_MAX_BATCH` 一致），批间 sleep 让出 CPU。
否则后台索引会持续占用那 1 核，拖慢主服务。

### 3. 失败必须可恢复，且**不写半成品**

- embedding 降级（服务不可用）→ 本轮跳过，**不标记为已索引**，下一轮重试；
- 单条编码失败 → 记失败计数与退避，不阻塞其他条目；
- 服务不可用时**不推进游标**，否则未编码的条目会被永久跳过。

## 与 `index_version` 的关系

向量绑定 `(chunk_id, model)`。换模型时旧向量按 `model` 列识别，不需要重写业务表。
`model` 标识同时参与游标，使「换模型」表现为「所有条目都需要重新索引」，
而不是「旧向量被当成新模型的」。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import embedding_client

logger = logging.getLogger(__name__)

#: 每批条数。与服务端 `EMBEDDING_MAX_BATCH` 一致（默认 4）。
#:
#: 更大的批次会减少往返但增加单批内存与延迟；在这台 4 核机器上，4 是保守起点。
DEFAULT_BATCH_SIZE = 4

#: 批间间隔（秒）。让出 CPU 给主服务。
#:
#: 不是「随便等一会儿」：那 1 核 CPU 限额是与生产/开发共享的，连续编码会持续
#: 占用它。间隔让在线请求有机会插进来。
DEFAULT_BATCH_PAUSE_SECONDS = 0.2


@dataclass
class IndexReport:
    """一轮索引的结果（只含计数器，无文本）。"""

    examined: int = 0
    indexed: int = 0
    skipped_degraded: int = 0
    failed: int = 0
    batches: int = 0
    degraded_reason: str | None = None
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        return {
            "examined": self.examined,
            "indexed": self.indexed,
            "skipped_degraded": self.skipped_degraded,
            "failed": self.failed,
            "batches": self.batches,
            "degraded_reason": self.degraded_reason,
            "errors": self.errors[:5],
        }


def pending_chunks(
    db: Session,
    *,
    model: str,
    limit: int,
) -> list[dict[str, object]]:
    """取需要编码的 chunk：没有该 model 向量的、且状态为 active 的。

    ## 为什么只取 active

    撤权/删除的 chunk 不应产生新向量。注意：**已有向量不会因为撤权而自动消失**
    （实测确认），因此检索必须按 `status` 过滤——本函数只负责不**新增**无效向量。

    ## 为什么按 (chunk_id, model) 判重

    换模型时 `model` 变了，所有条目都会重新出现在待索引集合里。这是正确的：
    旧模型的向量对新模型没有意义。
    """
    rows = db.execute(
        text(
            """
            SELECT c.id AS chunk_id, c.document_id, c.text, c.index_version, c.chunk_index
              FROM rag_chunks AS c
              JOIN rag_documents AS d ON d.id = c.document_id
             WHERE c.status = 'active'
               AND d.status = 'active'
               AND NOT EXISTS (
                     SELECT 1 FROM rag_chunk_embeddings AS e
                      WHERE e.chunk_id = c.id AND e.model = :model
                   )
             ORDER BY c.id
             LIMIT :limit
            """
        ),
        {"model": model, "limit": limit},
    ).mappings()
    return [dict(row) for row in rows]


def store_vectors(
    db: Session,
    *,
    model: str,
    rows: list[dict[str, object]],
    vectors: list[list[float]],
) -> int:
    """写入向量。按 `(chunk_id, model)` 幂等（ON CONFLICT DO NOTHING）。

    ## 为什么幂等

    后台索引可能被中断后重跑。幂等让重跑安全，且不需要先查后写（那有竞态）。
    """
    if len(rows) != len(vectors):
        # 数量不匹配是内部错误：错位会把向量与错误的 chunk 绑定，检索结果
        # 张冠李戴且看似正常。fail-loud 而不是猜。
        raise ValueError(f"行数 {len(rows)} 与向量数 {len(vectors)} 不匹配")
    written = 0
    for row, vector in zip(rows, vectors, strict=True):
        literal = "[" + ",".join(f"{v:.7f}" for v in vector) + "]"
        cursor = db.execute(
            text(
                """
                INSERT INTO rag_chunk_embeddings
                       (chunk_id, model, revision, scope, embedding)
                SELECT c.id, :model, d.revision, d.scope, CAST(:vec AS vector)
                  FROM rag_chunks AS c
                  JOIN rag_documents AS d ON d.id = c.document_id
                 WHERE c.id = :chunk_id
                ON CONFLICT (chunk_id, model) DO NOTHING
                """
            ),
            {"model": model, "vec": literal, "chunk_id": row["chunk_id"]},
        )
        # `CursorResult` 才有 `rowcount`；`Result` 类型注解上没有该属性，
        # 因此显式取 CursorResult（mypy 无法从 execute 的重载推断）。
        written += int(getattr(cursor, "rowcount", 0) or 0)
    return written


async def run_index_pass(
    db: Session,
    *,
    model: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 1,
    pause_seconds: float = DEFAULT_BATCH_PAUSE_SECONDS,
) -> IndexReport:
    """跑一轮后台索引（有界批数）。

    ## 为什么是「一轮有限批数」而不是「一直跑到完」

    维护循环按 tick 调用本函数。让单次调用有界（`max_batches`）意味着：

    - 一次调用不会长时间占用那 1 核；
    - 控制权定期回到维护循环，使其他维护工作（reaper、settle）能执行；
    - 进度通过「下一轮继续」推进，而不是「一次做完」。

    ## 服务不可用时不推进游标

    降级时 `skipped_degraded` 递增并**立即返回**，不继续消耗批数。因为服务不可用时
    继续循环只会得到同样的降级结果，白白占用 tick 时间。未编码的条目下一轮会被
    重新选中（`NOT EXISTS` 判据），因此不会丢失。
    """
    report = IndexReport()
    if not embedding_client.enabled():
        report.degraded_reason = "disabled"
        return report

    for _ in range(max_batches):
        rows = pending_chunks(db, model=model, limit=batch_size)
        if not rows:
            break
        report.examined += len(rows)
        report.batches += 1

        texts = [str(row["text"] or "") for row in rows]
        result = await embedding_client.embed_documents(texts)
        if not result.usable:
            report.degraded_reason = result.reason
            report.skipped_degraded += len(rows)
            # 不推进：下一轮重新选中同一批。服务恢复后继续。
            break

        assert result.vectors is not None
        try:
            written = store_vectors(db, model=model, rows=rows, vectors=result.vectors)
            db.commit()
            report.indexed += written
        except Exception as exc:  # noqa: BLE001 - 单批失败不应中止整轮
            db.rollback()
            report.failed += len(rows)
            report.errors.append(type(exc).__name__)
            logger.warning("embedding 索引批次写入失败 error_class=%s", type(exc).__name__)
            # 继续下一批：单批失败（例如某行被并发删除）不应阻塞其余条目。
            continue

        if pause_seconds > 0:
            # 让出 CPU：那 1 核限额是与生产/开发共享的。
            await asyncio.sleep(pause_seconds)

    return report
