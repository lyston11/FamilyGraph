"""空间管理员审批流程测试（任务 08-30-space-manager-approval）。

覆盖：已有空间 space_admin 申请、资格门禁、重复 pending、平台队列与裁决、
审计/领域事件、owner 不变、裁决回滚、自由建空间，以及提交/裁决并发竞态。
"""

from __future__ import annotations

from threading import Barrier, Thread

import pytest
from conftest import (
    auth_header,
    create_space_member,
    create_user_with_pin,
    login,
    seed_space_with_owner,
)
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.commands import manager_applications as manager_application_commands
from app.commands.context import ActorContext
from app.db import SessionLocal
from app.models.account import Account
from app.models.audit_log import AuditLog
from app.models.space import FamilySpace, SpaceManagerApplication, SpaceMember
from app.models.user import User
from app.models.v2_foundation import DomainEvent

APPLICATIONS_URL = "/api/spaces/manager-applications"
ADMIN_APPLICATIONS_URL = "/api/admin/manager-applications"


def _login_header(client: TestClient, name: str, pin: str) -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


@pytest.fixture()
def operator(db_session):
    return create_user_with_pin(db_session, "平台运营", "101010", is_admin=True)


@pytest.fixture()
def applicant(db_session):
    return create_user_with_pin(db_session, "申请人", "202020")


def _submit(client, headers, *, space_id=None, request_kind="space_admin", **extra):
    body: dict[str, object] = {"request_kind": request_kind, **extra}
    if space_id is not None:
        body["space_id"] = space_id
    return client.post(APPLICATIONS_URL, json=body, headers=headers)


def _decide(client, headers, application_id, *, decision, note=None):
    body: dict[str, object] = {"decision": decision}
    if note is not None:
        body["note"] = note
    return client.post(
        f"{ADMIN_APPLICATIONS_URL}/{application_id}/decision", json=body, headers=headers
    )


def _ctx_for(session, user_id: int) -> ActorContext:
    user = session.get(User, user_id)
    account = session.scalar(select(Account).where(Account.user_id == user_id))
    assert user is not None and account is not None
    return ActorContext.from_identity(user, account)


# ---- 用户侧：提交与自查 ----


def test_submit_space_admin_and_query_mine(db_session, client, applicant) -> None:
    owner = create_user_with_pin(db_session, "空间主", "303030")
    space = seed_space_with_owner(db_session, owner.id, name="已有空间")
    create_space_member(db_session, space.id, applicant.id)

    headers = _login_header(client, "申请人", "202020")
    submitted = _submit(client, headers, space_id=space.id)
    assert submitted.status_code == 201, submitted.text
    row = submitted.json()
    assert row["status"] == "pending"
    assert row["request_kind"] == "space_admin"
    assert row["space_id"] == space.id
    assert row["space_name"] == "已有空间"
    assert row["applicant_name"] == "申请人"

    mine = client.get(f"{APPLICATIONS_URL}/mine", headers=headers)
    assert mine.status_code == 200, mine.text
    assert len(mine.json()) == 1
    assert mine.json()[0]["id"] == row["id"]


def test_application_payload_only_accepts_space_admin(db_session, client, applicant) -> None:
    headers = _login_header(client, "申请人", "202020")
    unknown_kind = _submit(client, headers, request_kind="invalid", space_id=1)
    assert unknown_kind.status_code == 422

    owner = create_user_with_pin(db_session, "空间主甲", "303031")
    space = seed_space_with_owner(db_session, owner.id, name="甲空间")
    create_space_member(db_session, space.id, applicant.id)
    extra = _submit(client, headers, space_id=space.id, proposed_name="不应接受")
    assert extra.status_code == 422


