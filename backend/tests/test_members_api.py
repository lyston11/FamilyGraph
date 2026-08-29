"""m1a 成员档案 API 集成测试：建档一次性 PIN / 权限矩阵 / 认领降权 /
disclosure 校验 / 删除级联与审计快照（implement.md #5）。
"""

import itertools

from conftest import auth_header, create_user_with_pin, login
from fastapi.testclient import TestClient

from app.models.account import Account
from app.models.audit_log import AuditLog
from app.models.refresh_session import RefreshSession
from app.models.relation import Relation
from app.models.relationship_facts import SourceFact
from app.models.user import User
from app.models.v2_foundation import MemberCreationRequest

_key_counter = itertools.count(1)


def _create_member(client, headers, *, key=None, **overrides):
    payload = {
        "name": "母亲",
        "gender": "f",
        "birth": {"cal_type": "solar", "date": "1960-05-01", "mirror_date": "1960:4:6"},
        "privacy_mode": "handover",
        "relation_dir_class": "elder",
        **overrides,
    }
    if key is None:
        key = f"mk-{next(_key_counter)}"
    return client.post("/api/users", json=payload, headers={**headers, "Idempotency-Key": key})


def test_create_member_issues_one_time_pin_and_audit(client, db_session) -> None:
    creator = create_user_with_pin(db_session, "张三", "123456")
    tokens = login(client, "张三", "123456").json()

    response = _create_member(client, auth_header(tokens))
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"user", "pin", "replayed"}
    assert body["replayed"] is False
    assert len(body["pin"]) == 6 and body["pin"].isdigit()

    user_payload = body["user"]
    assert user_payload["name"] == "母亲"
    assert user_payload["claim_status"] == "managed"
    assert user_payload["privacy_mode"] == "handover"
    assert user_payload["created_by"] == creator.id
    # F3：provisional 最小节点 —— 代管创建者也不回读出生（字段投影遮罩）
    assert user_payload["birth"] == {"__masked__": True}
    assert user_payload["gender"] == {"__masked__": True}
    # AD-9 默认披露开关全 false
    assert set(user_payload["clan_disclosure"].values()) == {False}
    assert user_payload["permissions"] == {"edit": True, "delete": True}

    member = db_session.query(User).filter(User.id == user_payload["id"]).one()
    account = db_session.query(Account).filter(Account.user_id == member.id).one()
    assert account.pin_must_change is True

    # F-1：名字+关系原子落库 —— relation 直接 active（AD-4 新建例外）
    edge = db_session.query(Relation).filter(Relation.to_user == member.id).one()
    assert edge.from_user == creator.id
    assert edge.status == "active"
    assert edge.dir_class == "elder"
    # 关系事实落 proposed（待对方确档后再确认，profile_form 来源）
    fact = (
        db_session.query(SourceFact)
        .filter(
            SourceFact.subject_user_id == member.id,
            SourceFact.object_user_id == creator.id,
        )
        .one()
    )
    assert fact.state == "proposed"
    assert fact.provenance == "profile_form"
    # 幂等台账落一行
    assert db_session.query(MemberCreationRequest).filter_by(member_user_id=member.id).count() == 1

    audits = db_session.query(AuditLog).filter(AuditLog.action == "profile_created").all()
    assert len(audits) == 1
    assert audits[0].actor_id == creator.id
    assert body["pin"] not in audits[0].detail_json


def test_duplicate_names_coexist_with_independent_ids(client, db_session) -> None:
    create_user_with_pin(db_session, "张三", "123456")
    tokens = login(client, "张三", "123456").json()
    first = _create_member(client, auth_header(tokens), name="大壮")
    second = _create_member(client, auth_header(tokens), name="大壮")
    assert first.status_code == second.status_code == 201
    assert first.json()["user"]["id"] != second.json()["user"]["id"]


def test_related_list_scope_self_created_and_operator(db_session, client: TestClient) -> None:
    actor = create_user_with_pin(db_session, "张三", "123456")
    other = create_user_with_pin(db_session, "李四", "234567")
    create_user_with_pin(db_session, "外人档", "345678", created_by=other.id)
    own_member = create_user_with_pin(db_session, "父亲", "456789", created_by=actor.id)
    tokens = login(client, "张三", "123456").json()
    ids = {m["id"] for m in client.get("/api/users", headers=auth_header(tokens)).json()}
    assert ids == {actor.id, own_member.id}  # 不含他人创建的档案

    # v2：platform_operator 无全量家庭数据权，仅见自己
    operator = create_user_with_pin(db_session, "运营者", "999999", is_admin=True)
    operator_tokens = login(client, "运营者", "999999").json()
    operator_ids = {
        m["id"] for m in client.get("/api/users", headers=auth_header(operator_tokens)).json()
    }
    assert operator_ids == {operator.id}


