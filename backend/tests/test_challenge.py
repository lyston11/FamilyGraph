"""services/challenge.py：落库、原子单次使用、过期/重放拒绝（implement.md #4）。"""

import json
from datetime import timedelta

import pytest

from app import config
from app.models.auth_challenge import AuthChallenge
from app.services.challenge import consume_challenge, create_challenge, load_candidate_ids
from app.utils import timeutil


@pytest.fixture()
def challenge(db_session):
    return create_challenge(db_session, [11, 22], ip="10.0.0.1")


def test_create_challenge_persists_candidates(db_session, challenge) -> None:
    row = db_session.query(AuthChallenge).filter_by(jti=challenge.jti).one()
    assert json.loads(row.candidate_ids_json) == [11, 22]
    assert row.used_at is None
    assert row.ip == "10.0.0.1"
    expected = timeutil.utcnow() + timedelta(minutes=config.AUTH_CHALLENGE_TTL_MINUTES)
    assert abs((row.expires_at - expected).total_seconds()) < 2


def test_load_candidate_ids(db_session, challenge) -> None:
    assert load_candidate_ids(db_session, challenge.jti) == [11, 22]
    assert load_candidate_ids(db_session, "missing-jti") is None


def test_consume_is_single_use(db_session, challenge) -> None:
    """重放同一 jti 二次 select 必须被拒（PRD 验收）。"""
    assert consume_challenge(db_session, challenge.jti, ip="10.0.0.1", user_id=11)
    db_session.commit()
    assert not consume_challenge(db_session, challenge.jti, ip="10.0.0.1", user_id=22)


def test_expired_challenge_rejected_same_path(
    db_session, challenge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """过期与重放走同一拒绝路径：影响行数=0。"""
    base = timeutil.utcnow()
    monkeypatch.setattr(timeutil, "utcnow", lambda: base)
    created = create_challenge(db_session, [5], ip="10.0.0.1")
    monkeypatch.setattr(
        timeutil,
        "utcnow",
        lambda: base + timedelta(minutes=config.AUTH_CHALLENGE_TTL_MINUTES, seconds=1),
    )
    assert not consume_challenge(db_session, created.jti, ip="10.0.0.1", user_id=5)


def test_ip_binding_rejected_without_consuming(db_session, challenge) -> None:
    """IP 不匹配在原子 UPDATE 条件中直接拒绝，不消耗 challenge（合法用户可重试）。"""
    assert not consume_challenge(db_session, challenge.jti, ip="10.0.0.9", user_id=11)
    # 正确 IP 仍可正常完成选择
    assert consume_challenge(db_session, challenge.jti, ip="10.0.0.1", user_id=11)


def test_candidate_outside_list_rejected(db_session, challenge) -> None:
    assert not consume_challenge(db_session, challenge.jti, ip="10.0.0.1", user_id=33)


def test_unknown_jti_rejected(db_session) -> None:
    assert not consume_challenge(db_session, "nope", ip="10.0.0.1", user_id=1)
