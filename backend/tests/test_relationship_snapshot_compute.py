"""Semantic equivalence and isolation of the shared snapshot compute primitives."""

from __future__ import annotations

import pickle
import random
from datetime import timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.derived_fact import DerivedFact
from app.models.personal_family_view import PersonalFamilyBridge
from app.models.relationship_facts import SourceFact
from app.models.space import SpaceMember
from app.models.term_registry import TermEntry
from app.services import derived_facts, relationship_graph, relationship_resolver, terms
from app.services import source_facts as sf
from app.services.relationship_graph import ExtraEdge, GraphEdge, RelationshipGraph, load_graph
from app.services.relationship_resolver import (
    PathStep,
    SearchBudgetExceeded,
    advance_search,
    reachable_targets,
    resolve_graph,
    start_search,
)
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture, create_space_member, create_user_with_pin


def _graph(size: int, links: list[tuple[int, int, str]]) -> RelationshipGraph:
    adjacency: dict[int, list[GraphEdge]] = {i: [] for i in range(1, size + 1)}
    order = {"parent": 0, "sibling": 1, "spouse": 2, "partner": 3, "bridge": 4}
    for fid, (left, right, kind) in enumerate(links, 1):
        parent = kind in ("biological", "adoptive", "step", "guardian")
        edge_type = "parent" if parent else kind
        adjacency[left].append(
            GraphEdge(right, edge_type, kind if parent else None, "down" if parent else "sym", fid)
        )
        if kind != "bridge":
            adjacency[right].append(
                GraphEdge(left, edge_type, kind if parent else None, "up" if parent else "sym", fid)
            )
    for edges in adjacency.values():
        edges.sort(key=lambda edge: (edge.to_id, order[edge.edge_type], edge.fact_id))
    return RelationshipGraph(
        viewer_user_id=1,
        space_id=1,
        node_genders={i: "m" if i % 2 else "f" for i in adjacency},
        adjacency=adjacency,
        snapshot_hash="test-snapshot",
    )


def _legacy_paths(graph: RelationshipGraph, target: int) -> list[tuple[PathStep, ...]]:
    """Frozen pre-refactor DFS oracle; deliberately no new pruning/state helpers.

    The old collector can overshoot 128 inside a final frame and then slices its
    output. Comparing the complete first-128 prefix catches subtle cap/tie drift.
    """
    paths: list[tuple[PathStep, ...]] = []

    def visit(node: int, remaining: int, seen: frozenset[int], path: tuple[PathStep, ...]) -> None:
        if len(paths) >= 128:
            return
        for edge in graph.adjacency.get(node, ()):
            if edge.to_id in seen:
                continue
            extended = (
                *path,
                PathStep(
                    node, edge.to_id, edge.edge_type, edge.subtype, edge.direction, edge.fact_id
                ),
            )
            if edge.to_id == target:
                if remaining == 1:
                    paths.append(extended)
                continue
            if remaining == 1 or edge.edge_type == "partner":
                continue
            visit(edge.to_id, remaining - 1, seen | {edge.to_id}, extended)

    for depth in range(1, 13):
        visit(graph.viewer_user_id, depth, frozenset({graph.viewer_user_id}), ())
        if len(paths) >= 128:
            break
    return paths[:128]


def _old_sort_key(path: tuple[PathStep, ...]) -> tuple[Any, ...]:
    return (
        len(path),
        sum(step.edge_type != "parent" for step in path),
        sum(step.edge_type in ("spouse", "partner") for step in path),
        (path[0].from_id, *(step.to_id for step in path)),
    )


