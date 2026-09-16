"""Explicit, short SQLite read snapshots for the Steward coordinator.

Only detached data leaves these functions. Source triggers make a two-row
revision read sufficient for the writer fence, including remote bridge inputs.
Login counters, jobs, target progress and publication writes are not inputs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app import config
from app.models.account import Account
from app.models.personal_family_view import PersonalFamilyBridge
from app.models.space import SpaceMember
from app.models.steward import StewardInputRevision
from app.models.user import User
from app.services import relationship_resolver, visibility
from app.services.relationship_graph import (
    ExtraEdge,
    RelationshipGraph,
    birth_from_user,
    load_graph,
)
from app.services.steward_terminology_snapshot import TerminologyInput
from app.services.terms import TermSnapshot
from app.utils.timeutil import utcnow

SNAPSHOT_VERSION = "steward-snapshot-v4"


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def search_config_fingerprint() -> str:
    """Structural cache and search budgets exclude presentation-only changes."""
    from app.services.derived_facts import KINSHIP_ALGO_VERSION

    return canonical_hash(
        {
            "snapshot": SNAPSHOT_VERSION,
            "policy": config.POLICY_VERSION,
            "algorithm": KINSHIP_ALGO_VERSION,
            "depth": relationship_resolver.MAX_PATH_DEPTH,
            "max_paths": relationship_resolver.MAX_SIMPLE_PATHS,
        }
    )


def config_fingerprint() -> str:
    from app.services.personal_family_view import COMPUTATION_VERSION
    from app.services.steward_terminology_snapshot import rules_fingerprint

    return canonical_hash(
        {
            "search": search_config_fingerprint(),
            # 展示合同必须跟 PFV 的计算版本同源，否则改词义/查词规则后
            # Steward 代次不会因 presentation 变化而重算。
            "presentation": COMPUTATION_VERSION,
            "terminology": rules_fingerprint(),
            "terminology_model_enabled": config.STEWARD_ASSIST_TERMINOLOGY,
        }
    )


def input_versions(session: Session, space_id: int) -> dict[str, Any]:
    rows = session.execute(
        select(
            StewardInputRevision.scope_id,
            StewardInputRevision.structural,
            StewardInputRevision.presentation,
            StewardInputRevision.inferred,
        ).where(StewardInputRevision.scope_id.in_((0, space_id)))
    ).all()
    values = {int(row[0]): [int(row[1]), int(row[2]), int(row[3])] for row in rows}
    return {
        "version": SNAPSHOT_VERSION,
        "global": values.get(0, [0, 0, 0]),
        "space": values.get(space_id, [0, 0, 0]),
        "search_config": search_config_fingerprint(),
        "config": config_fingerprint(),
    }


def core_versions(versions: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": versions.get("version"),
        "global": versions.get("global", [])[:2],
        "space": versions.get("space", [])[:2],
        "config": versions.get("config"),
    }


def versions_match(session: Session, *, space_id: int, expected: dict[str, Any]) -> bool:
    return bool(expected) and core_versions(input_versions(session, space_id)) == core_versions(
        expected
    )


def valid_until(session: Session, *, space_id: int, now: datetime) -> datetime:
    # The existing minor overlay advances at date granularity. UTC midnight is
    # conservative for both solar and lunar birthdays, without duplicating the
    # visibility service's calendar implementation.
    boundary = datetime.combine((now + timedelta(days=1)).date(), time.min)
    expiries = session.scalars(
        select(PersonalFamilyBridge.expires_at).where(
            PersonalFamilyBridge.status == "active",
            PersonalFamilyBridge.expires_at > now,
            (PersonalFamilyBridge.lineage_space_a_id == space_id)
            | (PersonalFamilyBridge.lineage_space_b_id == space_id),
        )
    )
    return min([boundary, *(value for value in expiries if value is not None)])


@contextmanager
def read_transaction(bind: Engine | Connection) -> Iterator[Session]:
    """pysqlite legacy mode does not BEGIN for SELECT; make it explicit."""
    with Session(bind=bind, autoflush=False, expire_on_commit=False) as session:
        session.connection().exec_driver_sql("BEGIN")
        try:
            yield session
        finally:
            session.rollback()


@dataclass(frozen=True)
class ViewerInput:
    account_id: int
    root_user_id: int
    graph: RelationshipGraph
    terms: TermSnapshot
    nodes_json: str
    births: tuple[tuple[int, tuple[str, int] | None], ...]
    captured_at: datetime
    terminology: TerminologyInput = field(default_factory=TerminologyInput)


def read_viewer(
    bind: Engine | Connection,
    *,
    space_id: int,
    account_id: int,
    expected_versions: dict[str, Any],
) -> ViewerInput:
    """Read authorization, graph, fields and term entries in one snapshot."""
    with read_transaction(bind) as session:
        if not versions_match(session, space_id=space_id, expected=expected_versions):
            raise SnapshotChanged
        return viewer_from_session(session, space_id=space_id, account_id=account_id)


def viewer_from_session(
    session: Session,
    *,
    space_id: int,
    account_id: int,
    extra_edges: tuple[ExtraEdge, ...] = (),
) -> ViewerInput:
    """Detach all viewer inputs while the caller owns one explicit snapshot."""
    from app.services.personal_family_view import _node_display
    from app.services.steward_terminology_snapshot import load_input
    from app.services.terms import load_term_snapshot

    account = session.get(Account, account_id)
    if account is None:
        raise SnapshotChanged
    actor = session.get(User, account.user_id)
    member = session.scalar(
        select(SpaceMember.id).where(
            SpaceMember.space_id == space_id,
            SpaceMember.user_id == account.user_id,
            SpaceMember.status == "active",
        )
    )
    if actor is None or actor.deleted_at is not None or member is None:
        raise SnapshotChanged
    graph = load_graph(session, viewer_user_id=actor.id, space_id=space_id, extra_edges=extra_edges)
    nodes: list[dict[str, Any]] = []
    births: list[tuple[int, tuple[str, int] | None]] = []
    for user_id in sorted(graph.node_genders):
        target = session.get(User, user_id)
        if target is None:
            raise SnapshotChanged
        display, level, decision = _node_display(
            session,
            actor,
            target,
            space_id,
            bridge_authorized=user_id in graph.bridge_user_ids,
        )
        nodes.append(
            {
                "user_id": user_id,
                "display": display,
                "visibility_level": level,
                "inclusion_reason_code": "root" if user_id == actor.id else "confirmed_path",
            }
        )
        births.append(
            (
                user_id,
                birth_from_user(target)
                if decision.fields.get("birth") == visibility.FIELD_CLEAR
                else None,
            )
        )
    terms = load_term_snapshot(session, account_id=account.id, space_id=space_id)
    return ViewerInput(
        account_id=account.id,
        root_user_id=actor.id,
        graph=graph,
        terms=terms,
        nodes_json=json.dumps(nodes, ensure_ascii=False, separators=(",", ":")),
        births=tuple(births),
        captured_at=utcnow(),
        terminology=load_input(
            session, account_id=account.id, root_user_id=actor.id, space_id=space_id
        ),
    )


class SnapshotChanged(RuntimeError):
    """The observed input is invalid; discard it rather than retrying its writes."""
