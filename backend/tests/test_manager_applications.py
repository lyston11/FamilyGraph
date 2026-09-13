"""lineage 空间管理员申请与交接工单测试（08-31 契约 + 09-04 隔离适配）。

与旧契约的差别：
- 申请目标必须是 `lineage` 家族空间，household 一律拒绝；
- 裁决人是独立 `system_admin` 主体（09-04 起用户名+强密码登录 8002），
  家庭用户（含旧 is_admin 投影）无裁决权；
- approve 分两阶段：首次 approve 只发原管理员同意工单、申请仍 pending，
  工单 accepted 后再次 approve 才在同一事务内交换唯一 space_admin；
- 交换后原管理员降为普通 member，空间内恰好一个 active space_admin。

09-04 隔离说明：治理 HTTP 面恢复在 admin_app 的 /admin-api/v1（子任务 2）：
approve/reject 是后台唯一业务写端点，二次确认字段 confirm 必填，reject 理由
必填；家庭 listener 上这些路径保持与随机未知路径一致的普通 404。命令层断言
保留作回归，路由层断言覆盖 HTTP 合同（RM-F5）。
"""

from __future__ import annotations

from threading import Barrier, Thread

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.commands import manager_applications as manager_application_commands
from app.commands.context import ActorContext
from app.db import SessionLocal
from app.models.account import Account
from app.models.admin_access import AdminAccessAudit
from app.models.audit_log import AuditLog
from app.models.space import (
    FamilySpace,
    ManagerTransferConsent,
    SpaceManagerApplication,
    SpaceMember,
)
from app.models.user import User
from app.models.v2_foundation import DomainEvent
from conftest import (
    create_space_member,
    create_system_admin,
    create_user_with_pin,
    login,
    seed_space_with_owner,
)

APPLICATIONS_URL = "/api/spaces/manager-applications"
CONSENTS_URL = "/api/spaces/manager-transfer-consents"
ADMIN_APPLICATIONS_URL = "/api/admin/manager-applications"


def _login_header(client: TestClient, name: str, pin: str) -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def sysadmin(db_session):
    """独立系统管理员主体（不是家庭 User）。"""
    return create_system_admin(db_session)


@pytest.fixture()
def applicant(db_session):
    return create_user_with_pin(db_session, "申请人", "202020")


def _seed_lineage(db_session, manager, *, name: str) -> FamilySpace:
    """建一个带唯一 active space_admin 的 lineage 家族空间。"""
    return seed_space_with_owner(db_session, manager.id, name=name, kind="lineage")


def _submit(client, headers, *, space_id=None, request_kind="space_admin", **extra):
    body: dict[str, object] = {"request_kind": request_kind, **extra}
    if space_id is not None:
        body["space_id"] = space_id
    return client.post(APPLICATIONS_URL, json=body, headers=headers)


def _decide(
    db_session,
    sysadmin,
    application_id: int,
    *,
    decision: str,
    note: str | None = None,
) -> SpaceManagerApplication:
    """平台侧裁决走命令层；治理 HTTP 面由子任务 2 在 admin_app 挂载。"""
    return manager_application_commands.decide_manager_application_as_system_admin(
        db_session,
        application_id,
        decision=decision,
        note=note,
        system_admin_id=sysadmin.id,
        ip=None,
    )


def _decide_error(
    db_session, sysadmin, application_id: int, *, decision: str, note: str | None = None
) -> HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        _decide(db_session, sysadmin, application_id, decision=decision, note=note)
    return exc_info.value


def _api_code(exc: HTTPException) -> str:
    detail = exc.detail
    assert isinstance(detail, dict) and "__api_error__" in detail
    return str(detail["__api_error__"]["code"])


def _respond_consent(client, headers, consent_id, *, decision, reason=None):
    body: dict[str, object] = {"decision": decision}
    if reason is not None:
        body["reason"] = reason
    return client.post(f"{CONSENTS_URL}/{consent_id}/decision", json=body, headers=headers)


def _ctx_for(session, user_id: int) -> ActorContext:
    user = session.get(User, user_id)
    account = session.scalar(select(Account).where(Account.user_id == user_id))
    assert user is not None and account is not None
    return ActorContext.from_identity(user, account)