@pytest.mark.parametrize("seed", range(8))
def test_resumable_paths_match_legacy_dfs_with_cycles_and_mixed_edges(seed: int) -> None:
    rng = random.Random(seed)
    kinds = ["biological", "adoptive", "step", "guardian", "sibling", "spouse", "partner"]
    links = [
        (a, b, rng.choice(kinds))
        for a in range(1, 8)
        for b in range(a + 1, 8)
        if rng.random() < 0.42
    ]
    graph = _graph(7, links)
    distances = reachable_targets(graph)
    for target in range(2, 8):
        expected = _legacy_paths(graph, target)
        state = start_search(graph, target_user_id=target)
        while not state.complete:
            advance_search(state, max_expansions=13)
        assert state.paths == expected
        result = resolve_graph(graph, target_user_id=target)
        ordered = sorted(expected, key=_old_sort_key)
        assert result.found == bool(ordered)
        assert result.main_path == (ordered[0] if ordered else ())
        assert result.alt_paths == tuple(ordered[1:4])
        if ordered:
            assert distances[target] == len(ordered[0])
        else:
            assert target not in distances


@pytest.mark.parametrize("slice_size", [1, 7, 2048])
def test_capped_search_pickle_resumes_the_exact_first_128_paths(slice_size: int) -> None:
    graph = _graph(9, [(a, b, "spouse") for a in range(1, 10) for b in range(a + 1, 10)])
    expected = _legacy_paths(graph, 9)
    assert len(expected) == 128
    state = start_search(graph, target_user_id=9)
    previous = 0
    while True:
        state = pickle.loads(pickle.dumps(state))
        piece = advance_search(state, max_expansions=slice_size)
        assert 0 <= piece.state.expansions - previous <= slice_size
        previous = state.expansions
        if piece.resolution is not None:
            break
        assert not state.complete
    assert state.paths == expected
    assert piece.resolution == resolve_graph(graph, target_user_id=9)
    assert advance_search(state).resolution == piece.resolution


def test_tied_parallel_edges_and_directed_bridge_keep_old_path_order() -> None:
    graph = _graph(
        5,
        [
            (1, 2, "adoptive"),
            (1, 2, "biological"),
            (1, 3, "bridge"),
            (2, 4, "spouse"),
            (3, 4, "biological"),
            (4, 5, "partner"),
        ],
    )
    for target in (2, 4, 5):
        expected = _legacy_paths(graph, target)
        state = start_search(graph, target_user_id=target)
        while not state.complete:
            advance_search(state, max_expansions=2)
        assert state.paths == expected


def test_reachability_keeps_terminal_partner_but_extends_longer_nonpartner_prefix() -> None:
    graph = _graph(
        6,
        [
            (1, 2, "partner"),
            (1, 4, "spouse"),
            (4, 2, "spouse"),
            (2, 3, "biological"),
            (4, 5, "partner"),
            (5, 6, "spouse"),
        ],
    )
    assert reachable_targets(graph) == {1: 0, 2: 1, 4: 1, 3: 3, 5: 2}
    assert [step.edge_type for step in resolve_graph(graph, target_user_id=5).main_path] == [
        "spouse",
        "partner",
    ]
    assert len(resolve_graph(graph, target_user_id=3).main_path) == 3
    assert not resolve_graph(graph, target_user_id=6).found


def test_depth_boundary_and_disconnected_targets_are_proved_without_exhausting_budget() -> None:
    graph = _graph(15, [(i, i + 1, "biological") for i in range(1, 14)])
    assert reachable_targets(graph)[13] == 12
    assert 14 not in reachable_targets(graph)
    assert len(resolve_graph(graph, target_user_id=13).main_path) == 12
    for target in (14, 15, 99):
        state = start_search(graph, target_user_id=target, max_total_expansions=1)
        piece = advance_search(state, max_expansions=1)
        assert piece.resolution is not None and not piece.resolution.found
        assert state.expansions == 0


def test_expansion_and_state_budgets_never_produce_a_no_path_result() -> None:
    graph = _graph(7, [(i, i + 1, "biological") for i in range(1, 7)])
    state = start_search(graph, target_user_id=6, max_total_expansions=2)
    assert advance_search(state, max_expansions=1).resolution is None
    assert advance_search(state, max_expansions=1).resolution is None
    with pytest.raises(SearchBudgetExceeded) as caught:
        advance_search(state, max_expansions=1)
    assert caught.value.reason == "expansions"
    assert not state.complete
    restored = pickle.loads(pickle.dumps(caught.value))
    assert (restored.reason, restored.expansions, restored.limit) == ("expansions", 2, 2)
    with pytest.raises(SearchBudgetExceeded, match="state_bytes"):
        start_search(graph, target_user_id=6, max_state_bytes=1)
    # Finding a main path early still cannot claim a complete target before the
    # deterministic alternate-path search has completed or reached its cap.
    graph = _graph(4, [(1, 2, "biological"), (1, 3, "spouse"), (3, 4, "spouse"), (4, 2, "spouse")])
    partial = start_search(graph, target_user_id=2, max_total_expansions=1)
    assert advance_search(partial, max_expansions=1).resolution is None
    assert len(partial.paths) == 1
    with pytest.raises(SearchBudgetExceeded):
        advance_search(partial, max_expansions=1)


