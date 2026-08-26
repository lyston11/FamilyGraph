"""GET /spaces/{id}/profile-refs（AC-F2 可观测性，v2 D4 Gap1）。

断言：仅 active 引用、最小字段集 {profile_id, name, added_at}；
授权=该空间 active 成员（含 guest）；pending/无关用户与不存在同一 404。
"""

from __future__ import annotations

import pytest
from conftest import auth_header, create_user_with_pin, login
from fastapi.testclient import TestClient


@pytest.fixture()
def ref_scene(db_session):
    """宗族空间：owner 甲 + active 成员 乙 + guest 丙 + pending 丁 + 无关 庚；引用先祖。"""
    from app.models.space import FamilySpace, SpaceProfileRef
    from app.utils.timeutil import utcnow

    jia = create_user_with_pin(db_session, "甲", "111111", claim_status="claimed")
    yi = create_user_with_pin(db_session, "乙", "222222", claim_status="claimed")
    bing = create_user_with_pin(db_session, "丙", "333333", claim_status="claimed")
    ding = create_user_with_pin(db_session, "丁", "444444", claim_status="claimed")
    geng = create_user_with_pin(db_session, "庚", "555555", claim_status="claimed")
    ancestor = create_user_with_pin(db_session, "先祖", "121212", profile_status="provisional")

    now = utcnow()
    lineage = FamilySpace(name="宗族", owner_id=jia.id, kind="lineage", created_at=now)
    db_session.add(lineage)
    db_session.flush()

    from app.models.space import SpaceMember

    for user, role in ((jia, "owner"), (yi, "member")):
        db_session.add(
            SpaceMember(
                space_id=lineage.id,
                user_id=user.id,
                added_by=jia.id,
                role=role,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
    db_session.add(
        SpaceMember(
            space_id=lineage.id,
            user_id=bing.id,
            added_by=jia.id,
            role="guest",
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.add(
        SpaceMember(
            space_id=lineage.id,
            user_id=ding.id,
            added_by=jia.id,
            role="member",
            status="pending",
            created_at=now,
            updated_at=now,
        )
    )
    # active 引用一条 + removed 一条（后者不得出现）
    db_session.add(
        SpaceProfileRef(
            space_id=lineage.id,
            user_id=ancestor.id,
            added_by=jia.id,
            status="active",
            created_at=now,
        )
    )
    other = create_user_with_pin(db_session, "已移除", "131313", profile_status="provisional")
    db_session.add(
        SpaceProfileRef(
            space_id=lineage.id,
            user_id=other.id,
            added_by=jia.id,
            status="removed",
            created_at=now,
        )
    )
    db_session.commit()
    return {
        "space": lineage,
        "甲": jia,
        "乙": yi,
        "丙": bing,
        "丁": ding,
        "庚": geng,
        "先祖": ancestor,
        "removed": other,
    }


def _h(client: TestClient, name: str, pin: str) -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def test_active_member_reads_minimal_ref_fields(client, ref_scene) -> None:
    h = _h(client, "甲", "111111")
    r = client.get(f"/api/spaces/{ref_scene['space'].id}/profile-refs", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    # 最小字段集：无 dates/bio/avatar 等任何档案字段
    assert set(row.keys()) == {"profile_id", "name", "added_at"}
    assert row["profile_id"] == ref_scene["先祖"].id
    assert row["name"] == "先祖"


def test_guest_and_regular_member_also_read(client, ref_scene) -> None:
    space_id = ref_scene["space"].id
    for name, pin in (("乙", "222222"), ("丙", "333333")):
        h = _h(client, name, pin)
        r = client.get(f"/api/spaces/{space_id}/profile-refs", headers=h)
        assert r.status_code == 200
        assert [row["name"] for row in r.json()] == ["先祖"]


@pytest.mark.parametrize(("name", "pin"), [("丁", "444444"), ("庚", "555555")])
def test_pending_member_and_outsider_get_404(client, ref_scene, name: str, pin: str) -> None:
    """pending 与无权者同 404（防枚举，与空间不存在不可区分）。"""
    h = _h(client, name, pin)
    r = client.get(f"/api/spaces/{ref_scene['space'].id}/profile-refs", headers=h)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SPACE_NOT_FOUND"


def test_nonexistent_space_404(client, db_session) -> None:
    create_user_with_pin(db_session, "路人", "999999", claim_status="claimed")
    h = _h(client, "路人", "999999")
    r = client.get("/api/spaces/99999/profile-refs", headers=h)
    assert r.status_code == 404


def test_removed_refs_not_listed(db_session, client, ref_scene) -> None:
    names = [
        row["name"]
        for row in client.get(
            f"/api/spaces/{ref_scene['space'].id}/profile-refs",
            headers=_h(client, "甲", "111111"),
        ).json()
    ]
    assert "已移除" not in names
