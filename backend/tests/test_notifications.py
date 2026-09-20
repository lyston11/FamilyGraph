"""Notifications 投影与已读命令合同测试（PFV-F3）。

覆盖：出卡/邀请/加入申请的自然来源通知、收件人隔离与安全 404、字段白名单、
actor 遮蔽哨兵、已读只改 read_at（ActionCard state/revision 不变）、全部已读
空间隔离、ETag/304（授权先于 304）、引用损坏 fail-closed 丢弃。
"""

from __future__ import annotations

from typing import Any

import pytest

from app import config
from app.models.steward import ActionCard
from app.services import action_cards
from conftest import (
    auth_header,
    create_agent_fixture,
    create_space_member,
    create_user_with_pin,
    login,
)

ITEM_KEYS = {
    "id",
    "space_id",
    "kind",
    "payload",
    "domain_status",
    "action_card",
    "suggestion",
    "created_at",
    "read_at",
}
PAYLOAD_KEYS = {"title", "summary", "actor_name", "space_name"}
MASKED = {"__masked__": True}


@pytest.fixture(autouse=True)
def _pfv_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)


def _login_header(client, name: str) -> dict[str, str]:
    resp = login(client, name, "123456")
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def _list(client, headers, space_id: int, **kwargs: Any):
    return client.get(
        "/api/notifications", params={"space_id": space_id, **kwargs}, headers=headers
    )


def _make_card(session, space_id: int, recipient_account_id: int, subject_user_id: int):
    card, outcome = action_cards.create_card(
        session,
        kind="household_link",
        space_id=space_id,
        recipient_account_id=recipient_account_id,
        subject_user_id=subject_user_id,
        object_user_id=None,
        evidence_json={"k": "v"},
        proposed_action_json={"action": "create_shared_household"},
        reason_text="test",
    )
    assert outcome == "created" and card is not None
    session.commit()
    return card


def test_action_card_notification_contract(client, db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="nt-card")
    card = _make_card(db_session, space.id, viewer.account.id, viewer.id)

    resp = _list(client, _login_header(client, "nt-card"), space.id)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["space_id"] == space.id
    assert data["unread_count"] == 1
    assert len(data["items"]) == 1

    item = data["items"][0]
    assert set(item.keys()) == ITEM_KEYS
    assert item["space_id"] == space.id
    assert item["kind"] == "action_card"
    assert set(item["payload"].keys()) == PAYLOAD_KEYS
    assert item["payload"]["title"].endswith("推荐待确认")
    assert item["payload"]["summary"] is None
    # 本人主体恒可见：actor_name 为明文名字
    assert item["payload"]["actor_name"] == "nt-card"
    assert item["payload"]["space_name"] == space.name
    assert item["domain_status"] == "pending"
    assert item["action_card"] == {"card_id": card.id, "revision": card.revision}
    assert item["read_at"] is None
    assert item["created_at"]
    assert resp.headers.get("etag")


def test_recipient_isolation_and_safe_404(client, db_session) -> None:
    _a, space_a = create_agent_fixture(db_session, name="nt-iso-a")
    b, space_b = create_agent_fixture(db_session, name="nt-iso-b")
    _make_card(db_session, space_b.id, b.account.id, b.id)
    headers_a = _login_header(client, "nt-iso-a")

    own = _list(client, headers_a, space_a.id).json()
    assert own["items"] == [] and own["unread_count"] == 0

    # 他人空间 / 未知空间：与 PersonalFamilyView 同一安全 404，无存在性探针
    for foreign in (space_b.id, 999999):
        resp = _list(client, headers_a, foreign)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "PERSONAL_FAMILY_VIEW_NOT_FOUND"
    assert _list(client, headers_a, 0).status_code == 422

    # 单条已读收件人隔离：A 不能读 B 的通知
    b_headers = _login_header(client, "nt-iso-b")
    notif_id = _list(client, b_headers, space_b.id).json()["items"][0]["id"]
    resp = client.post(f"/api/notifications/{notif_id}/read", headers=headers_a)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOTIFICATION_NOT_FOUND"


