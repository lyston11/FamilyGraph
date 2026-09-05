"""并流绑定全流程集成测试（09-05 Chunk D，implement.md D4）。

覆盖：建档撞名转绑定（强/弱匹配、managed 回归 409、消歧开关旁路）、确认后人物
唯一（§0.9）、确认前发起人零数据可见、target 侧最小字段白名单、PIN 复验与防
枚举、重复确认/终态 409、reject/cancel 人物废弃、幂等重放、无凭据人物不阻塞
注册、target 已确档时条件跳过转换。
"""

import itertools

import pytest
from conftest import (
    auth_header,
    create_space_member,
    create_user_with_pin,
    login,
    seed_space_with_owner,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.account import Account
from app.models.account_binding import AccountBinding
from app.models.audit_log import AuditLog
from app.models.relation import Relation
from app.models.relationship_facts import SourceFact
from app.models.space import SpaceProfileRef
from app.models.user import User
from app.services import person_identity
from app.utils import timeutil

_key_counter = itertools.count(1)

_SOLAR = {"cal_type": "solar", "date": "1948-03-12"}


def _register(client: TestClient, name: str, *, pin: str = "654321"):
    return client.post("/api/auth/register", json={"name": name, "pin": pin})


def _create_member(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str,
    birth: dict | None = None,
    space_id: int | None = None,
    relation: str = "elder",
    allow: bool | None = None,
    key: str | None = None,
):
    if key is None:
        key = f"bind-{next(_key_counter)}"
    payload: dict = {"name": name, "relation_dir_class": relation}
    if birth is not None:
        payload["birth"] = birth
    if space_id is not None:
        payload["space_membership"] = {"space_id": space_id}
    if allow is not None:
        payload["allow_duplicate_person"] = allow
    return client.post("/api/users", json=payload, headers={**headers, "Idempotency-Key": key})


def _setup_collision(
    db_session, client: TestClient, *, target_name="张三", target_birth=None, member_birth=None
):
    """真实注册目标 + 空间成员资格 + 发起人建档撞名，返回上下文元组。"""
    registered = _register(client, target_name)
    assert registered.status_code == 200, registered.text
    target_tokens = registered.json()
    target_id = target_tokens["user"]["id"]
    if target_birth is not None:
        patched = client.patch(
            f"/api/users/{target_id}",
            headers=auth_header(target_tokens),
            json={"birth": target_birth},
        )
        assert patched.status_code == 200, patched.text

    initiator = create_user_with_pin(db_session, "发起人", "111111")
    space = seed_space_with_owner(db_session, initiator.id, name="绑定空间")
    create_space_member(db_session, space.id, target_id)
    db_session.commit()

    headers = auth_header(login(client, "发起人", "111111").json())
    response = _create_member(
        client, headers, name=target_name, birth=member_birth, space_id=space.id
    )
    return {
        "response": response,
        "target_id": target_id,
        "target_tokens": target_tokens,
        "initiator": initiator,
        "space": space,
        "headers": headers,
    }


def _binding_row(db_session, binding_id: int) -> AccountBinding:
    db_session.expire_all()
    return db_session.get(AccountBinding, binding_id)


# ---- 1. 撞名转绑定：不建 managed 账号 ----


def test_weak_collision_converts_to_pending_binding_without_account(db_session, client) -> None:
    """弱匹配（自注册目标无生日）→ 绑定(pending)；无凭据人物 + pending 关系边。"""
    context = _setup_collision(db_session, client)
    response = context["response"]
    # design.md §4：撞名转绑定分支返回 200（非建档成功语义）
    assert response.status_code == 200, response.text
    body = response.json()
    # 发起人响应精确字段集：无被绑定方任何数据（无 target id / 空间 / 关系信息）
    assert set(body) == {"user", "pin", "replayed", "bound_to_existing", "binding_id"}
    assert body["bound_to_existing"] is True
    assert body["pin"] is None  # 不建 managed 账号 → 无一次性 PIN
    assert body["replayed"] is False
    assert body["user"]["name"] == "张三"
    assert body["user"]["id"] != context["target_id"]
    # 响应不携带被绑定方任何数据：载荷中各 id 均指向人物/绑定行而非被绑定人
    # （结构保证：响应 schema 无 target 来源字段；binding/person 断言见下方 DB 校验）

    binding = _binding_row(db_session, body["binding_id"])
    assert binding.status == "pending"
    assert binding.initiator_id == context["initiator"].id
    assert binding.target_id == context["target_id"]
    person_id = binding.person_id
    assert person_id is not None and person_id != context["target_id"]

    # 不建 managed 账号：无凭据人物不参与登录（§0.9：重复建档 = 多出一份凭据）
    assert db_session.scalar(select(Account.id).where(Account.user_id == person_id)) is None
    person = db_session.get(User, person_id)
    assert person is not None and person.profile_status == "provisional"
    assert person.created_by == context["initiator"].id

    # provisional 人物只占空间身份槽位（SpaceProfileRef），不是 SpaceMember
    ref = (
        db_session.query(SpaceProfileRef)
        .filter(
            SpaceProfileRef.space_id == context["space"].id,
            SpaceProfileRef.user_id == person_id,
        )
        .one()
    )
    assert ref.status == "active"

    # 目标为已存在/claimed 账号 → 关系保持 pending（不走 AD-4 新建例外）
    edge = (
        db_session.query(Relation)
        .filter(
            Relation.from_user == context["initiator"].id,
            Relation.to_user == person_id,
        )
        .one()
    )
    assert edge.status == "pending"

    actions = {row.action for row in db_session.query(AuditLog).all()}
    assert "account_binding_created" in actions
    assert "profile_created" in actions


def test_strong_collision_with_self_registered_also_binds(db_session, client) -> None:
    """强匹配（同名同生日）命中自注册账号同样转绑定（allow 开关不放宽强匹配）。"""
    context = _setup_collision(db_session, client, target_birth=_SOLAR, member_birth=dict(_SOLAR))
    response = context["response"]
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bound_to_existing"] is True

    audit = db_session.query(AuditLog).filter(AuditLog.action == "account_binding_created").one()
    assert '"match_strength": "strong"' in audit.detail_json


# ---- 2. 确认流：PIN 复验 + 身份确认 + 人物并回（§0.9 人物唯一）----


def test_confirm_merges_person_into_target_and_confirms_identity(db_session, client) -> None:
    context = _setup_collision(db_session, client)
    body = context["response"].json()
    binding_id = body["binding_id"]
    person_id = _binding_row(db_session, binding_id).person_id
    target_id = context["target_id"]
    initiator = context["initiator"]
    space_id = context["space"].id

    # target 侧待确认视图：精确字段白名单（仅「这是我」判断所需最小信息）
    listing = client.get("/api/bindings", headers=auth_header(context["target_tokens"]))
    assert listing.status_code == 200, listing.text
    items = listing.json()
    assert len(items) == 1
    assert set(items[0]) == {
        "id",
        "initiator_name",
        "person_name",
        "status",
        "created_at",
        "resolved_at",
    }
    assert items[0]["id"] == binding_id
    assert items[0]["initiator_name"] == "发起人"
    assert items[0]["person_name"] == "张三"
    assert items[0]["status"] == "pending"
    assert items[0]["resolved_at"] is None

    confirmed = client.post(
        f"/api/bindings/{binding_id}/confirm",
        headers=auth_header(context["target_tokens"]),
        json={"pin": "654321"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert set(confirmed.json()) == {
        "id",
        "initiator_name",
        "person_name",
        "status",
        "created_at",
        "resolved_at",
    }
    assert confirmed.json()["status"] == "confirmed"

    db_session.expire_all()
    # 人物并回：无凭据 provisional 人物删除，不留重复人物（§0.9）
    assert db_session.get(User, person_id) is None
    assert db_session.get(AccountBinding, binding_id).person_id is None  # SET NULL 留处置记录
    assert (
        db_session.query(SpaceProfileRef).filter(SpaceProfileRef.user_id == person_id).count() == 0
    )
    candidates = person_identity.find_duplicate_candidates(
        db_session, space_id=space_id, name="张三", birth=None
    )
    assert [c.user_id for c in candidates] == [target_id]  # 空间内该人物身份唯一

    # 关系边挂接既有 user 并由本人 accept：initiator→target active
    edge = (
        db_session.query(Relation)
        .filter(Relation.from_user == initiator.id, Relation.to_user == target_id)
        .one()
    )
    assert edge.status == "active"
    # proposed 关系事实随并回改指向 target，无残留指向人物的事实
    facts = db_session.query(SourceFact).filter(
        (SourceFact.subject_user_id == person_id) | (SourceFact.object_user_id == person_id)
    )
    assert facts.count() == 0
    # elder 边映射为 biological_parent，方向合同 subject=target（长辈）、object=initiator
    repointed = (
        db_session.query(SourceFact)
        .filter(
            SourceFact.subject_user_id == target_id,
            SourceFact.object_user_id == initiator.id,
            SourceFact.state == "proposed",
        )
        .all()
    )
    assert repointed, "建档关系事实应改指向既有 user"

    # 「这是我」= identity_fsm 唯一转换点：target 档案 identity_confirmed
    target = db_session.get(User, target_id)
    assert target.profile_status == "identity_confirmed"
    assert target.profile_confirmed_at is not None

    actions = {row.action for row in db_session.query(AuditLog).all()}
    assert "account_binding_confirmed" in actions


def test_confirm_twice_conflicts_409(db_session, client) -> None:
    context = _setup_collision(db_session, client)
    binding_id = context["response"].json()["binding_id"]
    target_headers = auth_header(context["target_tokens"])

    first = client.post(
        f"/api/bindings/{binding_id}/confirm", headers=target_headers, json={"pin": "654321"}
    )
    assert first.status_code == 200
    repeat = client.post(
        f"/api/bindings/{binding_id}/confirm", headers=target_headers, json={"pin": "654321"}
    )
    assert repeat.status_code == 409
    assert repeat.json()["error"]["code"] == "BINDING_ALREADY_RESOLVED"


# ---- 3. 授权与 PIN 复验 ----


def test_confirm_requires_target_self_and_correct_pin(db_session, client) -> None:
    context = _setup_collision(db_session, client)
    binding_id = context["response"].json()["binding_id"]
    initiator_headers = auth_header(login(client, "发起人", "111111").json())

    # 非本人与不存在同一 404（防枚举）
    wrong_owner = client.post(
        f"/api/bindings/{binding_id}/confirm", headers=initiator_headers, json={"pin": "654321"}
    )
    missing = client.post(
        "/api/bindings/999999/confirm", headers=initiator_headers, json={"pin": "654321"}
    )
    assert wrong_owner.status_code == missing.status_code == 404
    assert wrong_owner.json() == missing.json()
    assert wrong_owner.json()["error"]["code"] == "BINDING_NOT_FOUND"

    # PIN 复验失败：防枚举统一文案 + 失败计数落库 + 绑定保持 pending
    bad_pin = client.post(
        f"/api/bindings/{binding_id}/confirm",
        headers=auth_header(context["target_tokens"]),
        json={"pin": "999999"},
    )
    assert bad_pin.status_code == 401
    error = bad_pin.json()["error"]
    assert error["code"] == "AUTH_INVALID_CREDENTIALS"
    assert error["message"] == "名字或 PIN 码错误"

    db_session.expire_all()
    binding = db_session.get(AccountBinding, binding_id)
    assert binding.status == "pending"
    target_account = db_session.get(Account, binding.target_id)
    assert target_account.failed_attempts == 1


def test_confirm_with_wrong_pin_does_not_advance_lockout_free_target_state(db_session, client):
    """正确 PIN 前的错误尝试不计入成功：成功后计数清零（登录侧 register_success 同源）。"""
    context = _setup_collision(db_session, client)
    binding_id = context["response"].json()["binding_id"]
    target_headers = auth_header(context["target_tokens"])

    client.post(
        f"/api/bindings/{binding_id}/confirm", headers=target_headers, json={"pin": "999999"}
    )
    ok = client.post(
        f"/api/bindings/{binding_id}/confirm", headers=target_headers, json={"pin": "654321"}
    )
    assert ok.status_code == 200
    db_session.expire_all()
    target_id = context["target_id"]
    assert db_session.get(Account, target_id).failed_attempts == 0


# ---- 4. 终态不可逆：reject / cancel 并废弃撞名人物 ----


def test_target_reject_disposes_person(db_session, client) -> None:
    context = _setup_collision(db_session, client)
    binding_id = context["response"].json()["binding_id"]
    person_id = _binding_row(db_session, binding_id).person_id

    rejected = client.post(
        f"/api/bindings/{binding_id}/reject", headers=auth_header(context["target_tokens"])
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    db_session.expire_all()
    assert db_session.get(User, person_id) is None  # 撞名人物随终态废弃
    assert (
        db_session.query(SpaceProfileRef).filter(SpaceProfileRef.user_id == person_id).count() == 0
    )
    assert db_session.query(Relation).filter(Relation.to_user == person_id).count() == 0
    binding = db_session.get(AccountBinding, binding_id)
    assert binding.status == "rejected" and binding.resolved_at is not None
    actions = {row.action for row in db_session.query(AuditLog).all()}
    assert "account_binding_rejected" in actions

    # 终态不可逆：confirm / 再次 reject 均 409
    late_confirm = client.post(
        f"/api/bindings/{binding_id}/confirm",
        headers=auth_header(context["target_tokens"]),
        json={"pin": "654321"},
    )
    assert late_confirm.status_code == 409
    late_reject = client.post(
        f"/api/bindings/{binding_id}/reject", headers=auth_header(context["target_tokens"])
    )
    assert late_reject.status_code == 409


def test_initiator_cancel_disposes_person_and_is_initiator_only(db_session, client) -> None:
    context = _setup_collision(db_session, client)
    binding_id = context["response"].json()["binding_id"]
    person_id = _binding_row(db_session, binding_id).person_id
    target_headers = auth_header(context["target_tokens"])

    # 非发起人（被绑定人）不可取消：与不存在同一 404
    forbidden = client.delete(f"/api/bindings/{binding_id}", headers=target_headers)
    assert forbidden.status_code == 404

    cancelled = client.delete(
        f"/api/bindings/{binding_id}", headers=auth_header(login(client, "发起人", "111111").json())
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    db_session.expire_all()
    assert db_session.get(User, person_id) is None
    binding = db_session.get(AccountBinding, binding_id)
    assert binding.status == "cancelled" and binding.resolved_at is not None
    # 发起人取消后 target 确认 → 409（终态不可逆）
    late_confirm = client.post(
        f"/api/bindings/{binding_id}/confirm", headers=target_headers, json={"pin": "654321"}
    )
    assert late_confirm.status_code == 409
    actions = {row.action for row in db_session.query(AuditLog).all()}
    assert "account_binding_cancelled" in actions


# ---- 5. 既有门禁回归：managed 撞名照旧 409；消歧开关照旧放行 ----


def test_managed_duplicate_still_409(db_session, client) -> None:
    """managed/无自注册语义的撞名维持既有强/弱门禁，绑定转换不得吞掉 409。"""
    initiator = create_user_with_pin(db_session, "建档人", "111111")
    space = seed_space_with_owner(db_session, initiator.id, name="门禁空间")
    db_session.commit()
    headers = auth_header(login(client, "建档人", "111111").json())

    first = _create_member(client, headers, name="李四", birth=dict(_SOLAR), space_id=space.id)
    assert first.status_code == 201, first.text
    strong = _create_member(client, headers, name="李四", birth=dict(_SOLAR), space_id=space.id)
    assert strong.status_code == 409
    assert strong.json()["error"]["code"] == "PERSON_DUPLICATE_IN_SPACE"
    assert db_session.query(AccountBinding).count() == 0

    weak_first = _create_member(client, headers, name="王五", space_id=space.id)
    assert weak_first.status_code == 201
    weak_second = _create_member(client, headers, name="王五", space_id=space.id)
    assert weak_second.status_code == 409
    assert weak_second.json()["error"]["code"] == "PERSON_DUPLICATE_AMBIGUOUS"


def test_allow_duplicate_flag_bypasses_self_registered_weak_candidate(db_session, client) -> None:
    """创建者显式消歧（这是另一个人）→ 照旧建 managed 账号，不转绑定。"""
    context = _setup_collision(db_session, client)
    assert context["response"].status_code == 200  # 未带开关：转绑定
    assert db_session.query(AccountBinding).count() == 1

    forced = _create_member(
        client,
        context["headers"],
        name="张三",
        space_id=context["space"].id,
        allow=True,
    )
    assert forced.status_code == 201, forced.text
    assert forced.json()["bound_to_existing"] is False
    assert forced.json()["binding_id"] is None
    assert forced.json()["pin"] is not None  # managed 账号 + 一次性 PIN 照常发放
    # 消歧放行不再产生新绑定
    assert db_session.query(AccountBinding).count() == 1
    forced_user = db_session.get(User, forced.json()["user"]["id"])
    assert forced_user.account is not None


# ---- 6. 幂等与冷启动并存 ----


def test_replay_returns_bound_marker(db_session, client) -> None:
    """同幂等键重放撞名建档：replayed=True 且保持 bound_to_existing 标记。"""
    initiator = create_user_with_pin(db_session, "重放发起人", "111111")
    space = seed_space_with_owner(db_session, initiator.id, name="重放空间")
    registered = _register(client, "赵六")
    target_id = registered.json()["user"]["id"]
    create_space_member(db_session, space.id, target_id)
    db_session.commit()
    headers = auth_header(login(client, "重放发起人", "111111").json())

    payload = {
        "name": "赵六",
        "relation_dir_class": "elder",
        "space_membership": {"space_id": space.id},
    }
    first = client.post(
        "/api/users", json=payload, headers={**headers, "Idempotency-Key": "replay-key"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["bound_to_existing"] is True
    replay = client.post(
        "/api/users", json=payload, headers={**headers, "Idempotency-Key": "replay-key"}
    )
    assert replay.status_code == 200, replay.text
    body = replay.json()
    assert body["replayed"] is True
    assert body["pin"] is None
    assert body["bound_to_existing"] is True
    assert body["binding_id"] == first.json()["binding_id"]
    assert body["user"]["id"] == first.json()["user"]["id"]


def test_ghost_person_does_not_block_registration_of_exact_name(db_session, client) -> None:
    """无凭据撞名人物不得占用注册用户名（注册查重只统计持有 Account 的行）。

    目标注册名「张三」（真实账号）；建档人物名「張三」（繁体，归一键与目标同名
    → 弱匹配命中并转绑定）。人物行存在但无 Account：注册「張三」必须成功，
    而非被人物行以「该用户名已被注册」阻塞。
    """
    initiator = create_user_with_pin(db_session, "繁体发起人", "111111")
    space = seed_space_with_owner(db_session, initiator.id, name="繁体空间")
    registered = _register(client, "张三")
    assert registered.status_code == 200
    create_space_member(db_session, space.id, registered.json()["user"]["id"])
    db_session.commit()
    headers = auth_header(login(client, "繁体发起人", "111111").json())

    ghosted = _create_member(client, headers, name="張三", space_id=space.id)
    assert ghosted.status_code == 200, ghosted.text  # 归一命中自注册目标 → 绑定
    assert ghosted.json()["bound_to_existing"] is True
    ghost_id = ghosted.json()["user"]["id"]
    assert db_session.get(User, ghost_id) is not None  # 人物行存在（无 Account）

    registration = _register(client, "張三", pin="777777")
    assert registration.status_code == 200, registration.text  # 人物行不占用注册用户名
    assert registration.json()["user"]["name"] == "張三"


def test_confirm_when_target_already_identity_confirmed(db_session, client) -> None:
    """target 已确档：跳过身份转换（不重复确认），人物并回照常完成。"""
    target = create_user_with_pin(
        db_session, "已确档目标", "654321", profile_status="identity_confirmed"
    )
    initiator = create_user_with_pin(db_session, "发起人乙", "111111")
    space = seed_space_with_owner(db_session, initiator.id, name="确档空间")
    create_space_member(db_session, space.id, target.id)
    db_session.commit()
    headers = auth_header(login(client, "发起人乙", "111111").json())

    created = _create_member(client, headers, name="已确档目标", space_id=space.id)
    assert created.status_code == 200, created.text
    binding_id = created.json()["binding_id"]

    target_headers = auth_header(login(client, "已确档目标", "654321").json())
    confirmed = client.post(
        f"/api/bindings/{binding_id}/confirm", headers=target_headers, json={"pin": "654321"}
    )
    assert confirmed.status_code == 200, confirmed.text

    db_session.expire_all()
    audit = db_session.query(AuditLog).filter(AuditLog.action == "account_binding_confirmed").one()
    assert '"identity_confirmed": false' in audit.detail_json  # 条件跳过，未重复转换
    assert db_session.get(User, target.id).profile_status == "identity_confirmed"


def test_binding_status_check_constraint(db_session) -> None:
    """DB CHECK 兜底：未知状态拒绝写入（枚举 + Pydantic 双重校验的 DB 侧）。"""
    binding = AccountBinding(
        initiator_id=None,
        target_id=None,
        person_id=None,
        status="bogus",
        created_at=timeutil.utcnow(),
    )
    db_session.add(binding)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_list_bindings_empty_for_new_user(db_session, client) -> None:
    create_user_with_pin(db_session, "无绑定用户", "123456")
    db_session.commit()
    headers = auth_header(login(client, "无绑定用户", "123456").json())
    response = client.get("/api/bindings", headers=headers)
    assert response.status_code == 200
    assert response.json() == []