def test_get_member_404_for_unrelated_user(client, db_session) -> None:
    creator = create_user_with_pin(db_session, "张三", "123456")
    member = create_user_with_pin(db_session, "母亲", "234567", created_by=creator.id)
    create_user_with_pin(db_session, "路人", "999999")
    stranger_tokens = login(client, "路人", "999999").json()

    hidden = client.get(f"/api/users/{member.id}", headers=auth_header(stranger_tokens))
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "USER_NOT_FOUND"

    missing = client.get("/api/users/99999", headers=auth_header(stranger_tokens))
    assert missing.json() == hidden.json()  # 不存在与不可见同响应，防枚举

    creator_tokens = login(client, "张三", "123456").json()
    visible = client.get(f"/api/users/{member.id}", headers=auth_header(creator_tokens))
    assert visible.status_code == 200


def _claim_member(client, name: str, initial_pin: str, new_pin: str) -> dict:
    """以建档返回的初始 PIN 走首登强制改 PIN 流程，返回新 token 对。"""
    forced = login(client, name, initial_pin).json()
    changed = client.put(
        "/api/me/pin",
        headers=auth_header(forced),
        json={"old_pin": initial_pin, "new_pin": new_pin},
    )
    assert changed.status_code == 200
    return login(client, name, new_pin).json()


def test_claim_flips_status_and_demotes_handover_creator(client, db_session) -> None:
    """验收：handover 档案被认领后创建者写接口 403；managed→claimed 唯一转换点。"""
    create_user_with_pin(db_session, "张三", "123456")
    creator_tokens = login(client, "张三", "123456").json()
    created = _create_member(client, auth_header(creator_tokens))
    member_id = created.json()["user"]["id"]
    initial_pin = created.json()["pin"]

    # 创建者代管期可编辑
    patch_before = client.patch(
        f"/api/users/{member_id}",
        headers=auth_header(creator_tokens),
        json={"bio": "代管期简介"},
    )
    assert patch_before.status_code == 200

    member_tokens = _claim_member(client, "母亲", initial_pin, "654321")
    db_session.expire_all()
    member = db_session.query(User).filter(User.id == member_id).one()
    assert member.account.status == "claimed"  # v2：唯一转换点在 accounts.status

    # 认领后：创建者写接口 403 CUSTODY_HANDOVER_DONE；读仍 full
    demoted = client.patch(
        f"/api/users/{member_id}",
        headers=auth_header(creator_tokens),
        json={"bio": "越权修改"},
    )
    assert demoted.status_code == 403
    assert demoted.json()["error"]["code"] == "CUSTODY_HANDOVER_DONE"
    disclosure_denied = client.put(
        f"/api/users/{member_id}/disclosure",
        headers=auth_header(creator_tokens),
        json={"avatar": True, "photos": True, "dates": True, "bio": True, "attachments": True},
    )
    assert disclosure_denied.status_code == 403
    delete_denied = client.delete(
        f"/api/users/{member_id}",
        headers=auth_header(creator_tokens),
        params={"confirm_name": "母亲"},
    )
    assert delete_denied.status_code == 403
    still_readable = client.get(f"/api/users/{member_id}", headers=auth_header(creator_tokens))
    assert still_readable.status_code == 200

    # 本人可编辑自己全部档案字段
    self_edit = client.patch(
        f"/api/users/{member_id}",
        headers=auth_header(member_tokens),
        json={"bio": "本人修改", "birth": None},
    )
    assert self_edit.status_code == 200
    assert self_edit.json()["bio"] == "本人修改"


def test_perpetual_creator_unaffected_after_claim(client, db_session) -> None:
    create_user_with_pin(db_session, "张三", "123456")
    tokens = login(client, "张三", "123456").json()
    created = _create_member(client, auth_header(tokens), name="祖父", privacy_mode="perpetual")
    member_id = created.json()["user"]["id"]
    _claim_member(client, "祖父", created.json()["pin"], "654321")

    after_claim = client.patch(
        f"/api/users/{member_id}", headers=auth_header(tokens), json={"bio": "永久编辑权"}
    )
    assert after_claim.status_code == 200
    assert after_claim.json()["permissions"]["edit"] is True


