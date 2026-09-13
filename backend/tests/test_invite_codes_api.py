"""邀请码管理 API 集成测试（09-05 Chunk C，implement.md C3；决策 13 修订）。

覆盖：provisional 建码成功（201，无身份确认门槛）、active 成员建码/列表、
provisional 非成员空间 404、零空间账号建陌生人码、空间管理员撤销他人码、
创建者撤销、未授权 404、过期/用尽/撤销后的字段级拒绝文案、设置页填码
（与注册同语义，provisional 兑换成功）与陌生人码登录态兑换 400。
"""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.commands import registration as registration_commands
from app.commands.context import ActorContext
from app.errors import INVITE_CODE_INVALID
from app.models.audit_log import AuditLog
from app.models.invite_code import InviteCode
from app.models.space import SpaceMember
from app.models.user import User
from app.utils import timeutil
from conftest import (
    auth_header,
    create_space_member,
    create_user_with_pin,
    login,
    seed_space_with_owner,
)


def _ctx(user) -> ActorContext:
    return ActorContext(user_id=user.id, account_id=user.account.id, account_status="claimed")


def _headers(client: TestClient, name: str, pin: str = "123456") -> dict[str, str]:
    tokens = login(client, name, pin)
    assert tokens.status_code == 200, tokens.text
    return auth_header(tokens.json())


# ---- 建码资格 ----


