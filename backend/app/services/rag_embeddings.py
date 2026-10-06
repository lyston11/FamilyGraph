"""RAG 语义检索的 embedding 抽象与 pgvector 候选（C6 剩余项）。

## 为什么需要 provider 抽象

代码库此前**没有**任何 embedding 能力（`app/services/` 下 grep 无结果，config 里也没有
相关项）。因此接入 pgvector 必须先定义：

1. **谁算 embedding**：模型、维度、是否本地；
2. **算完放哪**：与 chunk/revision/scope 绑定的存储；
3. **怎么检索**：filter-then-ANN（不是 post-filter）。

本模块负责 1 与 3 的接口；存储由 `rag_chunk_embeddings` 承担（见 `PGVECTOR_DDL`）。

## 安全边界（与词法路径同源）

**embedding 不承载授权**。实测（`10-03-pgvector-rag` 的 `pgvector-lifecycle-probe.md`）：
撤权后行与向量都还在，可见性完全依赖查询层的 `status`/`scope`/`revision` 过滤。

## filter-then-ANN 而不是 post-filter（本模块最重要的设计）

实测：post-filter 在低选择性下**静默返回不足 k**——允许 1/10 空间时只剩 1 条
（应为 10）。而 RAG **无法区分**「无相关内容」与「被授权过滤掉」，因此这个静默
缺失会表现为「检索不到」，用户与运维都无从发现。

因此正确形态是：

```text
先按授权过滤出候选集合（CTE / 子查询）
再在该集合上做 ANN 排序
```

代价是 ANN 索引只能用于过滤后的集合（可能退化为顺序扫描），因此**必须 over-fetch**
并在过滤后取 top-k。这比「静默少返回」正确。

## 未配置时 fail-closed

`embedding_status` 已经存在于 `rag_chunks`（CHECK 枚举含 `disabled`/`not_configured`）。
未配置 embedding provider 时**不伪造命中**，也不退化为「用随机向量假装语义检索」——
返回空候选，由词法路径承担检索。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text


class EmbeddingUnavailable(Exception):
    """embedding provider 未配置或不可用。

    调用方必须**回退到词法路径**，不得伪造向量命中，也不得把「不可用」当成
    「无相关内容」（那会让用户以为确实没有资料）。
    """


@dataclass(frozen=True)
class EmbeddingConfig:
    """embedding 的部署配置。

    `dimension` 必须与数据库列的 `vector(N)` 一致：不一致会让插入失败（fail-loud），
    这是刻意的——静默截断或补齐会让向量语义错乱且难以发现。
    """

    provider: str
    model: str
    dimension: int

    @property
    def configured(self) -> bool:
        return bool(self.provider) and bool(self.model) and self.dimension > 0


def load_config() -> EmbeddingConfig:
    """从环境读取配置。未配置时返回 `configured=False`（fail-closed，不报错）。

    不在这里抛异常：RAG 可能在未启用语义检索的部署上运行，那是合法状态。
    真正需要 embedding 时（索引或检索）才由调用方检查 `configured`。
    """
    return EmbeddingConfig(
        provider=os.environ.get("RAG_EMBEDDING_PROVIDER", "").strip(),
        model=os.environ.get("RAG_EMBEDDING_MODEL", "").strip(),
        dimension=int(os.environ.get("RAG_EMBEDDING_DIMENSION", "0") or "0"),
    )


class Embedder(Protocol):
    """算 embedding 的最小接口。

    实现方可以是远程 API（经 Provider gateway）或本地模型。协议只要求
    「给定文本，返回定长向量」，因此存储与检索代码不依赖具体实现。
    """

    @property
    def dimension(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class DeterministicEmbedder:
    """**测试与开发**用的确定性 embedder。

    ## 它不是生产语义

    它用 token 哈希投影到固定维度，因此**没有语义相似性**——"叔叔" 与 "伯父"
    不会被判为相近。它的用途只有一个：让存储、filter-then-ANN、union/rerank
    的**结构**可以在无外部依赖的情况下被验证。

    ## 为什么不能当生产实现

    若把它当生产 embedder，语义检索会退化成「字面重合度检索」——比词法路径更差，
    却让系统看起来「已启用向量检索」。因此 `load_config()` 不会返回它；
    只有显式构造才会得到。
    """

    dimension: int = 64

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        # 循环变量不叫 `text`：那会遮蔽 sqlalchemy 的 `text()`（ruff F402）。
        for item in texts:
            vector = [0.0] * self.dimension
            for token in item.split():
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                for i in range(0, min(len(digest), self.dimension)):
                    vector[i] += (digest[i] / 255.0) - 0.5
            norm = sum(v * v for v in vector) ** 0.5
            out.append([v / norm for v in vector] if norm else vector)
        return out


def resolve_embedder(config: EmbeddingConfig) -> Embedder:
    """按配置解析 embedder。

    当前只登记 `none`（未配置）。**新增 provider 必须同时补**：

    1. 一个真实实现（不得复用 `DeterministicEmbedder`——见其文档）；
    2. 维度校验（与 `vector(N)` 一致）；
    3. 超时与失败分类（provider 超时必须可归因，不得当成「无结果」）。
    """
    if not config.configured:
        raise EmbeddingUnavailable(
            "RAG_EMBEDDING_PROVIDER / RAG_EMBEDDING_MODEL / RAG_EMBEDDING_DIMENSION "
            "未配置：语义检索不可用，调用方必须回退到词法路径"
        )
    raise EmbeddingUnavailable(
        f"embedding provider {config.provider!r} 尚未实现；"
        "不得用 DeterministicEmbedder 替代（它没有语义相似性）"
    )


#: pgvector 存储的 DDL。
#:
#: ## 为什么列维度是动态的
#:
#: `vector(N)` 的 N 必须与 embedder 的维度一致。不一致时插入会失败——这是
#: **刻意**的 fail-loud：静默截断或补齐会让向量语义错乱且难以发现。
#:
#: ## 为什么与 chunk 分离而不是同表
#:
#: 同表会让「换 embedding 模型」变成「重写业务表」。分离后换模型只影响本表，
#: 且旧向量可按 `model` 列识别与清理。
#:
#: ## 为什么带 revision / scope
#:
#: 与 `rag_chunks` 同源：撤权后向量**不会**自动消失（实测），因此检索必须
#: 按这些列过滤，而不是依赖向量被删除。
def pgvector_ddl(dimension: int) -> str:
    if dimension <= 0:
        raise ValueError("dimension 必须为正")
    return f"""
