"""Generation/指纹短路/重试预算/demand 合同测试（09-13 design §3/§5/§7.1）。

覆盖：
- 作业生命周期创建/发布 generation；per-viewer 进度行 ready 且计数真实；
- 无变化图的指纹短路：结构重算被跳过，但到期检查/出卡仍执行（R5）；
- 必需阶段失败按输入指纹跨代持久记账，预算耗尽整代失败、不发布不推进水位；
- demand 端点：成员 200、非成员 404、越权 focus 422、重复登记合并；
- GET progressive 的 progress 来自 generation 进度行（generation 单调）。
"""

from __future__ import annotations

import pytest
from conftest import (
    auth_header,
    create_agent_fixture,
    create_space_member,
    create_user_with_pin,
    login,
)
from sqlalchemy import select

from app import config
from app.models.personal_family_view import PersonalFamilyView
from app.models.steward import StewardGeneration, StewardGenerationView, StewardJob
from app.services import personal_family_view as pfv
from app.services import steward, steward_generations
from app.services.source_facts import create_source_fact, transition_source_fact


@pytest.fixture()
def _steward_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)


def _account_id(session, user_id: int) -> int:
    from app.models.account import Account

    return int(session.scalar(select(Account.id).where(Account.user_id == user_id)))


def _seed_family(session, name: str):
    viewer, space = create_agent_fixture(session, name=name)
    parent = create_user_with_pin(session, f"{name}-parent", "123456", gender="m")
    create_space_member(session, space.id, parent.id)
    fact = create_source_fact(
        session,
        fact_type="biological_parent",
        subject_user_id=parent.id,
        object_user_id=viewer.id,
        provenance="manual_entry",
        space_id=space.id,
    )
    transition_source_fact(session, fact, "confirm")
    session.commit()
    return viewer, space, fact


def _run_job(db_session, space_id: int, *, cause: str = "integrity_scan"):
    # 事实确认事件会自动入队作业；优先租队首（含自动作业），无作业才登记。
    grant = steward.lease_next_steward_job(db_session, leased_by="w1")
    if grant is None:
        from app.services.steward import current_event_watermark

        steward.enqueue_steward_job(
            db_session,
            space_id=space_id,
            cause=cause,
            trigger_cursor=current_event_watermark(db_session),
        )
        grant = steward.lease_next_steward_job(db_session, leased_by="w1")
    assert grant is not None
    # execute_steward_job：异常时按确定性失败结算（终态），测试可重复调度
    summary = steward.execute_steward_job(
        db_session, grant, worker_id="w1", expected_attempt=grant.attempt
    )
    return grant, summary


def test_generation_lifecycle_and_view_progress(db_session, _steward_enabled) -> None:
    viewer, space, _fact = _seed_family(db_session, "gen-lifecycle")
    pfv.initialize_account_views(
        db_session, account_id=_account_id(db_session, viewer.id), user_id=viewer.id
    )
    db_session.commit()

    _job, summary = _run_job(db_session, space.id)
    gen = db_session.scalar(select(StewardGeneration).where(StewardGeneration.space_id == space.id))
    assert gen is not None and gen.status == "published"
    assert gen.job_id == _job.id
    assert summary["generation_id"] == gen.id

    row = db_session.scalar(
        select(StewardGenerationView).where(StewardGenerationView.generation_id == gen.id)
    )
    assert row is not None
    assert row.status == "ready"
    assert row.viewer_account_id == _account_id(db_session, viewer.id)
    # 1 个已授权目标（parent），1 条 confirmed 摘要边
    assert row.total_count == 1
    assert row.completed_count == 1


def test_fingerprint_short_circuit_skips_rebuild(db_session, _steward_enabled) -> None:
    viewer, space, _fact = _seed_family(db_session, "gen-shortcircuit")
    pfv.initialize_account_views(
        db_session, account_id=_account_id(db_session, viewer.id), user_id=viewer.id
    )
    db_session.commit()

    _job1, first = _run_job(db_session, space.id)
    assert first["stats"]["derived_recomputed"] > 0
    _job2, second = _run_job(db_session, space.id)
    # 无变化图：结构重算被短路，但代次仍登记并发布
    assert second["stats"].get("fingerprint_short_circuit") == 1
    assert second["stats"]["derived_recomputed"] == 0
    assert second["stats"]["personal_family_views_rebuilt"] == 0

    # 输入变化后恢复重算
    other = create_user_with_pin(db_session, "gen-sc-other", "123456", gender="f")
    create_space_member(db_session, space.id, other.id)
    fact2 = create_source_fact(
        db_session,
        fact_type="biological_parent",
        subject_user_id=other.id,
        object_user_id=viewer.id,
        provenance="manual_entry",
        space_id=space.id,
    )
    transition_source_fact(db_session, fact2, "confirm")
    db_session.commit()
    _job3, third = _run_job(db_session, space.id)
    assert third["stats"].get("fingerprint_short_circuit") is None
    assert third["stats"]["derived_recomputed"] > 0


