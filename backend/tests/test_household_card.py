"""Household card 服务端合同测试（PFV-F1）。

覆盖：字段白名单、family_user 主体隔离、lineage/未知/越权空间安全 404、
撤权后立即不可读、ETag/304 与「授权先于 304」。
"""

from __future__ import annotations

import pytest
from conftest import (
    auth_header,
    create_agent_fixture,
    create_space_member,
    create_system_admin,
    create_user_with_pin,
    login,
)

from app import config
from app.models.space import SpaceMember
from app.utils.timeutil import utcnow

EXPECTED_TOP_LEVEL_KEYS = {
    "space_id",
    "space_kind",
    "space_name",
    "view_version",
    "computed_at",
    "viewer",
    "members",
    "allowed_actions",
}


@pytest.fixture(autouse=True)
def _pfv_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)


def _login_header(client, name: str) -> dict[str, str]:
    resp = login(client, name, "123456")
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def test_household_card_happy_path_fields(client, db_session) -> None:
    admin, space = create_agent_fixture(db_session, name="hc-admin")
    member = create_user_with_pin(db_session, "hc-member", "123456")
    create_space_member(db_session, space.id, member.id)

    resp = client.get(
        "/api/household-card",
        params={"space_id": space.id},
        headers=_login_header(client, "hc-admin"),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert set(payload.keys()) == EXPECTED_TOP_LEVEL_KEYS
    assert payload["space_id"] == space.id
    assert payload["space_kind"] == "household"
    assert payload["space_name"] == space.name
    assert isinstance(payload["view_version"], int) and payload["view_version"] >= 1
    assert payload["computed_at"] is not None

    # viewer = 管理员本人；成员列表不含 viewer，且投影与 PersonalFamilyView 同口径
    assert payload["viewer"]["id"] == admin.id
    assert payload["viewer"]["name"] == admin.name
    member_ids = {m["user_id"] for m in payload["members"]}
    assert member_ids == {member.id}
    row = payload["members"][0]
    assert set(row.keys()) == {"user_id", "display", "household_label", "visibility_level"}
    assert row["household_label"] == "成员"
    assert row["visibility_level"] == "household_detail"
    assert row["display"]["id"] == member.id
    assert row["display"]["name"] == member.name
    assert {
        "id",
        "name",
        "gender",
        "birth",
        "death",
        "bio",
        "avatar_path",
        "privacy_mode",
        "claim_status",
    } <= set(row["display"].keys())

    # allowed_actions：active 成员可邀请；identity_confirmed 可建共同家庭
    actions = payload["allowed_actions"]
    assert actions["can_invite_members"] is True
    assert actions["can_create_household"] is True
    assert actions["empty_state_hint"] is None
    assert resp.headers.get("etag")


def test_household_card_admin_label_and_empty_state(client, db_session) -> None:
    _admin, space = create_agent_fixture(db_session, name="hc-label")
    member = create_user_with_pin(db_session, "hc-label-member", "123456")
    create_space_member(db_session, space.id, member.id)

    # 从普通成员视角读卡：管理员出现在成员列表且标签为「管理员」
    resp = client.get(
        "/api/household-card",
        params={"space_id": space.id},
        headers=_login_header(client, "hc-label-member"),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    labels = {m["household_label"] for m in payload["members"]}
    assert labels == {"管理员"}
    # 成员视角下成员列表只有管理员一人（不含自己）
    assert len(payload["members"]) == 1
    assert payload["allowed_actions"]["can_invite_members"] is True


def test_household_card_family_user_only(client, db_session) -> None:
    create_agent_fixture(db_session, name="hc-sys")
    create_system_admin(db_session)
    resp = client.get("/api/household-card", params={"space_id": 1})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_UNAUTHORIZED"


def test_household_card_lineage_space_safe_404(client, db_session) -> None:
    from app.models.space import FamilySpace

    owner, household_space = create_agent_fixture(db_session, name="hc-lineage")
    lineage = FamilySpace(
        name="hc-lineage-space", kind="lineage", owner_id=owner.id, created_at=utcnow()
    )
    db_session.add(lineage)
    db_session.commit()
    create_space_member(db_session, lineage.id, owner.id)

    headers = _login_header(client, "hc-lineage")
    # 有权限的 lineage 空间与未知空间同一拒绝路径（无存在性探针）
    resp_lineage = client.get(
        "/api/household-card", params={"space_id": lineage.id}, headers=headers
    )
    assert resp_lineage.status_code == 404
    assert resp_lineage.json()["error"]["code"] == "PERSONAL_FAMILY_VIEW_NOT_FOUND"
    resp_unknown = client.get("/api/household-card", params={"space_id": 999999}, headers=headers)
    assert resp_unknown.status_code == 404
    assert resp_unknown.json()["error"]["code"] == "PERSONAL_FAMILY_VIEW_NOT_FOUND"
    # household fixture 空间存在但不影响 lineage 404 语义
    assert household_space.kind == "household"


def test_household_card_unknown_or_unauthorized_space_safe_404(client, db_session) -> None:
    _admin, space = create_agent_fixture(db_session, name="hc-idor")
    outsider = create_user_with_pin(db_session, "hc-outsider", "123456")
    # outsider 在别的空间有成员资格，但不在目标空间：读取目标空间必须安全 404
    _admin2, other_space = create_agent_fixture(db_session, name="hc-idor2")
    create_space_member(db_session, other_space.id, outsider.id)

    headers = _login_header(client, "hc-outsider")
    resp = client.get("/api/household-card", params={"space_id": space.id}, headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PERSONAL_FAMILY_VIEW_NOT_FOUND"
    resp_other = client.get(
        "/api/household-card", params={"space_id": other_space.id}, headers=headers
    )
    assert resp_other.status_code == 200


def test_household_card_invalid_space_id_422(client, db_session) -> None:
    create_agent_fixture(db_session, name="hc-422")
    headers = _login_header(client, "hc-422")
    for bad in (0, -1):
        resp = client.get("/api/household-card", params={"space_id": bad}, headers=headers)
        assert resp.status_code == 422


def test_household_card_revoked_membership_hidden_on_next_read(client, db_session) -> None:
    admin, space = create_agent_fixture(db_session, name="hc-revoke")
    member = create_user_with_pin(db_session, "hc-revoke-member", "123456")
    create_space_member(db_session, space.id, member.id)
    headers = _login_header(client, "hc-revoke-member")
    assert (
        client.get(
            "/api/household-card", params={"space_id": space.id}, headers=headers
        ).status_code
        == 200
    )

    row = (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space.id, SpaceMember.user_id == member.id)
        .one()
    )
    row.status = "removed"
    row.updated_at = utcnow()
    db_session.commit()

    resp = client.get("/api/household-card", params={"space_id": space.id}, headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PERSONAL_FAMILY_VIEW_NOT_FOUND"
    # 管理员视角：被移出者立即从成员投影消失
    admin_headers = _login_header(client, "hc-revoke")
    payload = client.get(
        "/api/household-card", params={"space_id": space.id}, headers=admin_headers
    ).json()
    assert {m["user_id"] for m in payload["members"]} == set()


def test_household_card_etag_304_and_authorization_before_304(client, db_session) -> None:
    admin, space = create_agent_fixture(db_session, name="hc-etag")
    member = create_user_with_pin(db_session, "hc-etag-member", "123456")
    create_space_member(db_session, space.id, member.id)
    headers = _login_header(client, "hc-etag")

    first = client.get("/api/household-card", params={"space_id": space.id}, headers=headers)
    etag = first.headers["etag"]
    assert etag

    cached = client.get(
        "/api/household-card",
        params={"space_id": space.id},
        headers={**headers, "If-None-Match": etag},
    )
    assert cached.status_code == 304

    # 载荷变化（改名）→ ETag 失效 → 200 新载荷
    from app.models.space import FamilySpace

    row = db_session.get(FamilySpace, space.id)
    row.name = "hc-etag-renamed"
    db_session.commit()
    refreshed = client.get(
        "/api/household-card",
        params={"space_id": space.id},
        headers={**headers, "If-None-Match": etag},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["space_name"] == "hc-etag-renamed"
    assert refreshed.headers["etag"] != etag

    # 授权复核先于 304：撤权后携带旧 ETag 也必须安全 404 而非 304
    membership = (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space.id, SpaceMember.user_id == admin.id)
        .one()
    )
    membership.status = "removed"
    membership.updated_at = utcnow()
    db_session.commit()
    revoked = client.get(
        "/api/household-card",
        params={"space_id": space.id},
        headers={**headers, "If-None-Match": refreshed.headers["etag"]},
    )
    assert revoked.status_code == 404


def test_household_card_disabled_flag_503(client, db_session, monkeypatch) -> None:
    _admin, space = create_agent_fixture(db_session, name="hc-flag")
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", False)
    resp = client.get(
        "/api/household-card",
        params={"space_id": space.id},
        headers=_login_header(client, "hc-flag"),
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "PERSONAL_FAMILY_VIEW_DISABLED"