CREATE TABLE IF NOT EXISTS rag_chunk_embeddings (
  chunk_id int NOT NULL REFERENCES rag_chunks(id) ON DELETE CASCADE,
  model text NOT NULL,
  revision int NOT NULL,
  scope text NOT NULL,
  embedding vector({dimension}) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id, model)
);
-- HNSW 用于 ANN。注意它**不**承担授权：过滤条件在查询里，见 build_vector_candidates。
CREATE INDEX IF NOT EXISTS ix_rag_chunk_embeddings_hnsw
  ON rag_chunk_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_rag_chunk_embeddings_scope
  ON rag_chunk_embeddings (model, revision);
""".strip()


def build_vector_candidates(
    *,
    dimension: int,
    eligibility: str,
    over_fetch: int = 8,
) -> Any:
    """构造 **filter-then-ANN** 的向量候选 SQL。

    ## 为什么不是 `ORDER BY embedding <=> :q LIMIT k` 然后过滤

    实测（`10-03-pgvector-rag`）：post-filter 在低选择性下静默返回不足 k——
    允许 1/10 空间时只剩 1 条（应为 10）。RAG **无法区分**「无相关内容」与
    「被授权过滤掉」，因此这个静默缺失会表现为「检索不到」，无人能发现。

    ## 形态

    ```sql
    WITH authorized AS (           -- ① 先按授权过滤
      SELECT ... FROM rag_chunk_embeddings e JOIN rag_chunks c ...
      WHERE <eligibility>
    )
    SELECT ... FROM authorized     -- ② 再在授权集合上做 ANN
    ORDER BY embedding <=> :q
    LIMIT :limit
    ```

    ## over_fetch

    过滤后集合可能远小于全表，因此 `LIMIT` 需要比调用方要的 k 大（默认 8×），
    由调用方在 union/rerank 阶段裁到 k。这是「宁可多取再裁」而不是「可能静默少返回」。
    """
    if dimension <= 0:
        raise ValueError("dimension 必须为正")
    if over_fetch < 1:
        raise ValueError("over_fetch 必须 >= 1")
    return text(f"""
        WITH authorized AS (
            SELECT e.chunk_id, e.embedding,
                   c.document_id, c.text, c.index_version, c.chunk_index,
                   c.source_revision, c.status,
                   d.source_type, d.source_id, d.scope, d.sensitivity, d.revision
              FROM rag_chunk_embeddings e
              JOIN rag_chunks c ON c.id = e.chunk_id
              JOIN rag_documents d ON d.id = c.document_id
             WHERE {eligibility}
        )
        SELECT chunk_id, document_id, text, index_version, chunk_index, source_revision,
               source_type, source_id, scope, sensitivity, revision,
               (embedding <=> CAST(:query_vector AS vector)) AS distance
          FROM authorized
         ORDER BY distance ASC, chunk_id ASC
         LIMIT :limit OFFSET :offset
    """)


def fuse_candidates(
    *,
    lexical: list[dict[str, Any]],
    vector: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """把词法与向量候选按**确定性规则**融合。

    ## 为什么用 RRF 而不是比较分数

    词法的 `bm25`（越小越好）与向量的余弦距离（越小越好）**不同量纲**，
    直接比较或加权会随数据分布漂移，且不可复现。RRF（Reciprocal Rank Fusion）
    只用**名次**：

    ```text
    score(d) = Σ 1 / (k + rank_i(d))     k = 60（标准取值）
    ```

    这保证同一输入永远给出同一顺序（可复现），且不依赖两路分数的可比性。

    ## 去重

    同一 chunk 可能同时被两路命中：按 `chunk_id` 合并，分数相加。
    这使「两路都命中」的候选排在只被一路命中的前面——符合直觉且确定性。
    """
    if limit < 1:
        raise ValueError("limit 必须 >= 1")
    K = 60
    scores: dict[int, float] = {}
    payload: dict[int, dict[str, Any]] = {}

    for rank, hit in enumerate(lexical):
        chunk_id = int(hit["chunk_id"])
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (K + rank + 1)
        payload.setdefault(chunk_id, hit)
    for rank, hit in enumerate(vector):
        chunk_id = int(hit["chunk_id"])
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (K + rank + 1)
        payload.setdefault(chunk_id, hit)

    # 排序键：(分数降序, chunk_id 升序)。加 chunk_id 保证同分时顺序确定——
    # 否则同分候选的相对顺序取决于字典插入顺序，不可复现。
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [payload[chunk_id] for chunk_id, _score in ordered[:limit]]
