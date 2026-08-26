"""确档清单与身份确认流程测试（F-1）。

覆盖：「这是我」合并转换（Account+Profile 同时完成）、两条状态机其余路径独立、
首登门禁白名单内可先确认身份、建档播种清单项、本人决议 confirm/dispute（终态 +
争议附言）、非本人清单 404 防枚举、provisional 无推荐资格。
"""

from __future__ import annotations

import pytest
from conftest import auth_header, create_user_with_pin, login
from fastapi import HTTPException

from app.commands import identity as identity_commands
from app.commands import members as member_commands
from app.commands.context import ActorContext
from app.errors import extract_api_error
from app.models.v2_foundation import DomainEvent, ProfileFactReview
from app.services.identity_fsm import recommendation_eligible


def _ctx(user) -> ActorContext:
    return ActorContext(
        user_id=user.id,
        account_id=user.account.id,
        account_status=user.account.status,
    )


def test_confirm_identity_combined_transition(db_session) -> None:
    """「这是我」：managed→claimed 与 provisional→identity_confirmed 同步完成。"""
    user = create_user_with_pin(
        db_session, "待确档人", "111111", pin_must_change=True, profile_status="provisional"
    )
    result = identity_commands.claim_and_confirm_own_identity(db_session, _ctx(user))
    assert result == {"account_claimed": True, "profile_confirmed": True}

    db_session.expire_all()
    assert user.account.status == "claimed"
    assert user.account.claimed_at is not None
    assert user.profile_status == "identity_confirmed"
    assert user.profile_confirmed_at is not None

    events = {e.type for e in db_session.query(DomainEvent).all()}
    assert {"account.claimed", "profile.identity_confirmed"} <= events


def test_confirm_identity_repeated_409_and_independence_kept(db_session) -> None:
    """重复确认 409；他建档案的创建者不能借本命令确认对方档案（独立性红线）。"""
    confirmed = create_user_with_pin(db_session, "已确档人", "222222")
    with pytest.raises(HTTPException) as exc_info:
        identity_commands.claim_and_confirm_own_identity(db_session, _ctx(confirmed))
    error = extract_api_error(exc_info.value.detail)
    assert exc_info.value.status_code == 409
    assert error is not None and error["code"] == "IDENTITY_INVALID_TRANSITION"

    # 创建者自身也是待确档态；确认命令只影响创建者本人档案
    creator = create_user_with_pin(
        db_session, "代管人", "333333", pin_must_change=True, profile_status="provisional"
    )
    provisional_other = create_user_with_pin(
        db_session,
        "他人档案",
        "444444",
        pin_must_change=True,
        profile_status="provisional",
        created_by=creator.id,
    )
    result = identity_commands.claim_and_confirm_own_identity(db_session, _ctx(creator))
    assert result == {"account_claimed": True, "profile_confirmed": True}

    db_session.expire_all()
    assert creator.profile_status == "identity_confirmed"
    # 对方 provisional 档案保持不变：Account/Profile 状态机不得被他人联动
    assert provisional_other.profile_status == "provisional"


def test_identity_confirm_whitelisted_before_pin_change(db_session, client) -> None:
    """首登门禁白名单：pin_must_change 账号可先调 POST /me/identity/confirm。"""
    create_user_with_pin(
        db_session, "新认领", "555555", pin_must_change=True, profile_status="provisional"
    )
    tokens = login(client, "新认领", "555555").json()

    resp = client.post("/api/me/identity/confirm", headers=auth_header(tokens))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"account_claimed": True, "profile_confirmed": True}

    again = client.post("/api/me/identity/confirm", headers=auth_header(tokens))
    assert again.status_code == 409


def test_member_creation_seeds_fact_reviews(db_session) -> None:
    """建档播种确档清单：名字必审 + 已填可选字段 + 创建者关系。"""
    creator = create_user_with_pin(db_session, "建档人", "666666")
    member, pin = member_commands.create_member(
        db_session,
        _ctx(creator),
        name="小李",
        gender="m",
        bio="家族记录",
        birth={"cal_type": "solar", "date": "2000-01-01"},
    )
    assert len(pin) == 6

    reviews = (
        db_session.query(ProfileFactReview)
        .filter(ProfileFactReview.profile_id == member.id)
        .order_by(ProfileFactReview.id)
        .all()
    )
    item_types = [r.item_type for r in reviews]
    assert item_types[0] == "name"
    assert "bio" in item_types and "birth" in item_types and "relation_to_creator" in item_types
    relation_item = next(r for r in reviews if r.item_type == "relation_to_creator")
    assert relation_item.item_ref_json["creator_id"] == creator.id
    assert all(r.status == "proposed" for r in reviews)


def test_fact_review_decide_flow_terminal_with_note(db_session, client) -> None:
    """本人决议：confirmed / disputed(+附言)；终态不可再转；非本人 404。"""
    creator = create_user_with_pin(db_session, "清单创建者", "777777")
    member, _pin = member_commands.create_member(
        db_session, _ctx(creator), name="小王", bio="自我介绍占位"
    )
    db_session.commit()

    # 建档响应才含明文 PIN；此处直接以命令层上下文模拟该账号的已认证会话
    ctx = ActorContext(user_id=member.id, account_id=member.account.id, account_status="managed")

    reviews = identity_commands.list_own_fact_reviews(db_session, ctx)
    name_review = next(r for r in reviews if r.item_type == "name")
    bio_review = next(r for r in reviews if r.item_type == "bio")

    decided = identity_commands.decide_fact_review(
        db_session, ctx, name_review.id, decision="confirmed"
    )
    assert decided.status == "confirmed"

    disputed = identity_commands.decide_fact_review(
        db_session, ctx, bio_review.id, decision="disputed", note="这段描述不是我写的"
    )
    assert disputed.status == "disputed"
    assert disputed.item_ref_json["reviewer_note"] == "这段描述不是我写的"

    # 终态再决议 → 409
    with pytest.raises(HTTPException):
        identity_commands.decide_fact_review(db_session, ctx, name_review.id, decision="disputed")

    # 非本人清单项 404 防枚举
    outsider = create_user_with_pin(db_session, "无关者", "888888")
    with pytest.raises(HTTPException) as exc_info:
        identity_commands.decide_fact_review(
            db_session, _ctx(outsider), name_review.id, decision="confirmed"
        )
    assert exc_info.value.status_code == 404

    types = {e.type for e in db_session.query(DomainEvent).all()}
    assert "profile.fact_review.decided" in types


def test_provisional_not_recommendable_via_command_helper(db_session) -> None:
    provisional = create_user_with_pin(db_session, "未确档", "999999", profile_status="provisional")
    confirmed = create_user_with_pin(db_session, "已确档2", "101010")
    assert identity_commands.eligible_for_recommendation(provisional) is False
    assert identity_commands.eligible_for_recommendation(confirmed) is True
    assert recommendation_eligible(provisional) is False  # AC-F2 双入口一致
