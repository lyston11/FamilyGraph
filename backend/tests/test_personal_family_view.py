from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.personal_family_view import PersonalFamilyViewNode
from app.models.space import SpaceMember
from app.services import personal_family_view
from app.services import source_facts as sf
from conftest import create_agent_fixture, create_space_member, create_user_with_pin


def _confirm(session, fact_type: str, subject_id: int, object_id: int, space_id: int) -> None:
    fact = sf.create_source_fact(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        provenance="manual_entry",
        space_id=space_id,
    )
    sf.transition_source_fact(session, fact, "confirm")


def test_personal_family_view_keeps_authorized_members_without_paths(db_session) -> None:
    """09-18 R2：授权成员保留为节点；关系路径只决定个人称谓边。

    旧合同「只有 confirmed-reachable 的人进入 PFV」会把合法空间成员从家族树中
    丢掉，与空间成员资格/前端孤立成员布局冲突，已废止。
    """
    creator, space = create_agent_fixture(db_session, name="pfv")
    viewer = create_user_with_pin(db_session, "pfv-viewer", "123456", gender="f")
    parent = create_user_with_pin(db_session, "pfv-parent", "123456", gender="m")
    unrelated = create_user_with_pin(db_session, "pfv-unrelated", "123456", gender="f")
    for user in (viewer, parent, unrelated):
        create_space_member(db_session, space.id, user.id)
    _confirm(db_session, "biological_parent", parent.id, viewer.id, space.id)

    view = personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
    reasons = dict(
        db_session.execute(
            select(
                PersonalFamilyViewNode.user_id, PersonalFamilyViewNode.inclusion_reason_code
            ).where(PersonalFamilyViewNode.view_id == view.id)
        ).all()
    )
    # 所有授权成员都在（含无路径者），不是只有 confirmed 可达者
    assert set(reasons) == {viewer.id, parent.id, unrelated.id, creator.id}
    assert reasons[viewer.id] == "root"
    assert reasons[parent.id] == "confirmed_path"
    assert reasons[unrelated.id] == "space_member"
    assert reasons[creator.id] == "space_member"

    payload = personal_family_view.view_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    assert payload["status"] == "current"
    assert payload["edges"][0]["concept_code"] == "Um"
    # 无路径成员不写个人称谓边，也不伪造 confirmed_path
    assert {edge["to_user_id"] for edge in payload["edges"]} == {parent.id}
    assert unrelated.id not in {edge["to_user_id"] for edge in payload["edges"]}


def test_personal_family_view_isolated_members_have_no_edges(db_session) -> None:
    """多个无路径成员：各自独立保留，且不产生任何边（含拓扑边）。"""
    _account, space = create_agent_fixture(db_session, name="pfv-iso")
    viewer = create_user_with_pin(db_session, "pfv-iso-v", "123456", gender="f")
    create_space_member(db_session, space.id, viewer.id)
    first = create_user_with_pin(db_session, "pfv-iso-a", "123456", gender="m")
    second = create_user_with_pin(db_session, "pfv-iso-b", "123456", gender="f")
    for user in (first, second):
        create_space_member(db_session, space.id, user.id)

    personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
    payload = personal_family_view.view_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    node_ids = {node["user_id"] for node in payload["nodes"]}
    assert {first.id, second.id} <= node_ids
    assert payload["edges"] == []
    assert payload["topology_edges"] == []
    for node in payload["nodes"]:
        if node["user_id"] in (first.id, second.id):
            assert node["inclusion_reason_code"] == "space_member"


def test_personal_family_view_isolated_member_dropped_after_removal(db_session) -> None:
    """成员资格撤回后孤立节点不再返回（授权重验仍逐节点生效）。"""
    _account, space = create_agent_fixture(db_session, name="pfv-iso-revoke")
    viewer = create_user_with_pin(db_session, "pfv-iso-revoke-v", "123456", gender="f")
    create_space_member(db_session, space.id, viewer.id)
    guest = create_user_with_pin(db_session, "pfv-iso-revoke-g", "123456", gender="m")
    create_space_member(db_session, space.id, guest.id)

    personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
    before = personal_family_view.view_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    assert guest.id in {node["user_id"] for node in before["nodes"]}

    row = db_session.scalar(
        select(SpaceMember).where(SpaceMember.space_id == space.id, SpaceMember.user_id == guest.id)
    )
    assert row is not None
    row.status = "removed"
    db_session.commit()
    personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
    after = personal_family_view.view_payload(db_session, account=viewer.account, space_id=space.id)
    assert guest.id not in {node["user_id"] for node in after["nodes"]}


def test_personal_family_view_requires_active_membership(db_session) -> None:
    _account, space = create_agent_fixture(db_session, name="pfv-denied")
    viewer = create_user_with_pin(db_session, "pfv-denied-viewer", "123456", gender="f")
    with pytest.raises(HTTPException) as exc_info:
        personal_family_view.get_view(db_session, account=viewer.account, space_id=space.id)
    assert exc_info.value.status_code == 404
