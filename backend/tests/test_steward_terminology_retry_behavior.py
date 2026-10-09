"""行为级证明：临时上游失败不得把 terminology 目标永久封死。

## 为什么需要行为级（而不只是 `_is_transient_error` 的单测）

分类函数正确，不代表 `terminology_target_retryable` 真的用它。这里直接构造
plan + model_call 的历史行，断言**同一 request_hash** 下：

  - `failed/http_503`、`failed/PROVIDER_STREAM_ERROR`、
    `failed/PROVIDER_RETRY_BUDGET_EXHAUSTED` → 仍可重试（受 60s 冷却与至多两次约束）；
  - `failed/invalid_output`、`failed/prompt_too_large` → 判为终态，不再重发；
  - `succeeded` / `degraded` → 判为终态（已有结论，不重发）。

实测背景（生产，2026-10-09）：space 2 的 33 个合格目标全部被 `failed` 拦下，
且全部是临时失败，terminology 因此静默停摆 6 小时。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.services import steward_assist
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture


@pytest.fixture
def target_ctx(db_session):
    """一个真实 space + account，供 plan/attempt 的外键与 viewer 绑定使用。"""
    user, space = create_agent_fixture(db_session, name="term-retry")
    return {"space_id": space.id, "viewer_account_id": user.account.id}


def _seed(db_session, ctx, *, status: str, error_code: str | None, created_at):
    from app.models.steward import (
        STEWARD_JOB_CAUSES,
        StewardAssistPlan,
        StewardJob,
        StewardModelCall,
    )

    viewer = ctx["viewer_account_id"]
    job = StewardJob(
        space_id=ctx["space_id"],
        cause=sorted(STEWARD_JOB_CAUSES)[0],
        trigger_cursor=0,
        status="succeeded",
        policy_version="test",
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(job)
    db_session.flush()
    plan = StewardAssistPlan(
        space_id=ctx["space_id"],
        job_id=job.id,
        evidence_hash="e" * 64,
        policy_version="test",
        deadline_at=created_at + timedelta(hours=1),
        created_at=created_at,
        fence_json={
            "terminology_groups": [
                {
                    "viewer_account_id": viewer,
                    "root_user_id": viewer,
                    "digest": "d1",
                    "targets": [
                        {
                            "target_user_id": 5,
                            "semantic_hash": "s1",
                            "request_hash": "r1",
                        }
                    ],
                }
            ]
        },
    )
    db_session.add(plan)
    db_session.flush()
    db_session.add(
        StewardModelCall(
            space_id=ctx["space_id"],
            job_id=job.id,
            plan_id=plan.id,
            policy_version="test",
            assist_kind="terminology",
            carrier="pi",
            viewer_account_id=viewer,
            subject_key=f"terminology:{viewer}:{viewer}:d1",
            input_hash="i" * 64,
            prompt_digest="p" * 64,
            prompt_chars=100,
            completion_chars=0,
            total_tokens=0,
            model="test-model",
            status=status,
            error_code=error_code,
            created_at=created_at,
            reserved_input_tokens=100,
            reserved_output_tokens=100,
        )
    )
    db_session.flush()


def _retryable(db_session, ctx, *, now):
    viewer = ctx["viewer_account_id"]
    return steward_assist.terminology_target_retryable(
        db_session,
        space_id=ctx["space_id"],
        viewer_account_id=viewer,
        root_user_id=viewer,
        target_user_id=5,
        semantic_hash="s1",
        request_hash="r1",
        now=now,
    )


@pytest.mark.parametrize(
    "error_code",
    [
        "http_503",
        "http_502",
        "PROVIDER_STREAM_ERROR",
        "PROVIDER_RETRY_BUDGET_EXHAUSTED",
    ],
)
def test_transient_failure_does_not_retire_the_target(
    db_session, target_ctx, error_code: str
) -> None:
    """上游临时失败后，同一 request_hash 仍必须可重试（冷却期过后）。"""
    now = utcnow()
    _seed(
        db_session,
        target_ctx,
        status="failed",
        error_code=error_code,
        created_at=now - timedelta(hours=1),
    )
    assert _retryable(
        db_session, target_ctx, now=now
    ), f"{error_code} 是临时失败，却把目标判为终态——一次上游抖动会永久封死该目标"


def test_transient_failure_is_rate_limited_not_unlimited(db_session, target_ctx) -> None:
    """临时失败不是无限重试：累计两次后必须停止。"""
    now = utcnow()
    for hours in (3, 2):
        _seed(
            db_session,
            target_ctx,
            status="failed",
            error_code="http_503",
            created_at=now - timedelta(hours=hours),
        )
    assert not _retryable(db_session, target_ctx, now=now), "两次临时失败后仍可重试——缺少重试上界"


def test_transient_failure_respects_cooldown(db_session, target_ctx) -> None:
    """临时失败后有 60s 冷却，避免紧邻的下一轮立即重发。"""
    now = utcnow()
    _seed(
        db_session,
        target_ctx,
        status="failed",
        error_code="http_503",
        created_at=now - timedelta(seconds=5),
    )
    assert not _retryable(db_session, target_ctx, now=now), "冷却期内不应重试"


@pytest.mark.parametrize("error_code", ["invalid_output", "prompt_too_large"])
def test_permanent_failure_retires_the_target(db_session, target_ctx, error_code: str) -> None:
    """永久失败是终态：同一 request_hash 不再重发（避免无限烧钱）。"""
    now = utcnow()
    _seed(
        db_session,
        target_ctx,
        status="failed",
        error_code=error_code,
        created_at=now - timedelta(hours=1),
    )
    assert not _retryable(db_session, target_ctx, now=now), f"{error_code} 是永久失败，不应重试"


@pytest.mark.parametrize("status", ["succeeded", "degraded"])
def test_settled_result_retires_the_target(db_session, target_ctx, status: str) -> None:
    """已有结论（成功或降级）的目标不再重发。"""
    now = utcnow()
    _seed(
        db_session, target_ctx, status=status, error_code=None, created_at=now - timedelta(hours=1)
    )
    assert not _retryable(db_session, target_ctx, now=now)