def _roles(db_session, space_id: int) -> dict[int, str]:
    db_session.expire_all()
    return {
        m.user_id: m.role
        for m in db_session.scalars(
            select(SpaceMember).where(
                SpaceMember.space_id == space_id, SpaceMember.status == "active"
            )
        ).all()
    }


# ---- 用户侧：提交与自查 ----


def test_submit_lineage_admin_and_query_mine(db_session, client, applicant) -> None:
    """提交返回目标空间名称/类型与现任管理员，供申请卡片直接渲染。"""
    manager = create_user_with_pin(db_session, "家族管理", "303030")
    space = _seed_lineage(db_session, manager, name="母系家族")
    create_space_member(db_session, space.id, applicant.id)

    headers = _login_header(client, "申请人", "202020")
    submitted = _submit(client, headers, space_id=space.id)
    assert submitted.status_code == 201, submitted.text
    row = submitted.json()
    assert row["status"] == "pending"
    assert row["request_kind"] == "space_admin"
    assert row["space_id"] == space.id
    assert row["space_name"] == "母系家族"
    assert row["space_kind"] == "lineage"
    assert row["applicant_name"] == "申请人"
    assert row["current_manager_user_id"] == manager.id
    assert row["current_manager_name"] == "家族管理"
    assert row["transfer_consent_id"] is None

    mine = client.get(f"{APPLICATIONS_URL}/mine", headers=headers)
    assert mine.status_code == 200, mine.text
    assert len(mine.json()) == 1
    assert mine.json()[0]["id"] == row["id"]


def test_household_target_is_rejected(db_session, client, applicant) -> None:
    """household 不是管理员申请的合法目标（PRD R3）。"""
    manager = create_user_with_pin(db_session, "家庭管理", "303033")
    household = seed_space_with_owner(db_session, manager.id, name="共同家庭", kind="household")
    create_space_member(db_session, household.id, applicant.id)

    r = _submit(client, _login_header(client, "申请人", "202020"), space_id=household.id)
    assert r.status_code == 422
    assert "lineage" in r.json()["error"]["message"]


