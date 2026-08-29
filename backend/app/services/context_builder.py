"""ContextBuilder: policy-filtered, budgeted model context with provenance.

``context_hook`` accepts only the already fetched ``ContextSource`` values.  It
therefore cannot perform a database query in the hot path; the internal run
context endpoint is responsible for prefetching them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app import config
from app.errors import POLICY_CONTEXT_INVALID, POLICY_LOCAL_REQUIRED, raise_api_error
from app.models.context import ContextBuild, ContextBuildItem
from app.models.user import User
from app.services.memory_rag import RAGHit, query_hash, search_rag
from app.utils.timeutil import utcnow


@dataclass(frozen=True)
class ContextSource:
    source_type: str
    source_id: str
    text: str
    scope: str
    sensitivity: str
    revision: int
    citation_handle: str
    trust: str = "untrusted_data"

    @classmethod
    def from_hit(cls, hit: RAGHit) -> ContextSource:
        return cls(
            source_type=hit.source_type,
            source_id=hit.source_id,
            text=hit.text,
            scope=hit.scope,
            sensitivity=hit.sensitivity,
            revision=hit.revision,
            citation_handle=hit.citation_handle,
        )


@dataclass(frozen=True)
class BuiltContext:
    build_id: int | None
    identity: dict[str, Any]
    sources: tuple[ContextSource, ...]
    excluded: tuple[dict[str, Any], ...]
    provider_policy: str

    def as_data_blocks(self) -> list[dict[str, Any]]:
        """Serialize sources as data, never as system/tool instructions."""
        return [
            {
                "kind": "data",
                "trust": source.trust,
                "source_type": source.source_type,
                "source_id": source.source_id,
                "scope": source.scope,
                "sensitivity": source.sensitivity,
                "revision": source.revision,
                "citation": source.citation_handle,
                "content": source.text,
            }
            for source in self.sources
        ]


class ContextBuilder:
    """Build context in fixed order: identity → policy → rank → budget."""

    def __init__(self, db: Session | None = None):
        self.db = db

    def build(
        self,
        *,
        actor: User,
        space_id: int,
        agent_kind: str,
        query: str,
        run_id: int | None = None,
        prefetched: Iterable[ContextSource] | None = None,
        token_budget: int = 2000,
        provider_kind: str | None = None,
        policy_version: str = config.POLICY_VERSION,
    ) -> BuiltContext:
        if token_budget < 1 or token_budget > 32_000:
            raise_api_error(422, POLICY_CONTEXT_INVALID, "token_budget 超出范围")
        if agent_kind not in ("assistant", "steward"):
            raise_api_error(422, POLICY_CONTEXT_INVALID, "agent kind 不受支持")
        # Prefetched input is mandatory for a context hook.  DB-backed prefetch
        # is available only on this builder/service boundary.
        if prefetched is None:
            if self.db is None:
                raise_api_error(500, POLICY_CONTEXT_INVALID, "context 缺少预取数据")
            # RAG 关闭时助手仍需可运行：不把会话全文当隐式补偿上下文，只产出空
            # context（结构化 Assistant 工具路径保留）。RAG 检索由浏览器面
            # /rag/search 端点独立门禁（503 RAG_DISABLED），此处不硬阻断 Run。
            if config.RAG_ENABLED:
                sources = tuple(
                    ContextSource.from_hit(hit)
                    for hit in search_rag(
                        self.db,
                        actor=actor,
                        account=actor.account,
                        space_id=space_id,
                        query=query,
                        agent_kind=agent_kind,
                        provider_kind=provider_kind,
                        raise_on_restricted=True,
                    )
                )
            else:
                sources = ()
        else:
            sources = tuple(prefetched)
        if any(source.sensitivity in ("high", "local_required") for source in sources):
            if provider_kind != "local":
                raise_api_error(409, POLICY_LOCAL_REQUIRED, "当前 Context 要求本地 Provider")
        identity = {
            "actor_user_id": actor.id,
            "space_id": space_id,
            "agent_kind": agent_kind,
        }
        included: list[ContextSource] = []
        excluded: list[dict[str, Any]] = []
        used = 0
        for _rank, source in enumerate(sources):
            if source.trust != "untrusted_data":
                excluded.append({"source_id": source.source_id, "reason": "invalid_trust"})
                continue
            if agent_kind == "steward" and source.scope == "private":
                excluded.append({"source_id": source.source_id, "reason": "private_for_steward"})
                continue
            if source.sensitivity == "local_required" and provider_kind != "local":
                excluded.append({"source_id": source.source_id, "reason": "local_required"})
                continue
            estimate = max(1, len(source.text) // 4)
            if used + estimate > token_budget:
                excluded.append({"source_id": source.source_id, "reason": "token_budget"})
                continue
            included.append(source)
            used += estimate
        provider_policy = "allowed"
        if any(s.sensitivity in ("high", "local_required") for s in included):
            provider_policy = "local_required"

        build_id: int | None = None
        if self.db is not None and run_id is not None:
            build = ContextBuild(
                run_id=run_id,
                account_id=actor.account.id,
                space_id=space_id,
                agent_kind=agent_kind,
                query_hash=query_hash(query),
                policy_version=policy_version,
                token_budget=token_budget,
                created_at=utcnow(),
            )
            self.db.add(build)
            self.db.flush()
            build_id = build.id
            included_ids = {id(source) for source in included}
            for rank, source in enumerate(sources):
                estimate = max(1, len(source.text) // 4)
                reason = next(
                    (item["reason"] for item in excluded if item["source_id"] == source.source_id),
                    None,
                )
                self.db.add(
                    ContextBuildItem(
                        build_id=build.id,
                        source_type=source.source_type,
                        source_id=source.source_id,
                        citation_handle=source.citation_handle,
                        included=id(source) in included_ids,
                        exclusion_reason=reason,
                        rank=rank if id(source) in included_ids else None,
                        token_estimate=estimate,
                        policy_version=policy_version,
                        metadata_json={
                            "scope": source.scope,
                            "sensitivity": source.sensitivity,
                            "revision": source.revision,
                        },
                    )
                )
            self.db.flush()
        return BuiltContext(
            build_id=build_id,
            identity=identity,
            sources=tuple(included),
            excluded=tuple(excluded),
            provider_policy=provider_policy,
        )


def context_hook(
    prefetched: Iterable[ContextSource], *, token_budget: int = 2000
) -> list[dict[str, Any]]:
    """Pure hot-path hook: no Session, no DB, no hidden lookup."""
    if token_budget < 1:
        raise_api_error(422, POLICY_CONTEXT_INVALID, "token_budget 必须为正数")
    blocks: list[dict[str, Any]] = []
    used = 0
    for source in prefetched:
        estimate = max(1, len(source.text) // 4)
        if source.trust != "untrusted_data" or used + estimate > token_budget:
            continue
        blocks.append(
            {
                "kind": "data",
                "trust": source.trust,
                "source_type": source.source_type,
                "source_id": source.source_id,
                "scope": source.scope,
                "sensitivity": source.sensitivity,
                "revision": source.revision,
                "citation": source.citation_handle,
                "content": source.text,
            }
        )
        used += estimate
    return blocks


__all__ = ["BuiltContext", "ContextBuilder", "ContextSource", "context_hook"]
