"""数据权利流程测试（AC-F6）：导出/更正/删除/争议 + 撤权传播合同。

覆盖：导出异步生成（继承可见性、有过期下载与审计）、更正申请字段白名单与
operator break-glass 决议、删除请求冻结事件 → 执行 → tombstone 失效事件 +
缓存类表清理、争议证据保留（决议不覆盖原文）、撤权传播事件（关系撤销）。
"""

from __future__ import annotations

import pytest
from conftest import auth_header, create_user_with_pin, login
from fastapi import HTTPException

from app.commands import data_rights as dr_commands
from app.commands import identity as identity_commands
from app.commands import members as member_commands
from app.commands.context import ActorContext
from app.config import UPLOADS_DIR
from app.errors import extract_api_error
from app.models.node_position import NodePosition
from app.models.v2_foundation import ClaimDispute, DataRightRequest, DomainEvent
from app.utils.timeutil import utcnow


def _ctx(user) -> ActorContext:
    return ActorContext(
        user_id=user.id,
        account_id=user.account.id,
        account_status=user.account.status,
    )


def _event_types(db_session) -> set[str]:
    return {e.type for e in db_session.query(DomainEvent).all()}


# ---- export ----


def test_export_request_generates_file_and_download(db_session, client) -> None:
    create_user_with_pin(db_session, "导出者", "111111")
    tokens = login(client, "导出者", "111111").json()

    resp = client.post("/api/data-rights/export", headers=auth_header(tokens))
    assert resp.status_code == 201, resp.text
    request_id = resp.json()["id"]

    # BackgroundTasks 在 TestClient 中同步执行；完成状态以数据库为准（响应体先于任务返回）
    db_session.expire_all()
    row = db_session.get(DataRightRequest, request_id)
    assert row.status == "completed", row.status
    assert row.policy_version
    file_path = UPLOADS_DIR / "exports" / (row.result_path or "")
    assert row.result_path and file_path.exists()

    content = file_path.read_text(encoding="utf-8")
    assert '"profile"' in content and "导出者" in content  # 本人 self_private 全字段

    # 下载：归属校验 + 审计
    download = client.get(f"/api/data-rights/{row.id}/export-file", headers=auth_header(tokens))
    assert download.status_code == 200
    assert download.headers["X-Content-Type-Options"] == "nosniff"

    create_user_with_pin(db_session, "无关下载者", "222222")
    other_tokens = login(client, "无关下载者", "222222").json()
    foreign = client.get(
        f"/api/data-rights/{row.id}/export-file", headers=auth_header(other_tokens)
    )
    assert foreign.status_code == 404  # 非本人请求防枚举


def test_export_download_expires_with_lazy_cleanup(db_session, client) -> None:
    from datetime import timedelta

    user = create_user_with_pin(db_session, "过期下载人", "333333")
    tokens = login(client, "过期下载人", "333333").json()
    request = dr_commands.create_data_right_request(db_session, _ctx(user), request_type="export")
    dr_commands.process_export_request(request.id)
    db_session.expire_all()

    row = db_session.get(DataRightRequest, request.id)
    row.expires_at = utcnow() - timedelta(minutes=1)
    db_session.commit()
    file_path = UPLOADS_DIR / "exports" / row.result_path
    assert file_path.exists()

    gone = client.get(f"/api/data-rights/{request.id}/export-file", headers=auth_header(tokens))
    assert gone.status_code == 410  # 有过期下载
    assert gone.json()["error"]["code"] == "DATA_RIGHT_REQUEST_EXPIRED"
    assert not file_path.exists()  # 惰性清理文件
    assert "data_right.export.expired" in _event_types(db_session)


# ---- correction ----


def test_correction_field_whitelist_and_operator_resolution(db_session) -> None:
    subject = create_user_with_pin(db_session, "更正申请人", "444444")
    operator = create_user_with_pin(db_session, "决议运营", "555555", is_admin=True)

    # 白名单外字段 422
    with pytest.raises(HTTPException):
        dr_commands.create_data_right_request(
            db_session,
            _ctx(subject),
            request_type="correct",
            payload={"fields": {"avatar_path": "x"}},
        )

    request = dr_commands.create_data_right_request(
        db_session,
        _ctx(subject),
        request_type="correct",
        payload={"fields": {"bio": "本人更正后的简介"}},
    )
    op_ctx = _ctx(operator)

    # break-glass 理由必填
    with pytest.raises(HTTPException) as exc_info:
        dr_commands.resolve_correction_request(
            db_session, op_ctx, request.id, approve=True, note="  "
        )
    error = extract_api_error(exc_info.value.detail)
    assert error is not None and error["code"] == "BREAK_GLASS_NOTE_REQUIRED"

    resolved = dr_commands.resolve_correction_request(
        db_session, op_ctx, request.id, approve=True, note="申请人提交了有效证明"
    )
    assert resolved.status == "completed"
    db_session.expire_all()
    from app.models.user import User as UserRow

    subject_row = db_session.get(UserRow, subject.id)
    assert subject_row.bio == "本人更正后的简介"

    types = _event_types(db_session)
    assert "data_right.correct.completed" in types and "profile.updated" in types


def test_correction_reject_path_keeps_payload_and_note(db_session) -> None:
    subject = create_user_with_pin(db_session, "被拒申请人", "666666")
    operator = create_user_with_pin(db_session, "驳回运营", "777777", is_admin=True)
    request = dr_commands.create_data_right_request(
        db_session,
        _ctx(subject),
        request_type="correct",
        payload={"fields": {"name": "新名字"}},
    )
    resolved = dr_commands.resolve_correction_request(
        db_session, _ctx(operator), request.id, approve=False, note="证明材料不足"
    )
    assert resolved.status == "rejected" and resolved.payload_json is not None
    assert resolved.payload_json["_resolution_note"] == "证明材料不足"
    assert "data_right.correct.rejected" in _event_types(db_session)

    from app.models.user import User as UserRow

    assert db_session.get(UserRow, subject.id).name == "被拒申请人"  # 未应用


