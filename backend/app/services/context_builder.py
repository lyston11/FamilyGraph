"""Policy-filtered model context, immutable per execution and precisely sourced."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import config
from app.errors import (
    AGENT_CONTEXT_INVALIDATED,
    POLICY_CONTEXT_INVALID,
    POLICY_LOCAL_REQUIRED,
    raise_api_error,
)
from app.models.context import ContextBuild, ContextBuildItem
from app.models.platform_features import PlatformFeatureConfig
from app.models.user import User
from app.services import memory_sources, platform_features
from app.services.agent_execution import ExecutionIdentity, acquire_run_writer, fence_execution
from app.services.memory_rag import RAGHit, query_hash, search_rag
from app.services.policy_consumer import is_policy_consumer_kind
from app.services.rag_budget import ESTIMATOR_VERSION, MAX_INCLUDED_SOURCES, estimate_context
from app.services.rag_query import QUERY_PLAN_VERSION, plan_query
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
    source_ref: memory_sources.ExactChunkRef | None = None

    @classmethod
    def from_hit(cls, hit: RAGHit) -> ContextSource:
        ref = None
        if hit.chunk_index is not None and hit.source_revision is not None and hit.content_hash:
            ref = memory_sources.ExactChunkRef(
                document_id=hit.document_id,
                chunk_id=hit.chunk_id,
                source_type=hit.source_type,
                source_id=hit.source_id,
                source_revision=hit.source_revision,
                index_version=hit.index_version,
                chunk_index=hit.chunk_index,
                content_hash=hit.content_hash,
            )
        return cls(
            source_type=hit.source_type,
            source_id=hit.source_id,
            text=hit.text,
            scope=hit.scope,
            sensitivity=hit.sensitivity,
            revision=hit.revision,
            citation_handle=hit.citation_handle,
            source_ref=ref,
        )

    def as_data_block(self) -> dict[str, Any]:
        return {
            "kind": "data",
            "trust": self.trust,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "scope": self.scope,
            "sensitivity": self.sensitivity,
            "revision": self.revision,
            "citation": self.citation_handle,
            "content": self.text,
        }


@dataclass(frozen=True)
class BuiltContext:
    build_id: int | None
    identity: dict[str, Any]
    sources: tuple[ContextSource, ...]
    excluded: tuple[dict[str, Any], ...]
    provider_policy: str

    def as_data_blocks(self) -> list[dict[str, Any]]:
        return [source.as_data_block() for source in self.sources]


def invalidate_build(db: Session, build: ContextBuild, reason: str) -> None:
    """Flush the observed safety state; the endpoint owns its durable commit."""
    if build.invalidated_at is None:
        build.invalidated_at = utcnow()
        build.invalidation_reason = reason
        db.flush()


def rollback_preserving_invalidation(db: Session, execution: ExecutionIdentity) -> None:
    """Roll back rejected events, retaining only server-observed invalidation.

    Read the unique build of the authenticated execution, never a submitted
    reference id or reason. Copy scalar evidence before rollback expires ORM
    state, then conditionally restore it in the rejection audit transaction.
    A concurrent invalidation wins; a deleted/replaced build is not recreated.
    The caller commits the rejection audit and this safety state together.
    """
    scope = (
        ContextBuild.run_id == execution.run_id,
        ContextBuild.attempt == execution.expected_attempt,
        ContextBuild.account_id == execution.account_id,
        ContextBuild.space_id == execution.space_id,
        ContextBuild.agent_kind == execution.agent_kind,
    )
    observed = db.execute(
        select(
            ContextBuild.id, ContextBuild.invalidated_at, ContextBuild.invalidation_reason
        ).where(*scope, ContextBuild.invalidated_at.is_not(None))
    ).one_or_none()
    db.rollback()
    if observed is not None:
        db.execute(
            update(ContextBuild)
            .where(*scope, ContextBuild.id == observed.id, ContextBuild.invalidated_at.is_(None))
            .values(
                invalidated_at=observed.invalidated_at,
                invalidation_reason=observed.invalidation_reason,
            )
            .execution_options(synchronize_session=False)
        )


def _invalidated(db: Session, build: ContextBuild, reason: str) -> None:
    invalidate_build(db, build, reason)
    raise_api_error(
        409,
        AGENT_CONTEXT_INVALIDATED,
        "先前构建的 context 已失效，需要新的 attempt 重建",
        {"context_build_id": build.id},
    )


class ContextBuilder:
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
        attempt: int | None = None,
        execution: ExecutionIdentity | None = None,
        provider_decision: dict[str, Any] | None = None,
        recent_messages: Sequence[str] = (),
    ) -> BuiltContext:
        if token_budget < 1 or token_budget > 32_000:
            raise_api_error(422, POLICY_CONTEXT_INVALID, "token_budget 超出范围")
        if not is_policy_consumer_kind(agent_kind):
            raise_api_error(422, POLICY_CONTEXT_INVALID, "policy consumer 不受支持")
        if agent_kind == "steward" and run_id is not None:
            raise_api_error(
                422, POLICY_CONTEXT_INVALID, "Steward consumer 不得伪造 generic AgentRun"
            )
        if self.db is not None and run_id is not None:
            if execution is not None:
                if run_id != execution.run_id or attempt != execution.expected_attempt:
                    raise_api_error(422, POLICY_CONTEXT_INVALID, "执行身份不匹配")
                fence_execution(self.db, execution, allow_cancel_requested=True)
            else:
                # Trusted in-process callers retain unbound audit builds. The
                # internal API always supplies a signed immutable identity.
                acquire_run_writer(self.db, run_id)
        identity = {"actor_user_id": actor.id, "space_id": space_id, "agent_kind": agent_kind}
        if self.db is not None:
            # Do not reuse a feature flag from a pre-auth identity-map read.
            self.db.get(PlatformFeatureConfig, 1, populate_existing=True)
        rag_enabled = self.db is not None and platform_features.is_rag_enabled(self.db)
        bounded_history = tuple(recent_messages[-4:])
        plan = plan_query(query, recent_messages=bounded_history)
        policy = {
            "provider_kind": provider_kind,
            "provider_decision": provider_decision,
            "policy_version": policy_version,
            "deployment_policy_version": config.POLICY_VERSION,
            "plan_version": QUERY_PLAN_VERSION,
            "query_plan": plan.log_summary(),
            "estimator_version": ESTIMATOR_VERSION,
        }
        # Anchors are bounded text used to plan this query, never an added
        # source. Hash them with the question; audit metadata contains no text.
        query_digest = query_hash(plan.normalized_query + "\0" + "\0".join(plan.anchors))
        if self.db is not None and run_id is not None and attempt is not None:
            existing = self.db.scalar(
                select(ContextBuild)
                .where(ContextBuild.run_id == run_id, ContextBuild.attempt == attempt)
                .execution_options(populate_existing=True)
            )
            if existing is not None:
                return self._replay(
                    existing,
                    actor=actor,
                    space_id=space_id,
                    agent_kind=agent_kind,
                    query_digest=query_digest,
                    policy=policy,
                    token_budget=token_budget,
                    rag_enabled=rag_enabled,
                    provider_kind=provider_kind,
                    identity=identity,
                )
        if prefetched is None:
            if self.db is None:
                raise_api_error(500, POLICY_CONTEXT_INVALID, "context 缺少预取数据")
            sources = (
                tuple(
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
                        query_plan=plan,
                    )
                )
                if rag_enabled
                else ()
            )
        else:
            sources = tuple(prefetched)
        if (
            any(s.sensitivity in ("high", "local_required") for s in sources)
            and provider_kind != "local"
        ):
            raise_api_error(409, POLICY_LOCAL_REQUIRED, "当前 Context 要求本地 Provider")
        included: list[ContextSource] = []
        excluded: list[dict[str, Any]] = []
        estimates: list[int] = []
        reasons: list[str | None] = []
        used = 0
        for source in sources:
            candidate_blocks = [s.as_data_block() for s in (*included, source)]
            candidate_estimate = estimate_context(candidate_blocks)
            estimate = candidate_estimate - used
            reason = None
            if source.trust != "untrusted_data":
                reason = "invalid_trust"
            elif len(included) >= MAX_INCLUDED_SOURCES:
                reason = "source_limit"
            elif candidate_estimate > token_budget:
                reason = "token_budget"
            if reason is not None:
                excluded.append({"source_id": source.source_id, "reason": reason})
            else:
                included.append(source)
                used = candidate_estimate
            estimates.append(estimate)
            reasons.append(reason)
        build_id = None
        if self.db is not None and run_id is not None:
            build = ContextBuild(
                run_id=run_id,
                attempt=attempt,
                account_id=actor.account.id,
                space_id=space_id,
                agent_kind=agent_kind,
                query_hash=query_digest,
                policy_version=policy_version,
                token_budget=token_budget,
                created_at=utcnow(),
                policy_json={**policy, "rag_enabled": rag_enabled},
                # New builds persist descriptors only. Legacy snapshots remain
                # preserved in the database but cannot be replayed as evidence.
                blocks_json=None,
            )
            self.db.add(build)
            self.db.flush()
            build_id = build.id
            for rank, source in enumerate(sources):
                self.db.add(
                    ContextBuildItem(
                        build_id=build.id,
                        source_type=source.source_type,
                        source_id=source.source_id,
                        citation_handle=source.citation_handle,
                        included=reasons[rank] is None,
                        exclusion_reason=reasons[rank],
                        rank=rank,
                        token_estimate=estimates[rank],
                        policy_version=policy_version,
                        metadata_json={
                            "scope": source.scope,
                            "sensitivity": source.sensitivity,
                            "revision": source.revision,
                            "source_ref": source.source_ref.as_json()
                            if source.source_ref
                            else None,
                        },
                    )
                )
            self.db.flush()
        return BuiltContext(
            build_id, identity, tuple(included), tuple(excluded), _provider_policy(included)
        )

    def _replay(
        self,
        build: ContextBuild,
        *,
        actor: User,
        space_id: int,
        agent_kind: str,
        query_digest: str,
        policy: dict[str, Any],
        token_budget: int,
        rag_enabled: bool,
        provider_kind: str | None,
        identity: dict[str, Any],
    ) -> BuiltContext:
        db = self.db
        assert db is not None
        if build.invalidated_at is not None:
            _invalidated(db, build, build.invalidation_reason or "invalidated")
        stored_policy = build.policy_json or {}
        if (
            build.account_id != actor.account.id
            or build.space_id != space_id
            or build.agent_kind != agent_kind
            or build.query_hash != query_digest
            or build.token_budget != token_budget
            or any(stored_policy.get(k) != v for k, v in policy.items())
            or not stored_policy
            or (stored_policy.get("rag_enabled") and not rag_enabled)
        ):
            _invalidated(db, build, "policy_changed")
        items = list(
            db.scalars(
                select(ContextBuildItem)
                .where(ContextBuildItem.build_id == build.id)
                .order_by(ContextBuildItem.rank, ContextBuildItem.id)
            )
        )
        sources: list[ContextSource] = []
        excluded: list[dict[str, Any]] = []
        for item in items:
            if not item.included:
                excluded.append({"source_id": item.source_id, "reason": item.exclusion_reason})
                continue
            ref = memory_sources.ExactChunkRef.parse(item.metadata_json.get("source_ref"))
            resolved = (
                memory_sources.read_exact_chunk(
                    db,
                    ref,
                    actor=actor,
                    account=actor.account,
                    space_id=space_id,
                    agent_kind=agent_kind,
                    for_model=True,
                    provider_kind=provider_kind,
                )
                if ref
                else None
            )
            if (
                resolved is None
                or ref is None
                or ref.source_type != item.source_type
                or ref.source_id != item.source_id
                or item.metadata_json.get("scope") != resolved.document.scope
                or item.metadata_json.get("sensitivity") != resolved.document.sensitivity
                or item.metadata_json.get("revision") != ref.source_revision
            ):
                _invalidated(db, build, "source_changed")
                raise AssertionError("unreachable")
            sources.append(
                ContextSource(
                    source_type=ref.source_type,
                    source_id=ref.source_id,
                    text=resolved.chunk.text,
                    scope=resolved.document.scope,
                    sensitivity=resolved.document.sensitivity,
                    revision=ref.source_revision,
                    citation_handle=item.citation_handle,
                    source_ref=ref,
                )
            )
        if estimate_context([s.as_data_block() for s in sources]) > token_budget:
            _invalidated(db, build, "budget_changed")
        return BuiltContext(
            build.id, identity, tuple(sources), tuple(excluded), _provider_policy(sources)
        )


def _provider_policy(sources: Iterable[ContextSource]) -> str:
    return (
        "local_required"
        if any(s.sensitivity in ("high", "local_required") for s in sources)
        else "allowed"
    )


def context_hook(
    prefetched: Iterable[ContextSource], *, token_budget: int = 2000
) -> list[dict[str, Any]]:
    """Pure, bounded hot-path hook using the exact same envelope estimator."""
    if token_budget < 1:
        raise_api_error(422, POLICY_CONTEXT_INVALID, "token_budget 必须为正数")
    blocks: list[dict[str, Any]] = []
    for source in prefetched:
        candidate = [*blocks, source.as_data_block()]
        if (
            source.trust == "untrusted_data"
            and len(candidate) <= MAX_INCLUDED_SOURCES
            and estimate_context(candidate) <= token_budget
        ):
            blocks = candidate
    return blocks


__all__ = ["BuiltContext", "ContextBuilder", "ContextSource", "context_hook"]
