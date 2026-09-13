"""管家推测层闭环测试（任务 09-13-steward-inferred-tree-layer；PRD AC）。

覆盖：平台/空间开关真值表（fail-closed）；候选反连接幂等；confirmed 三元组
去重；驳回同证据冷却；PFV payload 推测区块与 inferred_path 节点；确认权限
分层（非当事人 202 提案 / 当事人 200 转正）；revision CAS 409；驳回→撤销。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app import config
from app.models.agent_provider import AgentSpaceProviderSetting
from app.models.relationship_facts import SourceFact
from app.models.space import SpaceMember
from app.models.steward import StewardJob, StewardLlmCandidate
from app.models.steward_inferred import StewardInferredEdge
from app.services import personal_family_view, steward_inferred
from app.services import source_facts as sf
from app.utils import timeutil
from conftest import auth_header, create_agent_fixture, create_user_with_pin, login


@pytest.fixture(autouse=True)
def _pfv_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)


def _platform_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "STEWARD_INFERRED_TREE_ENABLED", True)


def _space_flag_on(session, space_id: int) -> None:
    session.add(
        AgentSpaceProviderSetting(
            space_id=space_id,
            agent_kind="steward",
            provider_id=None,
            model=None,
            enabled=True,
            inferred_tree=True,
        )
    )
    session.commit()


def _member(session, space, name: str, gender: str):
    user = create_user_with_pin(session, name, "123456", gender=gender)
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


def _make_job(session, space) -> StewardJob:
    job = StewardJob(
        space_id=space.id,
        cause="integrity_scan",
        trigger_cursor=1,
        status="succeeded",
        policy_version="test-policy",
        checkpoint_json={},
        created_at=timeutil.utcnow(),
        updated_at=timeutil.utcnow(),
    )
    session.add(job)
    session.commit()
    return job


def _candidate(
    session,
    job: StewardJob,
    *,
    kind: str,
    subject_id: int,
    object_id: int,
) -> StewardLlmCandidate:
    row = StewardLlmCandidate(
        space_id=job.space_id,
        job_id=job.id,
        candidate_kind=kind,
        payload_json={"kind": kind, "subject_user_id": subject_id, "object_user_id": object_id},
        candidate_digest=f"d-{job.id}-{kind}-{subject_id}-{object_id}",
        status="proposed",
        created_at=timeutil.utcnow(),
    )
    session.add(row)
    session.commit()
    return row


def _edges(session, space_id: int) -> list[StewardInferredEdge]:
    return list(
        session.scalars(
            select(StewardInferredEdge).where(StewardInferredEdge.space_id == space_id)
        ).all()
    )


def _confirm_fact(session, fact_type: str, subject_id: int, object_id: int, space_id: int) -> None:
    fact = sf.create_source_fact(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        provenance="manual_entry",
        space_id=space_id,
    )
    sf.transition_source_fact(session, fact, "confirm")


# ---- 开关真值表与投影 ----


def test_projection_requires_platform_and_space_flags(db_session, monkeypatch) -> None:
    _account, space = create_agent_fixture(db_session, name="inf-flags")
    a = _member(db_session, space, "inf-a", "m")
    b = _member(db_session, space, "inf-b", "f")
    job = _make_job(db_session, space)
    _candidate(db_session, job, kind="spouse", subject_id=a.id, object_id=b.id)

    # 双关 → 0
    assert steward_inferred.project_for_job(db_session, job, facts=[], visible={a.id, b.id}) == 0
    # 仅空间开 → 0（平台缺省关，fail-closed）
    _space_flag_on(db_session, space.id)
    assert steward_inferred.project_for_job(db_session, job, facts=[], visible={a.id, b.id}) == 0
    assert _edges(db_session, space.id) == []
    # 双开 → 投影 1 条
    _platform_on(monkeypatch)
    created = steward_inferred.project_for_job(db_session, job, facts=[], visible={a.id, b.id})
    assert created == 1
    edges = _edges(db_session, space.id)
    assert edges[0].status == "proposed"
    assert edges[0].relation_kind == "spouse"


def test_projection_is_idempotent_and_skips_confirmed_triples(db_session, monkeypatch) -> None:
    _platform_on(monkeypatch)
    _account, space = create_agent_fixture(db_session, name="inf-idem")
    _space_flag_on(db_session, space.id)
    a = _member(db_session, space, "inf-c", "m")
    b = _member(db_session, space, "inf-d", "f")
    job = _make_job(db_session, space)
    _candidate(db_session, job, kind="spouse", subject_id=a.id, object_id=b.id)
    _candidate(db_session, job, kind="spouse", subject_id=b.id, object_id=a.id)  # 对称反方向

    assert steward_inferred.project_for_job(db_session, job, facts=[], visible={a.id, b.id}) == 1
    # 反连接幂等：同候选不再产生第二行
    assert steward_inferred.project_for_job(db_session, job, facts=[], visible={a.id, b.id}) == 0
    assert len(_edges(db_session, space.id)) == 1

    # confirmed 三元组去重：新候选与已确认事实同三元组 → 跳过
    _confirm_fact(db_session, "spouse", a.id, b.id, space.id)
    job2 = _make_job(db_session, space)
    _candidate(db_session, job2, kind="spouse", subject_id=a.id, object_id=b.id)
    facts = list(db_session.scalars(select(SourceFact)).all())
    assert (
        steward_inferred.project_for_job(db_session, job2, facts=facts, visible={a.id, b.id}) == 0
    )


def test_dismissed_evidence_cooldown_blocks_reprojection(db_session, monkeypatch) -> None:
    _platform_on(monkeypatch)
    _account, space = create_agent_fixture(db_session, name="inf-cool")
    _space_flag_on(db_session, space.id)
    a = _member(db_session, space, "inf-e", "m")
    b = _member(db_session, space, "inf-f", "f")
    job = _make_job(db_session, space)
    _candidate(db_session, job, kind="spouse", subject_id=a.id, object_id=b.id)
    assert steward_inferred.project_for_job(db_session, job, facts=[], visible={a.id, b.id}) == 1

    edge = _edges(db_session, space.id)[0]
    dismiss_edge = steward_inferred.dismiss_edge
    result = dismiss_edge(
        db_session,
        account=_account.account,
        space_id=space.id,
        edge_id=edge.id,
        expected_revision=edge.revision,
    )
    assert result["status"] == "rejected"

    # 同证据不再重投影
    assert steward_inferred.project_for_job(db_session, job, facts=[], visible={a.id, b.id}) == 0
    # 撤销驳回 → 重新 proposed（dismiss 已 bump revision；重读取当前值）
    db_session.expire(edge)
    reinstated = steward_inferred.reinstate_edge(
        db_session,
        account=_account.account,
        space_id=space.id,
        edge_id=edge.id,
        expected_revision=edge.revision,
    )
    assert reinstated["status"] == "proposed"


# ---- PFV payload 推测区块 ----


def test_pfv_payload_contains_inferred_edges(db_session, monkeypatch) -> None:
    _platform_on(monkeypatch)
    _account, space = create_agent_fixture(db_session, name="inf-pfv")
    _space_flag_on(db_session, space.id)
    viewer = _account
    a = _member(db_session, space, "inf-g", "m")
    b = _member(db_session, space, "inf-h", "f")
    _confirm_fact(db_session, "biological_parent", viewer.id, a.id, space.id)

    job = _make_job(db_session, space)
    _candidate(db_session, job, kind="spouse", subject_id=a.id, object_id=b.id)
    assert (
        steward_inferred.project_for_job(db_session, job, facts=[], visible={viewer.id, a.id, b.id})
        == 1
    )

    # 开关关闭 → payload 无推测区块
    monkeypatch.setattr(config, "STEWARD_INFERRED_TREE_ENABLED", False)
    personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
    payload = personal_family_view.view_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    assert payload["inferred_edges"] == []

    # 开关开启 → 推测边出现，b 以 inferred_path 上树
    monkeypatch.setattr(config, "STEWARD_INFERRED_TREE_ENABLED", True)
    personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
    payload = personal_family_view.view_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    inferred = payload["inferred_edges"]
    assert len(inferred) == 1
    assert inferred[0]["relation_kind"] == "spouse"
    assert inferred[0]["subject_user_id"] == a.id
    assert inferred[0]["object_user_id"] == b.id
    assert inferred[0]["term"] is not None  # 确定性单跳称谓（丈夫/妻子）
    node_ids = {node["user_id"] for node in payload["nodes"]}
    assert b.id in node_ids
    b_node = next(node for node in payload["nodes"] if node["user_id"] == b.id)
    assert b_node["inclusion_reason_code"] == "inferred_path"


# ---- 确认权限分层（API 路径）----


def _login_header(client, name: str) -> dict[str, str]:
    resp = login(client, name, "123456")
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def test_confirm_entitlement_matrix(db_session, monkeypatch, client) -> None:
    _platform_on(monkeypatch)
    _account, space = create_agent_fixture(db_session, name="inf-confirm")
    _space_flag_on(db_session, space.id)
    a = _member(db_session, space, "inf-owner-a", "m")
    b = _member(db_session, space, "inf-owner-b", "f")
    job = _make_job(db_session, space)
    _candidate(db_session, job, kind="spouse", subject_id=a.id, object_id=b.id)
    assert steward_inferred.project_for_job(db_session, job, facts=[], visible={a.id, b.id}) == 1
    edge = _edges(db_session, space.id)[0]

    # 释放 db_session 事务，避免与 TestClient 的写连接争锁
    db_session.commit()

    # owner（非当事人）确认 → 202 提案，推测边保持 proposed
    owner_header = _login_header(client, "inf-confirm")
    resp = client.post(
        f"/api/steward-inferred-edges/{edge.id}/confirm",
        json={"expected_revision": edge.revision},
        params={"space_id": space.id},
        headers=owner_header,
    )
    assert resp.status_code == 202, resp.text
    body: dict[str, Any] = resp.json()
    assert body["linked_proposal"] is not None
    assert body["edge"]["status"] == "proposed"
    assert len(body["pending_confirmations"]) >= 1

    # 当事人 a 确认 → 200 转正，confirmed SourceFact 入库
    db_session.commit()
    db_session.expire_all()
    a_header = _login_header(client, "inf-owner-a")
    resp = client.post(
        f"/api/steward-inferred-edges/{edge.id}/confirm",
        json={"expected_revision": edge.revision},
        params={"space_id": space.id},
        headers=a_header,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edge"]["status"] == "confirmed"
    assert body["linked_proposal"]["state"] == "confirmed"
    fact = db_session.get(SourceFact, body["linked_proposal"]["source_fact_id"])
    assert fact is not None and fact.state == "confirmed"


def test_confirm_revision_conflict(db_session, monkeypatch, client) -> None:
    _platform_on(monkeypatch)
    _account, space = create_agent_fixture(db_session, name="inf-cas")
    _space_flag_on(db_session, space.id)
    a = _member(db_session, space, "inf-cas-a", "m")
    b = _member(db_session, space, "inf-cas-b", "f")
    job = _make_job(db_session, space)
    _candidate(db_session, job, kind="spouse", subject_id=a.id, object_id=b.id)
    assert steward_inferred.project_for_job(db_session, job, facts=[], visible={a.id, b.id}) == 1
    edge = _edges(db_session, space.id)[0]

    db_session.commit()
    a_header = _login_header(client, "inf-cas-a")
    resp = client.post(
        f"/api/steward-inferred-edges/{edge.id}/confirm",
        json={"expected_revision": edge.revision + 5},
        params={"space_id": space.id},
        headers=a_header,
    )
    assert resp.status_code == 409