def test_invite_code_provisional_user_can_create(db_session, client) -> None:
    """决策 13 修订：provisional（身份未确认）可建码——为自己空间建家庭码 201，
    建陌生人码同样 201，不再有身份确认门槛。"""
    provisional = create_user_with_pin(
        db_session, "未确档成员", "123456", profile_status="provisional"
    )
    space = seed_space_with_owner(db_session, provisional.id, name="未确档成员空间")

    created = client.post(
        "/api/invite-codes",
        headers=_headers(client, "未确档成员"),
        json={"kind": "household", "space_id": space.id},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "household"
    assert body["space_id"] == space.id
    assert body["space_name"] == "未确档成员空间"
    assert body["max_uses"] == 1

    stranger = client.post(
        "/api/invite-codes",
        headers=_headers(client, "未确档成员"),
        json={"kind": "stranger"},
    )
    assert stranger.status_code == 201, stranger.text
    assert stranger.json()["kind"] == "stranger"
    assert stranger.json()["space_id"] is None


def test_invite_code_provisional_non_member_space_404(db_session, client) -> None:
    """provisional 为非成员空间建码仍 404：身份门槛已撤销，空间成员资格门不变。"""
    create_user_with_pin(db_session, "未确档外人", "123456", profile_status="provisional")
    owner = create_user_with_pin(db_session, "他空间主人", "123456")
    space = seed_space_with_owner(db_session, owner.id, name="他人空间")

    response = client.post(
        "/api/invite-codes",
        headers=_headers(client, "未确档外人"),
        json={"kind": "household", "space_id": space.id},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SPACE_NOT_FOUND"


def test_invite_code_zero_space_account_creates_stranger_code(db_session, client) -> None:
    """决策 13 修订：零空间已登录账号可建陌生人码（纯归因，不再要求
    存在于任一 active 空间）。"""
    create_user_with_pin(db_session, "零空间人", "123456")

    response = client.post(
        "/api/invite-codes",
        headers=_headers(client, "零空间人"),
        json={"kind": "stranger"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "stranger"
    assert body["space_id"] is None


def test_invite_code_active_member_creates_and_lists(db_session, client) -> None:
    owner = create_user_with_pin(db_session, "建码空间主", "123456")
    space = seed_space_with_owner(db_session, owner.id, name="建码空间")

    created = client.post(
        "/api/invite-codes",
        headers=_headers(client, "建码空间主"),
        json={"kind": "household", "space_id": space.id},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "household"
    assert body["space_id"] == space.id
    assert body["space_name"] == "建码空间"
    assert body["max_uses"] == 1
    assert body["used_count"] == 0
    assert body["revoked_at"] is None

    listing = client.get("/api/invite-codes", headers=_headers(client, "建码空间主"))
    assert listing.status_code == 200
    codes = listing.json()
    assert [row["id"] for row in codes] == [body["id"]]
    assert codes[0]["code"] == body["code"]  # 创建者可见明文码（分享用）


def test_invite_code_list_is_creator_scoped(db_session, client) -> None:
    owner_a = create_user_with_pin(db_session, "列表甲", "123456")
    owner_b = create_user_with_pin(db_session, "列表乙", "123456")
    seed_space_with_owner(db_session, owner_a.id, name="列表甲空间")
    seed_space_with_owner(db_session, owner_b.id, name="列表乙空间")
    assert (
        client.post(
            "/api/invite-codes", headers=_headers(client, "列表甲"), json={"kind": "stranger"}
        ).status_code
        == 201
    )
    mine = client.get("/api/invite-codes", headers=_headers(client, "列表甲")).json()
    theirs = client.get("/api/invite-codes", headers=_headers(client, "列表乙")).json()
    assert len(mine) == 1
    assert theirs == []


def test_invite_code_param_validation_422(db_session, client) -> None:
    owner = create_user_with_pin(db_session, "参数校验人", "123456")
    space = seed_space_with_owner(db_session, owner.id, name="参数校验空间")
    headers = _headers(client, "参数校验人")

    one_shot = client.post(
        "/api/invite-codes",
        headers=headers,
        json={"kind": "household", "space_id": space.id, "max_uses": 5},
    )
    assert one_shot.status_code == 422  # 一次性码不接受 max_uses
    missing_space = client.post("/api/invite-codes", headers=headers, json={"kind": "household"})
    assert missing_space.status_code == 422
    bad_ttl = client.post(
        "/api/invite-codes", headers=headers, json={"kind": "stranger", "ttl_days": 0}
    )
    assert bad_ttl.status_code == 422
    stranger_with_space = client.post(
        "/api/invite-codes",
        headers=headers,
        json={"kind": "stranger", "space_id": space.id},
    )
    assert stranger_with_space.status_code == 422


# ---- 撤销权限 ----


def test_invite_code_space_manager_revokes_another_members_code(db_session, client) -> None:
    """空间管理员可撤销其空间内他人创建的码（决策 11）。"""
    manager = create_user_with_pin(db_session, "撤销管理员", "123456")
    member = create_user_with_pin(db_session, "撤销成员", "123456")
    space = seed_space_with_owner(db_session, manager.id, name="撤销空间")
    create_space_member(db_session, space.id, member.id, role="member", status="active")

    created = client.post(
        "/api/invite-codes",
        headers=_headers(client, "撤销成员"),
        json={"kind": "household", "space_id": space.id},
    )
    assert created.status_code == 201
    code_id = created.json()["id"]

    revoked = client.delete(f"/api/invite-codes/{code_id}", headers=_headers(client, "撤销管理员"))
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked_at"] is not None

    # 撤销后兑换拒绝：文案仅描述码状态
    denied = client.post(
        "/api/me/invite-codes/redeem",
        headers=_headers(client, "撤销管理员"),
        json={"code": revoked.json()["code"]},
    )
    assert denied.status_code == 400
    assert denied.json()["error"]["message"] == "邀请码已撤销"

    # 终态不可再变：重复撤销 409
    again = client.delete(f"/api/invite-codes/{code_id}", headers=_headers(client, "撤销管理员"))
    assert again.status_code == 409


def test_invite_code_creator_revokes_own_code(db_session, client) -> None:
    owner = create_user_with_pin(db_session, "自撤码人", "123456")
    seed_space_with_owner(db_session, owner.id, name="自撤码空间")
    created = client.post(
        "/api/invite-codes",
        headers=_headers(client, "自撤码人"),
        json={"kind": "stranger"},
    )
    code_id = created.json()["id"]

    revoked = client.delete(f"/api/invite-codes/{code_id}", headers=_headers(client, "自撤码人"))
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None


def test_invite_code_revoke_unauthorized_member_404(db_session, client) -> None:
    """同空间非管理员成员撤销他人码 → 404（与未知 id 同形，防枚举）。"""
    manager = create_user_with_pin(db_session, "撤权管理员", "123456")
    member = create_user_with_pin(db_session, "撤权成员", "123456")
    other = create_user_with_pin(db_session, "撤权路人", "123456")
    space = seed_space_with_owner(db_session, manager.id, name="撤权空间")
    create_space_member(db_session, space.id, member.id, role="member", status="active")
    create_space_member(db_session, space.id, other.id, role="member", status="active")

    created = client.post(
        "/api/invite-codes",
        headers=_headers(client, "撤权成员"),
        json={"kind": "household", "space_id": space.id},
    )
    code_id = created.json()["id"]

    denied = client.delete(f"/api/invite-codes/{code_id}", headers=_headers(client, "撤权路人"))
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "INVITE_CODE_INVALID"

    # 未知 id 与未授权同一 404 形状（防枚举）
    unknown = client.delete("/api/invite-codes/999999", headers=_headers(client, "撤权路人"))
    assert unknown.status_code == 404
    assert unknown.content == denied.content


# ---- 状态拒绝：过期 / 用尽（兑换场景字段级文案）----


def test_invite_code_expired_and_exhausted_redeem_rejected(db_session, client) -> None:
    owner = create_user_with_pin(db_session, "过期码主人", "123456")
    joiner = create_user_with_pin(db_session, "过期码加入人", "123456")
    space = seed_space_with_owner(db_session, owner.id, name="过期码空间")

    expired_row = registration_commands.create_my_invite_code(
        db_session, _ctx(owner), kind="household", space_id=space.id
    )
    expired_row.expires_at = timeutil.utcnow() - timedelta(seconds=1)
    db_session.commit()
    expired = client.post(
        "/api/me/invite-codes/redeem",
        headers=_headers(client, "过期码加入人"),
        json={"code": expired_row.code},
    )
    assert expired.status_code == 400
    assert expired.json()["error"]["message"] == "邀请码已过期"
    assert db_session.query(SpaceMember).filter(SpaceMember.user_id == joiner.id).count() == 0

    exhausted_row = registration_commands.create_my_invite_code(
        db_session, _ctx(owner), kind="stranger", max_uses=1
    )
    exhausted_row.used_count = 1
    db_session.commit()
    exhausted = client.post(
        "/api/me/invite-codes/redeem",
        headers=_headers(client, "过期码加入人"),
        json={"code": exhausted_row.code},
    )
    assert exhausted.status_code == 400
    assert exhausted.json()["error"]["message"] == "邀请码使用次数已达上限"


# ---- 设置页填码：与注册同一加入语义 ----


def test_invite_code_redeem_household_code_activates_membership(db_session, client) -> None:
    creator = create_user_with_pin(db_session, "填码邀请人", "123456")
    joiner = create_user_with_pin(db_session, "填码加入人", "123456")
    space = seed_space_with_owner(db_session, creator.id, name="填码空间")
    code = registration_commands.create_my_invite_code(
        db_session, _ctx(creator), kind="household", space_id=space.id
    )

    response = client.post(
        "/api/me/invite-codes/redeem",
        headers=_headers(client, "填码加入人"),
        json={"code": code.code},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["space_id"] == space.id
    assert body["space_name"] == "填码空间"
    assert body["used_count"] == 1

    member = (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space.id, SpaceMember.user_id == joiner.id)
        .one()
    )
    assert member.status == "active"  # pending→accept 同一状态机，当场 active
    assert member.added_by == creator.id

    db_session.expire(code, ["used_count"])
    assert code.used_count == 1

    redeemed = db_session.query(AuditLog).filter(AuditLog.action == "invite_code_redeemed").one()
    assert '"scene": "redeem"' in redeemed.detail_json
    accepted = db_session.query(AuditLog).filter(AuditLog.action == "space_invite_accepted").one()
    assert accepted.target_id == joiner.id


def test_invite_code_redeem_allows_provisional_user(db_session, client) -> None:
    """接受码无身份门槛（决策 8 + 决策 13 修订）：provisional 用户在设置页
    兑换家庭码成功，走同一 pending→accept 状态机当场 active。"""
    creator = create_user_with_pin(db_session, "未确档兑码邀请人", "123456")
    provisional = create_user_with_pin(
        db_session, "未确档兑码人", "123456", profile_status="provisional"
    )
    space = seed_space_with_owner(db_session, creator.id, name="未确档兑码空间")
    code = registration_commands.create_my_invite_code(
        db_session, _ctx(creator), kind="household", space_id=space.id
    )

    response = client.post(
        "/api/me/invite-codes/redeem",
        headers=_headers(client, "未确档兑码人"),
        json={"code": code.code},
    )
    assert response.status_code == 200, response.text
    assert response.json()["space_id"] == space.id

    member = (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space.id, SpaceMember.user_id == provisional.id)
        .one()
    )
    assert member.status == "active"  # 同一状态机，无身份门槛
    assert member.added_by == creator.id
    db_session.expire(code, ["used_count"])
    assert code.used_count == 1


def test_invite_code_redeem_stranger_code_rejected(db_session, client) -> None:
    """陌生人码仅注册场景有效：登录态兑换 400 明确提示（PRD 决策 9）。"""
    creator = create_user_with_pin(db_session, "陌生码设页人", "123456")
    joiner = create_user_with_pin(db_session, "陌生码填码人", "123456")
    seed_space_with_owner(db_session, creator.id, name="陌生码设页空间")
    code = registration_commands.create_my_invite_code(db_session, _ctx(creator), kind="stranger")

    response = client.post(
        "/api/me/invite-codes/redeem",
        headers=_headers(client, "陌生码填码人"),
        json={"code": code.code},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "INVITE_CODE_STRANGER_REGISTER_ONLY"
    assert error["message"] == "陌生人码仅可在注册时使用"
    # 码未被核销
    db_session.expire(code, ["used_count"])
    assert code.used_count == 0
    assert db_session.query(InviteCode).filter(InviteCode.code == code.code).one().used_count == 0
    assert db_session.query(SpaceMember).filter(SpaceMember.user_id == joiner.id).count() == 0


def test_invite_code_unauthenticated_requests_401(client) -> None:
    assert client.get("/api/invite-codes").status_code == 401
    assert client.post("/api/invite-codes", json={"kind": "stranger"}).status_code == 401
    assert client.post("/api/me/invite-codes/redeem", json={"code": "ABCDEFGH"}).status_code == 401


# ---- 删除主体的码处置（09-05 P2-2：creator_id 不再阻断删除）----


def test_delete_code_creator_via_api_succeeds_and_auto_revokes(db_session, client) -> None:
    """创建过码的成员经 API 自删：204（修复前误报 OWNER_TRANSFER_REQUIRED 409），
    码自动撤销、行保留且创建者指针置空，audit 事件落痕。"""
    owner = create_user_with_pin(db_session, "自删空间主", "123456")
    space = seed_space_with_owner(db_session, owner.id, name="自删码空间")
    creator = create_user_with_pin(db_session, "自删持码人", "444444")
    create_space_member(db_session, space.id, creator.id, role="member", status="active")
    creator_id = creator.id  # API 删除在另一 session 执行；先取标量防过期实例刷新
    # 兑换者在删除前造数：删除后其主键可能被 SQLite 复用，避免身份映射脏状态
    joiner = create_user_with_pin(db_session, "自删后填码人", "555555")

    created = client.post(
        "/api/invite-codes",
        headers=_headers(client, "自删持码人", "444444"),
        json={"kind": "household", "space_id": space.id},
    )
    assert created.status_code == 201, created.text
    code_id = created.json()["id"]

    deleted = client.delete(
        f"/api/users/{creator_id}",
        headers=_headers(client, "自删持码人", "444444"),
        params={"confirm_name": "自删持码人"},
    )
    assert deleted.status_code == 204, deleted.text

    db_session.expire_all()
    assert db_session.query(User).filter(User.id == creator_id).count() == 0
    row = db_session.query(InviteCode).one()  # 码行保留，不随创建者级联消失
    assert row.id == code_id
    assert row.creator_id is None
    assert row.revoked_at is not None
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "invite_code_auto_revoked_on_delete")
        .one()
    )
    assert audit.target_id == creator_id
    assert audit.detail["code_ids"] == [code_id]
    # 自动撤销后的码不可再被兑换（创建者已删除，resolve 统一按无效处理）
    with pytest.raises(HTTPException) as exc:
        registration_commands.redeem_invite_code(db_session, _ctx(joiner), raw_code=row.code)
    assert exc.value.detail["__api_error__"]["code"] == INVITE_CODE_INVALID  # type: ignore[index]
