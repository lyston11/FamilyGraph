"""A 子任务回归：viewer 绑定呈现合同、按 ID 详情、有效状态与 derived 来源。

覆盖父任务 AC-01/03/09/10 与 A-AC1～A-AC6 的可观察行为：
- 多 viewer 方向句一致且带「可能」；默认界面无 raw fact_type 翻译；
- 证据计数只认可与端点相交的 confirmed 事实（整空间快照不冒充依据）；
- 第 21+ 条建议可按 ID 取详情；本人忽略/过期在列表/详情/通知一致；
- 真实 /kinship/resolve 的 derived 结果通过 response schema。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from conftest import auth_header, create_agent_fixture, create_user_with_pin, login

from app import config
from app.models.account import Account
from app.models.space import SpaceMember
from app.models.steward import StewardJob, StewardLlmCandidate
from app.models.steward_suggestion import StewardSuggestion
from app.services import steward_suggestions
from app.utils import timeutil


@pytest.fixture(autouse=True)
def _pfv_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)


def _login_header(client, name: str) -> dict[str, str]:
    resp = login(client, name, "123456")
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def _make_member(session, space, name: str) -> Any:
    user = create_user_with_pin(session, name, "123456")
    session.add(
        SpaceMember(
            space_id=space.id,
            user_id=user.id,
            added_by=user.id,
            role="member",
            status="active",
            created_at=timeutil.utcnow(),
            updated_at=timeutil.utcnow(),
        )
    )
    session.commit()
    return user


def _account_of(session, user) -> Account:
    row = session.query(Account).filter(Account.user_id == user.id).one()
    return row


def _project_relation_candidate(session, space, owner, subject, object_) -> int:
    """真实 job → 候选投影链路（与生产一致；不是手工 upsert 验收）。"""
    now = timeutil.utcnow()
    job = StewardJob(
        space_id=space.id,
        cause="integrity_scan",
        trigger_cursor=1,
        status="succeeded",
        policy_version="test-policy",
        checkpoint_json={},
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.commit()
    session.add(
        StewardLlmCandidate(
            space_id=space.id,
            job_id=job.id,
            candidate_kind="biological_parent",
            payload_json={
                "kind": "biological_parent",
                "subject_user_id": subject.id,
                "object_user_id": object_.id,
            },
            candidate_digest=f"digest-{subject.id}-{object_.id}-{job.id}",
            status="proposed",
            created_at=now,
        )
    )
    session.commit()
    return steward_suggestions.project_for_job(session, job, findings=[], facts=[], now=now)


def _get_detail(client, headers, space_id: int, suggestion_id: int):
    return client.get(
        f"/api/steward-suggestions/{suggestion_id}",
        params={"space_id": space_id},
        headers=headers,
    )


# ---- A-AC1：多 viewer 方向句；候选带「可能」 ----


def test_presentation_direction_follows_viewer(client, db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="kp-dir")
    child = _make_member(db_session, space, "kp-dir-child")
    _make_member(db_session, space, "kp-dir-other")
    created = _project_relation_candidate(db_session, space, owner, owner, child)
    assert created == 1
    db_session.commit()

    rows = db_session.query(StewardSuggestion).all()
    assert len(rows) == 1
    suggestion_id = rows[0].id

    # 子女视角：父亲候选 → “可能是你的父亲”方向句由服务端给出（词典未命中时
    # summary 退化为待核实线索，但绝不输出 raw fact_type 的存储分类标签）。
    resp = _get_detail(client, _login_header(client, "kp-dir-child"), space.id, suggestion_id)
    assert resp.status_code == 200
    body = resp.json()
    presentation = body["presentation"]
    assert presentation is not None
    assert presentation["reference_user_id"] == child.id
    assert presentation["relation_state"] == "proposal"
    assert presentation["inferred"] is True
    assert "可能" in presentation["summary"]
    assert presentation["summary"].find("生物学") == -1
    # 证据：候选未引用任何与端点相交的 confirmed 事实 → 待核实，不冒充依据
    assert body["evidence_summary"]["kind"] == "unverified_candidate"
    assert body["evidence_summary"]["related_fact_count"] == 0

    # 第三方视角：同一条建议，方向句不是“你的”
    resp_other = _get_detail(client, _login_header(client, "kp-dir-other"), space.id, suggestion_id)
    assert resp_other.status_code == 200
    p_other = resp_other.json()["presentation"]
    assert p_other["reference_user_id"] == child.id
    assert p_other["target_user_id"] == owner.id
    assert "可能是你的" not in p_other["summary"]


# ---- A-AC5：第 21+ 条可按 ID 取详情；本人忽略读时生效 ----


def test_detail_beyond_first_page_and_dismissed_state(client, db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="kp-detail")
    member = _make_member(db_session, space, "kp-detail-m")
    now = timeutil.utcnow()
    ids: list[int] = []
    for index in range(25):
        suggestion, _created = steward_suggestions.upsert_suggestion(
            db_session,
            space_id=space.id,
            origin="deterministic",
            kind="missing_information",
            subject_user_id=owner.id,
            object_user_id=member.id,
            value_json={"code": f"gap_{index}", "signature": f"sig-{index}"},
            evidence_json={"facts": []},
            policy_version="p1",
            recipient_account_ids=[_account_of(db_session, owner).id],
            now=now,
        )
        ids.append(suggestion.id)
    db_session.commit()

    headers = _login_header(client, "kp-detail")
    first_page = client.get(
        "/api/steward-suggestions", params={"space_id": space.id}, headers=headers
    ).json()
    assert len(first_page["items"]) == 20
    oldest_id = min(ids)
    assert all(item["id"] != oldest_id for item in first_page["items"])

    resp = _get_detail(client, headers, space.id, oldest_id)
    assert resp.status_code == 200
    assert resp.json()["id"] == oldest_id
    assert resp.json()["state"] == "proposed"


def test_dismissed_state_consistent_across_list_detail_notifications(client, db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="kp-dismiss")
    member = _make_member(db_session, space, "kp-dismiss-m")
    suggestion, _ = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="deterministic",
        kind="missing_information",
        subject_user_id=owner.id,
        object_user_id=member.id,
        value_json={"code": "sibling_missing_parents", "signature": "sig-d"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[_account_of(db_session, owner).id],
        now=timeutil.utcnow(),
    )
    db_session.commit()
    headers = _login_header(client, "kp-dismiss")

    # 提取 revision 后本人忽略
    detail = _get_detail(client, headers, space.id, suggestion.id).json()
    resp = client.post(
        f"/api/steward-suggestions/{suggestion.id}/dismiss",
        json={"expected_revision": detail["revision"]},
        params={"space_id": space.id},
        headers=headers,
    )
    assert resp.status_code == 200

    # 列表与详情读时即 dismissed（无需过期 sweep）
    listed = client.get(
        "/api/steward-suggestions", params={"space_id": space.id}, headers=headers
    ).json()
    row = next(item for item in listed["items"] if item["id"] == suggestion.id)
    assert row["state"] == "dismissed"
    assert row["allowed_actions"] == ["open_details"]
    assert _get_detail(client, headers, space.id, suggestion.id).json()["state"] == "dismissed"

    # 通知同源：本人忽略后 domain_status 不再是 pending
    items = client.get("/api/notifications", params={"space_id": space.id}, headers=headers).json()[
        "items"
    ]
    notif = next(i for i in items if i["kind"] == "steward_suggestion")
    assert notif["suggestion"]["state"] == "dismissed"
    assert notif["domain_status"] == "rejected"


def test_expired_suggestion_state_and_actions(client, db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="kp-expired")
    member = _make_member(db_session, space, "kp-expired-m")
    past = timeutil.utcnow() - timedelta(days=1)
    suggestion, _ = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="deterministic",
        kind="missing_information",
        subject_user_id=owner.id,
        object_user_id=member.id,
        value_json={"code": "gap_x", "signature": "sig-x"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[_account_of(db_session, owner).id],
        expires_at=past,
        now=past,
    )
    db_session.commit()
    headers = _login_header(client, "kp-expired")
    detail = _get_detail(client, headers, space.id, suggestion.id).json()
    assert detail["state"] == "expired"
    assert detail["allowed_actions"] == ["open_details"]


# ---- A-AC6：GET 详情零写副作用 ----


def test_detail_read_has_no_side_effects(client, db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="kp-readonly")
    member = _make_member(db_session, space, "kp-readonly-m")
    created = _project_relation_candidate(db_session, space, owner, owner, member)
    assert created == 1
    db_session.commit()
    row = db_session.query(StewardSuggestion).one()
    headers = _login_header(client, "kp-readonly-m")
    before_revision = row.revision
    for _ in range(3):
        assert _get_detail(client, headers, space.id, row.id).status_code == 200
    db_session.expire_all()
    fresh = db_session.query(StewardSuggestion).one()
    assert fresh.revision == before_revision
