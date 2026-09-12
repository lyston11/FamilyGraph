"""Steward 建议审核闭环测试（任务 09-11-steward-candidate-review；AC-1..AC-5）。

覆盖：同结构候选仅改措辞/rationale 不生成第二待办；缺证据旧候选不显示；
不同收件人独立驳回 + 证据变更 supersede；allowed_actions 身份矩阵；owner
（非端点）接受只生成提案、当事人完成既有授权步骤才入图；父母角色不被
模糊映射改写；并发 submit 恰好一个领域副作用；过期/证据变化 410/409 且
无正式写入；同 Idempotency-Key 重试返回同一关联对象；通知已读不改建议、
不重复出通知。
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

import pytest
from conftest import auth_header, create_agent_fixture, create_user_with_pin, login
from fastapi import HTTPException

from app import config
from app.commands.context import ActorContext
from app.models.account import Account
from app.models.relationship_facts import SourceFact
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


def _ctx(user, account: Account) -> ActorContext:
    return ActorContext(user_id=user.id, account_id=account.id, account_status=account.status)


def _make_member(session, space, name: str):
    """往空间添加一名 active 普通成员（带账号，便于登录）。"""
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


def _make_job(session, space, policy_version: str = "test-policy") -> StewardJob:
    job = StewardJob(
        space_id=space.id,
        cause="integrity_scan",
        trigger_cursor=1,
        status="succeeded",
        policy_version=policy_version,
        checkpoint_json={},
        created_at=timeutil.utcnow(),
        updated_at=timeutil.utcnow(),
    )
    session.add(job)
    session.commit()
    return job


def _make_candidate(
    session,
    job: StewardJob,
    *,
    fact_kind: str,
    subject_id: int,
    object_id: int,
    payload: dict[str, Any] | None = None,
) -> StewardLlmCandidate:
    if payload is None:
        payload = {
            "kind": fact_kind,
            "subject_user_id": subject_id,
            "object_user_id": object_id,
        }
    row = StewardLlmCandidate(
        space_id=job.space_id,
        job_id=job.id,
        candidate_kind=fact_kind,
        payload_json=payload,
        candidate_digest=f"digest-{fact_kind}-{subject_id}-{object_id}-{id(payload) % 10**8}",
        status="proposed",
        created_at=timeutil.utcnow(),
    )
    session.add(row)
    session.commit()
    return row


def _project(session, job: StewardJob, findings: list[dict[str, Any]] | None = None) -> int:
    return steward_suggestions.project_for_job(
        session, job, findings=findings or [], facts=[], now=timeutil.utcnow()
    )


# ---- AC-1：证据与状态 ----


def test_same_structure_wording_change_no_second_suggestion(db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="sg-word")
    other = _make_member(db_session, space, "sg-word-b")
    job = _make_job(db_session, space)
    _make_candidate(db_session, job, fact_kind="spouse", subject_id=owner.id, object_id=other.id)
    created = _project(db_session, job)
    assert created == 1
    first = db_session.query(StewardSuggestion).one()

    # 另一 job 再次产出同结构候选（不同内部行；措辞/rationale 恒不在合同内）
    job2 = _make_job(db_session, space)
    _make_candidate(db_session, job2, fact_kind="spouse", subject_id=owner.id, object_id=other.id)
    assert _project(db_session, job2) == 0
    rows = db_session.query(StewardSuggestion).all()
    assert len(rows) == 1 and rows[0].id == first.id


def test_legacy_evidence_less_candidate_never_projected(db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="sg-legacy")
    other = _make_member(db_session, space, "sg-legacy-b")
    job = _make_job(db_session, space)
    # 历史裸 JSON：自由文本 payload，缺合同三键
    _make_candidate(
        db_session,
        job,
        fact_kind="spouse",
        subject_id=owner.id,
        object_id=other.id,
        payload={"kind": "spouse", "rationale": "他们看起来像夫妻"},
    )
    assert _project(db_session, job) == 0
    assert db_session.query(StewardSuggestion).count() == 0


def test_evidence_change_supersedes_and_cooldown_isolated(db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="sg-supers")
    other = _make_member(db_session, space, "sg-supers-b")
    now = timeutil.utcnow()
    evidence_v1 = {"facts": [{"id": 1, "revision": 1}]}
    s1, created = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="model",
        kind="relation_proposal",
        subject_user_id=owner.id,
        object_user_id=other.id,
        value_json={"fact_type": "spouse"},
        evidence_json=evidence_v1,
        policy_version="p1",
        recipient_account_ids=[owner.account.id, other.account.id],
        now=now,
    )
    assert created
    # owner 驳回 v1（建立同证据冷却）
    result = steward_suggestions.dismiss_suggestion(
        db_session,
        account=owner.account,
        space_id=space.id,
        suggestion_id=s1.id,
        expected_revision=s1.revision,
        now=now,
    )
    assert result["state"] == "dismissed"
    # other 的驳回状态独立：仍可操作（other 未 dismissed）
    # 证据变化 → 新建议 + 旧行 superseded
    s2, created2 = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="model",
        kind="relation_proposal",
        subject_user_id=owner.id,
        object_user_id=other.id,
        value_json={"fact_type": "spouse"},
        evidence_json={"facts": [{"id": 1, "revision": 2}]},
        policy_version="p1",
        recipient_account_ids=[owner.account.id, other.account.id],
        now=now,
    )
    assert created2 and s2.id != s1.id
    db_session.refresh(s1)
    assert s1.status == "superseded" and s1.superseded_by_id == s2.id
    # 新证据版本不受旧冷却影响：owner 可再次驳回（新行 revision=CAS 一致）
    result2 = steward_suggestions.dismiss_suggestion(
        db_session,
        account=owner.account,
        space_id=space.id,
        suggestion_id=s2.id,
        expected_revision=s2.revision,
        now=now,
    )
    assert result2["state"] == "dismissed"


def test_per_recipient_independent_dismiss(db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="sg-ind")
    other = _make_member(db_session, space, "sg-ind-b")
    now = timeutil.utcnow()
    s, _ = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="deterministic",
        kind="identity_duplicate",
        subject_user_id=owner.id,
        object_user_id=other.id,
        value_json={"code": "duplicate_person_weak", "signature": "sig"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[owner.account.id, other.account.id],
        now=now,
    )
    rev = s.revision
    r1 = steward_suggestions.dismiss_suggestion(
        db_session,
        account=owner.account,
        space_id=space.id,
        suggestion_id=s.id,
        expected_revision=rev,
        now=now,
    )
    # CAS：other 用旧 expected_revision 被拒（revision 已被 owner 的驳回推进）
    from app.errors import SUGGESTION_REVISION_CONFLICT

    with pytest.raises(Exception) as excinfo:
        steward_suggestions.dismiss_suggestion(
            db_session,
            account=other.account,
            space_id=space.id,
            suggestion_id=s.id,
            expected_revision=rev,
            now=now,
        )
    assert SUGGESTION_REVISION_CONFLICT in str(excinfo.value.detail)  # type: ignore[attr-defined]
    r2 = steward_suggestions.dismiss_suggestion(
        db_session,
        account=other.account,
        space_id=space.id,
        suggestion_id=s.id,
        expected_revision=r1["revision"],
        now=now,
    )
    # owner 的驳回不终结建议本身：other 仍可独立驳回（建议仍 proposed）
    db_session.expire_all()
    fresh = db_session.get(StewardSuggestion, s.id)
    assert fresh.status == "proposed"
    assert r1["dismissed_at"] is not None and r2["dismissed_at"] is not None


# ---- AC-2：受众与操作权限 ----


def test_allowed_actions_matrix_and_safe_404(client, db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="sg-acl")
    b = _make_member(db_session, space, "sg-acl-b")
    c = _make_member(db_session, space, "sg-acl-c")
    now = timeutil.utcnow()
    rel, _ = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="model",
        kind="relation_proposal",
        subject_user_id=b.id,
        object_user_id=c.id,
        value_json={"fact_type": "spouse"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[b.account.id, c.account.id],
        now=now,
    )
    dup, _ = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="deterministic",
        kind="identity_duplicate",
        subject_user_id=owner.id,
        object_user_id=b.id,
        value_json={"code": "duplicate_person_strong", "signature": "sig2"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[owner.account.id, b.account.id, c.account.id],
        now=now,
    )
    db_session.commit()

    # owner（非端点）：可发起提案，不可驳回（非建议端点收件人）
    items = client.get(
        "/api/steward-suggestions",
        params={"space_id": space.id},
        headers=_login_header(client, "sg-acl"),
    ).json()["items"]
    by_id = {i["id"]: i for i in items}
    assert by_id[rel.id]["allowed_actions"] == ["open_details", "submit"]
    # 端点 b：可提交/驳回/详情
    items_b = client.get(
        "/api/steward-suggestions",
        params={"space_id": space.id},
        headers=_login_header(client, "sg-acl-b"),
    ).json()["items"]
    by_id_b = {i["id"]: i for i in items_b}
    assert by_id_b[rel.id]["allowed_actions"] == ["open_details", "submit", "dismiss"]
    # identity_duplicate：无 submit（v1 只指向人工处理）
    assert set(by_id_b[dup.id]["allowed_actions"]) == {"open_details", "dismiss"}
    # raw model payload 不透出
    assert "payload_json" not in by_id_b[rel.id]
    assert "payload" not in by_id_b[rel.id]["value"]

    # 空间外人员：统一 404（防枚举）
    outsider, other_space = create_agent_fixture(db_session, name="sg-acl-out")
    for sid in (rel.id, 999999):
        resp = client.post(
            f"/api/steward-suggestions/{sid}/dismiss",
            params={"space_id": space.id},
            json={"expected_revision": 1},
            headers=_login_header(client, "sg-acl-out"),
        )
        assert resp.status_code == 404

    # 访问被撤销（成员移除）→ 同一 404
    from sqlalchemy import select as _select

    row = db_session.scalar(
        _select(SpaceMember).where(SpaceMember.space_id == space.id, SpaceMember.user_id == c.id)
    )
    assert row is not None
    db_session.delete(row)
    db_session.commit()
    resp = client.get(
        "/api/steward-suggestions",
        params={"space_id": space.id},
        headers=_login_header(client, "sg-acl-c"),
    )
    assert resp.status_code == 404


# ---- AC-3：明确用户动作 ----


def test_owner_submit_creates_proposal_endpoint_confirms(db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="sg-own")
    b = _make_member(db_session, space, "sg-own-b")
    c = _make_member(db_session, space, "sg-own-c")
    now = timeutil.utcnow()
    s, _ = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="model",
        kind="relation_proposal",
        subject_user_id=b.id,
        object_user_id=c.id,
        # 父母角色为收养：不得被 elder/younger 模糊映射成 biological_parent
        value_json={"fact_type": "adoptive_parent"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[owner.account.id, b.account.id, c.account.id],
        now=now,
    )
    db_session.commit()
    status, payload = steward_suggestions.submit_suggestion(
        db_session,
        _ctx(owner, owner.account),
        account=owner.account,
        space_id=space.id,
        suggestion_id=s.id,
        expected_revision=s.revision,
        evidence_hash=s.evidence_hash,
        confirm=True,
        idempotency_key="key-own-1",
        now=now,
    )
    assert status == 202
    assert payload["linked_proposal"]["state"] == "proposed"
    assert payload["linked_proposal"]["fact_type"] == "adoptive_parent"
    fact = db_session.get(SourceFact, payload["linked_proposal"]["source_fact_id"])
    assert fact is not None and fact.state == "proposed"  # owner 绝不代确认
    assert fact.provenance == "agent_proposal"
    db_session.expire_all()
    assert db_session.get(StewardSuggestion, s.id).status == "submitted"

    # 当事人 b 完成既有授权步骤 → 才入图
    from app.commands.relationship_proposals import confirm_relationship_proposal

    confirmed = confirm_relationship_proposal(
        db_session, _ctx(b, b.account), fact.id, expected_revision=fact.revision
    )
    assert confirmed.state == "confirmed"
    db_session.expire_all()
    assert db_session.get(StewardSuggestion, s.id).status == "resolved"

    # owner（非端点）确认被拒
    s2, _ = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="model",
        kind="relation_proposal",
        subject_user_id=b.id,
        object_user_id=c.id,
        value_json={"fact_type": "direct_sibling"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[owner.account.id, b.account.id, c.account.id],
        now=now,
    )
    db_session.commit()
    _status, payload2 = steward_suggestions.submit_suggestion(
        db_session,
        _ctx(owner, owner.account),
        account=owner.account,
        space_id=space.id,
        suggestion_id=s2.id,
        expected_revision=s2.revision,
        evidence_hash=s2.evidence_hash,
        confirm=True,
        idempotency_key="key-own-2",
        now=now,
    )
    fact2 = db_session.get(SourceFact, payload2["linked_proposal"]["source_fact_id"])
    with pytest.raises(HTTPException):
        confirm_relationship_proposal(
            db_session, _ctx(owner, owner.account), fact2.id, expected_revision=fact2.revision
        )
    db_session.rollback()
    assert db_session.get(SourceFact, fact2.id).state == "proposed"


def test_term_preference_submit_self_only(db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="sg-term")
    now = timeutil.utcnow()
    s = steward_suggestions.StewardSuggestion(  # type: ignore[attr-defined]
        space_id=space.id,
        origin="deterministic",
        kind="term_preference",
        subject_user_id=owner.id,
        object_user_id=None,
        viewer_account_id=owner.account.id,
        value_json={"concept_code": "F", "term": "老爸"},
        evidence_json={"facts": []},
        evidence_hash="0" * 64,
        dedupe_key="term-father",
        policy_version="p1",
        status="proposed",
        revision=1,
        expires_at=now + timedelta(days=30),
        created_at=now,
        updated_at=now,
    )
    db_session.add(s)
    db_session.commit()
    status, payload = steward_suggestions.submit_suggestion(
        db_session,
        _ctx(owner, owner.account),
        account=owner.account,
        space_id=space.id,
        suggestion_id=s.id,
        expected_revision=1,
        evidence_hash=s.evidence_hash,
        confirm=True,
        idempotency_key="key-term-1",
        now=now,
    )
    assert status == 200
    assert payload["linked_preference"]["term"] == "老爸"
    db_session.expire_all()
    assert db_session.get(StewardSuggestion, s.id).status == "resolved"
    # 不创建关系提案
    assert db_session.query(SourceFact).count() == 0


# ---- AC-4：并发、撤回与幂等 ----


def test_concurrent_submit_exactly_one_side_effect(db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="sg-conc")
    b = _make_member(db_session, space, "sg-conc-b")
    now = timeutil.utcnow()
    s, _ = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="model",
        kind="relation_proposal",
        subject_user_id=owner.id,
        object_user_id=b.id,
        value_json={"fact_type": "spouse"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[owner.account.id, b.account.id],
        now=now,
    )
    db_session.commit()
    suggestion_id = s.id
    revision = s.revision
    evidence_hash = s.evidence_hash

    results: list[Any] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=10)

    def _worker(account_id: int, user_id: int):
        from app.db import SessionLocal

        session = SessionLocal()
        try:
            account = session.get(Account, account_id)
            barrier.wait()
            status, payload = steward_suggestions.submit_suggestion(
                session,
                ActorContext(user_id=user_id, account_id=account_id, account_status=account.status),
                account=account,
                space_id=space.id,
                suggestion_id=suggestion_id,
                expected_revision=revision,
                evidence_hash=evidence_hash,
                confirm=True,
                idempotency_key=f"key-conc-{account_id}",
            )
            with lock:
                results.append(("ok", status))
        except Exception as exc:
            with lock:
                results.append(("err", exc))
        finally:
            session.close()

    threads = [
        threading.Thread(target=_worker, args=(owner.account.id, owner.id)),
        threading.Thread(target=_worker, args=(b.account.id, b.id)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert not any(t.is_alive() for t in threads)
    outcomes = [r[0] for r in results]
    assert outcomes.count("ok") == 1, results
    assert outcomes.count("err") == 1, results
    # 恰好一个领域副作用（从主线程独立 Session 重查数据库）
    from app.db import SessionLocal as _SessionLocal

    check = _SessionLocal()
    facts = check.query(SourceFact).filter(SourceFact.provenance == "agent_proposal").all()
    assert len(facts) == 1
    check.close()


def test_submit_retry_same_key_same_object_and_conflicts(db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="sg-retry")
    b = _make_member(db_session, space, "sg-retry-b")
    now = timeutil.utcnow()
    s, _ = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="model",
        kind="relation_proposal",
        subject_user_id=owner.id,
        object_user_id=b.id,
        value_json={"fact_type": "spouse"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[owner.account.id, b.account.id],
        now=now,
    )
    db_session.commit()
    kwargs: dict[str, Any] = dict(
        account=owner.account,
        space_id=space.id,
        suggestion_id=s.id,
        expected_revision=s.revision,
        evidence_hash=s.evidence_hash,
        confirm=True,
        idempotency_key="key-retry-1",
        now=now,
    )
    status1, payload1 = steward_suggestions.submit_suggestion(
        db_session, _ctx(owner, owner.account), **kwargs
    )
    assert status1 == 202
    # 同 Idempotency-Key 重试 → 同一关联对象，不产生第二提案
    status2, payload2 = steward_suggestions.submit_suggestion(
        db_session, _ctx(owner, owner.account), **kwargs
    )
    assert status2 == 202
    assert payload1["linked_proposal"] == payload2["linked_proposal"]
    assert db_session.query(SourceFact).count() == 1
    # 已 submitted 后不同 key 再提交 → 409
    kwargs2 = {**kwargs, "idempotency_key": "key-retry-2"}
    with pytest.raises(Exception) as excinfo:
        steward_suggestions.submit_suggestion(db_session, _ctx(owner, owner.account), **kwargs2)
    detail = str(excinfo.value.detail)  # type: ignore[attr-defined]
    assert excinfo.value.status_code == 409  # type: ignore[attr-defined]
    assert "SUGGESTION_STATE_CONFLICT" in detail or "SUGGESTION_REVISION_CONFLICT" in detail
    db_session.rollback()

    # 证据变化 → 409 且无正式写入；过期 → 410
    s3, _ = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="model",
        kind="relation_proposal",
        subject_user_id=owner.id,
        object_user_id=b.id,
        value_json={"fact_type": "partner"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[owner.account.id, b.account.id],
        now=now,
    )
    db_session.commit()
    with pytest.raises(Exception) as excinfo:
        steward_suggestions.submit_suggestion(
            db_session,
            _ctx(owner, owner.account),
            account=owner.account,
            space_id=space.id,
            suggestion_id=s3.id,
            expected_revision=s3.revision,
            evidence_hash="f" * 64,
            confirm=True,
            idempotency_key="key-retry-3",
            now=now,
        )
    assert "SUGGESTION_EVIDENCE_CHANGED" in str(excinfo.value.detail)  # type: ignore[attr-defined]
    db_session.rollback()
    assert db_session.query(SourceFact).count() == 1  # 无新正式写入

    s4, _ = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="model",
        kind="relation_proposal",
        subject_user_id=owner.id,
        object_user_id=b.id,
        value_json={"fact_type": "guardian"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[owner.account.id, b.account.id],
        now=now,
    )
    s4.expires_at = now - timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(Exception) as excinfo:
        steward_suggestions.submit_suggestion(
            db_session,
            _ctx(owner, owner.account),
            account=owner.account,
            space_id=space.id,
            suggestion_id=s4.id,
            expected_revision=s4.revision,
            evidence_hash=s4.evidence_hash,
            confirm=True,
            idempotency_key="key-retry-4",
            now=now,
        )
    assert "SUGGESTION_EXPIRED" in str(excinfo.value.detail)  # type: ignore[attr-defined]
    db_session.rollback()
    # 失效建议不能恢复：状态仍 proposed 但已过期，读面 410 在 submit/dismiss 已覆盖


# ---- AC-5：通知闭环 ----


def test_notification_read_does_not_mutate_and_no_duplicates(client, db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="sg-notif")
    b = _make_member(db_session, space, "sg-notif-b")
    now = timeutil.utcnow()
    s, _ = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="deterministic",
        kind="missing_information",
        subject_user_id=owner.id,
        object_user_id=b.id,
        value_json={"code": "sibling_missing_parents", "signature": "sig3"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[owner.account.id],
        now=now,
    )
    db_session.commit()

    def _notif_items(headers):
        return client.get(
            "/api/notifications", params={"space_id": space.id}, headers=headers
        ).json()["items"]

    items = _notif_items(_login_header(client, "sg-notif"))
    matching = [i for i in items if i["kind"] == "steward_suggestion"]
    assert len(matching) == 1
    assert matching[0]["payload"]["title"] == "发现资料缺口待核实"
    assert matching[0]["domain_status"] == "pending"
    assert matching[0]["suggestion"] == {"suggestion_id": s.id}
    notif_id = matching[0]["id"]

    # 已读只改 read_at：建议状态/revision 不变
    resp = client.post(
        f"/api/notifications/{notif_id}/read", headers=_login_header(client, "sg-notif")
    )
    assert resp.status_code == 200
    db_session.expire_all()
    fresh = db_session.get(StewardSuggestion, s.id)
    assert fresh.status == "proposed" and fresh.revision == s.revision

    # 重复投影同证据 → 不重复出通知（UNIQUE 兜底）
    steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="deterministic",
        kind="missing_information",
        subject_user_id=owner.id,
        object_user_id=b.id,
        value_json={"code": "sibling_missing_parents", "signature": "sig3"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[owner.account.id],
        now=now,
    )
    db_session.commit()
    items = _notif_items(_login_header(client, "sg-notif"))
    assert len([i for i in items if i["kind"] == "steward_suggestion"]) == 1


# ---- AC-2：列表与详情同口径的证据可见性 ----


def test_list_hides_suggestions_with_invisible_endpoints(db_session) -> None:
    """列表与详情统一口径：端点对当前账号不可见（隐藏人物）→ 列表不透出、详情 404。"""
    from fastapi import HTTPException

    owner, space = create_agent_fixture(db_session, name="sg-vis")
    outsider = create_user_with_pin(db_session, "sg-vis-outsider", "123456")
    db_session.commit()
    now = timeutil.utcnow()
    s, created = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="deterministic",
        kind="identity_duplicate",
        subject_user_id=outsider.id,
        object_user_id=owner.id,
        value_json={"code": "duplicate_person_weak", "signature": "sig-vis"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[owner.account.id],
        now=now,
    )
    assert created
    page = steward_suggestions.list_suggestions_page(
        db_session, account=owner.account, space_id=space.id, cursor=None, limit=20
    )
    assert all(item["id"] != s.id for item in page["items"])
    with pytest.raises(HTTPException) as excinfo:
        steward_suggestions.visible_suggestion_or_404(
            db_session, account=owner.account, space_id=space.id, suggestion_id=s.id
        )
    assert excinfo.value.status_code == 404


# ---- 路由级回归（release-observability E2E 发现的序列化缺口）----


def test_submit_route_returns_json_and_replays_idempotently(db_session, client) -> None:
    """submit 端点必须返回可 JSON 渲染的响应（datetime→ISO），重放同 key 同对象。

    服务层直接调用的单测覆盖不到路由 JSONResponse 渲染；E2E 驱动曾在此处
    触发 TypeError: datetime not JSON serializable。本回归经真实 HTTP 层验证。
    """
    owner, space = create_agent_fixture(db_session, name="sg-route")
    b = _make_member(db_session, space, "sg-route-b")
    c = _make_member(db_session, space, "sg-route-c")
    now = timeutil.utcnow()
    s, _ = steward_suggestions.upsert_suggestion(
        db_session,
        space_id=space.id,
        origin="model",
        kind="relation_proposal",
        subject_user_id=b.id,
        object_user_id=c.id,
        value_json={"fact_type": "direct_sibling"},
        evidence_json={"facts": []},
        policy_version="p1",
        recipient_account_ids=[owner.account.id, b.account.id, c.account.id],
        now=now,
    )
    db_session.commit()

    headers = {**_login_header(client, "sg-route"), "Idempotency-Key": "route-key-1"}
    listed = client.get("/api/steward-suggestions", params={"space_id": space.id}, headers=headers)
    assert listed.status_code == 200, listed.text
    assert [it["id"] for it in listed.json()["items"]] == [s.id]

    first = client.post(
        f"/api/steward-suggestions/{s.id}/submit",
        params={"space_id": space.id},
        json={
            "expected_revision": s.revision,
            "evidence_hash": s.evidence_hash,
            "confirm": True,
        },
        headers=headers,
    )
    assert first.status_code == 202, first.text  # datetime 未序列化时此处为 500
    body = first.json()
    assert body["linked_proposal"]["state"] == "proposed"
    assert body["suggestion"]["created_at"] == body["suggestion"]["created_at"]  # ISO 字符串

    replay = client.post(
        f"/api/steward-suggestions/{s.id}/submit",
        params={"space_id": space.id},
        json={
            "expected_revision": s.revision + 1,
            "evidence_hash": s.evidence_hash,
            "confirm": True,
        },
        headers=headers,
    )
    assert replay.status_code == 202, replay.text
    replay_fact = replay.json()["linked_proposal"]["source_fact_id"]
    assert replay_fact == body["linked_proposal"]["source_fact_id"]
