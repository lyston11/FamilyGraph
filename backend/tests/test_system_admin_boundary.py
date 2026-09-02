from conftest import (
    create_space_member,
    create_system_admin,
    create_user_with_pin,
    seed_space_with_owner,
    system_admin_header,
)
from fastapi.testclient import TestClient

from app.main import app
from app.utils import security


def test_system_admin_is_independent_and_metadata_only(client: TestClient, db_session) -> None:
    initialized = client.post("/api/bootstrap/initialize", json={"name": "平台管理员"})
    assert initialized.status_code == 200
    one_time_pin = initialized.json()["one_time_pin"]

    first_login = client.post("/api/auth/login", json={"name": "平台管理员", "pin": one_time_pin})
    assert first_login.status_code == 200
    first_token = first_login.json()["access_token"]
    changed = client.put(
        "/api/me/pin",
        json={"old_pin": one_time_pin, "new_pin": "654321"},
        headers={"Authorization": f"Bearer {first_token}"},
    )
    assert changed.status_code == 200

    family_user = create_user_with_pin(db_session, "家庭用户", "123456")
    db_session.commit()
    system_login = client.post("/api/auth/login", json={"name": "平台管理员", "pin": "654321"})
    assert system_login.status_code == 200
    system_token = system_login.json()["access_token"]
    headers = {"Authorization": f"Bearer {system_token}"}

    accounts = client.get("/api/admin/accounts", headers=headers)
    assert accounts.status_code == 200
    assert {row["subject_type"] for row in accounts.json()} == {"system_admin", "family_user"}
    assert all(
        "bio" not in row and "birth" not in row and "gender" not in row for row in accounts.json()
    )

    assert client.get("/api/me", headers=headers).status_code == 401
    assert client.get("/api/spaces", headers=headers).status_code == 401

    family_login = client.post("/api/auth/login", json={"name": family_user.name, "pin": "123456"})
    assert family_login.status_code == 200
    family_headers = {"Authorization": f"Bearer {family_login.json()['access_token']}"}
    assert client.get("/api/admin/accounts", headers=family_headers).status_code == 403


def test_admin_metadata_routes_are_registered_once() -> None:
    paths = [route.path for route in app.routes if route.path.startswith("/api/admin/")]
    assert paths.count("/api/admin/accounts") == 1
    assert paths.count("/api/admin/spaces") == 1
    assert paths.count("/api/admin/space-managers") == 1