def test_duplicate_pending_rejected_but_resubmit_after_reject(
    db_session, client, applicant, operator
) -> None:
    owner = create_user_with_pin(db_session, "空间主乙", "303032")
    space = seed_space_with_owner(db_session, owner.id, name="乙空间")
    create_space_member(db_session, space.id, applicant.id)
    operator_tokens = _login_header(client, "平台运营", "101010")
    headers = _login_header(client, "申请人", "202020")

    first = _submit(client, headers, space_id=space.id)
    assert first.status_code == 201, first.text
    duplicate = _submit(client, headers, space_id=space.id)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "SPACE_MANAGER_APPLICATION_EXISTS"

    rejected = _decide(
        client,
        operator_tokens,
        first.json()["id"],
        decision="reject",
        note="请补充空间治理说明",
    )
    assert rejected.status_code == 200, rejected.text
    resubmit = _submit(client, headers, space_id=space.id)
    assert resubmit.status_code == 201, resubmit.text
    assert resubmit.json()["status"] == "pending"


def test_unconfirmed_and_guest_rejected(db_session, client) -> None:
    unconfirmed = create_user_with_pin(
        db_session, "未确档者", "404040", profile_status="provisional"
    )
    owner = create_user_with_pin(db_session, "空间主丙", "505050")
    space = seed_space_with_owner(db_session, owner.id, name="丙空间")
    create_space_member(db_session, space.id, unconfirmed.id)
    r = _submit(client, _login_header(client, "未确档者", "404040"), space_id=space.id)
    assert r.status_code == 403
    assert "身份确认" in r.json()["error"]["message"]

    guest = create_user_with_pin(db_session, "访客", "606060")
    create_space_member(db_session, space.id, guest.id, role="guest", status="active")
    rg = _submit(client, _login_header(client, "访客", "606060"), space_id=space.id)
    assert rg.status_code == 403
    assert "访客" in rg.json()["error"]["message"]


def test_space_admin_target_eligibility(db_session, client, applicant) -> None:
    headers = _login_header(client, "申请人", "202020")

    missing = _submit(client, headers, space_id=99999)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SPACE_NOT_FOUND"

    no_target = _submit(client, headers)
    assert no_target.status_code == 422

    owner = create_user_with_pin(db_session, "空间主丁", "707070")
    space = seed_space_with_owner(db_session, owner.id, name="丁空间")
    not_member = _submit(client, headers, space_id=space.id)
    assert not_member.status_code == 404
    create_space_member(db_session, space.id, applicant.id, status="pending")
    pending_member = _submit(client, headers, space_id=space.id)
    assert pending_member.status_code == 404

    owner_headers = _login_header(client, "空间主丁", "707070")
    r_owner = _submit(client, owner_headers, space_id=space.id)
    assert r_owner.status_code == 409
    assert "所有者或管理员" in r_owner.json()["error"]["message"]

    admin = create_user_with_pin(db_session, "现有管理", "808080")
    create_space_member(db_session, space.id, admin.id, role="space_admin", status="active")
    r_admin = _submit(client, _login_header(client, "现有管理", "808080"), space_id=space.id)
    assert r_admin.status_code == 409

    member = create_user_with_pin(db_session, "普通成员", "909090")
    create_space_member(db_session, space.id, member.id)
    r_member = _submit(client, _login_header(client, "普通成员", "909090"), space_id=space.id)
    assert r_member.status_code == 201, r_member.text


# ---- 平台侧：队列与裁决 ----


def test_admin_endpoints_require_platform_operator(db_session, client, applicant) -> None:
    headers = _login_header(client, "申请人", "202020")
    assert client.get(ADMIN_APPLICATIONS_URL, headers=headers).status_code == 403
    decide = client.post(
        f"{ADMIN_APPLICATIONS_URL}/1/decision",
        json={"decision": "approve"},
        headers=headers,
    )
    assert decide.status_code == 403


