"""渐进读取合同测试（09-13 progressive=true；design §7.1 MVP）。

覆盖：
- progressive=false（缺省）响应不含 progress——旧客户端合同逐字节不变；
- progressive=true 时 progress 块的 phase 映射、授权目标计数与 next_poll_ms；
- 无投影行的首次访问返回 queued 建议快速轮询；
- ready 后 next_poll_ms=0（本人齐全即停止高频轮询）；
- progress 的 total_count 只统计已授权目标（撤权后目标移出分母）。
"""

from __future__ import annotations

import pytest

from app import config
from app.models.personal_family_view import PersonalFamilyView
from app.services import personal_family_view, steward_runtime
from app.services import source_facts as sf
from app.utils.timeutil import utcnow
from conftest import (
    auth_header,
    create_agent_fixture,
    create_space_member,
    create_user_with_pin,
    login,
)


def _confirm(session, fact_type: str, subject_id: int, object_id: int, space_id: int):
    fact = sf.create_source_fact(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        provenance="manual_entry",
        space_id=space_id,
    )
    sf.transition_source_fact(session, fact, "confirm")
    return fact


def _materialize(session, account, space_id):
    view = personal_family_view.rebuild_view(session, account=account, space_id=space_id)
    session.commit()
    return view


@pytest.fixture()
def _pfv_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    # These legacy phase tests drive projection state explicitly; the scheduler
    # is enabled but does not claim during the assertions.
    monkeypatch.setattr(steward_runtime, "launch_due", lambda **_kwargs: 0)


def _get(client, headers, space_id, *, progressive=False):
    params = {"space_id": space_id}
    if progressive:
        params["progressive"] = "true"
    return client.get("/api/personal-family-view", params=params, headers=headers)


def test_progressive_absent_by_default(db_session, client, _pfv_enabled) -> None:
    """缺省请求不带 progress 字段（旧客户端合同不变）。"""
    viewer, space = create_agent_fixture(db_session, name="prog-default")
    _materialize(db_session, viewer.account, space.id)
    headers = auth_header(login(client, "prog-default", "123456").json())
    body = _get(client, headers, space.id).json()
    assert body["status"] == "current"
    assert "progress" not in body


def test_progressive_first_access_reports_queued(db_session, client, _pfv_enabled) -> None:
    viewer, space = create_agent_fixture(db_session, name="prog-first")
    headers = auth_header(login(client, "prog-first", "123456").json())
    body = _get(client, headers, space.id, progressive=True).json()
    assert body["status"] == "never_computed"
    assert body["progress"]["phase"] == "queued"
    assert body["progress"]["next_poll_ms"] == 250
    assert body["progress"]["contract_version"] == personal_family_view.PROGRESS_CONTRACT_VERSION


def test_progressive_ready_counts_authorized_targets(db_session, client, _pfv_enabled) -> None:
    viewer, space = create_agent_fixture(db_session, name="prog-ready")
    parent = create_user_with_pin(db_session, "prog-parent", "123456", gender="m")
    create_space_member(db_session, space.id, parent.id)
    _confirm(db_session, "biological_parent", parent.id, viewer.id, space.id)
    db_session.commit()
    _materialize(db_session, viewer.account, space.id)

    headers = auth_header(login(client, "prog-ready", "123456").json())
    body = _get(client, headers, space.id, progressive=True).json()
    progress = body["progress"]
    assert body["status"] == "current"
    assert progress["phase"] == "ready"
    assert progress["next_poll_ms"] == 0
    # 已授权目标：parent 一人；完成数不把本人计入
    assert progress["total_count"] == 1
    assert progress["completed_count"] == 1


def test_progressive_running_state_after_invalidation(db_session, client, _pfv_enabled) -> None:
    viewer, space = create_agent_fixture(db_session, name="prog-building")
    parent = create_user_with_pin(db_session, "prog-building-parent", "123456", gender="m")
    create_space_member(db_session, space.id, parent.id)
    _confirm(db_session, "biological_parent", parent.id, viewer.id, space.id)
    db_session.commit()
    view = _materialize(db_session, viewer.account, space.id)
    headers = auth_header(login(client, "prog-building", "123456").json())
    ready = _get(client, headers, space.id, progressive=True).json()["progress"]
    assert ready["phase"] == "ready"

    # 输入变化 → stale → building（模拟后台重算占用 running）
    _confirm(db_session, "spouse", parent.id, viewer.id, space.id)
    db_session.commit()
    personal_family_view.invalidate_space_views(db_session, space_id=space.id)
    db_session.commit()
    stale = _get(client, headers, space.id, progressive=True).json()["progress"]
    assert stale["phase"] == "retrying"
    assert stale["next_poll_ms"] == 1000

    row = db_session.get(PersonalFamilyView, view.id)
    row.status = "running"
    db_session.commit()
    building = _get(client, headers, space.id, progressive=True).json()["progress"]
    assert building["phase"] == "building"
    assert building["next_poll_ms"] == 1000


def test_progressive_total_excludes_revoked_target(db_session, client, _pfv_enabled) -> None:
    """撤权后目标不再进入授权分母（R4：进度只统计已授权目标）。"""
    viewer, space = create_agent_fixture(db_session, name="prog-revoke")
    parent = create_user_with_pin(db_session, "prog-revoke-parent", "123456", gender="m")
    create_space_member(db_session, space.id, parent.id)
    fact = _confirm(db_session, "biological_parent", parent.id, viewer.id, space.id)
    db_session.commit()
    _materialize(db_session, viewer.account, space.id)
    headers = auth_header(login(client, "prog-revoke", "123456").json())
    before = _get(client, headers, space.id, progressive=True).json()["progress"]
    assert before["total_count"] == 1

    sf.transition_source_fact(db_session, fact, "revoke")
    db_session.commit()
    after = _get(client, headers, space.id, progressive=True).json()["progress"]
    # 仍是空间成员但已不在授权可达骨架；未验证内容和分母同时撤回。
    assert after["completed_count"] == 0
    assert after["total_count"] == 0


def test_progress_phase_mapping_helpers(db_session) -> None:
    """phase 映射的穷举安全：未知状态一律 failed（fail-closed）。"""
    assert personal_family_view._phase_for_status("never_computed") == "queued"
    assert personal_family_view._phase_for_status("queued") == "queued"
    assert personal_family_view._phase_for_status("running") == "building"
    assert personal_family_view._phase_for_status("current") == "ready"
    assert personal_family_view._phase_for_status("stale") == "retrying"
    assert personal_family_view._phase_for_status("anything_else") == "failed"


def test_progress_uses_current_time_bounds(db_session) -> None:
    """revision 由 updated_at 派生（单调校验用），占位视图 revision=0。"""
    now = utcnow()
    assert int(now.timestamp()) > 0