def test_disclosure_exact_key_validation_and_permissions(client, db_session) -> None:
    create_user_with_pin(db_session, "张三", "123456")
    tokens = login(client, "张三", "123456").json()
    member_id = _create_member(client, auth_header(tokens)).json()["user"]["id"]

    ok = client.put(
        f"/api/users/{member_id}/disclosure",
        headers=auth_header(tokens),
        json={"avatar": True, "photos": False, "dates": True, "bio": False, "attachments": False},
    )
    assert ok.status_code == 200
    assert ok.json()["clan_disclosure"]["avatar"] is True
    assert ok.json()["clan_disclosure"]["photos"] is False

    missing_key = client.put(
        f"/api/users/{member_id}/disclosure",
        headers=auth_header(tokens),
        json={"avatar": True, "photos": False, "dates": True, "bio": False},
    )
    assert missing_key.status_code == 422
    extra_key = client.put(
        f"/api/users/{member_id}/disclosure",
        headers=auth_header(tokens),
        json={
            "avatar": True,
            "photos": False,
            "dates": True,
            "bio": False,
            "attachments": False,
            "extra": True,
        },
    )
    assert extra_key.status_code == 422


def test_delete_requires_confirm_name_then_cascades_with_audit_snapshot(client, db_session) -> None:
    create_user_with_pin(db_session, "张三", "123456")
    tokens = login(client, "张三", "123456").json()
    created = _create_member(client, auth_header(tokens))
    member_id = created.json()["user"]["id"]

    # 非本人且非代管者 → 403/404（无权者不可删）
    create_user_with_pin(db_session, "路人", "999999")
    stranger_tokens = login(client, "路人", "999999").json()
    denied = client.delete(
        f"/api/users/{member_id}",
        headers=auth_header(stranger_tokens),
        params={"confirm_name": "母亲"},
    )
    assert denied.status_code == 404  # 无 view 即无删除入口（防枚举）

    # 二次确认不符 → 409
    mismatch = client.delete(
        f"/api/users/{member_id}",
        headers=auth_header(tokens),
        params={"confirm_name": "错误名字"},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "CONFIRM_NAME_MISMATCH"

    # 正确确认：单事务级联 + audit 快照保留
    deleted = client.delete(
        f"/api/users/{member_id}",
        headers=auth_header(tokens),
        params={"confirm_name": "母亲"},
    )
    assert deleted.status_code == 204
    assert db_session.query(User).filter(User.id == member_id).count() == 0
    assert db_session.query(Account).filter(Account.user_id == member_id).count() == 0
    assert db_session.query(RefreshSession).filter_by(user_id=member_id).count() == 0

    audits = (
        db_session.query(AuditLog)
        .filter(AuditLog.action.in_(["profile_created", "profile_deleted"]))
        .order_by(AuditLog.action)
        .all()
    )
    actions = {a.action for a in audits}
    assert actions == {"profile_created", "profile_deleted"}  # 历史审计未被清除
    snapshot_audit = next(a for a in audits if a.action == "profile_deleted")
    assert snapshot_audit.detail["snapshot"]["name"] == "母亲"
    assert snapshot_audit.target_id == member_id

    # 目标若曾持有会话，账号行删除后 refresh 自然失效
    assert client.post("/api/auth/refresh", json={"refresh_token": "whatever"}).status_code == 401


def test_deleted_member_sessions_invalidated_end_to_end(client, db_session) -> None:
    """被删档案的既有会话即刻失效（AD-5：随账号行删除自然失效）。"""
    create_user_with_pin(db_session, "张三", "123456")
    tokens = login(client, "张三", "123456").json()
    created = _create_member(client, auth_header(tokens), name="叔父")
    member_id = created.json()["user"]["id"]
    member_tokens = login(client, "叔父", created.json()["pin"]).json()

    delete = client.delete(
        f"/api/users/{member_id}",
        headers=auth_header(tokens),
        params={"confirm_name": "叔父"},
    )
    assert delete.status_code == 204
    assert client.get("/api/me", headers=auth_header(member_tokens)).status_code == 401


def test_operator_cannot_edit_or_delete_foreign_profiles(db_session, client: TestClient) -> None:
    """v2 §0.2：platform_operator 无家庭数据编辑/删除权（404 防枚举）。"""
    create_user_with_pin(db_session, "张三", "123456")
    tokens = login(client, "张三", "123456").json()
    member_id = _create_member(client, auth_header(tokens)).json()["user"]["id"]

    create_user_with_pin(db_session, "运营者", "888888", is_admin=True)
    operator_tokens = login(client, "运营者", "888888").json()
    patched = client.patch(
        f"/api/users/{member_id}",
        headers=auth_header(operator_tokens),
        json={"bio": "越权修正"},
    )
    assert patched.status_code == 404
    removed = client.delete(
        f"/api/users/{member_id}",
        headers=auth_header(operator_tokens),
        params={"confirm_name": "母亲"},
    )
    assert removed.status_code == 404
    # 目标档案未被删除，且无 profile_deleted 审计产生
    assert db_session.query(User).filter(User.id == member_id).count() == 1
    assert db_session.query(AuditLog).filter(AuditLog.action == "profile_deleted").count() == 0


def test_challenge_candidates_include_created_by_name(client, db_session) -> None:
    """同名同 PIN 消歧候选补代管创建者名（design 兼容项）。"""
    creator = create_user_with_pin(db_session, "李四", "111111")
    managed = create_user_with_pin(
        db_session, "大壮", "123456", pin_must_change=True, created_by=creator.id
    )
    standalone = create_user_with_pin(db_session, "大壮", "123456")

    conflict = login(client, "大壮", "123456")
    assert conflict.status_code == 409
    candidates = conflict.json()["candidates"]
    by_id = {c["id"]: c for c in candidates}
    assert by_id[standalone.id]["created_by_name"] is None
    assert by_id[managed.id]["created_by_name"] == "李四"


def test_member_list_does_not_bypass_field_projection(client, db_session) -> None:
    """F2/F3：列表出口与详情出口同源字段投影，provisional 内容字段不回读。"""
    create_user_with_pin(db_session, "张三", "123456")
    tokens = login(client, "张三", "123456").json()
    created = _create_member(client, auth_header(tokens), gender="f")
    assert created.status_code == 201
    member_id = created.json()["user"]["id"]

    rows = client.get("/api/users", headers=auth_header(tokens)).json()
    row = next(r for r in rows if r["id"] == member_id)
    # 代管创建者对 provisional 档案不得经列表读到性别/出生（与 GET /users/{id} 同源）
    assert row["gender"] == {"__masked__": True}
    assert row["birth"] == {"__masked__": True}
    assert row["bio"] == {"__masked__": True}
    assert row["name"] == "母亲"


def test_create_member_idempotent_replay_returns_same_member_without_pin(
    client, db_session
) -> None:
    """F-1：同 key 同内容重放返回首结果（replayed=true, pin=null），不重复建边。"""
    create_user_with_pin(db_session, "张三", "123456")
    headers = auth_header(login(client, "张三", "123456").json())
    key = "k-replay"

    first = _create_member(client, headers, key=key)
    assert first.status_code == 201
    assert first.json()["replayed"] is False
    member_id = first.json()["user"]["id"]
    assert first.json()["pin"] is not None

    second = _create_member(client, headers, key=key)
    assert second.status_code == 201
    assert second.json()["replayed"] is True
    assert second.json()["pin"] is None
    assert second.json()["user"]["id"] == member_id

    # 未产生第二个档案或第二条边；幂等台账仍只有一行
    assert db_session.query(User).filter(User.name == "母亲").count() == 1
    assert db_session.query(Relation).filter(Relation.to_user == member_id).count() == 1
    assert (
        db_session.query(MemberCreationRequest).filter_by(idempotency_key="k-replay").count() == 1
    )


def test_create_member_same_key_different_payload_conflict(client, db_session) -> None:
    """F-1：同 key 不同内容 → 409 IDEMPOTENCY_PAYLOAD_CONFLICT，不落新档案。"""
    create_user_with_pin(db_session, "张三", "123456")
    headers = auth_header(login(client, "张三", "123456").json())

    first = _create_member(client, headers, key="k-conflict")
    assert first.status_code == 201
    member_id = first.json()["user"]["id"]

    second = _create_member(client, headers, key="k-conflict", name="另一个名字")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"
    assert db_session.query(User).filter(User.id == member_id).count() == 1


def test_create_member_invalid_space_rolls_back_profile_and_relation(client, db_session) -> None:
    """F-1：任一步失败整体回滚 —— 空间无效时不留孤儿档案或边。"""
    create_user_with_pin(db_session, "张三", "123456")
    headers = auth_header(login(client, "张三", "123456").json())

    resp = _create_member(client, headers, key="k-rollback", space_membership={"space_id": 999999})
    assert resp.status_code == 404
    assert db_session.query(User).filter(User.name == "母亲").count() == 0
    assert db_session.query(Relation).count() == 0
    assert db_session.query(MemberCreationRequest).count() == 0