def _confirm(
    session: Session, space_id: int, left: int, right: int, kind: str = "spouse"
) -> SourceFact:
    row = sf.create_source_fact(
        session,
        fact_type=kind,
        subject_user_id=left,
        object_user_id=right,
        provenance="manual_entry",
        space_id=space_id,
    )
    sf.transition_source_fact(session, row, "confirm")
    return row


def _cache_world(session: Session) -> tuple[Any, Any, Any, SourceFact]:
    viewer, space = create_agent_fixture(session, name="snapshot-viewer")
    target = create_user_with_pin(session, "snapshot-target", "123456", gender="m")
    create_space_member(session, space.id, target.id)
    fact = _confirm(session, space.id, viewer.id, target.id)
    return viewer, space, target, fact


def test_loaded_graph_and_search_work_after_session_closes(db_session, monkeypatch) -> None:
    viewer, space, target, _ = _cache_world(db_session)
    target_id = target.id
    graph = load_graph(db_session, viewer_user_id=viewer.id, space_id=space.id)
    db_session.close()
    with monkeypatch.context() as patch:
        patch.setattr(
            Session, "execute", Mock(side_effect=AssertionError("compute used a Session"))
        )
        patch.setattr(Session, "get", Mock(side_effect=AssertionError("compute used ORM loading")))
        detached = pickle.loads(pickle.dumps(graph))
        result = resolve_graph(detached, target_user_id=target_id)
        assert result.found
        assert pickle.loads(pickle.dumps(result)) == result
        with pytest.raises(TypeError):
            detached.node_genders[target_id] = "f"
        assert isinstance(detached.adjacency[target_id], tuple)


def test_cache_hit_does_no_search_or_write_and_reconstructs_the_full_result(
    db_session, monkeypatch
) -> None:
    viewer, space, target, _ = _cache_world(db_session)
    arguments = {"viewer_user_id": viewer.id, "target_user_id": target.id, "space_id": space.id}
    first = derived_facts.get_or_compute(db_session, **arguments)
    row = db_session.scalar(select(DerivedFact).where(DerivedFact.target_user_id == target.id))
    assert row is not None
    computed_at = row.computed_at
    monkeypatch.setattr(
        derived_facts, "resolve_graph", Mock(side_effect=AssertionError("searched"))
    )
    monkeypatch.setattr(
        relationship_resolver,
        "_enumerate_simple_paths",
        Mock(side_effect=AssertionError("enumerated")),
    )
    cached = derived_facts.get_or_compute(db_session, **arguments)
    assert cached.cache_hit
    assert cached.resolution == first.resolution
    assert cached.explanation_structural == first.explanation_structural
    assert row.computed_at == computed_at and row not in db_session.dirty
    terms.set_personal_term(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Sm",
        term="家里的称呼",
    )
    after_terms = derived_facts.get_or_compute(db_session, **arguments)
    assert after_terms.cache_hit and after_terms.resolution == first.resolution