# ---- delete ----


def test_delete_request_freeze_then_execute_with_tombstones(db_session) -> None:
    """AC-F6 撤权传播合同：冻结事件 → 执行 → tombstone 失效事件 + 缓存类表清理。"""
    user = create_user_with_pin(db_session, "注销人", "888888")
    member, _pin = member_commands.create_member(db_session, _ctx(user), name="注销人的关联档")
    db_session.commit()

    # 缓存类表造数：node_positions 随档案删除必须消失。
    # 空间由他人所有（删除者自身名下无空间，义务预检放行）。
    from app.models.space import FamilySpace, SpaceMember

    space_owner = create_user_with_pin(db_session, "空间持有人", "141414")
    space = FamilySpace(
        name="共享空间", owner_id=space_owner.id, kind="household", created_at=utcnow()
    )
    db_session.add(space)
    db_session.flush()
    db_session.add(
        SpaceMember(
            space_id=space.id,
            user_id=space_owner.id,
            added_by=space_owner.id,
            role="owner",
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    db_session.add(
        SpaceMember(
            space_id=space.id,
            user_id=user.id,
            added_by=space_owner.id,
            role="member",
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    pos = NodePosition(space_id=space.id, user_id=user.id, x=1.0, y=2.0)
    db_session.add(pos)
    db_session.commit()

    request = dr_commands.create_data_right_request(db_session, _ctx(user), request_type="delete")
    assert "profile.delete.requested" in _event_types(db_session)  # 冻结合同先行发布

    executed, purge = dr_commands.execute_delete_request(
        db_session, _ctx(user), request.id, confirm_name="注销人"
    )
    # 请求行随账号级联消失（self-delete）；执行留痕以 audit_log + domain_events 为准
    assert isinstance(purge, list)

    types = _event_types(db_session)
    assert {
        "profile.deleted",
        "attachments.invalidated",
        "disclosure.invalidated",
        "data_right.delete.executed",
    } <= types

    # 真源已删：账号/档案随级联消失；位置缓存行同事务清理；空间与他人成员资格保留
    db_session.expire_all()
    assert db_session.query(NodePosition).filter(NodePosition.user_id == user.id).count() == 0
    from app.models.user import User as UserRow

    assert db_session.get(UserRow, user.id) is None
    assert db_session.get(FamilySpace, space.id) is not None  # 空间不被级联误删
    assert (
        db_session.query(SpaceMember)
        .filter(SpaceMember.user_id == space_owner.id, SpaceMember.role == "owner")
        .count()
        == 1
    )

    # 已处理请求不可重复执行
    with pytest.raises(HTTPException):
        dr_commands.execute_delete_request(
            db_session, _ctx(user), request.id, confirm_name="注销人"
        )


# ---- claim disputes ----


def test_claim_dispute_lifecycle_preserves_evidence(db_session) -> None:
    raiser = create_user_with_pin(db_session, "争议发起人", "999999")
    target_profile = create_user_with_pin(db_session, "争议档案", "101010")
    operator = create_user_with_pin(db_session, "争议运营", "111112", is_admin=True)
    evidence = {"claim": "这是我的身份", "id_card_last4": "1234"}

    dispute = identity_commands.raise_claim_dispute(
        db_session, _ctx(raiser), profile_id=target_profile.id, evidence=evidence
    )
    assert dispute.status == "open"
    assert dispute.evidence_json == evidence  # 原文保留

    # 发起人可撤回
    withdrawn_probe = identity_commands.raise_claim_dispute(
        db_session, _ctx(raiser), profile_id=target_profile.id, evidence=evidence
    )
    withdrawn = identity_commands.withdraw_claim_dispute(
        db_session, _ctx(raiser), withdrawn_probe.id
    )
    assert withdrawn.status == "withdrawn"

    # operator 决议：理由必填；evidence 原文不被覆盖
    with pytest.raises(HTTPException):
        dr_commands.resolve_claim_dispute(
            db_session, _ctx(operator), dispute.id, outcome="resolved_claim", note=""
        )
    dr_commands.resolve_claim_dispute(
        db_session, _ctx(operator), dispute.id, outcome="resolved_claim", note="证件比对通过"
    )
    db_session.expire_all()
    row = db_session.get(ClaimDispute, dispute.id)
    assert row.status == "resolved_claim"
    assert row.evidence_json == evidence  # 决议不覆盖原始证据
    assert row.resolution_note == "证件比对通过"
    assert "claim_dispute.resolved" in _event_types(db_session)


# ---- revocation propagation contract ----


def test_relation_revocation_emits_invalidation_event(db_session) -> None:
    """撤权传播：关系撤销/成员移除后 domain_events 含对应失效事实。"""
    from app.commands import connections as connection_commands

    a = create_user_with_pin(db_session, "断连甲", "121212")
    b = create_user_with_pin(db_session, "断连乙", "131313")

    edge = connection_commands.create_connection_request(
        db_session, _ctx(a), target_id=b.id, dir_class="peer", label="朋友"
    )
    connection_commands.decide_connection_request(db_session, _ctx(b), edge.id, accept=True)
    revoked = connection_commands.revoke_relation(db_session, _ctx(a), edge.id)
    assert revoked.status == "revoked"

    types = _event_types(db_session)
    assert {"relation.requested", "relation.accepted", "relation.revoked"} <= types
