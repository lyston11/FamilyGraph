"""PersonalFamilyView 上的只读亲属推荐与冷却记忆。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import config
from app.errors import PERSONAL_FAMILY_VIEW_NOT_FOUND, raise_api_error
from app.models.account import Account
from app.models.relation import Relation
from app.models.relationship_facts import SourceFact
from app.models.steward import BehaviorProjection
from app.models.user import User
from app.models.v2_foundation import ProfileFactReview
from app.services import personal_family_view, steward, visibility
from app.utils.timeutil import utcnow

DISMISS_PREFIX = "kinship_recommendation_dismissed:"
REASON_CONFIRMED_BLOOD_PATH = "confirmed_blood_path"
REASON_PENDING_RELATION_FACT = "pending_relation_fact"


def _cooldown_active(
    session: Session, *, space_id: int, account_id: int, target_id: int, now: datetime
) -> bool:
    row = session.scalar(
        select(BehaviorProjection).where(
            BehaviorProjection.space_id == space_id,
            BehaviorProjection.account_id == account_id,
            BehaviorProjection.projection_key == f"{DISMISS_PREFIX}{target_id}",
        )
    )
    if row is None:
        return False
    raw = row.value_json.get("until")
    if not isinstance(raw, str):
        return False
    try:
        return datetime.fromisoformat(raw) > now
    except ValueError:
        return False


def recommendations_payload(session: Session, *, account: Account, space_id: int) -> dict[str, Any]:
    payload = personal_family_view.current_view_payload(session, account=account, space_id=space_id)
    version = payload["view_version"] if payload is not None else 0
    base: dict[str, Any] = {
        "space_id": space_id,
        "view_status": payload["status"] if payload is not None else "never_computed",
        "view_version": version,
        "generated_from_view_version": version,
        "items": [],
        "truncated": False,
    }
    if payload is None or payload["status"] != "current":
        return base
    nodes = {int(node["user_id"]): node for node in payload["nodes"]}
    actor = session.get(User, account.user_id)
    if actor is None:
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "个人家族视图不存在")
    items: list[dict[str, Any]] = []
    now = utcnow()
    for edge in payload["edges"]:
        target_id = int(edge["to_user_id"])
        if target_id not in nodes or _cooldown_active(
            session, space_id=space_id, account_id=account.id, target_id=target_id, now=now
        ):
            continue
        path = edge.get("path") or []
        if any(
            isinstance(step, dict) and step.get("edge_type") in {"spouse", "partner"}
            for step in path
        ):
            continue
        items.append(
            {
                "category": "confirmed_kinship",
                "target_user_id": target_id,
                "display": nodes[target_id]["display"],
                "term": edge.get("term"),
                "concept_code": edge.get("concept_code"),
                "path_class": edge.get("path_class"),
                "path_summary": [step.get("edge_type") for step in path if isinstance(step, dict)],
                "reason_code": REASON_CONFIRMED_BLOOD_PATH,
            }
        )
    for fact in session.scalars(
        select(SourceFact).where(
            SourceFact.state == "proposed",
            or_(SourceFact.space_id.is_(None), SourceFact.space_id == space_id),
            or_(SourceFact.subject_user_id == actor.id, SourceFact.object_user_id == actor.id),
        )
    ).all():
        pending_target_id = (
            fact.object_user_id if fact.subject_user_id == actor.id else fact.subject_user_id
        )
        if pending_target_id == actor.id or _cooldown_active(
            session,
            space_id=space_id,
            account_id=account.id,
            target_id=pending_target_id,
            now=now,
        ):
            continue
        target = session.get(User, pending_target_id)
        if target is None:
            continue
        decision = visibility.evaluate(
            session, actor, target, space_context=space_id, purpose=visibility.PURPOSE_GRAPH
        )
        if not decision.visible:
            continue
        items.append(
            {
                "category": "pending_relation",
                "target_user_id": pending_target_id,
                "display": visibility.payload_from_decision(decision, target),
                "proposed_fact_type": fact.fact_type,
                "reason_code": REASON_PENDING_RELATION_FACT,
            }
        )

    pending_relations = session.scalars(
        select(Relation).where(
            Relation.status == "pending",
            or_(Relation.from_user == actor.id, Relation.to_user == actor.id),
            or_(Relation.pending_space_id.is_(None), Relation.pending_space_id == space_id),
        )
    ).all()
    for relation in pending_relations:
        pending_target_id = (
            relation.to_user if relation.from_user == actor.id else relation.from_user
        )
        if _cooldown_active(
            session,
            space_id=space_id,
            account_id=account.id,
            target_id=pending_target_id,
            now=now,
        ):
            continue
        target = session.get(User, pending_target_id)
        if target is None:
            continue
        decision = visibility.evaluate(
            session, actor, target, space_context=space_id, purpose=visibility.PURPOSE_GRAPH
        )
        if not decision.visible:
            continue
        items.append(
            {
                "category": "pending_relation",
                "target_user_id": pending_target_id,
                "display": visibility.payload_from_decision(decision, target),
                "proposed_relation_type": relation.dir_class,
                "reason_code": REASON_PENDING_RELATION_FACT,
            }
        )

    review_rows = session.scalars(
        select(ProfileFactReview).where(
            ProfileFactReview.profile_id == actor.id,
            ProfileFactReview.status == "proposed",
            ProfileFactReview.item_type == "relation_to_creator",
        )
    ).all()
    for review in review_rows:
        creator_id = review.item_ref_json.get("creator_id")
        if not isinstance(creator_id, int) or _cooldown_active(
            session,
            space_id=space_id,
            account_id=account.id,
            target_id=creator_id,
            now=now,
        ):
            continue
        target = session.get(User, creator_id)
        if target is None:
            continue
        decision = visibility.evaluate(
            session, actor, target, space_context=space_id, purpose=visibility.PURPOSE_GRAPH
        )
        if not decision.visible:
            continue
        items.append(
            {
                "category": "pending_relation",
                "target_user_id": creator_id,
                "display": visibility.payload_from_decision(decision, target),
                "reason_code": REASON_PENDING_RELATION_FACT,
            }
        )
    unique: dict[tuple[str, int], dict[str, Any]] = {}
    for item in items:
        unique.setdefault((str(item["category"]), int(item["target_user_id"])), item)
    items = list(unique.values())
    items.sort(key=lambda item: (str(item["category"]), int(item["target_user_id"])))
    base["items"] = items
    return base


def dismiss_recommendation(
    session: Session,
    *,
    account: Account,
    space_id: int,
    target_user_id: int,
    category: str,
) -> None:
    payload = recommendations_payload(session, account=account, space_id=space_id)
    if payload["view_status"] != "current" or not any(
        int(item["target_user_id"]) == target_user_id for item in payload["items"]
    ):
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "推荐不存在")
    until = utcnow() + timedelta(days=config.STEWARD_COOLDOWN_DAYS)
    steward.put_projection(
        session,
        space_id=space_id,
        account_id=account.id,
        projection_key=f"{DISMISS_PREFIX}{target_user_id}",
        value={"until": until.isoformat(), "category": category},
    )
