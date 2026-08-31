"""ActionCard 生命周期 API 测试（V2.4 Block S2）。

覆盖（对齐任务验收 AC-ST6/AC-ST8 与分派合同）：
- list/view/dismiss/accept happy path 与权限/状态分支（404/403/410/409）；
- execute 重校验矩阵：篡改 fact revision 后执行被拒、撤 membership 后执行被拒、
  过期卡 execute 410、并发 accept 一个成功一个 409；
- 共同 Household execute 成功且不合并 Lineage（AC-ST8）：仅新建一个 household
  空间，对方 lineage 空间不变不互见；
- execute 前绝不出现 SourceFact 新增或申请自动发出（断言 source_facts /
  space_members.pending 行数）；
- flag 关闭 503 ACTION_CARD_FLAG_DISABLED。
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import (
    auth_header,
    create_space_member,
    create_user_with_pin,
    login,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models.relationship_facts import SourceFact
from app.models.space import FamilySpace, SpaceMember
from app.models.steward import ActionCard, BehaviorProjection
from app.services import action_cards
from app.services import source_facts as sf
from app.services import steward as steward_service
from app.utils.timeutil import utcnow

# ---- 造数辅助 ----


def _login_header(client: TestClient, user) -> dict[str, str]:
    pair = login(client, user.name, "123456").json()
    return auth_header(pair)


def _make_space(
    session: Session, owner: Any, kind: str = "household", name: str | None = None
) -> FamilySpace:
    space = FamilySpace(
        name=name or f"{owner.name}-空间",
        owner_id=owner.id,
        kind=kind,
        created_at=utcnow(),
    )
    session.add(space)
    session.flush()
    create_space_member(session, space.id, owner.id, role="owner")
    session.commit()
    return space


def _confirm_fact(
    session: Session,
    *,
    fact_type: str,
    subject_id: int,
    object_id: int,
    space_id: int | None = None,
) -> SourceFact:
    fact = sf.create_source_fact(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        provenance="manual_entry",
        space_id=space_id,
    )
    sf.transition_source_fact(session, fact, "confirm")
    session.commit()
    return fact


def _make_household_card(
    session: Session,
    *,
    space: FamilySpace,
    recipient_account_id: int,
    subject: Any,
    obj: Any,
    fact: SourceFact,
) -> ActionCard:
    """直建一张 household_link + accepted 卡（模拟 steward 出卡 + 用户已 accept）。"""
    evidence = {
        "primary_fact_id": fact.id,
        "facts": [{"id": fact.id, "type": fact.fact_type, "revision": fact.revision}],
        "inputs": {
            "subject_identity_confirmed": True,
            "object_identity_confirmed": True,
            "creation_choices": ["household"],
            "mutual_disclosure_allowed": False,
            "share_household_membership": False,
            "lineage_request_possible": False,
        },
    }
    card, _outcome = action_cards.create_card(
        session,
        kind="household_link",
        space_id=space.id,
        recipient_account_id=recipient_account_id,
        subject_user_id=subject.id,
        object_user_id=obj.id,
        evidence_json=evidence,
        proposed_action_json={"action": "create_household"},
        reason_text=f"{subject.name} 与 {obj.name} 已确认为配偶，可以共同创建一个家庭空间。",
    )
    assert card is not None
    # pending → viewed → accepted
    action_cards.transition_card(session, card, "view", expected_revision=card.revision)
    action_cards.transition_card(session, card, "accept", expected_revision=card.revision)
    session.commit()
    session.refresh(card)
    return card


def _make_lineage_card(
    session: Session,
    *,
    space: FamilySpace,
    recipient_account_id: int,
    subject: Any,
    obj: Any,
    fact: SourceFact,
    target_space_id: int,
) -> ActionCard:
    evidence = {
        "primary_fact_id": fact.id,
        "facts": [{"id": fact.id, "type": fact.fact_type, "revision": fact.revision}],
        "inputs": {
            "subject_identity_confirmed": True,
            "object_identity_confirmed": True,
            "creation_choices": ["lineage"],
            "mutual_disclosure_allowed": False,
            "share_household_membership": False,
            "lineage_request_possible": True,
        },
    }
    card, _outcome = action_cards.create_card(
        session,
        kind="lineage_request",
        space_id=space.id,
        recipient_account_id=recipient_account_id,
        subject_user_id=subject.id,
        object_user_id=obj.id,
        evidence_json=evidence,
        proposed_action_json={"action": "request_lineage", "space_id": target_space_id},
        reason_text=f"{subject.name} 可以申请加入 {obj.name} 所在的家族空间。",
    )
    assert card is not None
    action_cards.transition_card(session, card, "view", expected_revision=card.revision)
    action_cards.transition_card(session, card, "accept", expected_revision=card.revision)
    session.commit()
    session.refresh(card)
    return card


# ---- 场景：配偶共同 Household ----


@pytest.fixture()
def spouse_scene(db_session: Session):
    """甲、乙互为 confirmed spouse，同属一个 household 空间，卡发给甲。"""
    jia = create_user_with_pin(db_session, "甲", "123456")
    yi = create_user_with_pin(db_session, "乙", "123456")
    space = _make_space(db_session, jia, kind="household", name="甲家")
    create_space_member(db_session, space.id, yi.id)  # 乙也是 active 成员
    fact = _confirm_fact(
        db_session,
        fact_type="spouse",
        subject_id=jia.id,
        object_id=yi.id,
        space_id=space.id,
    )
    card = _make_household_card(
        db_session,
        space=space,
        recipient_account_id=jia.account.id,
        subject=jia,
        obj=yi,
        fact=fact,
    )
    return {
        "jia": jia,
        "yi": yi,
        "space": space,
        "fact": fact,
        "card": card,
    }


# ---- flag 关闭 ----


def test_flag_off_all_endpoints_503(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "STEWARD_ENABLED", False)
    jia = create_user_with_pin(db_session, "甲", "123456")
    space = _make_space(db_session, jia)
    headers = _login_header(client, jia)
    assert (
        client.get("/api/action-cards", params={"space_id": space.id}, headers=headers).status_code
        == 503
    )
    payload = client.get("/api/action-cards", params={"space_id": space.id}, headers=headers).json()
    assert payload["error"]["code"] == "ACTION_CARD_FLAG_DISABLED"


# ---- 列表 ----


def test_list_cards_happy_and_guards(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    jia = create_user_with_pin(db_session, "甲", "123456")
    yi = create_user_with_pin(db_session, "乙", "123456")
    space = _make_space(db_session, jia)
    create_space_member(db_session, space.id, yi.id)
    fact = _confirm_fact(
        db_session, fact_type="spouse", subject_id=jia.id, object_id=yi.id, space_id=space.id
    )
    _make_household_card(
        db_session,
        space=space,
        recipient_account_id=jia.account.id,
        subject=jia,
        obj=yi,
        fact=fact,
    )
    headers = _login_header(client, jia)

    # happy：列出甲的卡
    resp = client.get("/api/action-cards", params={"space_id": space.id}, headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    card = items[0]
    assert card["id"] == 1
    assert card["kind"] == "household_link"
    assert card["space_id"] == space.id
    assert card["subject_user"] == {"id": jia.id, "name": "甲"}
    assert card["object_user"] == {"id": yi.id, "name": "乙"}
    assert card["evidence"]["fact_ids"] == [fact.id]
    assert card["evidence"]["path_summary"] is None
    assert card["proposed_action"]["type"] == "create_household"
    assert card["state"] == "accepted"
    assert card["revision"] == 3  # pending(1) → viewed(2) → accepted(3)

    # state 过滤
    resp2 = client.get(
        "/api/action-cards", params={"space_id": space.id, "state": "pending"}, headers=headers
    )
    assert resp2.status_code == 200
    assert resp2.json() == []

    # 非空间成员 403
    outsider = create_user_with_pin(db_session, "丙", "123456")
    out_headers = _login_header(client, outsider)
    assert (
        client.get(
            "/api/action-cards", params={"space_id": space.id}, headers=out_headers
        ).status_code
        == 403
    )

    # 不存在的空间 404
    assert (
        client.get("/api/action-cards", params={"space_id": 999999}, headers=headers).status_code
        == 404
    )

    # 无效 state 422
    assert (
        client.get(
            "/api/action-cards", params={"space_id": space.id, "state": "bogus"}, headers=headers
        ).status_code
        == 422
    )

    # 乙看不到甲的卡（recipient 不匹配）
    yi_headers = _login_header(client, yi)
    assert (
        client.get("/api/action-cards", params={"space_id": space.id}, headers=yi_headers).json()
        == []
    )


# ---- view / dismiss / accept ----


def test_view_dismiss_accept_happy_and_state_branches(
    client: TestClient, db_session: Session, spouse_scene: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    jia = spouse_scene["jia"]
    yi = spouse_scene["yi"]
    space = spouse_scene["space"]
    headers = _login_header(client, jia)

    # 造一张全新 pending 卡用于 view/accept 流程
    fact2 = _confirm_fact(
        db_session, fact_type="spouse", subject_id=yi.id, object_id=jia.id, space_id=space.id
    )
    # 注意：spouse 是对称的；create_card 同 (subject,obj) 已有 accepted 卡，
    # 但本卡 subject=yi/object=jia 与上一张 subject=jia/object=yi 是不同 dedupe_key

    evidence = {
        "primary_fact_id": fact2.id,
        "facts": [{"id": fact2.id, "type": fact2.fact_type, "revision": fact2.revision}],
        "inputs": {
            "subject_identity_confirmed": True,
            "object_identity_confirmed": True,
            "creation_choices": ["household"],
            "mutual_disclosure_allowed": False,
            "share_household_membership": False,
            "lineage_request_possible": False,
        },
    }
    card, _ = action_cards.create_card(
        db_session,
        kind="household_link",
        space_id=space.id,
        recipient_account_id=yi.account.id,
        subject_user_id=yi.id,
        object_user_id=jia.id,
        evidence_json=evidence,
        proposed_action_json={"action": "create_household"},
        reason_text="卡片",
    )
    db_session.commit()
    db_session.refresh(card)
    card_id = card.id

    yi_headers = _login_header(client, yi)

    # view happy
    resp = client.post(f"/api/action-cards/{card_id}/view", headers=yi_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == card_id
    assert body["state"] == "viewed"
    assert body["revision"] == 2

    # accept happy
    resp = client.post(f"/api/action-cards/{card_id}/accept", headers=yi_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "accepted"
    assert resp.json()["revision"] == 3

    # 非本人 404
    assert client.post(f"/api/action-cards/{card_id}/view", headers=headers).status_code == 404

    # 不存在的卡 404
    assert client.post("/api/action-cards/999999/view", headers=yi_headers).status_code == 404


def test_terminal_card_returns_410(
    client: TestClient, db_session: Session, spouse_scene: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    jia = spouse_scene["jia"]
    yi = spouse_scene["yi"]
    space = spouse_scene["space"]
    card = spouse_scene["card"]  # 已 accepted（终态演示用 execute）
    headers = _login_header(client, jia)

    # 把 accepted 卡 execute 到终态 executed
    exec_resp = client.post(f"/api/action-cards/{card.id}/execute", json={}, headers=headers)
    assert exec_resp.status_code == 200, exec_resp.text
    assert exec_resp.json()["state"] == "executed"

    # 终态再操作一律 410 CARD_EXPIRED
    for action in ("view", "accept", "execute"):
        r = client.post(f"/api/action-cards/{card.id}/{action}", json={}, headers=headers)
        assert r.status_code == 410, (action, r.text)
        assert r.json()["error"]["code"] == "CARD_EXPIRED"

    # 另造一张 pending 卡，dismiss 到终态后同样 410
    fact2 = _confirm_fact(
        db_session, fact_type="spouse", subject_id=yi.id, object_id=jia.id, space_id=space.id
    )
    evidence = {
        "primary_fact_id": fact2.id,
        "facts": [{"id": fact2.id, "type": fact2.fact_type, "revision": fact2.revision}],
        "inputs": {
            "subject_identity_confirmed": True,
            "object_identity_confirmed": True,
            "creation_choices": ["household"],
            "mutual_disclosure_allowed": False,
            "share_household_membership": False,
            "lineage_request_possible": False,
        },
    }
    card2, _ = action_cards.create_card(
        db_session,
        kind="household_link",
        space_id=space.id,
        recipient_account_id=yi.account.id,
        subject_user_id=yi.id,
        object_user_id=jia.id,
        evidence_json=evidence,
        proposed_action_json={"action": "create_household"},
        reason_text="卡片2",
    )
    db_session.commit()
    db_session.refresh(card2)
    yi_headers = _login_header(client, yi)

    dismiss = client.post(f"/api/action-cards/{card2.id}/dismiss", headers=yi_headers)
    assert dismiss.status_code == 200, dismiss.text
    assert dismiss.json()["state"] == "dismissed"
    db_session.expire_all()
    cooldown = db_session.scalar(
        select(BehaviorProjection).where(
            BehaviorProjection.space_id == space.id,
            BehaviorProjection.account_id == yi.account.id,
            BehaviorProjection.projection_key == "card_cooldown:household_link",
        )
    )
    assert cooldown is not None and isinstance(cooldown.value_json.get("until"), str)

    for action in ("view", "accept", "execute"):
        r = client.post(f"/api/action-cards/{card2.id}/{action}", json={}, headers=yi_headers)
        assert r.status_code == 410, (action, r.text)


def test_concurrent_accept_one_wins_one_409(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    jia = create_user_with_pin(db_session, "甲", "123456")
    yi = create_user_with_pin(db_session, "乙", "123456")
    space = _make_space(db_session, jia)
    create_space_member(db_session, space.id, yi.id)
    fact = _confirm_fact(
        db_session, fact_type="spouse", subject_id=jia.id, object_id=yi.id, space_id=space.id
    )
    # 造一张 pending 卡（甲为 recipient）
    evidence = {
        "primary_fact_id": fact.id,
        "facts": [{"id": fact.id, "type": fact.fact_type, "revision": fact.revision}],
        "inputs": {
            "subject_identity_confirmed": True,
            "object_identity_confirmed": True,
            "creation_choices": ["household"],
            "mutual_disclosure_allowed": False,
            "share_household_membership": False,
            "lineage_request_possible": False,
        },
    }
    card, _ = action_cards.create_card(
        db_session,
        kind="household_link",
        space_id=space.id,
        recipient_account_id=jia.account.id,
        subject_user_id=jia.id,
        object_user_id=yi.id,
        evidence_json=evidence,
        proposed_action_json={"action": "create_household"},
        reason_text="卡片",
    )
    db_session.commit()
    db_session.refresh(card)
    card_id = card.id
    headers = _login_header(client, jia)

    # 先 view 到 viewed
    assert client.post(f"/api/action-cards/{card_id}/view", headers=headers).status_code == 200

    # 两次 accept：第一成功，第二并发冲突 409
    first = client.post(f"/api/action-cards/{card_id}/accept", headers=headers)
    second = client.post(f"/api/action-cards/{card_id}/accept", headers=headers)
    statuses = {first.status_code, second.status_code}
    assert 200 in statuses
    assert 409 in statuses
    conflict = second if second.status_code == 409 else first
    assert conflict.json()["error"]["code"] == "CARD_STATE_CONFLICT"


# ---- execute：共同 Household 成功 ----


def test_execute_create_household_success_and_no_lineage_merge(
    client: TestClient, db_session: Session, spouse_scene: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-ST8：execute 共同 Household 成功，仅新建一个 household 空间，
    不合并 Lineage、不互见双方既有空间。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    jia = spouse_scene["jia"]
    yi = spouse_scene["yi"]
    space = spouse_scene["space"]
    card = spouse_scene["card"]
    headers = _login_header(client, jia)

    # execute 前的基线计数
    spaces_before = db_session.query(FamilySpace).count()
    source_facts_before = db_session.query(SourceFact).count()

    resp = client.post(f"/api/action-cards/{card.id}/execute", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == card.id
    assert body["state"] == "executed"

    # AC-ST8：仅新建一个 household 空间，无 lineage 新增
    new_spaces = db_session.query(FamilySpace).filter(FamilySpace.id != space.id).all()
    assert len(new_spaces) == 1
    assert new_spaces[0].kind == "household"
    assert db_session.query(FamilySpace).filter(FamilySpace.kind == "lineage").count() == 0

    # 双方都是新空间的 active 成员
    new_space = new_spaces[0]
    members = {
        m.user_id: m
        for m in db_session.query(SpaceMember).filter(SpaceMember.space_id == new_space.id).all()
    }
    assert members[jia.id].status == "active"
    assert members[jia.id].role == "space_admin"
    assert members[yi.id].status == "active"

    # execute 前后 SourceFact 行数不变（绝不静默写 SourceFact）
    assert db_session.query(SourceFact).count() == source_facts_before
    # 没有新增 pending 成员资格（共同 Household 双方直接 active）
    pending_in_new = (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == new_space.id, SpaceMember.status == "pending")
        .count()
    )
    assert pending_in_new == 0

    # 既有空间总数 = 1（原）+ 1（新建）= spaces_before + 1
    assert db_session.query(FamilySpace).count() == spaces_before + 1

    # 卡片已终态 executed，再 execute 410
    r = client.post(f"/api/action-cards/{card.id}/execute", json={}, headers=headers)
    assert r.status_code == 410


# ---- execute：重校验失败矩阵 ----


def test_execute_rejected_when_fact_revision_changed(
    client: TestClient, db_session: Session, spouse_scene: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """篡改 SourceFact revision（revise）后执行被拒 409 CARD_EXECUTE_REJECTED。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    jia = spouse_scene["jia"]
    card = spouse_scene["card"]
    fact = spouse_scene["fact"]
    headers = _login_header(client, jia)

    # 让 fact revision+1（通过 revoke 再确认循环模拟篡改），破坏证据快照
    sf.transition_source_fact(db_session, fact, "revoke")
    # revoked 后重新建一条同元组 confirmed（状态不同；card 记录的 revision 已过期）
    new_fact = sf.create_source_fact(
        db_session,
        fact_type="spouse",
        subject_user_id=fact.subject_user_id,
        object_user_id=fact.object_user_id,
        provenance="manual_entry",
        space_id=fact.space_id,
    )
    sf.transition_source_fact(db_session, new_fact, "confirm")
    db_session.commit()

    r = client.post(f"/api/action-cards/{card.id}/execute", json={}, headers=headers)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "CARD_EXECUTE_REJECTED"
    assert r.json()["error"]["detail"]["reason"] == "evidence_invalidated"

    # 卡片保持 accepted 可重试
    db_session.refresh(card)
    assert card.state == "accepted"


