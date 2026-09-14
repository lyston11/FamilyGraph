"""空间限定统计合同测试（PFV-F2）。

覆盖：授权聚合口径、none 成员不可计、pending 计数、stale/failed 状态路径、
ETag/304、撤权即时隐藏、lineage 空间支持、旧无空间 /stats 合同保持不变。
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import (
    auth_header,
    create_agent_fixture,
    create_space_member,
    create_user_with_pin,
    login,
)

from app import config
from app.models.steward import ActionCard
from app.services import personal_family_view
from app.services import source_facts as sf
from app.utils.timeutil import utcnow


@pytest.fixture(autouse=True)
def _pfv_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)


def _login_header(client, name: str) -> dict[str, str]:
    resp = login(client, name, "123456")
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def _confirm_parent(session, parent_id: int, child_id: int, space_id: int) -> None:
    fact = sf.create_source_fact(
        session,
        fact_type="biological_parent",
        subject_user_id=parent_id,
        object_user_id=child_id,
        provenance="manual_entry",
        space_id=space_id,
    )
    sf.transition_source_fact(session, fact, "confirm")
    # 释放写锁：后续走 API（独立会话）时不得携带未提交事务
    session.commit()


def _stats(client, headers, space_id: int, **kwargs: Any):
    return client.get("/api/stats", params={"space_id": space_id, **kwargs}, headers=headers)


def test_space_stats_aggregates_authorized_projection_only(client, db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="ss-viewer")
    parent = create_user_with_pin(db_session, "ss-parent", "123456", gender="m")
    create_space_member(db_session, space.id, parent.id)
    _confirm_parent(db_session, parent.id, viewer.id, space.id)
    personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
    db_session.commit()

    resp = _stats(client, _login_header(client, "ss-viewer"), space.id)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data.keys()) == {
        "space_id",
        "space_kind",
        "status",
        "view_version",
        "node_count",
        "edge_count",
        "member_count",
        "relation_distribution",
        "pending_action_cards",
        "pending_memberships",
        "computed_at",
        "stale_reason",
    }
    assert data["space_id"] == space.id
    assert data["space_kind"] == "household"
    assert data["status"] == "current"
    assert data["stale_reason"] is None
    # 未确认路径的空间成员不进入视图节点（无占位），但成员投影可见
    assert data["node_count"] == 2  # viewer + parent（confirmed path）
    assert data["edge_count"] == 1
    assert data["member_count"] == 2
    assert data["relation_distribution"] == [{"dir_class": "elder", "count": 1}]
    assert data["pending_action_cards"] == 0
    assert data["pending_memberships"] == 0
    assert data["computed_at"] is not None
    assert resp.headers.get("etag")


def test_space_stats_pending_counts(client, db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="ss-pending")
    other = create_user_with_pin(db_session, "ss-pending-other", "123456")
    other_space_owner, other_space = create_agent_fixture(db_session, name="ss-pending2")

    from app.services import action_cards

    card, outcome = action_cards.create_card(
        db_session,
        kind="household_link",
        space_id=space.id,
        recipient_account_id=viewer.account.id,
        subject_user_id=viewer.id,
        object_user_id=None,
        evidence_json={"k": "v"},
        proposed_action_json={"action": "create_shared_household"},
        reason_text="test",
    )
    assert outcome == "created" and card is not None
    # 他人卡片/他空间卡片不得计入
    action_cards.create_card(
        db_session,
        kind="household_link",
        space_id=other_space.id,
        recipient_account_id=other.account.id,
        subject_user_id=other.id,
        object_user_id=None,
        evidence_json={"k": "v2"},
        proposed_action_json={"action": "create_shared_household"},
        reason_text="test",
    )
    # 空间 pending 成员行（对 active 成员可见）
    create_space_member(db_session, space.id, other.id, status="pending")
    db_session.commit()

    data = _stats(client, _login_header(client, "ss-pending"), space.id).json()
    assert data["pending_action_cards"] == 1
    assert data["pending_memberships"] == 1

    # 卡片被查看（非 pending）→ 即使家谱尚未发布，pending 计数仍即时归零
    card.state = "viewed"
    db_session.commit()
    data = _stats(client, _login_header(client, "ss-pending"), space.id).json()
    assert data["pending_action_cards"] == 0


def test_space_stats_stale_status_and_revocation_immediate_hide(client, db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="ss-stale")
    parent = create_user_with_pin(db_session, "ss-stale-parent", "123456")
    create_space_member(db_session, space.id, parent.id)
    _confirm_parent(db_session, parent.id, viewer.id, space.id)
    personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
    db_session.commit()
    headers = _login_header(client, "ss-stale")
    assert _stats(client, headers, space.id).json()["status"] == "current"

    # parent 成员资格移除：可见性即时 none；视图经失效标记进入 stale
    from app.models.space import SpaceMember

    row = (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space.id, SpaceMember.user_id == parent.id)
        .one()
    )
    row.status = "removed"
    row.updated_at = utcnow()
    db_session.commit()
    personal_family_view.invalidate_space_views(db_session, space_id=space.id)
    db_session.commit()

    data = _stats(client, headers, space.id).json()
    assert data["status"] == "stale"
    assert data["stale_reason"] is not None
    # 非 current 只返回安全空图统计；成员计数仍按当前授权计算。
    assert data["node_count"] == 0
    assert data["edge_count"] == 0
    assert data["member_count"] == 1
    assert data["relation_distribution"] == []


def test_space_stats_queued_and_failed_status_paths(client, db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="ss-status")
    personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
    db_session.commit()
    headers = _login_header(client, "ss-status")
    assert _stats(client, headers, space.id).json()["status"] == "current"

    view = personal_family_view.get_view(db_session, account=viewer.account, space_id=space.id)

    # queued/failed：不把上一份投影行聚合成当前统计，保留明确状态。
    view.status = "queued"
    db_session.commit()
    data = _stats(client, headers, space.id).json()
    assert data["status"] == "queued"
    assert data["stale_reason"] == "projection_not_current"
    assert data["node_count"] == 0

    # 即使旧节点仍在，也不能在失败状态下暴露旧计数。
    view.status = "failed"
    view.failed_reason = "boom"
    db_session.commit()
    data = _stats(client, headers, space.id).json()
    assert data["status"] == "failed"
    assert data["stale_reason"] == "boom"
    assert data["node_count"] == 0
    assert data["edge_count"] == 0
    assert data["member_count"] == 1  # viewer 自身仍可见
    assert data["relation_distribution"] == []
    assert "total" not in data and "by_gender" not in data


def test_space_stats_lineage_space_supported(client, db_session) -> None:
    from app.models.space import FamilySpace

    owner, _household = create_agent_fixture(db_session, name="ss-lineage")
    lineage = FamilySpace(
        name="ss-lineage-space", kind="lineage", owner_id=owner.id, created_at=utcnow()
    )
    db_session.add(lineage)
    db_session.commit()
    create_space_member(db_session, lineage.id, owner.id)

    data = _stats(client, _login_header(client, "ss-lineage"), lineage.id).json()
    assert data["space_kind"] == "lineage"
    assert data["status"] == "never_computed"
    assert data["node_count"] == 0
    assert data["member_count"] == 1


def test_space_stats_etag_304_and_authorization_before_304(client, db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="ss-etag")
    headers = _login_header(client, "ss-etag")
    first = _stats(client, headers, space.id)
    etag = first.headers["etag"]
    cached = client.get(
        "/api/stats",
        params={"space_id": space.id},
        headers={**headers, "If-None-Match": etag},
    )
    assert cached.status_code == 304

    # 聚合变化 → 新 ETag
    member = create_user_with_pin(db_session, "ss-etag-member", "123456")
    create_space_member(db_session, space.id, member.id)
    refreshed = client.get(
        "/api/stats",
        params={"space_id": space.id},
        headers={**headers, "If-None-Match": etag},
    )
    assert refreshed.status_code == 200
    assert refreshed.headers["etag"] != etag
    assert refreshed.json()["member_count"] == 2

    # 撤权后携带旧 ETag → 安全 404（授权先于 304）
    from app.models.space import SpaceMember

    row = (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space.id, SpaceMember.user_id == viewer.id)
        .one()
    )
    row.status = "removed"
    row.updated_at = utcnow()
    db_session.commit()
    revoked = client.get(
        "/api/stats",
        params={"space_id": space.id},
        headers={**headers, "If-None-Match": refreshed.headers["etag"]},
    )
    assert revoked.status_code == 404


def test_space_stats_unknown_or_unauthorized_space_safe_404(client, db_session) -> None:
    create_agent_fixture(db_session, name="ss-404a")
    _admin2, other = create_agent_fixture(db_session, name="ss-404b")
    headers = _login_header(client, "ss-404a")
    resp_unknown = _stats(client, headers, 999999)
    assert resp_unknown.status_code == 404
    assert resp_unknown.json()["error"]["code"] == "PERSONAL_FAMILY_VIEW_NOT_FOUND"
    resp_foreign = _stats(client, headers, other.id)
    assert resp_foreign.status_code == 404
    assert resp_foreign.json()["error"]["code"] == "PERSONAL_FAMILY_VIEW_NOT_FOUND"
    resp_bad = _stats(client, headers, 0)
    assert resp_bad.status_code == 422


def test_legacy_stats_without_space_id_unchanged(client, db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="ss-legacy")
    resp = client.get("/api/stats", headers=_login_header(client, "ss-legacy"))
    assert resp.status_code == 200
    data = resp.json()
    # 旧合同字段原样保留，未混入任何空间合同字段
    assert set(data.keys()) == {
        "total",
        "by_gender",
        "generation_histogram",
        "birthdays_this_month",
    }
    assert data["total"] == 1
    assert resp.headers.get("etag") is None


def test_space_stats_action_cards_never_leak_other_account_counts(client, db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="ss-leak")
    outsider = create_user_with_pin(db_session, "ss-leak-out", "123456")
    create_space_member(db_session, space.id, outsider.id)
    from app.services import action_cards

    action_cards.create_card(
        db_session,
        kind="household_link",
        space_id=space.id,
        recipient_account_id=outsider.account.id,
        subject_user_id=outsider.id,
        object_user_id=None,
        evidence_json={"k": "v"},
        proposed_action_json={"action": "create_shared_household"},
        reason_text="test",
    )
    db_session.commit()

    data = _stats(client, _login_header(client, "ss-leak"), space.id).json()
    assert data["pending_action_cards"] == 0
    assert db_session.query(ActionCard).count() == 1  # 行存在，但不对他账号可见计数
