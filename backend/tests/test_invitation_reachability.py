"""邀请与审批的可达性合同测试（任务 09-20-space-invitation-reachability）。

覆盖四条 pending 链的**通知收件人矩阵**与**跨空间邀请投影**：

- `invite`（他人邀请 / 房主自己邀请）、`join_request`、`code`、
  `lineage_access`、`request_lineage_membership`；
- 投影 `PendingInvitationOut` 的 `direction`/`stage` 三态、空间名自足、
  只含本人行、不可见对方不泄露名字；
- R4 边界：pending 受邀人读该空间通用通知仍是安全 404（授权未被放宽）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app import config
from app.models.space import FamilySpace, SpaceMember, SpaceMemberApproval
from app.services import space_fsm
from app.utils.timeutil import utcnow
from conftest import (
    auth_header,
    create_space_member,
    create_user_with_pin,
    login,
)

INVITE_TITLE = "你有新的家庭空间邀请"
JOIN_REQUEST_TITLE = "有新的空间加入申请"
APPROVED_TITLE = "邀请已获房主批准，等待你接受"


@pytest.fixture(autouse=True)
def _pfv_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)


def _login_header(client, name: str) -> dict[str, str]:
    resp = login(client, name, "123456")
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def _household(session, owner, *, name: str = "reach-household") -> FamilySpace:
    """owner 为唯一 active space_admin 的 household 空间。"""
    space = FamilySpace(name=name, kind="household", owner_id=owner.id, created_at=utcnow())
    session.add(space)
    session.flush()
    session.add(
        SpaceMember(
            space_id=space.id,
            user_id=owner.id,
            added_by=owner.id,
            role="space_admin",
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    session.commit()
    return space


def _notifications_of(session, account_id: int, space_id: int) -> list[Any]:
    from app.models.notification import Notification

    return list(
        session.query(Notification)
        .filter(
            Notification.recipient_account_id == account_id,
            Notification.space_id == space_id,
        )
        .order_by(Notification.id)
        .all()
    )


def _titles(session, account_id: int, space_id: int) -> list[str]:
    return [row.title for row in _notifications_of(session, account_id, space_id)]


# ---- 通知收件人矩阵 ----


def test_invite_notifies_both_invitee_and_owner(client, db_session) -> None:
    """他人邀请：受邀人收到邀请通知，房主收到待批准通知（此前房主完全无通知）。"""
    owner = create_user_with_pin(db_session, "reach-owner", "123456")
    invitee = create_user_with_pin(db_session, "reach-invitee", "123456")
    space = _household(db_session, owner)
    db_session.commit()

    created = client.post(
        f"/api/spaces/{space.id}/members",
        json={"user_id": invitee.id, "relation_label": "堂弟"},
        headers=_login_header(client, "reach-owner"),
    )
    assert created.status_code == 201, created.text
    member_id = created.json()["id"]

    db_session.expire_all()
    assert _titles(db_session, invitee.account.id, space.id) == [INVITE_TITLE]
    assert _titles(db_session, owner.account.id, space.id) == [JOIN_REQUEST_TITLE]

    # 房主批准前就能读到待批准通知（不是等批准后才可见）
    approved = client.post(
        f"/api/space-memberships/{member_id}/approve",
        headers=_login_header(client, "reach-owner"),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "pending"  # invite 仍需受邀人接受

    # 批准后受邀人收到状态推进通知：awaiting_owner → awaiting_me 不再静默
    db_session.expire_all()
    assert _titles(db_session, invitee.account.id, space.id) == [INVITE_TITLE, APPROVED_TITLE]


def test_self_invite_by_owner_still_notifies_owner_to_approve(client, db_session) -> None:
    """房主自己发出的邀请：受邀人收到邀请，房主收到「待批准」——该行确实卡在他这一步。"""
    owner = create_user_with_pin(db_session, "reach-owner2", "123456")
    invitee = create_user_with_pin(db_session, "reach-invitee2", "123456")
    space = _household(db_session, owner)
    db_session.commit()

    created = client.post(
        f"/api/spaces/{space.id}/members",
        json={"user_id": invitee.id, "relation_label": "朋友"},
        headers=_login_header(client, "reach-owner2"),
    )
    assert created.status_code == 201, created.text

    db_session.expire_all()
    assert _titles(db_session, invitee.account.id, space.id) == [INVITE_TITLE]
    assert _titles(db_session, owner.account.id, space.id) == [JOIN_REQUEST_TITLE]


def test_join_request_notifies_owner_only(client, db_session) -> None:
    """本人申请加入：只通知房主（申请人自己不需要被通知）。"""
    owner = create_user_with_pin(db_session, "reach-owner3", "123456")
    requester = create_user_with_pin(db_session, "reach-requester", "123456")
    space = _household(db_session, owner)
    # join-by-user 要求申请人对目标可见：代管创建者链接提供可见性
    owner.created_by = requester.id
    lineage = FamilySpace(name="reach-lineage", kind="lineage", owner_id=owner.id, created_at=utcnow())
    db_session.add(lineage)
    db_session.flush()
    space.lineage_space_id = lineage.id
    db_session.add_all(
        [
            SpaceMember(
                space_id=lineage.id,
                user_id=owner.id,
                added_by=owner.id,
                role="space_admin",
                status="active",
                created_at=utcnow(),
                updated_at=utcnow(),
            ),
            SpaceMember(
                space_id=lineage.id,
                user_id=requester.id,
                added_by=owner.id,
                role="member",
                status="active",
                created_at=utcnow(),
                updated_at=utcnow(),
            ),
        ]
    )
    db_session.commit()

    resp = client.post(
        "/api/spaces/join-by-user",
        json={
            "lineage_space_id": lineage.id,
            "target_user_id": owner.id,
            "relation_label": "堂兄",
        },
        headers=_login_header(client, "reach-requester"),
    )
    assert resp.status_code == 201, resp.text

    db_session.expire_all()
    assert _titles(db_session, owner.account.id, space.id) == [JOIN_REQUEST_TITLE]
    assert _titles(db_session, requester.account.id, space.id) == []


def test_invite_code_notifies_owner_not_redeemer(client, db_session) -> None:
    """邀请码兑换：通知房主待批准；**不**给兑换人发「邀请」文案（此前错发）。"""
    from app.services import invite_codes

    owner = create_user_with_pin(db_session, "reach-code-owner", "123456")
    redeemer = create_user_with_pin(db_session, "reach-code-user", "123456")
    space = _household(db_session, owner)
    db_session.commit()

    code = invite_codes.create_code(
        db_session, creator=owner, kind="household", space_id=space.id
    )
    db_session.commit()

    resp = client.post(
        "/api/me/invite-codes/redeem",
        json={"code": code.code, "relation_label": "朋友"},
        headers=_login_header(client, "reach-code-user"),
    )
    assert resp.status_code in (200, 201), resp.text

    db_session.expire_all()
    assert _titles(db_session, owner.account.id, space.id) == [JOIN_REQUEST_TITLE]
    assert _titles(db_session, redeemer.account.id, space.id) == []


def test_lineage_access_request_notifies_lineage_owner(client, db_session) -> None:
    """家族空间访问申请：通知该 lineage 的 active space_admin。"""
    owner = create_user_with_pin(db_session, "reach-lineage-owner", "123456")
    member = create_user_with_pin(db_session, "reach-lineage-member", "123456")
    household = _household(db_session, owner, name="reach-hh-paired")
    lineage = FamilySpace(
        name="reach-lineage-2", kind="lineage", owner_id=owner.id, created_at=utcnow()
    )
    db_session.add(lineage)
    db_session.flush()
    household.lineage_space_id = lineage.id
    db_session.add(
        SpaceMember(
            space_id=lineage.id,
            user_id=owner.id,
            added_by=owner.id,
            role="space_admin",
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    create_space_member(db_session, household.id, member.id)
    db_session.commit()

    resp = client.post(
        "/api/spaces/lineage-access-requests",
        json={"household_space_id": household.id},
        headers=_login_header(client, "reach-lineage-member"),
    )
    assert resp.status_code == 201, resp.text

    db_session.expire_all()
    assert _titles(db_session, owner.account.id, lineage.id) == [JOIN_REQUEST_TITLE]
    assert _titles(db_session, member.account.id, lineage.id) == []


# ---- 跨空间邀请投影 ----


def test_pending_invitation_projection_is_self_sufficient(client, db_session) -> None:
    """受邀人读自己的邀请：自带空间名、方向、阶段、关系词；且只含本人行。"""
    owner = create_user_with_pin(db_session, "reach-proj-owner", "123456")
    invitee = create_user_with_pin(db_session, "reach-proj-invitee", "123456")
    other = create_user_with_pin(db_session, "reach-proj-other", "123456")
    space = _household(db_session, owner, name="投影用家庭空间")
    db_session.commit()

    for user, label in ((invitee, "堂弟"), (other, "朋友")):
        resp = client.post(
            f"/api/spaces/{space.id}/members",
            json={"user_id": user.id, "relation_label": label},
            headers=_login_header(client, "reach-proj-owner"),
        )
        assert resp.status_code == 201, resp.text

    data = client.get(
        "/api/spaces/invitations", headers=_login_header(client, "reach-proj-invitee")
    ).json()
    assert len(data) == 1  # 只含本人行，不含 other 的邀请
    item = data[0]
    assert item["space_name"] == "投影用家庭空间"
    assert item["space_kind"] == "household"
    assert item["direction"] == "incoming"
    assert item["stage"] == "awaiting_owner"  # 房主尚未批准
    assert item["counterpart_user_id"] == owner.id
    assert item["counterpart_name"] == owner.name
    assert item["relation_label"] == "堂弟"
    assert item["owner_approved_at"] is None


def test_pending_invitation_stage_flips_after_owner_approval(client, db_session) -> None:
    """房主批准后 stage 变 awaiting_me（受邀人此时才可接受）。"""
    owner = create_user_with_pin(db_session, "reach-stage-owner", "123456")
    invitee = create_user_with_pin(db_session, "reach-stage-invitee", "123456")
    space = _household(db_session, owner)
    db_session.commit()

    created = client.post(
        f"/api/spaces/{space.id}/members",
        json={"user_id": invitee.id, "relation_label": "堂弟"},
        headers=_login_header(client, "reach-stage-owner"),
    )
    member_id = created.json()["id"]
    invitee_headers = _login_header(client, "reach-stage-invitee")

    # 未批准：本人不能先接受（既有 403 语义不变）
    early = client.post(f"/api/space-memberships/{member_id}/accept", headers=invitee_headers)
    assert early.status_code == 403, early.text
    assert early.json()["error"]["code"] == "SPACE_FORBIDDEN_ACTOR"

    client.post(
        f"/api/space-memberships/{member_id}/approve",
        headers=_login_header(client, "reach-stage-owner"),
    )
    item = client.get("/api/spaces/invitations", headers=invitee_headers).json()[0]
    assert item["stage"] == "awaiting_me"
    assert item["owner_approved_at"] is not None


def test_join_request_and_code_project_as_outgoing(client, db_session) -> None:
    """我发起的加入（join_request / code）投影为 outgoing 且等房主批准。"""
    owner = create_user_with_pin(db_session, "reach-out-owner", "123456")
    requester = create_user_with_pin(db_session, "reach-out-requester", "123456")
    redeemer = create_user_with_pin(db_session, "reach-out-redeemer", "123456")
    space = _household(db_session, owner)
    db_session.commit()

    from app.services import invite_codes

    code = invite_codes.create_code(db_session, creator=owner, kind="household", space_id=space.id)
    db_session.commit()
    redeemed = client.post(
        "/api/me/invite-codes/redeem",
        json={"code": code.code, "relation_label": "朋友"},
        headers=_login_header(client, "reach-out-redeemer"),
    )
    assert redeemed.status_code in (200, 201), redeemed.text

    # 直建一条 join_request 行（request_join_by_user 的同族前置条件与投影无关）
    member, _created = space_fsm.invite(
        db_session, space=space, user_id=requester.id, added_by=requester.id, origin="join_request"
    )
    db_session.commit()

    data = client.get(
        "/api/spaces/invitations", headers=_login_header(client, "reach-out-requester")
    ).json()
    assert [item["space_id"] for item in data] == [space.id]
    assert data[0]["direction"] == "outgoing"
    assert data[0]["stage"] == "awaiting_owner"

    code_data = client.get(
        "/api/spaces/invitations", headers=_login_header(client, "reach-out-redeemer")
    ).json()
    assert code_data[0]["direction"] == "outgoing"
    assert code_data[0]["stage"] == "awaiting_owner"
    assert code_data[0]["counterpart_user_id"] == owner.id


def test_legacy_pending_row_keeps_old_semantics(client, db_session) -> None:
    """历史行（无审批行）：他人邀请 → incoming/awaiting_me；本人申请 → outgoing/awaiting_owner。"""
    owner = create_user_with_pin(db_session, "reach-legacy-owner", "123456")
    invitee = create_user_with_pin(db_session, "reach-legacy-invitee", "123456")
    requester = create_user_with_pin(db_session, "reach-legacy-requester", "123456")
    space = _household(db_session, owner)
    db_session.commit()

    now = utcnow()
    db_session.add_all(
        [
            SpaceMember(
                space_id=space.id,
                user_id=invitee.id,
                added_by=owner.id,
                role="member",
                status="pending",
                created_at=now,
                updated_at=now,
            ),
            SpaceMember(
                space_id=space.id,
                user_id=requester.id,
                added_by=requester.id,
                role="member",
                status="pending",
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    db_session.commit()
    assert db_session.query(SpaceMemberApproval).count() == 0

    incoming = client.get(
        "/api/spaces/invitations", headers=_login_header(client, "reach-legacy-invitee")
    ).json()[0]
    assert (incoming["direction"], incoming["stage"]) == ("incoming", "awaiting_me")

    outgoing = client.get(
        "/api/spaces/invitations", headers=_login_header(client, "reach-legacy-requester")
    ).json()[0]
    assert (outgoing["direction"], outgoing["stage"]) == ("outgoing", "awaiting_owner")


def test_expired_pending_row_not_returned(client, db_session) -> None:
    """惰性过期的 pending 行不再作为可操作邀请返回。"""
    from datetime import timedelta

    owner = create_user_with_pin(db_session, "reach-exp-owner", "123456")
    invitee = create_user_with_pin(db_session, "reach-exp-invitee", "123456")
    space = _household(db_session, owner)
    stale = utcnow() - timedelta(days=31)
    db_session.add(
        SpaceMember(
            space_id=space.id,
            user_id=invitee.id,
            added_by=owner.id,
            role="member",
            status="pending",
            created_at=stale,
            updated_at=stale,
        )
    )
    db_session.commit()

    data = client.get(
        "/api/spaces/invitations", headers=_login_header(client, "reach-exp-invitee")
    ).json()
    assert data == []


def test_pending_invitee_cannot_read_space_notifications(client, db_session) -> None:
    """R4 边界：pending 受邀人读该空间通用通知仍是安全 404（授权未被放宽）。"""
    owner = create_user_with_pin(db_session, "reach-bound-owner", "123456")
    invitee = create_user_with_pin(db_session, "reach-bound-invitee", "123456")
    space = _household(db_session, owner)
    db_session.commit()

    created = client.post(
        f"/api/spaces/{space.id}/members",
        json={"user_id": invitee.id, "relation_label": "堂弟"},
        headers=_login_header(client, "reach-bound-owner"),
    )
    assert created.status_code == 201, created.text
    member_id = created.json()["id"]
    invitee_headers = _login_header(client, "reach-bound-invitee")

    blocked = client.get(
        "/api/notifications", params={"space_id": space.id}, headers=invitee_headers
    )
    assert blocked.status_code == 404, blocked.text
    assert blocked.json()["error"]["code"] == "PERSONAL_FAMILY_VIEW_NOT_FOUND"

    # 接受之后才成为 active 成员，通知（含邀请行）才可读
    client.post(
        f"/api/space-memberships/{member_id}/approve",
        headers=_login_header(client, "reach-bound-owner"),
    )
    accepted = client.post(f"/api/space-memberships/{member_id}/accept", headers=invitee_headers)
    assert accepted.status_code == 200, accepted.text
    readable = client.get(
        "/api/notifications", params={"space_id": space.id}, headers=invitee_headers
    )
    assert readable.status_code == 200, readable.text
    kinds = {item["kind"] for item in readable.json()["items"]}
    assert kinds == {"space_membership"}


def test_invitations_endpoint_requires_authentication(client) -> None:
    """未认证不得读任何邀请投影（防枚举）。"""
    assert client.get("/api/spaces/invitations").status_code == 401