@pytest.mark.parametrize("change", ["gender", "endpoint", "visible_node", "membership"])
def test_cache_input_hash_catches_changes_without_a_fact_revision(db_session, change: str) -> None:
    viewer, space, target, fact = _cache_world(db_session)
    arguments = {"viewer_user_id": viewer.id, "target_user_id": target.id, "space_id": space.id}
    original = derived_facts.get_or_compute(db_session, **arguments)
    revision = fact.revision
    if change == "gender":
        target.gender = "f"
    elif change in ("endpoint", "visible_node"):
        other = create_user_with_pin(db_session, "snapshot-other", "123456")
        create_space_member(db_session, space.id, other.id)
        if change == "endpoint":
            fact.object_user_id = other.id
    else:
        member = db_session.scalar(
            select(SpaceMember).where(
                SpaceMember.space_id == space.id, SpaceMember.user_id == target.id
            )
        )
        assert member is not None
        member.status = "removed"
    db_session.flush()
    changed = derived_facts.get_or_compute(db_session, **arguments)
    assert fact.revision == revision
    assert not changed.cache_hit
    assert changed.resolution.snapshot_hash != original.resolution.snapshot_hash
    if change == "gender":
        assert changed.concept_code == "Sf"
    elif change in ("endpoint", "membership"):
        assert not changed.found


def test_birth_and_inferred_edges_do_not_change_confirmed_structural_hash(db_session) -> None:
    viewer, space, target, _ = _cache_world(db_session)
    other = create_user_with_pin(db_session, "snapshot-inferred", "123456", gender="f")
    create_space_member(db_session, space.id, other.id)
    graph = load_graph(db_session, viewer_user_id=viewer.id, space_id=space.id)
    target.birth = {"cal_type": "solar", "date": "1980-01-01"}
    db_session.flush()
    augmented = load_graph(
        db_session,
        viewer_user_id=viewer.id,
        space_id=space.id,
        extra_edges=[ExtraEdge(123, target.id, other.id, "biological_parent")],
    )
    assert augmented.snapshot_hash == graph.snapshot_hash
    assert not resolve_graph(graph, target_user_id=other.id).found
    inferred = resolve_graph(augmented, target_user_id=other.id)
    assert inferred.found and any(step.fact_id == -123 for step in inferred.main_path)
    with pytest.raises(ValueError, match="confirmed graph"):
        derived_facts.compute_pair(
            db_session,
            viewer_user_id=viewer.id,
            target_user_id=other.id,
            space_id=space.id,
            graph=augmented,
        )