def test_invite_notification_live_domain_status(client, db_session) -> None:
    admin, space = create_agent_fixture(db_session, name="nt-invite")
    invitee = create_user_with_pin(db_session, "nt-invitee", "123456")
    db_session.commit()

    resp = client.post(
        f"/api/spaces/{space.id}/members",
        json={"user_id": invitee.id, "relation_label": "堂兄弟"},
        headers=_login_header(client, "nt-invite"),
    )
    assert resp.status_code == 201, resp.text
    member_id = resp.json()["id"]

    # 授权先于内容：受邀人仍为 pending，读取该空间通知是安全 404
    pending_view = _list(client, _login_header(client, "nt-invitee"), space.id)
    assert pending_view.status_code == 404
    assert pending_view.json()["error"]["code"] == "PERSONAL_FAMILY_VIEW_NOT_FOUND"

    # 09-20 审批链：房主先批准，受邀人再接受，之后才 active
    approved = client.post(
        f"/api/space-memberships/{member_id}/approve",
        headers=_login_header(client, "nt-invite"),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "pending"  # invite 仍需受邀人本人接受
    accept = client.post(
        f"/api/space-memberships/{member_id}/accept",
        headers=_login_header(client, "nt-invitee"),
    )
    assert accept.status_code == 200, accept.text
    data = _list(client, _login_header(client, "nt-invitee"), space.id).json()
    # 09-20 可达性修复：受邀人同时收到「邀请」与「房主已批准，等待你接受」两条
    # （后者让 awaiting_owner → awaiting_me 的跃迁不再静默）
    assert data["unread_count"] == 2
    assert {item["payload"]["title"] for item in data["items"]} == {
        "你有新的家庭空间邀请",
        "邀请已获房主批准，等待你接受",
    }
    item = next(i for i in data["items"] if i["payload"]["title"] == "你有新的家庭空间邀请")
    assert item["kind"] == "space_membership"
    assert item["action_card"] is None
    assert item["domain_status"] == "active"
    assert item["payload"]["actor_name"] == "nt-invite"
    assert item["read_at"] is None  # 接受不改变已读状态


def test_join_request_notification_to_target(client, db_session) -> None:
    manager, space = create_agent_fixture(db_session, name="nt-join")
    requester = create_user_with_pin(db_session, "nt-joiner", "123456")
    # join-by-user 要求申请人对目标用户可见：代管创建者链接（custodian）提供可见性。
    manager.created_by = requester.id
    # 09-20 准入边界：双方须同属该 active 家族空间，且目标的空间配对到该家族空间。
    from app.models.space import FamilySpace
    from app.utils.timeutil import utcnow

    lineage = FamilySpace(
        name="nt-join-lineage", kind="lineage", owner_id=manager.id, created_at=utcnow()
    )
    db_session.add(lineage)
    db_session.flush()
    create_space_member(db_session, lineage.id, manager.id, role="space_admin")
    create_space_member(db_session, lineage.id, requester.id)
    space.lineage_space_id = lineage.id
    db_session.commit()

    resp = client.post(
        "/api/spaces/join-by-user",
        json={
            "lineage_space_id": lineage.id,
            "target_user_id": manager.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "nt-joiner"),
    )
    assert resp.status_code == 201, resp.text

    data = _list(client, _login_header(client, "nt-join"), space.id).json()
    assert data["unread_count"] == 1
    item = data["items"][0]
    assert item["kind"] == "space_membership"
    assert item["domain_status"] == "pending"
    assert item["payload"]["title"] == "有新的空间加入申请"
    assert item["payload"]["actor_name"] == "nt-joiner"


def test_actor_masked_when_unrelated(client, db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="nt-mask")
    outsider = create_user_with_pin(db_session, "nt-outsider", "123456")
    db_session.commit()
    _make_card(db_session, space.id, viewer.account.id, outsider.id)

    data = _list(client, _login_header(client, "nt-mask"), space.id).json()
    assert data["items"][0]["payload"]["actor_name"] == MASKED


def test_read_only_sets_read_at_card_state_untouched(client, db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="nt-read")
    card = _make_card(db_session, space.id, viewer.account.id, viewer.id)
    headers = _login_header(client, "nt-read")
    notif_id = _list(client, headers, space.id).json()["items"][0]["id"]

    resp = client.post(f"/api/notifications/{notif_id}/read", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"id", "read_at"}
    assert body["id"] == notif_id and body["read_at"]

    # 已读只改 read_at：ActionCard 状态与 revision 原样
    row = db_session.query(ActionCard).filter(ActionCard.id == card.id).one()
    db_session.refresh(row)
    assert row.state == "pending"
    assert row.revision == card.revision

    # 列表投影：已读不改变领域状态，unread 归零
    data = _list(client, headers, space.id).json()
    assert data["unread_count"] == 0
    assert data["items"][0]["domain_status"] == "pending"
    assert data["items"][0]["read_at"] == body["read_at"]

    # 幂等：重复已读返回同一 read_at，不产生新变更
    again = client.post(f"/api/notifications/{notif_id}/read", headers=headers).json()
    assert again["read_at"] == body["read_at"]


def test_read_all_scoped_to_account_and_space(client, db_session) -> None:
    viewer, space_a = create_agent_fixture(db_session, name="nt-all")
    _other, space_b = create_agent_fixture(db_session, name="nt-all-b")
    from conftest import create_space_member

    create_space_member(db_session, space_b.id, viewer.id)
    _make_card(db_session, space_a.id, viewer.account.id, viewer.id)
    _make_card(db_session, space_b.id, viewer.account.id, viewer.id)
    headers = _login_header(client, "nt-all")

    resp = client.post(
        "/api/notifications/read-all", json={"space_id": space_a.id}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"space_id": space_a.id, "marked_count": 1}

    # 其他空间行不受影响
    assert _list(client, headers, space_b.id).json()["unread_count"] == 1
    # 幂等：已读行不再计数
    again = client.post(
        "/api/notifications/read-all", json={"space_id": space_a.id}, headers=headers
    ).json()
    assert again["marked_count"] == 0


def test_etag_304_and_authorization_before_304(client, db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="nt-etag")
    _make_card(db_session, space.id, viewer.account.id, viewer.id)
    headers = _login_header(client, "nt-etag")

    first = _list(client, headers, space.id)
    etag = first.headers["etag"]
    cached = client.get(
        "/api/notifications",
        params={"space_id": space.id},
        headers={**headers, "If-None-Match": etag},
    )
    assert cached.status_code == 304

    # 已读改变载荷 → 新 ETag
    notif_id = first.json()["items"][0]["id"]
    client.post(f"/api/notifications/{notif_id}/read", headers=headers)
    refreshed = client.get(
        "/api/notifications",
        params={"space_id": space.id},
        headers={**headers, "If-None-Match": etag},
    )
    assert refreshed.status_code == 200
    assert refreshed.headers["etag"] != etag
    assert refreshed.json()["unread_count"] == 0

    # 撤权后携带旧 ETag → 安全 404（授权先于 304）
    from app.models.space import SpaceMember
    from app.utils.timeutil import utcnow

    row = (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space.id, SpaceMember.user_id == viewer.id)
        .one()
    )
    row.status = "removed"
    row.updated_at = utcnow()
    db_session.commit()
    revoked = client.get(
        "/api/notifications",
        params={"space_id": space.id},
        headers={**headers, "If-None-Match": refreshed.headers["etag"]},
    )
    assert revoked.status_code == 404


def test_broken_action_card_ref_fail_closed(client, db_session) -> None:
    from app.models.notification import Notification

    viewer, space_a = create_agent_fixture(db_session, name="nt-broken")
    other, space_b = create_agent_fixture(db_session, name="nt-broken-b")
    _make_card(db_session, space_a.id, viewer.account.id, viewer.id)
    other_card = _make_card(db_session, space_b.id, other.account.id, other.id)

    # 引用被改指他空间卡片：读取端校验 space/recipient 匹配，fail-closed 丢弃
    notif = (
        db_session.query(Notification)
        .filter(Notification.recipient_account_id == viewer.account.id)
        .one()
    )
    notif.action_card_id = other_card.id
    db_session.commit()

    data = _list(client, _login_header(client, "nt-broken"), space_a.id).json()
    assert data["items"] == [] and data["unread_count"] == 0