def test_retry_budget_exhaustion_fails_generation(
    db_session, monkeypatch, _steward_enabled
) -> None:
    viewer, space, _fact = _seed_family(db_session, "gen-budget")
    pfv.initialize_account_views(
        db_session, account_id=_account_id(db_session, viewer.id), user_id=viewer.id
    )
    db_session.commit()
    monkeypatch.setattr(config, "STEWARD_STAGE_MAX_ATTEMPTS", 2)

    import app.services.personal_family_view as pfv_mod

    calls = {"n": 0}

    def flaky(session, *, account, space_id):
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(pfv_mod, "rebuild_view", flaky)
    # 第一次失败：预算记账（attempts=1 < max=2），作业照常结算（视图 failed）
    _job1, _summary1 = _run_job(db_session, space.id)
    db_session.rollback()
    # 第二次失败：attempts=2 达到上限 → 整代失败（raise）
    with pytest.raises(pfv_mod.StewardStageBudgetExhausted):
        _run_job(db_session, space.id)
    db_session.rollback()
    # 预算已耗尽：同指纹再次执行仍整代失败，不重复执行重计算
    calls["n"] = 0
    with pytest.raises(pfv_mod.StewardStageBudgetExhausted):
        _run_job(db_session, space.id)
    job = db_session.scalar(
        select(StewardJob).where(StewardJob.space_id == space.id).order_by(StewardJob.id.desc())
    )
    assert job is not None
    # 水位从未推进（必需目标耗尽 → 不发布）
    assert job.last_event_cursor is None

    budget = db_session.execute(
        select(steward_generations.StewardRetryBudget).where(
            steward_generations.StewardRetryBudget.space_id == space.id
        )
    ).scalar()
    assert budget is not None and budget.exhausted
    gen = db_session.scalar(
        select(StewardGeneration)
        .where(StewardGeneration.space_id == space.id)
        .order_by(StewardGeneration.id.desc())
    )
    assert gen is not None and gen.status == "failed"


def test_demand_endpoint_authorization_and_idempotency(
    db_session, client, _steward_enabled, monkeypatch
) -> None:
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    viewer, space, _fact = _seed_family(db_session, "gen-demand")
    headers = auth_header(login(client, "gen-demand", "123456").json())

    # 非成员空间 404（fail-closed）
    create_user_with_pin(db_session, "gen-demand-stranger", "123456", gender="f")
    stranger_headers = auth_header(login(client, "gen-demand-stranger", "123456").json())
    assert (
        client.post(
            "/api/personal-family-view/demand",
            json={"space_id": space.id},
            headers=stranger_headers,
        ).status_code
        == 404
    )

    # 先结算种子事件自动登记的作业，保证 demand 从干净队列开始
    _run_job(db_session, space.id)
    db_session.commit()
    # 合法成员：queued；focus 必须在授权骨架内
    assert (
        client.post(
            "/api/personal-family-view/demand",
            json={"space_id": space.id, "focus_user_id": viewer.id},
            headers=headers,
        ).json()["status"]
        == "queued"
    )
    # 活跃作业存在 → already_active（重复登记合并）
    assert (
        client.post(
            "/api/personal-family-view/demand",
            json={"space_id": space.id},
            headers=headers,
        ).json()["status"]
        == "already_active"
    )
    # 骨架外目标 422（不接受任意 viewer/隐藏目标）
    outsider = create_user_with_pin(db_session, "gen-demand-outsider", "123456", gender="m")
    body = client.post(
        "/api/personal-family-view/demand",
        json={"space_id": space.id, "focus_user_id": outsider.id},
        headers=headers,
    )
    assert body.status_code == 422


def test_progress_uses_generation_rows(db_session, client, _steward_enabled, monkeypatch) -> None:
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    viewer, space, _fact = _seed_family(db_session, "gen-progress")
    pfv.initialize_account_views(
        db_session, account_id=_account_id(db_session, viewer.id), user_id=viewer.id
    )
    db_session.commit()
    _run_job(db_session, space.id)

    headers = auth_header(login(client, "gen-progress", "123456").json())
    body = client.get(
        "/api/personal-family-view",
        params={"space_id": space.id, "progressive": "true"},
        headers=headers,
    ).json()
    progress = body["progress"]
    gen = db_session.scalar(select(StewardGeneration).where(StewardGeneration.space_id == space.id))
    assert gen is not None
    assert progress["generation"] == gen.id
    assert progress["phase"] == "ready"
    assert progress["completed_count"] == 1
    assert progress["total_count"] == 1
    assert progress["next_poll_ms"] == 0


def test_display_until_header_on_200_and_304(
    db_session, client, _steward_enabled, monkeypatch
) -> None:
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    viewer, space, _fact = _seed_family(db_session, "gen-display")
    pfv.initialize_account_views(
        db_session, account_id=_account_id(db_session, viewer.id), user_id=viewer.id
    )
    db_session.commit()
    view = db_session.scalar(
        select(PersonalFamilyView).where(
            PersonalFamilyView.space_id == space.id,
            PersonalFamilyView.viewer_account_id == _account_id(db_session, viewer.id),
        )
    )
    assert view is not None
    view.status = "stale"  # 保证 GET 会触发重建登记路径前先有行
    db_session.commit()

    headers = auth_header(login(client, "gen-display", "123456").json())
    first = client.get("/api/personal-family-view", params={"space_id": space.id}, headers=headers)
    assert first.status_code == 200
    until = first.headers.get("X-PFV-Display-Until")
    assert until is not None and until.isdigit()

    view = db_session.scalar(
        select(PersonalFamilyView).where(
            PersonalFamilyView.space_id == space.id,
            PersonalFamilyView.viewer_account_id == _account_id(db_session, viewer.id),
        )
    )
    if view is not None and view.status == "current":
        etag = first.headers["ETag"]
        second = client.get(
            "/api/personal-family-view",
            params={"space_id": space.id},
            headers={**headers, "If-None-Match": etag},
        )
        assert second.status_code == 304
        assert second.headers.get("X-PFV-Display-Until", "").isdigit()