def test_admin_queue_lists_and_filters_by_status(db_session, client, applicant, operator) -> None:
    owner = create_user_with_pin(db_session, "队列空间主", "313131")
    first_space = seed_space_with_owner(db_session, owner.id, name="队列空间一")
    second_space = seed_space_with_owner(db_session, owner.id, name="队列空间二")
    create_space_member(db_session, first_space.id, applicant.id)
    create_space_member(db_session, second_space.id, applicant.id)
    headers = _login_header(client, "申请人", "202020")
    first = _submit(client, headers, space_id=first_space.id)
    second = _submit(client, headers, space_id=second_space.id)
    assert first.status_code == 201 and second.status_code == 201

    op_headers = _login_header(client, "平台运营", "101010")
    approved = _decide(client, op_headers, first.json()["id"], decision="approve")
    rejected = _decide(
        client, op_headers, second.json()["id"], decision="reject", note="暂缓治理升级"
    )
    assert approved.status_code == 200 and rejected.status_code == 200

    pending = client.get(ADMIN_APPLICATIONS_URL, params={"status": "pending"}, headers=op_headers)
    assert pending.status_code == 200 and pending.json() == []
    all_rows = client.get(ADMIN_APPLICATIONS_URL, headers=op_headers)
    assert all_rows.status_code == 200
    assert {row["status"] for row in all_rows.json()} == {"approved", "rejected"}
    assert {row["request_kind"] for row in all_rows.json()} == {"space_admin"}


def test_approve_space_admin_upgrades_role_without_touching_owner(
    db_session, client, applicant, operator
) -> None:
    owner = create_user_with_pin(db_session, "家族主人", "111213")
    space = seed_space_with_owner(db_session, owner.id, name="大家族")
    create_space_member(db_session, space.id, applicant.id)

    headers = _login_header(client, "申请人", "202020")
    submitted = _submit(client, headers, space_id=space.id)
    assert submitted.status_code == 201
    op_headers = _login_header(client, "平台运营", "101010")
    decided = _decide(client, op_headers, submitted.json()["id"], decision="approve")
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved"

    db_session.expire_all()
    upgraded = db_session.scalars(
        select(SpaceMember).where(
            SpaceMember.space_id == space.id, SpaceMember.user_id == applicant.id
        )
    ).one()
    assert upgraded.role == "space_admin" and upgraded.status == "active"
    fresh_space = db_session.get(FamilySpace, space.id)
    assert fresh_space is not None and fresh_space.owner_id == owner.id
    owner_membership = db_session.scalars(
        select(SpaceMember).where(SpaceMember.space_id == space.id, SpaceMember.user_id == owner.id)
    ).one()
    assert owner_membership.role == "owner"

    actions = set(
        db_session.scalars(
            select(AuditLog.action).where(AuditLog.target_id == submitted.json()["id"])
        ).all()
    )
    assert {"manager_application_submitted", "manager_application_approved"} <= actions
    event_types = set(db_session.scalars(select(DomainEvent.type)).all())
    assert "space.manager_application.decided" in event_types

    again = _decide(client, op_headers, submitted.json()["id"], decision="reject", note="改判")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "SPACE_MANAGER_APPLICATION_DECIDED"


def test_reject_requires_note_and_records_it(db_session, client, applicant, operator) -> None:
    owner = create_user_with_pin(db_session, "驳回空间主", "121212")
    space = seed_space_with_owner(db_session, owner.id, name="驳回空间")
    create_space_member(db_session, space.id, applicant.id)
    headers = _login_header(client, "申请人", "202020")
    submitted = _submit(client, headers, space_id=space.id)
    assert submitted.status_code == 201
    app_id = submitted.json()["id"]

    op_headers = _login_header(client, "平台运营", "101010")
    no_note = _decide(client, op_headers, app_id, decision="reject")
    assert no_note.status_code == 422
    assert no_note.json()["error"]["code"] == "SPACE_MANAGER_APPLICATION_NOTE_REQUIRED"
    blank_note = _decide(client, op_headers, app_id, decision="reject", note="   ")
    assert blank_note.status_code == 422

    rejected = _decide(client, op_headers, app_id, decision="reject", note="成员资格尚未稳定")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["decision_note"] == "成员资格尚未稳定"
    db_session.expire_all()
    row = db_session.get(SpaceManagerApplication, app_id)
    assert row is not None and row.status == "rejected" and row.decided_by == operator.id
    assert row.decided_at is not None
    member = db_session.scalars(
        select(SpaceMember).where(
            SpaceMember.space_id == space.id, SpaceMember.user_id == applicant.id
        )
    ).one()
    assert member.role == "member"