def test_system_admin_refresh_keeps_principal_type(client: TestClient, db_session) -> None:
    """system_admin refresh 只能续期 system_admin 主体，不得转换为 family_user（SAR-F2）。"""
    create_system_admin(db_session)
    first = client.post("/api/auth/login", json={"name": "平台管理员", "pin": "654321"})
    assert first.status_code == 200
    pair = first.json()
    assert pair["user"]["principal_type"] == "system_admin"

    refreshed = client.post("/api/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert refreshed.status_code == 200
    assert refreshed.json()["user"]["principal_type"] == "system_admin"

    headers = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
    assert client.get("/api/admin/accounts", headers=headers).status_code == 200
    # 家庭端点继续拒绝该续期会话
    assert client.get("/api/me", headers=headers).status_code == 401


def test_pin_change_invalidates_old_system_admin_token(client: TestClient, db_session) -> None:
    """改 PIN 后 token_version+1：旧 access 即刻失效，新 PIN 登录才可进入后台（SAR-F2）。"""
    initialized = client.post("/api/bootstrap/initialize", json={"name": "平台管理员"})
    one_time_pin = initialized.json()["one_time_pin"]
    first_login = client.post("/api/auth/login", json={"name": "平台管理员", "pin": one_time_pin})
    assert first_login.status_code == 200
    old_token = first_login.json()["access_token"]
    old_headers = {"Authorization": f"Bearer {old_token}"}

    # 首登未改 PIN：后台元数据在白名单外（architecture.md §0.8.4）
    assert client.get("/api/admin/accounts", headers=old_headers).status_code == 403

    changed = client.put(
        "/api/me/pin",
        json={"old_pin": one_time_pin, "new_pin": "654321"},
        headers=old_headers,
    )
    assert changed.status_code == 200

    # 旧 access 按 token_version 失效：治理路由对不可解析主体统一 403
    # （require_system_admin 合同，与无 token 行为一致），绝不再放行
    assert client.get("/api/admin/accounts", headers=old_headers).status_code == 403
    # 家庭端点的严格认证依赖返回 401
    assert client.get("/api/me", headers=old_headers).status_code == 401

    relogin = client.post("/api/auth/login", json={"name": "平台管理员", "pin": "654321"})
    assert relogin.status_code == 200
    new_headers = {"Authorization": f"Bearer {relogin.json()['access_token']}"}
    assert client.get("/api/admin/accounts", headers=new_headers).status_code == 200


def test_admin_routes_reject_missing_or_mistyped_principal_tokens(
    client: TestClient, db_session
) -> None:
    """无 token / 错误 principal_type / 未知 principal_type 一律 403（治理 API 边界）。"""
    admin = create_system_admin(db_session)
    admin_account = admin.account

    def _token(principal_type: str) -> str:
        return security.create_access_token(
            admin.id,
            admin_account.token_version,
            is_platform_operator=True,
            principal_type=principal_type,
        )

    admin_routes = (
        "/api/admin/accounts",
        "/api/admin/spaces",
        "/api/admin/space-managers",
        "/api/admin/manager-transfer-consents",
        "/api/admin/manager-applications",
        "/api/admin/spaces/1/members",
    )

    # 无 token
    for route in admin_routes:
        assert client.get(route).status_code == 403, route

    # 主体类型声明为 family_user（含缺失 principal_type 的兼容缺省语义）
    family_typed = {"Authorization": f"Bearer {_token('family_user')}"}
    for route in admin_routes:
        assert client.get(route, headers=family_typed).status_code == 403, route

    # 未知 principal_type
    unknown_typed = {"Authorization": f"Bearer {_token('space_admin')}"}
    for route in admin_routes:
        assert client.get(route, headers=unknown_typed).status_code == 403, route

    # 反向隔离同步成立：上述伪造 token 也不能落成家庭主体
    for headers in (family_typed, unknown_typed):
        assert client.get("/api/me", headers=headers).status_code == 401
        assert client.get("/api/spaces", headers=headers).status_code == 401


def test_unknown_space_member_query_is_safe_empty(client: TestClient, db_session) -> None:
    """未知 space_id 的成员查询返回安全空集，不提供空间存在性探针（SAR-F3）。"""
    create_system_admin(db_session)
    headers = system_admin_header(client)

    existing = seed_space_with_owner(
        db_session, create_user_with_pin(db_session, "管理员", "101010").id, name="真实空间"
    )
    missing = existing.id + 9999

    known = client.get(f"/api/admin/spaces/{existing.id}/members", headers=headers)
    unknown = client.get(f"/api/admin/spaces/{missing}/members", headers=headers)
    assert known.status_code == 200
    assert unknown.status_code == 200
    # 与真实空间不存在时的行为一致：空集 + 200，二者不可区分
    assert unknown.json() == []
    nonexistent_after_any = client.get("/api/admin/spaces/424242/members", headers=headers)
    assert nonexistent_after_any.status_code == 200
    assert nonexistent_after_any.json() == []


def test_governance_metadata_field_whitelist(client: TestClient, db_session) -> None:
    """治理响应只含最小元数据：无档案日期/性别/简介/头像/附件/图边/披露字段（SAR-F3）。"""
    create_system_admin(db_session)
    manager = create_user_with_pin(
        db_session,
        "空间管理员",
        "202020",
        gender="f",
        birth={"cal_type": "solar", "date": "1970-01-01"},
        bio="敏感情报",
    )
    space = seed_space_with_owner(db_session, manager.id, name="母系家族", kind="lineage")
    member = create_user_with_pin(
        db_session,
        "普通成员",
        "303030",
        gender="m",
        birth={"cal_type": "solar", "date": "1980-02-02"},
        bio="成员隐私",
    )
    create_space_member(db_session, space.id, member.id)
    db_session.commit()

    headers = system_admin_header(client)
    responses = {
        "accounts": client.get("/api/admin/accounts", headers=headers),
        "spaces": client.get("/api/admin/spaces", headers=headers),
        "space_managers": client.get("/api/admin/space-managers", headers=headers),
        "space_members": client.get(f"/api/admin/spaces/{space.id}/members", headers=headers),
        "manager_applications": client.get("/api/admin/manager-applications", headers=headers),
        "transfer_consents": client.get("/api/admin/manager-transfer-consents", headers=headers),
    }
    for label, resp in responses.items():
        assert resp.status_code == 200, f"{label}: {resp.text}"

    forbidden_fields = (
        "birth",
        "gender",
        "bio",
        "avatar",
        "avatar_path",
        "privacy_mode",
        "profile_status",
        "attachments",
        "relations",
        "graph",
        "disclosure",
        "memory",
        "session",
        "pin_hash",
    )
    for label, resp in responses.items():
        rows = resp.json()
        assert isinstance(rows, list)
        for row in rows:
            leaked = [field for field in forbidden_fields if field in row]
            assert leaked == [], f"{label} leaked {leaked}"

    # 成员/空间元数据确实包含必要的最小字段（投影非空洞）
    member_row = next(
        row for row in responses["space_members"].json() if row["user_id"] == member.id
    )
    assert member_row["name"] == "普通成员"
    assert member_row["role"] == "member"
    assert set(member_row) == {
        "user_id",
        "account_id",
        "name",
        "role",
        "status",
        "created_at",
        "updated_at",
    }
    space_row = next(row for row in responses["spaces"].json() if row["id"] == space.id)
    assert space_row["manager_user_id"] == manager.id
    assert set(space_row) == {
        "id",
        "name",
        "kind",
        "status",
        "created_at",
        "manager_user_id",
        "manager_account_id",
        "manager_name",
    }


def test_legacy_break_glass_admin_routes_are_not_registered() -> None:
    """旧 admin.py 的家庭 break-glass 路由必须保持未注册（安全基线，禁止回装）。"""
    registered = {getattr(route, "path", "") for route in app.routes}
    legacy_paths = {
        "/api/admin/users",
        "/api/admin/users/lookup",
        "/api/admin/users/{user_id}",
        "/api/admin/users/{user_id}/reset-pin",
        "/api/admin/audit-logs",
        "/api/admin/owner-invitations",
        "/api/admin/owner-invitations/{invitation_id}/revoke",
        "/api/admin/data-rights",
        "/api/admin/data-rights/{request_id}/resolve-correction",
        "/api/admin/claim-disputes",
        "/api/admin/claim-disputes/{dispute_id}/resolve",
    }
    assert registered.isdisjoint(legacy_paths), sorted(registered & legacy_paths)
    # 兜底：任何 /api/admin/users* 形态的旧家庭数据路由都不允许存在
    assert not any(path.startswith("/api/admin/users") for path in registered)