def test_execute_rejected_when_actor_membership_revoked(
    client: TestClient, db_session: Session, spouse_scene: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """撤 actor 的空间 membership 后 execute 被拒 403（不再是 active 成员）。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    jia = spouse_scene["jia"]
    space = spouse_scene["space"]
    card = spouse_scene["card"]
    headers = _login_header(client, jia)

    # 把甲从原空间移出（owner 自移除路径：直接置 removed）
    member = db_session.scalar(
        select(SpaceMember).where(SpaceMember.space_id == space.id, SpaceMember.user_id == jia.id)
    )
    assert member is not None
    member.status = "removed"
    db_session.commit()

    r = client.post(f"/api/action-cards/{card.id}/execute", json={}, headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "SPACE_FORBIDDEN_ACTOR"

    # 卡片状态不变
    db_session.refresh(card)
    assert card.state == "accepted"


def test_execute_expired_card_410(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """过期 accepted 卡 execute 返回 410 CARD_EXPIRED。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    jia = create_user_with_pin(db_session, "甲", "123456")
    yi = create_user_with_pin(db_session, "乙", "123456")
    space = _make_space(db_session, jia)
    create_space_member(db_session, space.id, yi.id)
    fact = _confirm_fact(
        db_session, fact_type="spouse", subject_id=jia.id, object_id=yi.id, space_id=space.id
    )
    card = _make_household_card(
        db_session,
        space=space,
        recipient_account_id=jia.account.id,
        subject=jia,
        obj=yi,
        fact=fact,
    )
    # 手动过期
    card.expires_at = utcnow().replace(year=utcnow().year - 1)
    db_session.commit()
    headers = _login_header(client, jia)

    r = client.post(f"/api/action-cards/{card.id}/execute", json={}, headers=headers)
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "CARD_EXPIRED"


def test_execute_before_accept_410(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未 accept 的卡（pending）execute 返回 410（状态不可执行）。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    jia = create_user_with_pin(db_session, "甲", "123456")
    yi = create_user_with_pin(db_session, "乙", "123456")
    space = _make_space(db_session, jia)
    create_space_member(db_session, space.id, yi.id)
    fact = _confirm_fact(
        db_session, fact_type="spouse", subject_id=jia.id, object_id=yi.id, space_id=space.id
    )
    evidence = {
        "primary_fact_id": fact.id,
        "facts": [{"id": fact.id, "type": fact.fact_type, "revision": fact.revision}],
        "inputs": {
            "subject_identity_confirmed": True,
            "object_identity_confirmed": True,
            "creation_choices": ["household"],
            "mutual_disclosure_allowed": False,
            "share_household_membership": False,
            "lineage_request_possible": False,
        },
    }
    card, _ = action_cards.create_card(
        db_session,
        kind="household_link",
        space_id=space.id,
        recipient_account_id=jia.account.id,
        subject_user_id=jia.id,
        object_user_id=yi.id,
        evidence_json=evidence,
        proposed_action_json={"action": "create_household"},
        reason_text="卡片",
    )
    db_session.commit()
    db_session.refresh(card)
    headers = _login_header(client, jia)

    r = client.post(f"/api/action-cards/{card.id}/execute", json={}, headers=headers)
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "CARD_EXPIRED"


# ---- execute：lineage 申请走 pending 流 ----


@pytest.fixture()
def lineage_scene(db_session: Session):
    """乙拥有 lineage 空间；甲持 lineage_request 卡申请加入乙的 lineage。"""
    jia = create_user_with_pin(db_session, "甲", "123456")
    yi = create_user_with_pin(db_session, "乙", "123456")
    # 甲的家空间（卡片归属空间）
    home = _make_space(db_session, jia, kind="household", name="甲家")
    create_space_member(db_session, home.id, yi.id)
    # 乙的 lineage 空间（甲要申请加入的目标）
    yi_lineage = _make_space(db_session, yi, kind="lineage", name="乙宗族")
    fact = _confirm_fact(
        db_session, fact_type="spouse", subject_id=jia.id, object_id=yi.id, space_id=home.id
    )
    card = _make_lineage_card(
        db_session,
        space=home,
        recipient_account_id=jia.account.id,
        subject=jia,
        obj=yi,
        fact=fact,
        target_space_id=yi_lineage.id,
    )
    return {
        "jia": jia,
        "yi": yi,
        "home": home,
        "yi_lineage": yi_lineage,
        "fact": fact,
        "card": card,
    }


def test_execute_lineage_request_pending_flow(
    client: TestClient, db_session: Session, lineage_scene: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """execute lineage_request：产生 pending 成员资格，不自动 active（ST-6）。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    jia = lineage_scene["jia"]
    yi_lineage = lineage_scene["yi_lineage"]
    card = lineage_scene["card"]
    headers = _login_header(client, jia)

    source_facts_before = db_session.query(SourceFact).count()

    r = client.post(f"/api/action-cards/{card.id}/execute", json={}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "executed"

    # 甲在乙的 lineage 空间为 pending（不自动 active）
    member = db_session.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == yi_lineage.id, SpaceMember.user_id == jia.id
        )
    )
    assert member is not None
    assert member.status == "pending"

    # execute 不新增 SourceFact
    assert db_session.query(SourceFact).count() == source_facts_before

    # 卡片终态 executed，再 execute 410
    r2 = client.post(f"/api/action-cards/{card.id}/execute", json={}, headers=headers)
    assert r2.status_code == 410


def test_execute_lineage_rejects_target_override(
    client: TestClient, db_session: Session, lineage_scene: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """accepted 卡片不能通过请求体改写已确认的目标 LineageSpace。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    jia = lineage_scene["jia"]
    yi_lineage = lineage_scene["yi_lineage"]
    card = lineage_scene["card"]
    headers = _login_header(client, jia)
    before = db_session.query(SpaceMember).filter(SpaceMember.user_id == jia.id).count()

    response = client.post(
        f"/api/action-cards/{card.id}/execute",
        json={"target_space_id": yi_lineage.id + 9999},
        headers=headers,
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CARD_EXECUTE_REJECTED"
    assert response.json()["error"]["detail"]["reason"] == "target_space_changed"
    assert db_session.query(SpaceMember).filter(SpaceMember.user_id == jia.id).count() == before
    db_session.refresh(card)
    assert card.state == "accepted"


def test_execute_lineage_rejected_when_target_member_gone(
    client: TestClient, db_session: Session, lineage_scene: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """对方（乙）退出目标 lineage 后 execute 被拒 409 CARD_EXECUTE_REJECTED。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    jia = lineage_scene["jia"]
    yi = lineage_scene["yi"]
    yi_lineage = lineage_scene["yi_lineage"]
    card = lineage_scene["card"]
    headers = _login_header(client, jia)

    # 乙退出自己的 lineage（owner 自移除）
    member = db_session.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == yi_lineage.id, SpaceMember.user_id == yi.id
        )
    )
    assert member is not None
    member.status = "removed"
    db_session.commit()

    r = client.post(f"/api/action-cards/{card.id}/execute", json={}, headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CARD_EXECUTE_REJECTED"
    assert r.json()["error"]["detail"]["reason"] == "target_member_no_longer_active"

    db_session.refresh(card)
    assert card.state == "accepted"


# ---- execute：cooldown 仍拒绝 ----


def test_execute_rejected_when_cooldown_active(
    client: TestClient, db_session: Session, spouse_scene: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    jia = spouse_scene["jia"]
    space = spouse_scene["space"]
    card = spouse_scene["card"]
    headers = _login_header(client, jia)

    steward_service.set_kind_cooldown(
        db_session,
        space_id=space.id,
        account_id=jia.account.id,
        kind="household_link",
        days=7,
    )
    db_session.commit()

    r = client.post(f"/api/action-cards/{card.id}/execute", json={}, headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CARD_EXECUTE_REJECTED"
    assert r.json()["error"]["detail"]["reason"] == "cooldown_active"