def test_approve_conflicts_when_member_changes_and_keeps_pending(
    db_session, client, applicant, operator
) -> None:
    owner = create_user_with_pin(db_session, "变动空间主", "171819")
    space = seed_space_with_owner(db_session, owner.id, name="变动空间")
    membership = create_space_member(db_session, space.id, applicant.id)
    submitted = _submit(client, _login_header(client, "申请人", "202020"), space_id=space.id)
    assert submitted.status_code == 201

    membership.role = "space_admin"
    db_session.commit()
    op_headers = _login_header(client, "平台运营", "101010")
    decided = _decide(client, op_headers, submitted.json()["id"], decision="approve")
    assert decided.status_code == 409
    assert "普通成员" in decided.json()["error"]["message"]
    db_session.expire_all()
    row = db_session.get(SpaceManagerApplication, submitted.json()["id"])
    assert row is not None and row.status == "pending"


def test_direct_space_creation_remains_available(db_session, client, applicant) -> None:
    headers = _login_header(client, "申请人", "202020")
    response = client.post("/api/spaces", json={"name": "直建空间"}, headers=headers)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["name"] == "直建空间"
    assert created["owner_id"] == applicant.id
    space = db_session.get(FamilySpace, created["id"])
    assert space is not None and space.owner_id == applicant.id
    membership = db_session.scalars(
        select(SpaceMember).where(
            SpaceMember.space_id == space.id, SpaceMember.user_id == applicant.id
        )
    ).one()
    assert membership.role == "owner" and membership.status == "active"


# ---- 并发回归 ----


def test_concurrent_duplicate_submissions_have_one_winner(db_session, applicant) -> None:
    owner = create_user_with_pin(db_session, "并发空间主", "313233")
    space = seed_space_with_owner(db_session, owner.id, name="并发申请空间")
    create_space_member(db_session, space.id, applicant.id)
    db_session.expire_all()

    results: list[str] = []
    barrier = Barrier(2)

    def worker() -> None:
        session = SessionLocal()
        try:
            ctx = _ctx_for(session, applicant.id)
            barrier.wait()
            manager_application_commands.submit_manager_application(session, ctx, space_id=space.id)
            results.append("won")
        except HTTPException as exc:
            results.append(str(exc.status_code))
        except Exception as exc:  # pragma: no cover - assertion exposes unexpected DB errors
            results.append(f"error:{type(exc).__name__}")
        finally:
            session.close()

    threads = [Thread(target=worker), Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["409", "won"]
    db_session.expire_all()
    assert (
        db_session.query(SpaceManagerApplication)
        .filter_by(applicant_user_id=applicant.id, space_id=space.id, status="pending")
        .count()
        == 1
    )


def test_concurrent_decisions_have_one_winner(db_session, applicant, operator) -> None:
    owner = create_user_with_pin(db_session, "并发裁决空间主", "414243")
    space = seed_space_with_owner(db_session, owner.id, name="并发裁决空间")
    create_space_member(db_session, space.id, applicant.id)
    application = manager_application_commands.submit_manager_application(
        db_session, _ctx_for(db_session, applicant.id), space_id=space.id
    )
    db_session.expire_all()

    results: list[str] = []
    barrier = Barrier(2)

    def worker(decision: str) -> None:
        session = SessionLocal()
        try:
            ctx = _ctx_for(session, operator.id)
            barrier.wait()
            manager_application_commands.decide_manager_application(
                session,
                ctx,
                application.id,
                decision=decision,
                note="并发裁决测试" if decision == "reject" else None,
                decided_by=operator.id,
            )
            results.append("won")
        except HTTPException as exc:
            results.append(str(exc.status_code))
        except Exception as exc:  # pragma: no cover - assertion exposes unexpected DB errors
            results.append(f"error:{type(exc).__name__}")
        finally:
            session.close()

    threads = [Thread(target=worker, args=("approve",)), Thread(target=worker, args=("reject",))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["409", "won"]
    db_session.expire_all()
    row = db_session.get(SpaceManagerApplication, application.id)
    assert row is not None and row.status in {"approved", "rejected"}


def test_unknown_application_404_for_operator(db_session, client, operator) -> None:
    op_headers = _login_header(client, "平台运营", "101010")
    response = _decide(client, op_headers, 424242, decision="approve")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SPACE_MANAGER_APPLICATION_NOT_FOUND"