def test_ordinary_queries_reuse_only_published_structure_even_after_terms_change(
    db_session, monkeypatch
) -> None:
    from app.models.steward import (
        StewardGeneration,
        StewardGenerationView,
        StewardPublication,
        StewardViewTarget,
    )
    from app.services import steward_pipeline, steward_snapshot

    viewer, space, target, _ = _cache_world(db_session)
    graph = load_graph(db_session, viewer_user_id=viewer.id, space_id=space.id)
    resolution = resolve_graph(graph, target_user_id=target.id)
    now = utcnow()
    generation = StewardGeneration(
        space_id=space.id,
        status="running",
        execution_cursor=0,
        input_versions_json=steward_snapshot.input_versions(db_session, space.id),
        valid_until=now + timedelta(minutes=5),
        manifest_sealed=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(generation)
    db_session.flush()
    view = StewardGenerationView(
        generation_id=generation.id,
        space_id=space.id,
        viewer_account_id=viewer.account.id,
        root_user_id=viewer.id,
        status="ready",
        structural_hash=graph.snapshot_hash,
        completed_count=1,
        total_count=1,
        created_at=now,
        updated_at=now,
    )
    db_session.add(view)
    db_session.flush()
    db_session.add(
        StewardViewTarget(
            view_id=view.id,
            target_user_id=target.id,
            status="ready",
            distance=1,
            resolution_json=steward_pipeline.encode_resolution(resolution),
            updated_at=now,
        )
    )
    # Even a faulty pointer cannot make an unpublished generation a cache source.
    db_session.add(
        StewardPublication(space_id=space.id, generation_id=generation.id, updated_at=now)
    )
    db_session.flush()
    search = Mock(wraps=derived_facts.resolve_graph)
    monkeypatch.setattr(derived_facts, "resolve_graph", search)
    arguments = {"viewer_user_id": viewer.id, "target_user_id": target.id, "space_id": space.id}
    draft_result, row, _ = derived_facts.compute_pair(db_session, **arguments)
    assert row is None and draft_result == resolution
    assert search.call_count == 1
    generation.status = "published"
    db_session.flush()
    search.side_effect = AssertionError("recomputed already-published structural paths")
    first = derived_facts.get_or_compute(db_session, **arguments)
    assert first.resolution == resolution
    # Remove the legacy row: this must hit the published target even if only
    # display inputs have changed and the full PFV generation is now outdated.
    db_session.execute(delete(DerivedFact))
    terms.set_personal_term(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Sm",
        term="新的称呼",
    )
    changed = derived_facts.get_or_compute(db_session, **arguments)
    assert changed.resolution == resolution
    # Built-in presentation rules also invalidate rendered terms, not paths.
    db_session.execute(delete(DerivedFact))
    monkeypatch.setattr(terms, "PRESENTATION_RULE_VERSION", "test-next-presentation")
    changed_rules = derived_facts.get_or_compute(db_session, **arguments)
    assert changed_rules.resolution == resolution
    # A structural algorithm version is a different input, unlike a term edit.
    # Neither the legacy row nor the publication may bypass a fresh search.
    db_session.execute(delete(DerivedFact))
    monkeypatch.setattr(derived_facts, "KINSHIP_ALGO_VERSION", "test-next-algorithm")
    search.side_effect = None
    next_algorithm = derived_facts.get_or_compute(db_session, **arguments)
    assert next_algorithm.found and search.call_count == 2


def test_bridge_fingerprint_covers_scope_anchors_membership_and_clock_expiry(
    db_session, monkeypatch
) -> None:
    viewer, space, _, _ = _cache_world(db_session)
    anchor, foreign_space = create_agent_fixture(db_session, name="snapshot-foreign")
    relative = create_user_with_pin(db_session, "snapshot-foreign-child", "123456")
    create_space_member(db_session, foreign_space.id, relative.id)
    fact = _confirm(db_session, foreign_space.id, anchor.id, relative.id, "biological_parent")
    now = utcnow()
    bridge = PersonalFamilyBridge(
        lineage_space_a_id=space.id,
        lineage_space_b_id=foreign_space.id,
        anchor_a_user_id=viewer.id,
        anchor_b_user_id=anchor.id,
        normalized_pair_key=f"snapshot:{viewer.id}:{anchor.id}",
        initiated_by_account_id=viewer.account.id,
        consent_a_account_id=viewer.account.id,
        consent_b_account_id=anchor.account.id,
        scope_json={"mode": "anchor_paths"},
        status="active",
        revision=1,
        expires_at=now + timedelta(seconds=60),
        created_at=now,
        updated_at=now,
    )
    db_session.add(bridge)
    db_session.flush()
    graph = load_graph(db_session, viewer_user_id=viewer.id, space_id=space.id)
    assert resolve_graph(graph, target_user_id=relative.id).path_class == "cross_space"
    assert next(f for f in graph.confirmed_facts if f.id == fact.id).space_id == foreign_space.id
    assert graph.valid_until == bridge.expires_at
    bridge.scope_json = {"mode": "anchor_paths", "revision_note": "changed"}
    db_session.flush()
    scope_changed = load_graph(db_session, viewer_user_id=viewer.id, space_id=space.id)
    assert scope_changed.snapshot_hash != graph.snapshot_hash
    bridge.anchor_b_user_id = relative.id
    db_session.flush()
    anchor_changed = load_graph(db_session, viewer_user_id=viewer.id, space_id=space.id)
    assert anchor_changed.snapshot_hash != scope_changed.snapshot_hash
    member = db_session.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == foreign_space.id, SpaceMember.user_id == anchor.id
        )
    )
    assert member is not None
    member.status = "removed"
    db_session.flush()
    membership_changed = load_graph(db_session, viewer_user_id=viewer.id, space_id=space.id)
    assert membership_changed.snapshot_hash != anchor_changed.snapshot_hash
    monkeypatch.setattr(relationship_graph, "utcnow", lambda: now + timedelta(seconds=60))
    expired = load_graph(db_session, viewer_user_id=viewer.id, space_id=space.id)
    assert expired.snapshot_hash != membership_changed.snapshot_hash
    assert not expired.bridges and expired.valid_until is None
    assert not resolve_graph(expired, target_user_id=relative.id).found


