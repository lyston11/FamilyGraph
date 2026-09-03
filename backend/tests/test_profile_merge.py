from __future__ import annotations

from fastapi import HTTPException

from app.commands import members as member_commands
from app.commands.context import ActorContext
from app.errors import extract_api_error
from app.models.relation import Relation
from app.models.space import FamilySpace, SpaceMember, SpaceProfileRef
from app.models.relationship_facts import SourceFact
from app.services import person_identity
from app.utils.timeutil import utcnow
from conftest import create_user_with_pin


def _space(db, owner, *users):
    space = FamilySpace(name="merge-space", owner_id=owner.id, kind="lineage", created_at=utcnow())
    db.add(space)
    db.flush()
    for user in (owner, *users):
        db.add(SpaceMember(space_id=space.id, user_id=user.id, added_by=owner.id, role="member", status="active", created_at=utcnow(), updated_at=utcnow()))
        db.add(SpaceProfileRef(space_id=space.id, user_id=user.id, added_by=owner.id, status="active", created_at=utcnow()))
    db.commit()
    return space


def _ctx(actor):
    return ActorContext(user_id=actor.id, account_id=actor.account.id, account_status=actor.account.status)


def test_merge_requires_same_space_and_repoints_relation_creator(db_session):
    actor = create_user_with_pin(db_session, "创建者", "111111", claim_status="claimed")
    survivor = create_user_with_pin(db_session, "李秀英", "222222", claim_status="managed", birth={"cal_type": "solar", "date": "1948-03-12"}, created_by=actor.id)
    retired = create_user_with_pin(db_session, "李秀英", "333333", claim_status="managed", birth={"cal_type": "solar", "date": "1948-03-12"}, created_by=actor.id)
    space = _space(db_session, actor, survivor, retired)
    relation = Relation(from_user=survivor.id, to_user=actor.id, dir_class="elder", created_by=retired.id, status="pending", created_at=utcnow(), updated_at=utcnow())
    db_session.add(relation)
    db_session.commit()

    result = member_commands.merge_duplicate_profile(db_session, _ctx(actor), space_id=space.id, survivor_id=survivor.id, retired_id=retired.id, confirm_same_person=True)
    assert result.already_merged is False
    assert db_session.get(Relation, relation.id).created_by == survivor.id
    assert db_session.get(type(retired), retired.id) is None


def test_merge_rejects_pair_outside_requested_space(db_session):
    actor = create_user_with_pin(db_session, "创建者", "111111", claim_status="claimed")
    survivor = create_user_with_pin(db_session, "王丽", "222222", claim_status="managed", birth={"cal_type": "solar", "date": "1948-03-12"}, created_by=actor.id)
    retired = create_user_with_pin(db_session, "王丽", "333333", claim_status="managed", birth={"cal_type": "solar", "date": "1948-03-12"}, created_by=actor.id)
    space = _space(db_session, actor)
    db_session.commit()
    try:
        member_commands.merge_duplicate_profile(db_session, _ctx(actor), space_id=space.id, survivor_id=survivor.id, retired_id=retired.id, confirm_same_person=True)
    except HTTPException as exc:
        assert extract_api_error(exc.detail)["code"] == "USER_NOT_FOUND"
    else:
        raise AssertionError("expected safe not found")


def test_merge_does_not_repoint_revoked_facts(db_session):
    actor = create_user_with_pin(db_session, "创建者", "111111", claim_status="claimed")
    survivor = create_user_with_pin(db_session, "赵强", "222222", claim_status="managed", birth={"cal_type": "solar", "date": "1948-03-12"}, created_by=actor.id)
    retired = create_user_with_pin(db_session, "赵强", "333333", claim_status="managed", birth={"cal_type": "solar", "date": "1948-03-12"}, created_by=actor.id)
    space = _space(db_session, actor, survivor, retired)
    fact = SourceFact(fact_type="direct_sibling", subject_user_id=retired.id, object_user_id=actor.id, space_id=space.id, provenance="manual_entry", state="revoked", revision=4, created_at=utcnow(), updated_at=utcnow())
    db_session.add(fact)
    db_session.commit()
    member_commands.merge_duplicate_profile(db_session, _ctx(actor), space_id=space.id, survivor_id=survivor.id, retired_id=retired.id, confirm_same_person=True)
    assert db_session.get(SourceFact, fact.id) is None or db_session.get(SourceFact, fact.id).state == "revoked"
