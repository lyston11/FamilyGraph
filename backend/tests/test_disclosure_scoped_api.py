"""逐空间披露偏好（v2 D4 Gap3 + 09-05 高敏感策略放开）。

断言：PUT 携带 space_id 写逐空间覆盖行且仅本人可调；基础五类全局/逐空间
正常读写；09-05 起高敏感类别本人可显式开启（成年人 200 + 审计留痕），未成年
人（is_minor）对高敏感开启请求整体 422（DISCLOSURE_MINOR_FORBIDDEN）；默认
仍为关闭；GET 返回合并矩阵；逐空间行双向覆盖全局行（false 收紧全局 true）。
"""

from __future__ import annotations

import pytest
from conftest import auth_header, create_user_with_pin, login
from fastapi.testclient import TestClient

BASIC_FALSE = {"avatar": False, "photos": False, "dates": False, "bio": False, "attachments": False}

_MINOR_BIRTH = {"cal_type": "solar", "date": "2018-04-25"}


@pytest.fixture()
def scope_scene(db_session):
    """本人 self（宗族成员）+ 代管创建者 creator + 无关 stranger + 空间。"""
    from app.models.space import FamilySpace, SpaceMember
    from app.utils.timeutil import utcnow

    self_user = create_user_with_pin(db_session, "本人", "111111", claim_status="claimed")
    creator = create_user_with_pin(
        db_session, "创建者", "222222", claim_status="claimed", created_by=None
    )
    # managed 子档：creator 有编辑权但不是本人
    ward = create_user_with_pin(
        db_session,
        "受管者",
        "333333",
        pin_must_change=True,
        created_by=creator.id,
        profile_status="provisional",
    )
    stranger = create_user_with_pin(db_session, "路人", "444444", claim_status="claimed")

    now = utcnow()
    lineage = FamilySpace(name="宗族", owner_id=self_user.id, kind="lineage", created_at=now)
    db_session.add(lineage)
    db_session.flush()
    db_session.add(
        SpaceMember(
            space_id=lineage.id,
            user_id=self_user.id,
            added_by=self_user.id,
            role="owner",
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()
    return {
        "space": lineage,
        "self": self_user,
        "creator": creator,
        "ward": ward,
        "stranger": stranger,
    }


def _h(client: TestClient, name: str, pin: str) -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def test_self_sets_space_override_and_matrix_merges(client, db_session, scope_scene) -> None:
    h = _h(client, "本人", "111111")
    space_id = scope_scene["space"].id

    # 全局开放 avatar/bio
    put_global = client.put(
        f"/api/users/{scope_scene['self'].id}/disclosure",
        json={**BASIC_FALSE, "avatar": True, "bio": True},
        headers=h,
    )
    assert put_global.status_code == 200, put_global.text

    # 逐空间覆盖：avatar 收紧为 false、dates 扩展为 true
    put_space = client.put(
        f"/api/users/{scope_scene['self'].id}/disclosure",
        json={**BASIC_FALSE, "dates": True, "space_id": space_id},
        headers=h,
    )
    assert put_space.status_code == 200, put_space.text

    matrix = client.get(f"/api/users/{scope_scene['self'].id}/disclosure", headers=h).json()
    assert matrix["global"]["avatar"] is True
    assert matrix["global"]["dates"] is False
    assert matrix["spaces"] == [
        {
            "space_id": space_id,
            "allowed": {
                "avatar": False,
                "photos": False,
                "dates": True,
                "bio": False,
                "attachments": False,
                "health": False,
                "address": False,
                "school": False,
                "contact": False,
                "private_notes": False,
            },
        }
    ]

    # 合并语义单点验证：该空间上下文下 avatar/bio 被显式收紧、dates 被扩展
    from app.services.disclosure import disclosed_categories

    in_space = disclosed_categories(db_session, scope_scene["self"], space_id)
    outside = disclosed_categories(db_session, scope_scene["self"], None)
    assert in_space == frozenset({"dates"})
    assert outside == frozenset({"avatar", "bio"})


def test_space_scope_rejected_for_non_self_editor(client, scope_scene) -> None:
    """代管创建者有全局编辑权，但逐空间覆盖仅档案本人（防越权代设）。"""
    hc = _h(client, "创建者", "222222")
    r = client.put(
        f"/api/users/{scope_scene['ward'].id}/disclosure",
        json={**BASIC_FALSE, "space_id": scope_scene["space"].id},
        headers=hc,
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "DISCLOSURE_SCOPE_REQUIRES_SELF"

    # 同一主体走全局仍允许（custody 编辑权语义不变）
    ok = client.put(
        f"/api/users/{scope_scene['ward'].id}/disclosure", json=dict(BASIC_FALSE), headers=hc
    )
    assert ok.status_code == 200


@pytest.mark.parametrize("with_space", [False, True])
def test_adult_self_can_enable_high_sensitive(
    client, db_session, scope_scene, with_space: bool
) -> None:
    """09-05 放开：成年人本人可显式开启高敏感类别（全局与逐空间），落行 + 审计。"""
    h = _h(client, "本人", "111111")
    space_id = scope_scene["space"].id
    body = {**BASIC_FALSE, "health": True}
    if with_space:
        body["space_id"] = space_id
    r = client.put(f"/api/users/{scope_scene['self'].id}/disclosure", json=body, headers=h)
    assert r.status_code == 200, r.text

    from app.models.audit_log import AuditLog
    from app.services.disclosure import all_disclosure_flags, disclosed_categories

    # 空间级开启写的是 space 覆盖行（all_disclosure_flags 只读 global 行），
    # 合并视图以 disclosed_categories 为准
    if with_space:
        assert disclosed_categories(db_session, scope_scene["self"], space_id) >= {"health"}
    else:
        assert disclosed_categories(db_session, scope_scene["self"], None) >= {"health"}
        assert all_disclosure_flags(db_session, scope_scene["self"])["health"] is True

    audit_row = (
        db_session.query(AuditLog).filter(AuditLog.action == "disclosure_high_risk_changed").one()
    )
    assert audit_row.detail["categories"] == ["health"]
    assert audit_row.detail["allowed"] == {"health": True}
    assert audit_row.detail["scope"] == ("space" if with_space else "global")
    if with_space:
        assert audit_row.detail["space_id"] == space_id
    # 审计只含类别与目标值，不含任何内容文本
    assert "raw" not in audit_row.detail


def test_minor_high_sensitive_true_rejected_422(client, db_session, scope_scene) -> None:
    """红线：未成年人（is_minor）对高敏感开启请求整体 422，不落任何 true 行。"""
    minor = create_user_with_pin(
        db_session, "未成年", "555555", claim_status="claimed", birth=_MINOR_BIRTH
    )
    db_session.commit()
    h = _h(client, "未成年", "555555")
    for body in (
        {**BASIC_FALSE, "health": True},
        {**BASIC_FALSE, "private_notes": True, "contact": True, "space_id": scope_scene["space"].id},
    ):
        r = client.put(f"/api/users/{minor.id}/disclosure", json=body, headers=h)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "DISCLOSURE_MINOR_FORBIDDEN"

    from app.services.disclosure import all_disclosure_flags

    assert all(v is False for v in all_disclosure_flags(db_session, minor).values())


def test_minor_high_sensitive_false_accepted(client, db_session, scope_scene) -> None:
    """未成年人显式 false 不触发守卫（最小披露默认不变）。"""
    minor = create_user_with_pin(
        db_session, "未成年", "555555", claim_status="claimed", birth=_MINOR_BIRTH
    )
    db_session.commit()
    h = _h(client, "未成年", "555555")
    r = client.put(
        f"/api/users/{minor.id}/disclosure",
        json={**BASIC_FALSE, "health": False},
        headers=h,
    )
    assert r.status_code == 200, r.text


def test_high_sensitive_false_accepted_and_never_stored(client, db_session, scope_scene) -> None:
    """无既有行时显式 false 不落行（false 语义由缺省表达）。"""
    h = _h(client, "本人", "111111")
    body = {**BASIC_FALSE, "health": False}
    r = client.put(f"/api/users/{scope_scene['self'].id}/disclosure", json=body, headers=h)
    assert r.status_code == 200

    from app.models.v2_foundation import DisclosurePreference

    rows = (
        db_session.query(DisclosurePreference)
        .filter(DisclosurePreference.profile_id == scope_scene["self"].id)
        .all()
    )
    assert all(row.category not in ("health",) for row in rows)


def test_high_sensitive_turn_off_writes_audit(db_session, client, scope_scene) -> None:
    """开启→关闭同样属于高敏感披露变更，审计记目标值 False。"""
    from app.models.audit_log import AuditLog

    h = _h(client, "本人", "111111")
    r = client.put(
        f"/api/users/{scope_scene['self'].id}/disclosure",
        json={**BASIC_FALSE, "address": True},
        headers=h,
    )
    assert r.status_code == 200
    r = client.put(
        f"/api/users/{scope_scene['self'].id}/disclosure",
        json={**BASIC_FALSE, "address": False},
        headers=h,
    )
    assert r.status_code == 200

    rows = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "disclosure_high_risk_changed")
        .order_by(AuditLog.id)
        .all()
    )
    assert [row.detail["allowed"]["address"] for row in rows] == [True, False]


def test_unknown_space_404(client, scope_scene) -> None:
    h = _h(client, "本人", "111111")
    r = client.put(
        f"/api/users/{scope_scene['self'].id}/disclosure",
        json={**BASIC_FALSE, "space_id": 987654},
        headers=h,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SPACE_NOT_FOUND"


def test_matrix_reader_domain_matches_put(client, scope_scene) -> None:
    """无编辑权的无关用户读矩阵 → 与不存在同一 404（防枚举）。"""
    hs = _h(client, "路人", "444444")
    r = client.get(f"/api/users/{scope_scene['self'].id}/disclosure", headers=hs)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "USER_NOT_FOUND"


def test_audit_and_event_carry_scope(db_session, client, scope_scene) -> None:
    from app.models.audit_log import AuditLog
    from app.models.v2_foundation import DomainEvent

    h = _h(client, "本人", "111111")
    client.put(
        f"/api/users/{scope_scene['self'].id}/disclosure",
        json={**BASIC_FALSE, "space_id": scope_scene["space"].id},
        headers=h,
    )
    audit_row = db_session.query(AuditLog).filter(AuditLog.action == "disclosure_updated").one()
    assert audit_row.detail["scope"] == "space"
    event = db_session.query(DomainEvent).filter(DomainEvent.type == "disclosure.updated").one()
    assert event.payload["scope"] == "space"
    assert event.space_id == scope_scene["space"].id
