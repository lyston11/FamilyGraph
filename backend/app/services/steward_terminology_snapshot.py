"""Detached terminology inputs and one pure selector for every PFV consumer.

Database reads are confined to load_input. Path validation, vocabulary, explicit
usage and suppression use the caller's authorized graph and immutable values.
Automatic output is never an input to its own semantic identity.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models.steward import StewardTermProjection, StewardTermSuppression
from app.models.term_registry import BUILTIN_TERM_SEEDS, TermEntry, TermUsage
from app.services import terms
from app.services.derived_facts import steps_from_json
from app.services.relationship_graph import FrozenMapping
from app.services.relationship_resolver import concept_code_for_path, describe_path, resolve_graph

if TYPE_CHECKING:
    from app.services.steward_snapshot import ViewerInput


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


_BUILTIN_VOCABULARY_HASH = _hash(BUILTIN_TERM_SEEDS)


@dataclass(frozen=True)
class UsageInput:
    id: int
    entry_id: int
    entry_revision: int
    concept_code: str
    term: str
    created_at: str


@dataclass(frozen=True)
class SuppressionInput:
    target_user_id: int
    key: str


@dataclass(frozen=True)
class ProjectionInput:
    id: int
    target_user_id: int
    concept_code: str | None
    semantic_hash: str
    baseline_term: str | None
    baseline_source: str | None
    term: str
    origin: str | None
    status: str
    revision: int
    rule_version: str
    suppression_key: str | None


@dataclass(frozen=True)
class TerminologyInput:
    usages: tuple[UsageInput, ...] = ()
    suppressions: tuple[SuppressionInput, ...] = ()
    projections: tuple[ProjectionInput, ...] = ()
    model_enabled: bool = False
    policy_version: str = ""
    rule_version: str = "terminology-v2"
    prompt_version: str = "terminology-prompt-v2"
    by_target: Mapping[int, ProjectionInput] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "by_target", FrozenMapping({row.target_user_id: row for row in self.projections})
        )

    @property
    def fingerprint(self) -> str:
        # Audit/send bookkeeping and prompt versions cannot invalidate display.
        return _hash(
            {
                "usages": [asdict(row) for row in self.usages],
                "suppressions": [asdict(row) for row in self.suppressions],
                "projections": [asdict(row) for row in self.projections],
                "model_enabled": self.model_enabled,
                "policy": self.policy_version,
                "rule": self.rule_version,
            }
        )


def rules_fingerprint() -> str:
    from app.services.steward_terminology import RULE_VERSION

    return _hash([RULE_VERSION, terms.PRESENTATION_RULE_VERSION, _BUILTIN_VOCABULARY_HASH])


def load_input(
    session: Session, *, account_id: int, root_user_id: int, space_id: int
) -> TerminologyInput:
    from app.services.steward_assist import assist_enabled
    from app.services.steward_terminology import PROMPT_VERSION, RULE_VERSION

    usages = tuple(
        UsageInput(
            id=row.id,
            entry_id=row.term_entry_id,
            entry_revision=entry.revision,
            concept_code=entry.concept_code,
            term=entry.term,
            created_at=row.created_at.isoformat(),
        )
        for row, entry in session.execute(
            select(TermUsage, TermEntry)
            .join(TermEntry, TermEntry.id == TermUsage.term_entry_id)
            .where(TermUsage.account_id == account_id, TermUsage.space_id == space_id)
            .order_by(TermUsage.id)
        )
    )
    suppressions = tuple(
        SuppressionInput(target_user_id=row.target_user_id, key=row.suppression_key)
        for row in session.scalars(
            select(StewardTermSuppression)
            .where(
                StewardTermSuppression.viewer_account_id == account_id,
                StewardTermSuppression.space_id == space_id,
            )
            .order_by(StewardTermSuppression.target_user_id, StewardTermSuppression.suppression_key)
        )
    )
    projections = tuple(
        ProjectionInput(
            id=row.id,
            target_user_id=row.target_user_id,
            concept_code=row.concept_code,
            semantic_hash=row.semantic_hash,
            baseline_term=row.baseline_term,
            baseline_source=row.baseline_source,
            term=row.term,
            origin=row.origin,
            status=row.status,
            revision=row.revision,
            rule_version=row.rule_version,
            suppression_key=row.suppression_key,
        )
        for row in session.scalars(
            select(StewardTermProjection)
            .where(
                StewardTermProjection.viewer_account_id == account_id,
                StewardTermProjection.root_user_id == root_user_id,
                StewardTermProjection.space_id == space_id,
                StewardTermProjection.term.is_not(None),
            )
            .order_by(StewardTermProjection.id)
        )
        if row.term is not None
    )
    return TerminologyInput(
        usages=usages,
        suppressions=suppressions,
        projections=projections,
        model_enabled=assist_enabled(session, space_id, "terminology"),
        policy_version=config.POLICY_VERSION,
        rule_version=RULE_VERSION,
        prompt_version=PROMPT_VERSION,
    )


def allowed_terms(
    registry: terms.TermSnapshot,
    *,
    concept_code: str,
    variant_context: terms.VariantContext | None = None,
) -> set[str]:
    """本码可合法显示/建议的全部词：原码优先，再补安全等价别名。

    别名只贡献 locale/system 与内置包词，不把其他原码上的 personal/space
    自定义词扩到这里（与 terms._registry_alias_term 同口径）。
    """
    resolved = terms._resolve_registry_code(registry, concept_code)
    allowed = {resolved.term} if resolved.term else set()
    if resolved.source_level in (terms.TERM_LEVEL_PERSONAL, terms.TERM_LEVEL_SPACE):
        return allowed
    aliases = set(terms.concept_code_aliases(concept_code))
    allowed.update(
        text
        for level, locale, code, text in BUILTIN_TERM_SEEDS
        if code in ({concept_code} | aliases) and (level == "system" or locale == registry.locale)
    )
    allowed.update(
        row.term
        for row in registry.entries
        # 原码贡献全部层级；别名码只贡献 locale/system——与显示路径
        # `terms._registry_alias_term` 同口径，否则别的原码上的 space 自定义词
        # 会经模型写回被应用到本路径（模型可绕过显示层的层级约束）。
        if (row.concept_code == concept_code and row.level in ("system", "locale", "space"))
        or (row.concept_code in aliases and row.level in ("system", "locale"))
    )
    if variant_context is not None:
        variant = terms._sibling_variant_term(concept_code, variant_context)
        if variant:
            allowed.add(variant)
    return allowed


def candidate_terms(
    registry: terms.TermSnapshot,
    *,
    concept_code: str,
    variant_context: terms.VariantContext | None = None,
) -> set[str]:
    allowed = allowed_terms(registry, concept_code=concept_code, variant_context=variant_context)
    tokens = concept_code.split("-")
    for cut in range(1, len(tokens)):
        words = [terms.residual_word_for(token) for token in tokens[cut:]]
        if any(word is None for word in words):
            continue
        context = (
            terms.VariantContext(
                viewer_user_id=variant_context.viewer_user_id,
                path=variant_context.path[:cut],
                births=variant_context.births,
            )
            if variant_context is not None
            else None
        )
        for prefix in allowed_terms(
            registry, concept_code="-".join(tokens[:cut]), variant_context=context
        ):
            combined = "的".join([prefix, *(word for word in words if word is not None)])
            if len(combined) <= 64:
                allowed.add(combined)
    return allowed


def _registry_hash(registry: terms.TermSnapshot, concept_codes: set[str]) -> str:
    rows = [
        (row.id, row.revision, row.concept_code, row.term)
        for row in sorted(registry.entries, key=lambda entry: entry.id)
        if row.concept_code in concept_codes
    ]
    # Preserve the main branch's persisted semantic identities across this merge.
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def current_target_context(
    snapshot: ViewerInput,
    *,
    target_user_id: int,
    path: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    from app.services.steward_terminology import suppression_key_for

    root = snapshot.root_user_id
    if root == target_user_id or target_user_id not in snapshot.graph.node_genders:
        return None
    if path is None:
        result = resolve_graph(snapshot.graph, target_user_id=target_user_id)
        if not result.found:
            return None
        path = [step.to_json() for step in result.main_path]
    if not path:
        return None
    fact_by_id = {fact.id: fact for fact in snapshot.graph.confirmed_facts}
    cursor, persons = root, {root}
    fact_revisions: list[list[int]] = []
    for step in path:
        if not isinstance(step, dict) or step.get("from") != cursor:
            return None
        fact_id = step.get("fact_id")
        if type(fact_id) is not int or fact_id <= 0:
            return None
        match = next(
            (
                edge
                for edge in snapshot.graph.adjacency.get(cursor, ())
                if edge.to_id == step.get("to")
                and edge.fact_id == fact_id
                and edge.edge_type == step.get("edge_type")
                and edge.subtype == step.get("subtype")
                and edge.direction == step.get("direction")
            ),
            None,
        )
        fact = fact_by_id.get(fact_id)
        if match is None or match.to_id in persons or fact is None:
            return None
        fact_revisions.append([fact_id, fact.revision])
        cursor = match.to_id
        persons.add(cursor)
    if cursor != target_user_id:
        return None
    steps = steps_from_json(path)
    concept = concept_code_for_path(steps, snapshot.graph.path_genders)
    if concept is None or concept == "SELF":
        return None
    variants = terms.VariantContext(viewer_user_id=root, path=path, births=dict(snapshot.births))
    baseline = terms.resolve_term_from_snapshot(
        snapshot.terms,
        concept_code=concept,
        structural_description=describe_path(steps, snapshot.graph.path_genders),
        variant_context=variants,
    )
    age_order = "unknown"
    base = terms.sibling_base_hop(concept.split("-"))
    if base is not None and base[0] < len(path):
        step = path[base[0]]
        reference = root if base[0] else int(step["from"])
        age_order = terms._age_order(variants.births, reference, int(step["to"])) or "unknown"
    all_allowed = candidate_terms(snapshot.terms, concept_code=concept, variant_context=variants)
    suppressions = sorted(
        row.key for row in snapshot.terminology.suppressions if row.target_user_id == target_user_id
    )
    allowed = {
        term
        for term in all_allowed
        if suppression_key_for(
            viewer_account_id=snapshot.account_id,
            space_id=snapshot.terms.space_id,
            target_user_id=target_user_id,
            concept_code=concept,
            term=term,
        )
        not in suppressions
    }
    usages = [row for row in snapshot.terminology.usages if row.concept_code == concept]
    usage = next(
        (
            row
            for row in sorted(usages, key=lambda row: (row.created_at, row.id), reverse=True)
            if row.term in all_allowed
        ),
        None,
    )
    # 查词闭包（原码 + 逐级前缀 + 各自安全别名）必须进语义哈希：别名命中会改变
    # allowed/baseline，只哈希原码前缀会让改词后旧投影仍被判为同一语义。
    lookup_codes = terms.concept_lookup_codes(concept)
    semantic = _hash(
        [
            snapshot.terminology.rule_version,
            snapshot.account_id,
            snapshot.terms.space_id,
            target_user_id,
            concept,
            fact_revisions,
            _registry_hash(snapshot.terms, lookup_codes),
            root,
            {
                "policy_version": snapshot.terminology.policy_version,
                "path": path,
                "age_order": age_order,
                "baseline": baseline,
                "usage": [
                    [row.id, row.entry_id, row.entry_revision, row.term]
                    for row in sorted(usages, key=lambda row: row.id)
                ],
                "suppressions": suppressions,
                "allowed_terms": sorted(allowed),
            },
        ]
    )
    return {
        "target_user_id": target_user_id,
        "concept_code": concept,
        "path": path,
        "baseline_term": baseline["term"],
        "baseline_source": baseline["source_level"],
        "age_order": age_order,
        "allowed_terms": sorted(allowed),
        "preferred_term": usage.term if usage is not None and usage.term in allowed else None,
        "semantic_hash": semantic,
        "request_hash": _hash([snapshot.terminology.prompt_version, semantic]),
    }


def effective_override(
    snapshot: ViewerInput,
    *,
    target_user_id: int,
    concept_code: str | None,
    baseline_term: str | None,
    baseline_source: str | None,
    path: list[dict[str, Any]] | None = None,
) -> str | None:
    if (
        concept_code is None
        or concept_code == "SELF"
        or baseline_source
        not in (
            None,
            "locale",
            "system",
            "derived",
            "structural",
        )
    ):
        return None
    row = snapshot.terminology.by_target.get(target_user_id)
    if (
        row is None
        or row.status != "active"
        or row.concept_code != concept_code
        or row.rule_version != snapshot.terminology.rule_version
        or (row.origin == "model" and not snapshot.terminology.model_enabled)
        or (row.baseline_term is not None and row.baseline_term != baseline_term)
    ):
        return None
    current = current_target_context(snapshot, target_user_id=target_user_id, path=path)
    if (
        current is None
        or current["semantic_hash"] != row.semantic_hash
        or current["concept_code"] != concept_code
        or current["baseline_source"] not in ("locale", "system", "derived", "structural")
        or row.term not in current["allowed_terms"]
    ):
        return None
    return row.term


def resolve_display_term(
    snapshot: ViewerInput,
    *,
    target_user_id: int,
    concept_code: str | None,
    structural_description: str,
    path: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline = terms.resolve_term_from_snapshot(
        snapshot.terms,
        concept_code=concept_code,
        structural_description=structural_description,
        variant_context=terms.VariantContext(
            viewer_user_id=snapshot.root_user_id, path=path, births=dict(snapshot.births)
        ),
    )
    override = effective_override(
        snapshot,
        target_user_id=target_user_id,
        concept_code=concept_code,
        baseline_term=baseline["term"],
        baseline_source=baseline["source_level"],
        path=path,
    )
    if override is not None and override != baseline["term"]:
        return {**baseline, "term": override, "source_level": terms.SOURCE_LEVEL_STEWARD}
    return baseline