def test_eligible_targets_lists_only_lineage_memberships(db_session, client, applicant) -> None:
    """入口数据来自服务端：只列 active 普通成员身份所在的 lineage 空间。"""
    manager = create_user_with_pin(db_session, "目标管理", "303034")
    lineage = _seed_lineage(db_session, manager, name="父系家族")
    household = seed_space_with_owner(db_session, manager.id, name="小家庭", kind="household")
    other = _seed_lineage(db_session, manager, name="无关家族")
    create_space_member(db_session, lineage.id, applicant.id)
    create_space_member(db_session, household.id, applicant.id)
    create_space_member(db_session, other.id, applicant.id, status="pending")

    headers = _login_header(client, "申请人", "202020")
    resp = client.get(f"{APPLICATIONS_URL}/eligible-targets", headers=headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [row["space_id"] for row in rows] == [lineage.id]
    assert rows[0]["space_name"] == "父系家族"
    assert rows[0]["space_kind"] == "lineage"
    assert rows[0]["current_manager_name"] == "目标管理"
    assert rows[0]["has_pending_application"] is False

    assert _submit(client, headers, space_id=lineage.id).status_code == 201
    after = client.get(f"{APPLICATIONS_URL}/eligible-targets", headers=headers).json()
    assert after[0]["has_pending_application"] is True


def test_unknown_kind_is_422(db_session, client, applicant) -> None:
    manager = create_user_with_pin(db_session, "类型空间主", "303035")
    space = _seed_lineage(db_session, manager, name="类型家族")
    create_space_member(db_session, space.id, applicant.id)
    headers = _login_header(client, "申请人", "202020")
    unknown_kind = _submit(client, headers, space_id=space.id, request_kind="owner")
    assert unknown_kind.status_code == 422


def test_duplicate_pending_rejected_but_resubmit_after_reject(
    db_session, client, applicant, sysadmin
) -> None:
    manager = create_user_with_pin(db_session, "空间主乙", "303032")
    space = _seed_lineage(db_session, manager, name="乙家族")
    create_space_member(db_session, space.id, applicant.id)
    headers = _login_header(client, "申请人", "202020")

    first = _submit(client, headers, space_id=space.id)
    assert first.status_code == 201, first.text
    duplicate = _submit(client, headers, space_id=space.id)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "SPACE_MANAGER_APPLICATION_EXISTS"

    rejected_row = _decide(
        db_session, sysadmin, first.json()["id"], decision="reject", note="请补充空间治理说明"
    )
    assert rejected_row.status == "rejected"
    resubmit = _submit(client, headers, space_id=space.id)
    assert resubmit.status_code == 201, resubmit.text
    assert resubmit.json()["status"] == "pending"


def test_unconfirmed_applicant_rejected(db_session, client) -> None:
    unconfirmed = create_user_with_pin(
        db_session, "未确档者", "404040", profile_status="provisional"
    )
    manager = create_user_with_pin(db_session, "空间主丙", "505050")
    space = _seed_lineage(db_session, manager, name="丙家族")
    create_space_member(db_session, space.id, unconfirmed.id)
    r = _submit(client, _login_header(client, "未确档者", "404040"), space_id=space.id)
    assert r.status_code == 403
    assert "身份确认" in r.json()["error"]["message"]


def test_target_eligibility_gates(db_session, client, applicant) -> None:
    headers = _login_header(client, "申请人", "202020")

    missing = _submit(client, headers, space_id=99999)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SPACE_NOT_FOUND"

    no_target = _submit(client, headers)
    assert no_target.status_code == 422

    manager = create_user_with_pin(db_session, "空间主丁", "707070")
    space = _seed_lineage(db_session, manager, name="丁家族")
    not_member = _submit(client, headers, space_id=space.id)
    assert not_member.status_code == 404
    create_space_member(db_session, space.id, applicant.id, status="pending")
    pending_member = _submit(client, headers, space_id=space.id)
    assert pending_member.status_code == 404

    # 现任管理员本人不能申请成为自己已管理空间的管理员
    manager_headers = _login_header(client, "空间主丁", "707070")
    r_manager = _submit(client, manager_headers, space_id=space.id)
    assert r_manager.status_code == 409

    member = create_user_with_pin(db_session, "普通成员", "909090")
    create_space_member(db_session, space.id, member.id)
    r_member = _submit(client, _login_header(client, "普通成员", "909090"), space_id=space.id)
    assert r_member.status_code == 201, r_member.text


# ---- 平台侧：队列与裁决（命令层）----


def test_admin_decision_routes_absent_from_family_listener(db_session, client, applicant) -> None:
    """治理路由不在家庭 listener：家庭用户与旧 is_admin 投影得到普通 404。"""
    headers = _login_header(client, "申请人", "202020")
    assert client.get(ADMIN_APPLICATIONS_URL, headers=headers).status_code == 404
    decide = client.post(
        f"{ADMIN_APPLICATIONS_URL}/1/decision",
        json={"decision": "approve"},
        headers=headers,
    )
    assert decide.status_code == 404

    create_user_with_pin(db_session, "旧运营", "101010", is_admin=True)
    legacy_headers = _login_header(client, "旧运营", "101010")
    assert client.get(ADMIN_APPLICATIONS_URL, headers=legacy_headers).status_code == 404


def test_admin_queue_lists_and_filters_by_status(db_session, client, applicant, sysadmin) -> None:
    manager = create_user_with_pin(db_session, "队列空间主", "313131")
    first_space = _seed_lineage(db_session, manager, name="队列家族一")
    second_space = _seed_lineage(db_session, manager, name="队列家族二")
    create_space_member(db_session, first_space.id, applicant.id)
    create_space_member(db_session, second_space.id, applicant.id)
    headers = _login_header(client, "申请人", "202020")
    first = _submit(client, headers, space_id=first_space.id)
    second = _submit(client, headers, space_id=second_space.id)
    assert first.status_code == 201 and second.status_code == 201

    # 首次 approve 只进入交接准备，申请仍 pending
    prepared = _decide(db_session, sysadmin, first.json()["id"], decision="approve")
    assert prepared.status == "pending"
    consent = db_session.scalar(
        select(ManagerTransferConsent).where(
            ManagerTransferConsent.application_id == first.json()["id"]
        )
    )
    assert consent is not None and consent.status == "pending"

    rejected = _decide(
        db_session, sysadmin, second.json()["id"], decision="reject", note="暂缓治理升级"
    )
    assert rejected.status == "rejected"

    pending_rows = manager_application_commands.list_applications(db_session, status="pending")
    assert [row.id for row in pending_rows] == [first.json()["id"]]
    all_rows = manager_application_commands.list_applications(db_session)
    assert {row.status for row in all_rows} == {"pending", "rejected"}
    assert {row.request_kind for row in all_rows} == {"space_admin"}


def test_approve_requires_consent_then_swaps_single_manager(
    db_session, client, applicant, sysadmin
) -> None:
    """完整闭环：审核 → 工单 → 原管理员同意 → 最终 approve 交换唯一管理员。"""
    manager = create_user_with_pin(db_session, "家族主人", "111213")
    space = _seed_lineage(db_session, manager, name="大家族")
    create_space_member(db_session, space.id, applicant.id)

    headers = _login_header(client, "申请人", "202020")
    submitted = _submit(client, headers, space_id=space.id)
    assert submitted.status_code == 201
    app_id = submitted.json()["id"]

    # 阶段一：发工单，角色一律不变
    prepared = _decide(db_session, sysadmin, app_id, decision="approve")
    assert prepared.status == "pending"
    assert _roles(db_session, space.id) == {manager.id: "space_admin", applicant.id: "member"}

    # 原管理员同意前，再次 approve 必须失败
    early = _decide_error(db_session, sysadmin, app_id, decision="approve")
    assert early.status_code == 409
    assert "同意" in early.detail["__api_error__"]["message"]

    # 原管理员工单自带目标空间名称与申请人标识
    manager_headers = _login_header(client, "家族主人", "111213")
    tickets = client.get(f"{CONSENTS_URL}/mine", headers=manager_headers)
    assert tickets.status_code == 200, tickets.text
    ticket = tickets.json()[0]
    assert ticket["space_name"] == "大家族"
    assert ticket["space_kind"] == "lineage"
    assert ticket["applicant_user_id"] == applicant.id
    assert ticket["applicant_name"] == "申请人"
    assert ticket["status"] == "pending"

    accepted = _respond_consent(client, manager_headers, ticket["id"], decision="accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    # 同意本身不改变任何管理员关系
    assert _roles(db_session, space.id) == {manager.id: "space_admin", applicant.id: "member"}

    # 阶段二：最终 approve 在同一事务内完成交换
    final = _decide(db_session, sysadmin, app_id, decision="approve")
    assert final.status == "approved"

    roles = _roles(db_session, space.id)
    assert roles == {applicant.id: "space_admin", manager.id: "member"}
    assert list(roles.values()).count("space_admin") == 1

    row = db_session.get(SpaceManagerApplication, app_id)
    assert row is not None
    assert row.system_admin_decided_by == sysadmin.id
    assert row.decided_by is None  # 裁决人不是家庭 User
    assert row.decided_at is not None

    actions = set(
        db_session.scalars(select(AuditLog.action).where(AuditLog.target_id == app_id)).all()
    )
    assert {
        "manager_application_submitted",
        "manager_transfer_consent_sent",
        "manager_application_approved",
    } <= actions

    again = _decide_error(db_session, sysadmin, app_id, decision="reject", note="改判")
    assert again.status_code == 409
    assert _api_code(again) == "SPACE_MANAGER_APPLICATION_DECIDED"


def test_manager_rejection_keeps_current_manager(db_session, client, applicant, sysadmin) -> None:
    """原管理员拒绝 → 申请终止，管理员关系保持不变，系统管理员不能绕过。"""
    manager = create_user_with_pin(db_session, "拒绝管理", "141516")
    space = _seed_lineage(db_session, manager, name="拒绝家族")
    create_space_member(db_session, space.id, applicant.id)

    submitted = _submit(client, _login_header(client, "申请人", "202020"), space_id=space.id)
    app_id = submitted.json()["id"]
    assert _decide(db_session, sysadmin, app_id, decision="approve").status == "pending"

    manager_headers = _login_header(client, "拒绝管理", "141516")
    ticket = client.get(f"{CONSENTS_URL}/mine", headers=manager_headers).json()[0]
    rejected = _respond_consent(
        client, manager_headers, ticket["id"], decision="reject", reason="暂不移交"
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["response_reason"] == "暂不移交"

    db_session.expire_all()
    row = db_session.get(SpaceManagerApplication, app_id)
    assert row is not None and row.status == "rejected"
    assert _roles(db_session, space.id) == {manager.id: "space_admin", applicant.id: "member"}

    # 系统管理员不能在原管理员拒绝后继续批准
    blocked = _decide_error(db_session, sysadmin, app_id, decision="approve")
    assert blocked.status_code == 409


def test_consent_only_actionable_by_current_manager(
    db_session, client, applicant, sysadmin
) -> None:
    """工单只能由仍在任的目标空间管理员处理；他人 404，卸任后失效。"""
    manager = create_user_with_pin(db_session, "在任管理", "151617")
    create_user_with_pin(db_session, "无关者", "161718")
    space = _seed_lineage(db_session, manager, name="工单家族")
    create_space_member(db_session, space.id, applicant.id)

    submitted = _submit(client, _login_header(client, "申请人", "202020"), space_id=space.id)
    prepared = _decide(db_session, sysadmin, submitted.json()["id"], decision="approve")
    assert prepared.status == "pending"

    consent = db_session.scalars(select(ManagerTransferConsent)).one()

    # 无关用户看不到也处理不了
    outsider_headers = _login_header(client, "无关者", "161718")
    assert client.get(f"{CONSENTS_URL}/mine", headers=outsider_headers).json() == []
    assert (
        _respond_consent(client, outsider_headers, consent.id, decision="accept").status_code == 404
    )

    # 原管理员卸任后旧工单失效，不可复用
    manager_membership = db_session.scalars(
        select(SpaceMember).where(
            SpaceMember.space_id == space.id, SpaceMember.user_id == manager.id
        )
    ).one()
    manager_membership.role = "member"
    db_session.commit()
    manager_headers = _login_header(client, "在任管理", "151617")
    stale = _respond_consent(client, manager_headers, consent.id, decision="accept")
    assert stale.status_code == 409
    db_session.expire_all()
    assert db_session.get(ManagerTransferConsent, consent.id).status == "expired"


def test_reject_requires_note_and_records_it(db_session, client, applicant, sysadmin) -> None:
    manager = create_user_with_pin(db_session, "驳回空间主", "121212")
    space = _seed_lineage(db_session, manager, name="驳回家族")
    create_space_member(db_session, space.id, applicant.id)
    headers = _login_header(client, "申请人", "202020")
    submitted = _submit(client, headers, space_id=space.id)
    assert submitted.status_code == 201
    app_id = submitted.json()["id"]

    no_note = _decide_error(db_session, sysadmin, app_id, decision="reject")
    assert no_note.status_code == 422
    assert _api_code(no_note) == "SPACE_MANAGER_APPLICATION_NOTE_REQUIRED"
    blank_note = _decide_error(db_session, sysadmin, app_id, decision="reject", note="   ")
    assert blank_note.status_code == 422

    rejected = _decide(db_session, sysadmin, app_id, decision="reject", note="成员资格尚未稳定")
    assert rejected.status == "rejected"
    assert rejected.decision_note == "成员资格尚未稳定"
    db_session.expire_all()
    row = db_session.get(SpaceManagerApplication, app_id)
    assert row is not None and row.status == "rejected"
    assert row.system_admin_decided_by == sysadmin.id
    assert row.decided_at is not None
    assert _roles(db_session, space.id)[applicant.id] == "member"


def test_approve_conflicts_when_member_changes_and_keeps_pending(
    db_session, client, applicant, sysadmin
) -> None:
    manager = create_user_with_pin(db_session, "变动空间主", "171819")
    space = _seed_lineage(db_session, manager, name="变动家族")
    membership = create_space_member(db_session, space.id, applicant.id)
    submitted = _submit(client, _login_header(client, "申请人", "202020"), space_id=space.id)
    assert submitted.status_code == 201

    # 申请人成员资格在裁决前失效 → 裁决必须安全失败并保持 pending
    membership.status = "pending"
    db_session.commit()
    decided = _decide_error(db_session, sysadmin, submitted.json()["id"], decision="approve")
    assert decided.status_code == 409
    assert "普通成员" in decided.detail["__api_error__"]["message"]
    db_session.expire_all()
    row = db_session.get(SpaceManagerApplication, submitted.json()["id"])
    assert row is not None and row.status == "pending"


def test_direct_space_creation_remains_available(db_session, client, applicant) -> None:
    headers = _login_header(client, "申请人", "202020")
    response = client.post("/api/spaces", json={"name": "直建空间"}, headers=headers)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["name"] == "直建空间"
    space = db_session.get(FamilySpace, created["id"])
    assert space is not None
    membership = db_session.scalars(
        select(SpaceMember).where(
            SpaceMember.space_id == space.id, SpaceMember.user_id == applicant.id
        )
    ).one()
    # 自建空间的创建者即该空间唯一管理员
    assert membership.role == "space_admin" and membership.status == "active"


# ---- 并发回归 ----


def test_concurrent_duplicate_submissions_have_one_winner(db_session, applicant) -> None:
    manager = create_user_with_pin(db_session, "并发空间主", "313233")
    space = _seed_lineage(db_session, manager, name="并发申请家族")
    create_space_member(db_session, space.id, applicant.id)
    space_id = space.id
    db_session.expire_all()
    # 释放夹具会话持有的事务锁：SQLite 未设 busy timeout，工作线程的写会无限等待。
    db_session.rollback()

    results: list[str] = []
    barrier = Barrier(2)

    def worker() -> None:
        session = SessionLocal()
        try:
            ctx = _ctx_for(session, applicant.id)
            barrier.wait()
            manager_application_commands.submit_manager_application(session, ctx, space_id=space_id)
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
        .filter_by(applicant_user_id=applicant.id, space_id=space_id, status="pending")
        .count()
        == 1
    )


def test_concurrent_decisions_have_one_winner(db_session, applicant, sysadmin) -> None:
    """并发裁决恰好一个胜出；不产生双管理员或重复终态。"""
    manager = create_user_with_pin(db_session, "并发裁决空间主", "414243")
    space = _seed_lineage(db_session, manager, name="并发裁决家族")
    create_space_member(db_session, space.id, applicant.id)
    application = manager_application_commands.submit_manager_application(
        db_session, _ctx_for(db_session, applicant.id), space_id=space.id
    )
    application_id = application.id
    admin_id = sysadmin.id
    db_session.expire_all()
    # 同上：释放夹具会话的事务锁，避免工作线程写等待无限挂起。
    db_session.rollback()

    results: list[str] = []
    barrier = Barrier(2)

    def worker(decision: str) -> None:
        session = SessionLocal()
        try:
            barrier.wait()
            manager_application_commands.decide_manager_application_as_system_admin(
                session,
                application_id,
                decision=decision,
                note="并发裁决测试" if decision == "reject" else None,
                system_admin_id=admin_id,
                ip=None,
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

    # approve 只发工单（申请仍 pending），reject 直接终态；两者不得同时生效
    assert "won" in results
    db_session.expire_all()
    row = db_session.get(SpaceManagerApplication, application_id)
    assert row is not None
    if row.status == "pending":
        assert db_session.scalar(select(ManagerTransferConsent)) is not None
    else:
        assert row.status == "rejected"
    assert list(_roles(db_session, space.id).values()).count("space_admin") == 1


def test_unknown_application_404_for_system_admin(db_session, sysadmin) -> None:
    missing = _decide_error(db_session, sysadmin, 424242, decision="approve")
    assert missing.status_code == 404
    assert _api_code(missing) == "SPACE_MANAGER_APPLICATION_NOT_FOUND"


def test_domain_event_emitted_on_decision(db_session, client, applicant, sysadmin) -> None:
    """裁决产生可查询领域事件（审计与事件双留痕）。"""
    manager = create_user_with_pin(db_session, "事件空间主", "515253")
    space = _seed_lineage(db_session, manager, name="事件家族")
    create_space_member(db_session, space.id, applicant.id)
    submitted = _submit(client, _login_header(client, "申请人", "202020"), space_id=space.id)
    _decide(db_session, sysadmin, submitted.json()["id"], decision="reject", note="记录事件")
    event_types = set(db_session.scalars(select(DomainEvent.type)).all())
    assert "space.manager_application.decided" in event_types


# ---- 平台侧：HTTP 裁决面（/admin-api/v1，8002，子任务 2 恢复）----


@pytest.fixture()
def admin_v1_headers(admin_client: TestClient, db_session) -> dict[str, str]:
    from conftest import admin_session_headers

    create_system_admin(db_session)
    return admin_session_headers(admin_client)


def _http_approve(
    admin_client: TestClient, headers: dict[str, str], application_id: int, **extra: object
):
    body: dict[str, object] = {"confirm": True, **extra}
    return admin_client.post(
        f"/admin-api/v1/manager-applications/{application_id}/approve",
        json=body,
        headers=headers,
    )


def _http_reject(
    admin_client: TestClient, headers: dict[str, str], application_id: int, **extra: object
):
    body: dict[str, object] = {"confirm": True, **extra}
    return admin_client.post(
        f"/admin-api/v1/manager-applications/{application_id}/reject",
        json=body,
        headers=headers,
    )


def test_http_decision_requires_confirm_and_reject_note(
    admin_client: TestClient, db_session, client, applicant, admin_v1_headers
) -> None:
    manager = create_user_with_pin(db_session, "HTTP空间主", "616263")
    space = _seed_lineage(db_session, manager, name="HTTP家族")
    create_space_member(db_session, space.id, applicant.id)
    submitted = _submit(client, _login_header(client, "申请人", "202020"), space_id=space.id)
    application_id = submitted.json()["id"]

    # 二次确认字段缺失 / 显式 false → 422
    assert (
        admin_client.post(
            f"/admin-api/v1/manager-applications/{application_id}/approve",
            json={"note": None},
            headers=admin_v1_headers,
        ).status_code
        == 422
    )
    no_confirm = admin_client.post(
        f"/admin-api/v1/manager-applications/{application_id}/reject",
        json={"note": "理由"},
        headers=admin_v1_headers,
    )
    assert no_confirm.status_code == 422
    false_confirm = admin_client.post(
        f"/admin-api/v1/manager-applications/{application_id}/approve",
        json={"confirm": False},
        headers=admin_v1_headers,
    )
    assert false_confirm.status_code == 422

    # reject 理由必填非空（HTTP 面语义与命令层一致）
    missing_note = _http_reject(admin_client, admin_v1_headers, application_id)
    assert missing_note.status_code == 422
    blank_note = _http_reject(admin_client, admin_v1_headers, application_id, note="   ")
    assert blank_note.status_code == 422
    assert blank_note.json()["error"]["code"] == "VALIDATION_ERROR"

    # 不存在申请统一 404
    missing = _http_approve(admin_client, admin_v1_headers, 424242)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SPACE_MANAGER_APPLICATION_NOT_FOUND"


def test_http_approve_two_phase_swaps_single_manager(
    admin_client: TestClient, db_session, client, applicant, admin_v1_headers
) -> None:
    """HTTP 面完整闭环：approve→工单→原管理员同意→最终 approve 交换唯一管理员。"""
    manager = create_user_with_pin(db_session, "HTTP家族主人", "717273")
    space = _seed_lineage(db_session, manager, name="HTTP大家族")
    create_space_member(db_session, space.id, applicant.id)
    submitted = _submit(client, _login_header(client, "申请人", "202020"), space_id=space.id)
    application_id = submitted.json()["id"]

    prepared = _http_approve(admin_client, admin_v1_headers, application_id)
    assert prepared.status_code == 200, prepared.text
    row = prepared.json()
    assert set(row) == {
        "id",
        "applicant_user_id",
        "applicant_name",
        "space_id",
        "space_name",
        "space_kind",
        "request_kind",
        "status",
        "decision_note",
        "transfer_consent_id",
        "transfer_consent_status",
        "created_at",
        "decided_at",
        "system_admin_decided_by",
    }
    assert row["status"] == "pending"
    assert row["space_name"] == "HTTP大家族"
    assert row["transfer_consent_status"] == "pending"
    # 首次 approve 只发工单：还不是终态裁决，裁决人列暂为空
    assert row["system_admin_decided_by"] is None
    assert _roles(db_session, space.id) == {manager.id: "space_admin", applicant.id: "member"}

    # 工单未同意前再次 approve → 409
    early = _http_approve(admin_client, admin_v1_headers, application_id)
    assert early.status_code == 409

    manager_headers = _login_header(client, "HTTP家族主人", "717273")
    tickets = client.get(
        "/api/spaces/manager-transfer-consents/mine", headers=manager_headers
    ).json()
    ticket = tickets[0]
    accepted = _respond_consent(client, manager_headers, ticket["id"], decision="accept")
    assert accepted.status_code == 200, accepted.text

    final = _http_approve(admin_client, admin_v1_headers, application_id, note="资质已核验")
    assert final.status_code == 200
    assert final.json()["status"] == "approved"
    assert final.json()["decision_note"] == "资质已核验"

    roles = _roles(db_session, space.id)
    assert roles == {applicant.id: "space_admin", manager.id: "member"}
    assert list(roles.values()).count("space_admin") == 1

    # 终态不可改判（重复裁决 409，两个端点同样语义）
    again = _http_reject(admin_client, admin_v1_headers, application_id, note="事后改判")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "SPACE_MANAGER_APPLICATION_DECIDED"

    # 独立 admin 审计与裁决同事务落库
    admin_actions = set(
        db_session.scalars(
            select(AdminAccessAudit.action).where(
                AdminAccessAudit.action.like("manager_application%")
            )
        ).all()
    )
    assert admin_actions == {
        "manager_application_consent_requested",
        "manager_application_approved",
    }


def test_http_reject_keeps_current_manager_and_audits(
    admin_client: TestClient, db_session, client, applicant, admin_v1_headers
) -> None:
    manager = create_user_with_pin(db_session, "HTTP驳回主", "818283")
    space = _seed_lineage(db_session, manager, name="HTTP驳回家族")
    create_space_member(db_session, space.id, applicant.id)
    submitted = _submit(client, _login_header(client, "申请人", "202020"), space_id=space.id)
    application_id = submitted.json()["id"]

    rejected = _http_reject(admin_client, admin_v1_headers, application_id, note="成员资格尚未稳定")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert _roles(db_session, space.id) == {manager.id: "space_admin", applicant.id: "member"}

    audit_row = (
        db_session.query(AdminAccessAudit)
        .filter(AdminAccessAudit.action == "manager_application_rejected")
        .one()
    )
    assert audit_row.system_admin_id == _admin_id(db_session)
    assert audit_row.target_type == "space"
    assert audit_row.target_id == space.id
    assert audit_row.filters_json["applicant_user_id"] == applicant.id


def _admin_id(db_session) -> int:  # type: ignore[no-untyped-def]
    """admin_v1_headers 夹具创建的唯一管理员主体。"""
    from app.models.system_admin import SystemAdmin

    return db_session.query(SystemAdmin).filter_by(username="admin").one().id


def test_http_decision_routes_absent_from_admin_listener_for_family_tokens(
    admin_client: TestClient, db_session, client, applicant
) -> None:
    """admin token 在 8000 401（既有隔离）；家庭用户在 8002 无凭据 401。"""
    create_user_with_pin(db_session, "孤立申请者", "919293")
    headers = _login_header(client, "孤立申请者", "919293")
    # 8000 上治理路径仍是普通 404（既有测试覆盖 body 一致性），这里验证 8002 认证
    assert (
        admin_client.post(
            "/admin-api/v1/manager-applications/1/approve", json={"confirm": True}
        ).status_code
        == 401
    )
    family_token = headers["Authorization"]
    assert (
        admin_client.post(
            "/admin-api/v1/manager-applications/1/approve",
            json={"confirm": True},
            headers={"Authorization": family_token},
        ).status_code
        == 401
    )