def _legacy_registry_lookup(
    session: Session, account_id: int, space_id: int, code: str
) -> terms.TermResolution:
    """The four original SQL queries, independent of the bulk snapshot matcher."""
    for level, scope in (
        ("personal", TermEntry.owner_account_id == account_id),
        ("space", TermEntry.space_id == space_id),
        ("locale", TermEntry.locale == terms.space_locale(session, space_id)),
        ("system", True),
    ):
        row = session.scalar(
            select(TermEntry).where(
                TermEntry.level == level,
                scope,
                TermEntry.concept_code == code,
                TermEntry.status == "active",
            )
        )
        if row is not None:
            return terms.TermResolution(row.term, level, row.id)
    return terms.TermResolution(None, None, None)


def test_term_snapshot_keeps_registry_precedence_and_legacy_space_synonym_choice(
    db_session, monkeypatch
) -> None:
    viewer, space, _, _ = _cache_world(db_session)
    terms.seed_builtin_packs(db_session)
    for term in ("zzz", "aaa"):
        db_session.add(
            TermEntry(
                concept_code="Um",
                level="space",
                space_id=space.id,
                term=term,
                status="active",
                revision=1,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    terms.set_personal_term(
        db_session, account_id=viewer.account.id, space_id=space.id, concept_code="Uf", term="亲娘"
    )
    db_session.flush()
    snapshot = terms.load_term_snapshot(db_session, account_id=viewer.account.id, space_id=space.id)
    expected = {
        code: _legacy_registry_lookup(db_session, viewer.account.id, space.id, code)
        for code in {*snapshot.resolved, "Qm"}
    }
    assert expected["Um"].term == "zzz"
    with monkeypatch.context() as patch:
        patch.setattr(Session, "execute", Mock(side_effect=AssertionError("compute read registry")))
        patch.setattr(Session, "scalar", Mock(side_effect=AssertionError("compute read registry")))
        detached = pickle.loads(pickle.dumps(snapshot))
        for code, old in expected.items():
            actual = terms.resolve_term_from_snapshot(
                detached, concept_code=code, structural_description="fallback"
            )
            assert actual == {
                "term": old.term if old.term is not None else "fallback",
                "source_level": old.source_level or "structural",
                "entry_id": old.entry_id,
            }


def test_term_snapshot_variants_generalization_and_personal_changes_are_detached(
    db_session,
) -> None:
    viewer, space, _, _ = _cache_world(db_session)
    terms.seed_builtin_packs(db_session)
    snapshot = terms.load_term_snapshot(db_session, account_id=viewer.account.id, space_id=space.id)
    context = terms.VariantContext(
        viewer_user_id=1,
        path=[{"from": 1, "to": 2}, {"from": 2, "to": 3}, {"from": 3, "to": 4}],
        births={3: ("solar", 1988), 4: ("solar", 1989), 1: ("solar", 1980), 2: None},
    )
    result = terms.resolve_term_from_snapshot(
        snapshot,
        concept_code="Um-Df-Sm",
        structural_description="fallback",
        variant_context=context,
    )
    assert result["term"] == "妹夫"
    assert (
        terms.resolve_term_from_snapshot(
            snapshot, concept_code="Um-Um-Sf", structural_description="fallback"
        )["term"]
        == "爷爷的妻子"
    )
    assert (
        terms.resolve_term_from_snapshot(
            snapshot, concept_code="Qm", structural_description="fallback"
        )["source_level"]
        == "structural"
    )
    terms.set_personal_term(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Df-Sm",
        term="自定义称谓",
    )
    changed = terms.resolve_term_or_structural(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Df-Sm",
        structural_description="fallback",
        variant_context=context,
    )
    assert changed["term"] == "自定义称谓" and changed["source_level"] == "personal"
    assert (
        terms.resolve_term_from_snapshot(
            snapshot,
            concept_code="Um-Df-Sm",
            structural_description="fallback",
            variant_context=context,
        )
        == result
    )
