"""敏感详情访问会话合同测试（09-04 RM-F3）。

覆盖：
- 会话签发：reason 校验（空/空白/控制字符/超长）、TTL 30 分钟、hash-only 持久化、
  明文票据只出现在响应中、allowed_scopes 按目标类型固定；
- 未知目标统一安全 404（user 与 space 同文，防存在性枚举）；
- 敏感详情端点门禁：无票据 / 错目标 / 过期 / 撤销 / 跨管理员 / 跨目标统一 403
  且每次拒绝写审计；
- 详情响应 Cache-Control: no-store。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import (
    admin_session_headers,
    create_agent_fixture,
    create_space_member,
    create_system_admin,
    create_user_with_pin,
    seed_space_with_owner,
)
from fastapi.testclient import TestClient

from app.models.admin_access import AdminAccessSession
from app.utils import timeutil

V1 = "/admin-api/v1"
SESSION_HEADER = "X-Admin-Access-Session"

REASON = "处理该空间成员资料异常"


@pytest.fixture()
def v1_headers(admin_client: TestClient, db_session) -> dict[str, str]:
    create_system_admin(db_session)
    return admin_session_headers(admin_client)


def _create_session(
    admin_client: TestClient, headers: dict[str, str], *, target_type: str, target_id: int
):
    return admin_client.post(
        f"{V1}/access-sessions",
        json={"target_type": target_type, "target_id": target_id, "reason": REASON},
        headers=headers,
    )


# ---- 签发与持久化 ----


def test_create_session_whitelist_ttl_and_hash_only(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    manager, space = create_agent_fixture(db_session, name="会话管理")
    before = timeutil.utcnow()

    response = _create_session(admin_client, v1_headers, target_type="space", target_id=space.id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "session_id",
        "target_type",
        "target_id",
        "allowed_scopes",
        "issued_at",
        "expires_at",
    }
    assert body["target_type"] == "space"
    assert body["target_id"] == space.id
    assert body["allowed_scopes"] == ["member.detail", "relation.detail", "fact.detail"]
    ttl = _parse(body["expires_at"]) - _parse(body["issued_at"])
    assert timedelta(minutes=29) <= ttl <= timedelta(minutes=31)

    rows = db_session.query(AdminAccessSession).all()
    assert len(rows) == 1
    row = rows[0]
    # hash-only 持久化：明文票据不落库，token_hash 为 64 位 hex
    assert row.token_hash != body["session_id"]
    assert body["session_id"] not in row.token_hash
    assert len(row.token_hash) == 64
    int(row.token_hash, 16)
    assert row.reason == REASON
    assert row.revoked_at is None
    assert row.issued_at >= before - timedelta(seconds=1)


def _parse(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def test_create_session_reason_validation(admin_client: TestClient, db_session, v1_headers) -> None:
    manager, space = create_agent_fixture(db_session, name="理由校验")
    for reason in ("", "   ", "带\x00控制字符", "x" * 501):
        response = admin_client.post(
            f"{V1}/access-sessions",
            json={"target_type": "space", "target_id": space.id, "reason": reason},
            headers=v1_headers,
        )
        assert response.status_code == 422, reason
    # 非法目标类型
    response = admin_client.post(
        f"{V1}/access-sessions",
        json={"target_type": "world", "target_id": space.id, "reason": REASON},
        headers=v1_headers,
    )
    assert response.status_code == 422


def test_create_session_unknown_target_uniform_404(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    create_user_with_pin(db_session, "占位用户", "101010")
    missing_user = _create_session(admin_client, v1_headers, target_type="user", target_id=999999)
    missing_space = _create_session(admin_client, v1_headers, target_type="space", target_id=999999)
    assert missing_user.status_code == 404
    assert missing_space.status_code == 404
    # user 与 space 的 404 完全一致（不区分目标类型的存在性）
    assert missing_user.json() == missing_space.json()
    assert missing_user.json()["error"]["code"] == "ADMIN_TARGET_NOT_FOUND"


# ---- 敏感详情门禁 ----


def _seed_space_with_relations(db_session):  # type: ignore[no-untyped-def]
    from conftest import create_v1_relation, seed_structural_edge_to_fact

    elder = create_user_with_pin(db_session, "长辈", "202020")
    younger = create_user_with_pin(db_session, "晚辈", "202021")
    space = seed_space_with_owner(db_session, elder.id, name="门禁空间", kind="lineage")
    create_space_member(db_session, space.id, younger.id)
    edge = create_v1_relation(
        db_session, from_user_id=younger.id, to_user_id=elder.id, dir_class="elder"
    )
    edge.label = "三叔公"
    db_session.commit()
    fact = seed_structural_edge_to_fact(db_session, edge, space_id=space.id)
    return space, elder, younger, edge, fact


def test_sensitive_endpoints_require_session_header(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    space, elder, _younger, _edge, _fact = _seed_space_with_relations(db_session)
    probes = (
        f"{V1}/users/{elder.id}/profile",
        f"{V1}/users/{elder.id}/avatar/thumbnail",
        f"{V1}/users/{elder.id}/attachments",
        f"{V1}/spaces/{space.id}/relations",
        f"{V1}/spaces/{space.id}/facts",
    )
    for probe in probes:
        response = admin_client.get(probe, headers=v1_headers)
        assert response.status_code == 403, probe
        assert response.json()["error"]["code"] == "ADMIN_ACCESS_SESSION_INVALID"


def test_session_target_binding_and_no_store(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    space, elder, _younger, _edge, _fact = _seed_space_with_relations(db_session)
    user_session = _create_session(
        admin_client, v1_headers, target_type="user", target_id=elder.id
    ).json()
    space_session = _create_session(
        admin_client, v1_headers, target_type="space", target_id=space.id
    ).json()

    # 正向：绑定的 user/space 可访问，响应 no-store
    profile = admin_client.get(
        f"{V1}/users/{elder.id}/profile",
        headers={**v1_headers, SESSION_HEADER: user_session["session_id"]},
    )
    assert profile.status_code == 200
    assert profile.headers["Cache-Control"] == "no-store"
    assert set(profile.json()) == {
        "id",
        "name",
        "gender",
        "birth",
        "death",
        "bio",
        "avatar_available",
        "profile_status",
        "claim_status",
        "created_at",
    }

    relations = admin_client.get(
        f"{V1}/spaces/{space.id}/relations",
        headers={**v1_headers, SESSION_HEADER: space_session["session_id"]},
    )
    assert relations.status_code == 200
    assert relations.headers["Cache-Control"] == "no-store"
    assert set(relations.json()["items"][0]) == {
        "id",
        "from_user_id",
        "from_user_name",
        "to_user_id",
        "to_user_name",
        "dir_class",
        "status",
        "space_id",
        "label_safe",
        "created_at",
        "updated_at",
    }

    facts = admin_client.get(
        f"{V1}/spaces/{space.id}/facts",
        headers={**v1_headers, SESSION_HEADER: space_session["session_id"]},
    )
    assert facts.status_code == 200
    assert facts.headers["Cache-Control"] == "no-store"
    assert set(facts.json()["items"][0]) == {
        "id",
        "fact_type",
        "subject_user_id",
        "subject_name",
        "object_user_id",
        "object_name",
        "space_id",
        "state",
        "provenance",
        "revision",
        "created_at",
        "updated_at",
    }
    assert facts.json()["items"][0]["state"] == "confirmed"

    attachments = admin_client.get(
        f"{V1}/users/{elder.id}/attachments",
        headers={**v1_headers, SESSION_HEADER: user_session["session_id"]},
    )
    assert attachments.status_code == 200
    assert attachments.headers["Cache-Control"] == "no-store"

    # 错目标：user 票据不能用于 space 端点，反之亦然
    wrong1 = admin_client.get(
        f"{V1}/spaces/{space.id}/relations",
        headers={**v1_headers, SESSION_HEADER: user_session["session_id"]},
    )
    assert wrong1.status_code == 403
    wrong2 = admin_client.get(
        f"{V1}/users/{elder.id}/profile",
        headers={**v1_headers, SESSION_HEADER: space_session["session_id"]},
    )
    assert wrong2.status_code == 403


def test_session_cross_target_and_cross_admin_rejected(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    _space, elder, _younger, _edge, _fact = _seed_space_with_relations(db_session)
    other, _other_space = create_agent_fixture(db_session, name="其他空间用户")

    own = _create_session(admin_client, v1_headers, target_type="user", target_id=elder.id).json()
    other_session = _create_session(
        admin_client, v1_headers, target_type="user", target_id=other.id
    ).json()

    # 跨目标复用：A 的票据查 B 的档案 → 403
    swapped = admin_client.get(
        f"{V1}/users/{other.id}/profile",
        headers={**v1_headers, SESSION_HEADER: own["session_id"]},
    )
    assert swapped.status_code == 403

    # 跨管理员复用：另一个管理员持 A 的票据 → 403
    create_system_admin(db_session, username="admin2", password="SecondAdmin-2026x")
    second = admin_client.post(
        "/admin-api/auth/login", json={"username": "admin2", "password": "SecondAdmin-2026x"}
    )
    second_headers = {"Authorization": f"Bearer {second.json()['access_token']}"}
    reused = admin_client.get(
        f"{V1}/users/{elder.id}/profile",
        headers={**second_headers, SESSION_HEADER: own["session_id"]},
    )
    assert reused.status_code == 403
    assert reused.json()["error"]["code"] == "ADMIN_ACCESS_SESSION_INVALID"
    assert other_session["session_id"] != own["session_id"]


def test_session_expiry_and_revocation(
    admin_client: TestClient, db_session, v1_headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    space, elder, _younger, _edge, _fact = _seed_space_with_relations(db_session)
    created = _create_session(
        admin_client, v1_headers, target_type="user", target_id=elder.id
    ).json()
    headers = {**v1_headers, SESSION_HEADER: created["session_id"]}
    assert admin_client.get(f"{V1}/users/{elder.id}/profile", headers=headers).status_code == 200

    # 过期：TTL 30 分钟后一律 403
    base = timeutil.utcnow()
    monkeypatch.setattr(timeutil, "utcnow", lambda: base + timedelta(minutes=31))
    expired = admin_client.get(f"{V1}/users/{elder.id}/profile", headers=headers)
    assert expired.status_code == 403
    monkeypatch.undo()

    # 撤销：revoked_at 置位后一律 403
    fresh = _create_session(admin_client, v1_headers, target_type="user", target_id=elder.id).json()
    row = db_session.query(AdminAccessSession).order_by(AdminAccessSession.id.desc()).first()
    assert row is not None
    row.revoked_at = timeutil.utcnow()
    db_session.commit()
    revoked = admin_client.get(
        f"{V1}/users/{elder.id}/profile",
        headers={**v1_headers, SESSION_HEADER: fresh["session_id"]},
    )
    assert revoked.status_code == 403


def test_denied_attempts_are_audited(admin_client: TestClient, db_session, v1_headers) -> None:
    space, elder, _younger, _edge, _fact = _seed_space_with_relations(db_session)
    from app.models.admin_access import AdminAccessAudit

    denied = admin_client.get(
        f"{V1}/users/{elder.id}/profile",
        headers={**v1_headers, SESSION_HEADER: "bogus-ticket"},
    )
    assert denied.status_code == 403
    row = (
        db_session.query(AdminAccessAudit)
        .filter(AdminAccessAudit.action == "access_session.denied")
        .one()
    )
    assert row.target_type == "user"
    assert row.target_id == elder.id
    assert row.endpoint == "/admin-api/v1/users/{user_id}/profile"
    # 审计不保存票据明文
    assert "bogus-ticket" not in str(row.filters_json)

    # 成功使用同样写审计
    created = _create_session(admin_client, v1_headers, target_type="user", target_id=elder.id)
    assert created.status_code == 200
    admin_client.get(
        f"{V1}/users/{elder.id}/profile",
        headers={**v1_headers, SESSION_HEADER: created.json()["session_id"]},
    )
    used = (
        db_session.query(AdminAccessAudit)
        .filter(AdminAccessAudit.action == "access_session.used")
        .count()
    )
    assert used == 1


def test_relation_label_and_fact_provenance_are_safe(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    """label-safe：脱敏可靠时返回清理值；证据原文/raw text 永不出现。"""
    from conftest import create_v1_relation

    creator = create_user_with_pin(db_session, "标注用户", "303030")
    target = create_user_with_pin(db_session, "被标注", "303031")
    space = seed_space_with_owner(db_session, creator.id, name="标注空间", kind="lineage")
    create_space_member(db_session, space.id, target.id)
    # 含手机号的称谓 → 不可靠脱敏 → label_safe 为 None（不给原文）
    labeled = create_v1_relation(
        db_session, from_user_id=target.id, to_user_id=creator.id, dir_class="elder"
    )
    labeled.label = "有事联系13800138000"
    db_session.commit()
    session_id = _create_session(
        admin_client, v1_headers, target_type="space", target_id=space.id
    ).json()["session_id"]
    response = admin_client.get(
        f"{V1}/spaces/{space.id}/relations",
        headers={**v1_headers, SESSION_HEADER: session_id},
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["label_safe"] is None
    assert "13800138000" not in str(response.json())
