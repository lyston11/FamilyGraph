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
import os
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import embedding_chunking, embedding_client

logger = logging.getLogger(__name__)

#: 每批条数。与服务端 `EMBEDDING_MAX_BATCH` 一致（默认 4）。
#:
#: 更大的批次会减少往返但增加单批内存与延迟；在这台 4 核机器上，4 是保守起点。
DEFAULT_BATCH_SIZE = 4

#: 单次 `pending_chunks` 最多扫描多少个 chunk 候选。
#:
#: 已完整的 chunk 也要被扫描（完整性判定在 Python 里），因此需要一个上界，
#: 否则「大量已完整 chunk」会让单次调用扫描整表。
_DEFAULT_MAX_SCAN = 500

#: 分页大小（keyset 分页，按 id）。
_SCAN_PAGE = 200

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
    segments_written: int = 0
    skipped_degraded: int = 0
    failed: int = 0
    batches: int = 0
    degraded_reason: str | None = None
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        return {
            "examined": self.examined,
            "indexed": self.indexed,
            "segments_written": self.segments_written,
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
    algorithm: str,
    limit: int,
    max_scan: int = _DEFAULT_MAX_SCAN,
) -> list[dict[str, object]]:
    """取需要编码的 chunk：该 model+algorithm 下尚无**完整**分段集合的 active chunk。

    ## 为什么只取 active

    撤权/删除的 chunk 不应产生新向量。注意：**已有向量不会因为撤权而自动消失**
    （实测确认），因此检索必须按 `status` 过滤——本函数只负责不**新增**无效向量。

    ## 为什么按 (model, algorithm) 判重，而不是「有没有任意分段」

    换模型或换切分算法都会产生不同的分段集合。若只判「有没有分段」，旧算法留下的
    分段会让该 chunk 永远不再被索引——检索用的却是旧分段。

    这里用**分段数是否等于期望值**判定完整性：先把 chunk 切分算出期望分段数，
    再与已存分段数比较。这同时覆盖两种情况：

    - 完全未索引（0 个分段）；
    - 索引**中断**（部分分段已写）。后者若只判「> 0」，残缺集合会被当作已完成。

    ## 为什么在 Python 里切分而不是在 SQL 里

    切分逻辑（句边界、重叠、硬切）是业务规则，必须与 `embedding_chunking` 同一
    实现。放在 SQL 里会形成第二套实现，两边漂移后无法发现。
    """
    # ## 为什么必须分页扫描而不是 `LIMIT :limit`
    #
    # 完整性判定需要「期望分段数」，而那要先切分文本——切分逻辑在 Python 里
    # （必须与 `embedding_chunking` 同一实现，见上文）。因此无法在 SQL 里过滤。
    #
    # 若直接用 `LIMIT :limit` 取候选，**已完整的 chunk 会反复填满批次**：
    # 第 1 批索引 chunk 1..N，第 2 批的 SQL 仍返回 1..N（它们按 id 最小），
    # 在 Python 里被全部过滤掉 → `pending` 为空 → 循环 break → **后面的 chunk
    # 永远不会被索引**。实测就是这个表现：只索引了 2 个 chunk 就停了。
    #
    # 修法：按 id 做 keyset 分页，持续扫描直到凑够 `limit` 个待索引 chunk，
    # 或候选耗尽。`max_scan` 保证单次调用有界（不因大量已完整 chunk 而扫描整表）。
    pending: list[dict[str, object]] = []
    cursor = 0
    scanned = 0
    while len(pending) < limit and scanned < max_scan:
        page = min(_SCAN_PAGE, max_scan - scanned)
        rows = (
            db.execute(
                text(
                    """
                SELECT c.id AS chunk_id, c.document_id, c.text, c.index_version,
                       c.chunk_index,
                       COALESCE(
                           (SELECT count(*) FROM rag_embedding_segments AS s
                             WHERE s.chunk_id = c.id AND s.model = :model
                               AND s.algorithm = :algorithm),
                           0
                       ) AS existing_segments
                  FROM rag_chunks AS c
                  JOIN rag_documents AS d ON d.id = c.document_id
                 WHERE c.status = 'active'
                   AND d.status = 'active'
                   AND c.id > :cursor
                 ORDER BY c.id
                 LIMIT :page
                """
                ),
                {
                    "model": model,
                    "algorithm": algorithm,
                    "cursor": cursor,
                    "page": page,
                },
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        for row in rows:
            cursor = int(str(row["chunk_id"]))
            scanned += 1
            item = dict(row)
            segments = embedding_chunking.segment_text(str(item["text"] or ""))
            if len(segments) != int(str(item["existing_segments"])):
                item["expected_segments"] = segments
                pending.append(item)
                if len(pending) >= limit:
                    break
    return pending


def store_segments(
    db: Session,
    *,
    model: str,
    algorithm: str,
    row: dict[str, object],
    vectors: list[list[float]],
) -> int:
    """写入一个 chunk 的分段向量。按 `(chunk_id, model, segment_index)` 幂等。

    ## 为什么先删后写

    期望分段数可能与已存不同（中断后重跑、算法换版）。先删除该 `(chunk_id, model)`
    的旧分段再插入，使集合恰好等于期望——否则残留的旧分段会让「分段数」永远对不上，
    该 chunk 每轮都被重新索引（无限循环）。

    ## 为什么幂等（ON CONFLICT DO NOTHING）

    同一事务内先删后插已经保证唯一性，但并发（两个 worker 同 chunk）下仍可能撞主键。
    `DO NOTHING` 让并发安全：重复插入不报错，`rowcount` 如实反映实际写入数。
    """
    segments: list[embedding_chunking.Segment] = row["expected_segments"]  # type: ignore[assignment]
    if len(segments) != len(vectors):
        # 数量不匹配是内部错误：错位会把向量与错误的分段绑定，检索结果张冠李戴
        # 且看似正常。fail-loud 而不是猜。
        raise ValueError(f"分段数 {len(segments)} 与向量数 {len(vectors)} 不匹配")

    # `dict[str, object]` 的取值类型是 `object`，mypy 无法从重载推断 `int(...)`
    # 接受它。显式断言而不是 ignore：断言在运行期也成立，且不掩盖真实类型错误。
    chunk_id = int(str(row["chunk_id"]))
    db.execute(
        text("DELETE FROM rag_embedding_segments" " WHERE chunk_id = :chunk_id AND model = :model"),
        {"chunk_id": chunk_id, "model": model},
    )

    written = 0
    for segment, vector in zip(segments, vectors, strict=True):
        literal = "[" + ",".join(f"{v:.7f}" for v in vector) + "]"
        cursor = db.execute(
            text(
                """
                INSERT INTO rag_embedding_segments
                       (chunk_id, model, segment_index, algorithm,
                        char_start, char_end, revision, scope, embedding)
                SELECT c.id, :model, :segment_index, :algorithm,
                       :char_start, :char_end, d.revision, d.scope, CAST(:vec AS vector)
                  FROM rag_chunks AS c
                  JOIN rag_documents AS d ON d.id = c.document_id
                 WHERE c.id = :chunk_id
                ON CONFLICT (chunk_id, model, segment_index) DO NOTHING
                """
            ),
            {
                "model": model,
                "segment_index": segment.index,
                "algorithm": algorithm,
                "char_start": segment.char_start,
                "char_end": segment.char_end,
                "vec": literal,
                "chunk_id": chunk_id,
            },
        )
        written += int(getattr(cursor, "rowcount", 0) or 0)
    return written


async def run_index_pass(
    db: Session,
    *,
    model: str,
    algorithm: str = embedding_chunking.SEGMENT_ALGORITHM,
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
        rows = pending_chunks(db, model=model, algorithm=algorithm, limit=batch_size)
        if not rows:
            break
        report.examined += len(rows)
        report.batches += 1

        # 按 chunk 逐个处理：一个 chunk 的所有分段必须在**一次** embed 调用里编码，
        # 才能保证向量与分段一一对应。跨 chunk 混批会让数量核对变复杂，
        # 且单 chunk 的分段数（默认上限 64）可能已经接近服务端批次上限。
        for row in rows:
            segments: list[embedding_chunking.Segment] = row["expected_segments"]  # type: ignore[assignment]
            texts = [segment.text for segment in segments]
            if not texts:
                continue
            result = await embedding_client.embed_documents(texts)
            if not result.usable:
                report.degraded_reason = result.reason
                report.skipped_degraded += 1
                # 不推进：下一轮重新选中同一 chunk。服务恢复后继续。
                break

            assert result.vectors is not None
            try:
                written = store_segments(
                    db,
                    model=model,
                    algorithm=algorithm,
                    row=row,
                    vectors=result.vectors,
                )
                db.commit()
                report.indexed += 1
                report.segments_written += written
            except Exception as exc:  # noqa: BLE001 - 单 chunk 失败不应中止整轮
                db.rollback()
                report.failed += 1
                report.errors.append(type(exc).__name__)
                logger.warning("embedding 索引写入失败 error_class=%s", type(exc).__name__)
                # 继续下一个 chunk：单个失败（例如该行被并发删除）不应阻塞其余。
                continue

            if pause_seconds > 0:
                # 让出 CPU：那 1 核限额是与生产/开发共享的。
                await asyncio.sleep(pause_seconds)

        if report.degraded_reason is not None:
            # 服务降级时不再继续下一批：只会得到同样的降级结果，白白占用 tick。
            break

    return report


def model_identity() -> str:
    """当前 embedding 模型的标识，用作向量行的 `model` 列。

    ## 为什么是「provider:model」而不是只有 model 名

    换 provider（本地 ONNX → 远程 API）但模型名相同时，向量**不可互换**
    （不同实现的分词、池化、精度都可能不同）。把 provider 编进标识使换 provider
    表现为「所有条目需要重新索引」，而不是「旧向量被当成新 provider 的」。

    未配置时返回空串，调用方（维护循环）应先检查 `embedding_client.enabled()`。
    """
    provider = os.environ.get("RAG_EMBEDDING_PROVIDER", "local").strip() or "local"
    model = os.environ.get("RAG_EMBEDDING_MODEL", "").strip()
    if not model:
        return ""
    return f"{provider}:{model}"
