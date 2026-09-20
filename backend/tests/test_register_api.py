"""POST /api/auth/register 全流程集成测试（09-05 Chunk B，implement.md B5）。

覆盖：四分支（无码/家庭码/家族码/陌生人码）、开关关 404（与未知路径逐字节
一致）、用户名占用防枚举统一文案、IP 滑窗限流 429、冷启动（零账号库首个注册）。
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import config
from app.commands import registration as registration_commands
from app.commands.context import ActorContext
from app.models.audit_log import AuditLog
from app.models.invite_code import InviteCode
from app.models.space import FamilySpace, SpaceMember
from app.models.user import User
from app.services import rate_limit, space_fsm
from conftest import create_user_with_pin, seed_space_with_owner


def _ctx(user) -> ActorContext:
    return ActorContext(user_id=user.id, account_id=user.account.id, account_status="claimed")


def _create_code(
    db_session: Session,
    creator,
    kind: str,
    *,
    space_id: int | None = None,
    max_uses: int | None = None,
) -> InviteCode:
    """命令层建码（走与 API 相同的资格校验与事务）。"""
    return registration_commands.create_my_invite_code(
        db_session,
        _ctx(creator),
        kind=kind,
        space_id=space_id,
        max_uses=max_uses,
    )


def _register(
    client: TestClient,
    name: str,
    *,
    pin: str = "123456",
    code: str | None = None,
    relation_label: str | None = None,
):
    payload: dict = {"name": name, "pin": pin}
    if code is not None:
        payload["code"] = code
        # 09-20：使用邀请码注册时必须填写与码创建者的关系词
        payload["relation_label"] = relation_label or "堂兄弟"
    return client.post("/api/auth/register", json=payload)


# ---- 无码注册 + 冷启动 ----


def test_register_cold_start_first_account_no_code(db_session, client) -> None:
    """零账号库首个注册：无码 → 直登态 + §215 空态（后端不代建空间）。"""
    assert db_session.query(FamilySpace).count() == 0  # 冷启动：库内零空间
    response = _register(client, "首个注册者")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"access_token", "refresh_token", "token_type", "user"}
    user = body["user"]
    assert user["name"] == "首个注册者"
    assert user["pin_must_change"] is False  # 自设 PIN，无强制改密步骤
    assert user["claim_status"] == "claimed"  # Account 初始态直接 claimed
    assert user["profile_status"] == "provisional"  # 身份确认只能由家人确认获得

    # token 即刻可用（直接登录态）
    me = client.get("/api/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["profile_status"] == "provisional"

    # 无码分支不代建空间；随后可用同一 PIN 重新登录
    assert db_session.query(FamilySpace).count() == 0
    relogin = client.post("/api/auth/login", json={"name": "首个注册者", "pin": "123456"})
    assert relogin.status_code == 200


def test_register_provisions_no_membership_without_code(db_session, client) -> None:
    assert _register(client, "无码用户").status_code == 200
    assert db_session.query(SpaceMember).count() == 0


# ---- 家庭码 / 家族码分支 ----


def test_register_with_household_code_becomes_active_member(db_session, client) -> None:
    creator = create_user_with_pin(db_session, "家庭码创建者", "654321")
    space = seed_space_with_owner(db_session, creator.id, name="家庭码空间")
    code = _create_code(db_session, creator, "household", space_id=space.id)

    response = _register(client, "持家庭码人", code=code.code)
    assert response.status_code == 200, response.text

    registrant_id = response.json()["user"]["id"]
    member = (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space.id, SpaceMember.user_id == registrant_id)
        .one()
    )
    # 09-20：持码只产生待房主批准的 pending，房主批准后才 active
    assert member.status == "pending"
    assert space_fsm.approval_for(db_session, member.id).origin == "code"
    assert member.role == "member"
    assert member.added_by == creator.id  # added_by 归因邀请人

    db_session.expire(code, ["used_count"])
    assert code.used_count == 1  # 一次性码用后即焚

    # 归因审计
    actions = {row.action for row in db_session.query(AuditLog).all()}
    assert "space_invite_code_redeemed" in actions
    redeemed = db_session.query(AuditLog).filter(AuditLog.action == "invite_code_redeemed").one()
    assert '"code_kind": "household"' in redeemed.detail_json
    assert '"creator_id"' in redeemed.detail_json


def test_register_with_lineage_code_becomes_active_member(db_session, client) -> None:
    creator = create_user_with_pin(db_session, "家族码创建者", "654321")
    space = seed_space_with_owner(db_session, creator.id, name="家族码空间", kind="lineage")
    code = _create_code(db_session, creator, "lineage", space_id=space.id)

    response = _register(client, "持家族码人", code=code.code)
    assert response.status_code == 200, response.text
    registrant_id = response.json()["user"]["id"]
    member = (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space.id, SpaceMember.user_id == registrant_id)
        .one()
    )
    assert member.status == "pending"
    assert space_fsm.approval_for(db_session, member.id).origin == "code"
    assert member.added_by == creator.id


def test_register_one_shot_code_second_use_fails(db_session, client) -> None:
    creator = create_user_with_pin(db_session, "一次性码创建者", "654321")
    space = seed_space_with_owner(db_session, creator.id, name="一次性码空间")
    code = _create_code(db_session, creator, "household", space_id=space.id)

    assert _register(client, "第一用码人", code=code.code).status_code == 200
    second = _register(client, "第二用码人", code=code.code)
    assert second.status_code == 400
    error = second.json()["error"]
    assert error["code"] == "INVITE_CODE_INVALID"
    assert error["message"] == "邀请码使用次数已达上限"
    # 第二人未产生成员关系（仅剩创建者管理员行 + 第一用码人）
    assert db_session.query(SpaceMember).count() == 2


# ---- 陌生人码分支 ----


def test_register_with_stranger_code_provisions_own_space(db_session, client) -> None:
    """陌生人码 = 纯拉新归因：注册者获得自己的「我的家庭」空间，双方零连接。"""
    creator = create_user_with_pin(db_session, "陌生码邀请人", "654321")
    creator_space = seed_space_with_owner(db_session, creator.id, name="邀请人自己的空间")
    code = _create_code(db_session, creator, "stranger")

    response = _register(client, "陌生码注册者", code=code.code)
    assert response.status_code == 200, response.text
    registrant_id = response.json()["user"]["id"]

    # 注册者得到自己的独立新家庭空间（创建者即 space_admin）
    own_space = db_session.query(FamilySpace).filter(FamilySpace.owner_id == registrant_id).one()
    assert own_space.name == registration_commands.MY_FAMILY_SPACE_NAME
    assert own_space.kind == "household"
    own_member = db_session.query(SpaceMember).filter(SpaceMember.space_id == own_space.id).one()
    assert own_member.user_id == registrant_id
    assert own_member.role == "space_admin"
    assert own_member.status == "active"

    # 双方之间无任何成员关系：邀请人空间没有注册者，注册者空间没有邀请人
    assert (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == creator_space.id, SpaceMember.user_id == registrant_id)
        .count()
        == 0
    )
    assert (
        db_session.query(SpaceMember)
        .filter(SpaceMember.user_id == creator.id, SpaceMember.space_id == own_space.id)
        .count()
        == 0
    )
    db_session.expire(code, ["used_count"])
    assert code.used_count == 1

    # 归因留痕：码、邀请人、被邀请 user_id（陌生人码场景同样落 audit）
    redeemed = db_session.query(AuditLog).filter(AuditLog.action == "invite_code_redeemed").one()
    assert '"code_kind": "stranger"' in redeemed.detail_json
    assert '"creator_id"' in redeemed.detail_json
    assert '"scene": "register"' in redeemed.detail_json
    registered = db_session.query(AuditLog).filter(AuditLog.action == "user_registered").one()
    assert '"code_kind": "stranger"' in registered.detail_json


# ---- 码无效 / 参数 ----


def test_register_with_unknown_code_rejected_without_user(db_session, client) -> None:
    response = _register(client, "无效码用户", code="ZZZZZZZZ")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVITE_CODE_INVALID"
    assert response.json()["error"]["message"] == "邀请码不存在或无效"
    assert db_session.query(User).count() == 0


def test_register_rejects_malformed_pin(client) -> None:
    short_pin = client.post("/api/auth/register", json={"name": "短PIN", "pin": "12345"})
    empty_name = client.post("/api/auth/register", json={"name": "", "pin": "123456"})
    assert short_pin.status_code == 422
    assert empty_name.status_code == 422


# ---- 开关关闭 ----


def test_register_disabled_returns_plain_404_identical_to_unknown(
    db_session, client, monkeypatch
) -> None:
    monkeypatch.setattr(config, "REGISTRATION_ENABLED", False)
    register_response = _register(client, "被关闭注册")
    unknown_response = client.post(f"/api/unknown-{uuid.uuid4().hex}", json={"name": "x"})
    assert register_response.status_code == 404
    # 与随机未知路径逐字节一致：不给「功能存在但关闭」的探测信号（design §4）
    assert register_response.content == unknown_response.content

    # 畸形请求同样 404（404 判定先于 body 校验短路）
    garbage = client.post("/api/auth/register", json={"garbage": True})
    assert garbage.status_code == 404
    assert garbage.content == unknown_response.content
    assert db_session.query(User).count() == 0


# ---- 用户名占用（防枚举统一文案）----


def test_register_username_taken_unified_message(db_session, client) -> None:
    assert _register(client, "已占用户名").status_code == 200
    replay = _register(client, "已占用户名")
    assert replay.status_code == 409
    error = replay.json()["error"]
    assert error["code"] == "USERNAME_ALREADY_REGISTERED"
    assert error["message"] == "该用户名已被注册"

    # 既有 managed 建档名同样占用，响应体与自注册占用完全一致（不泄露账号类型）
    create_user_with_pin(
        db_session, "建档占用名", "999999", claim_status="managed", profile_status="provisional"
    )
    against_managed = _register(client, "建档占用名")
    assert against_managed.status_code == 409
    assert against_managed.content == replay.content


# ---- IP 滑窗限流 ----


def test_register_rate_limited_429_with_retry_after(db_session, client, monkeypatch) -> None:
    monkeypatch.setattr(config, "REGISTRATION_RATE_LIMIT_MAX_ATTEMPTS", 2)
    assert _register(client, "限流一人").status_code == 200
    assert _register(client, "限流二人").status_code == 200
    third = _register(client, "限流三人")
    assert third.status_code == 429
    error = third.json()["error"]
    assert error["code"] == "REGISTRATION_RATE_LIMITED"
    assert int(third.headers["Retry-After"]) >= 1
    assert db_session.query(User).count() == 2

    # 窗口计数为进程内状态：手动清空后恢复可用（生产窗口由配置控制）
    rate_limit.registration_limiter.reset()
    assert _register(client, "限流四人").status_code == 200
