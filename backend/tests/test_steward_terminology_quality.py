"""Regression checks for automatic terminology as consumed by real readers."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from test_steward import _confirm, _person, _space
from test_steward_terminology import (
    _account_id,
    _completions_fake,
    _drain,
    _enable_provider,
    _grandchild_family,
    _terminology_payload,
)

from app import config
from app.models.account import Account
from app.models.relationship_facts import SourceFact
from app.models.steward import StewardTermProjection
from app.services import personal_family_view, steward_assist, steward_terminology, terms


@pytest.fixture(autouse=True)
def terminology_setup(db_session, monkeypatch):
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    for kind in ("CANDIDATE", "RANKING", "EXPLANATION"):
        monkeypatch.setattr(config, f"STEWARD_ASSIST_{kind}", False)
    monkeypatch.setattr(config, "STEWARD_ASSIST_TERMINOLOGY", True)
    terms.seed_builtin_packs(db_session)
    db_session.commit()


def _choose_grandmother_term(db_session, space, gc):
    terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Uf-Uf",
        term="姥姥",
        account_id=_account_id(db_session, gc),
        profile_id=gc.id,
        source_event="manual_select",
    )
    db_session.commit()
    _drain(db_session, space)


def _model_grandmother_term(db_session, space):
    _enable_provider(db_session, space)
    _drain(db_session, space)
    assert (
        steward_assist.run_due_batch(
            db_session,
            transport=_completions_fake(
                _terminology_payload(
                    {
                        "target_ref": "t002",
                        "concept_code": "Uf-Uf",
                        "term": "姥姥",
                        "reason_code": "synonym",
                    }
                )
            ),
        )
        == "applied"
    )


def _nephew_family(db_session):
    """本人—父亲—兄弟—侄子：resolver 产出展开式 `Um-Dm-Dm`，内置词按 `Bm-Dm` 编码。"""
    space = _space(db_session, "nephew-autonomy", kind="household")
    viewer = _person(db_session, space.id, "nep-viewer", gender="m")
    father = _person(db_session, space.id, "nep-father", gender="m")
    brother = _person(db_session, space.id, "nep-brother", gender="m")
    nephew = _person(db_session, space.id, "nep-nephew", gender="m")
    _confirm(db_session, "biological_parent", father.id, viewer.id, space_id=space.id)
    _confirm(db_session, "biological_parent", father.id, brother.id, space_id=space.id)
    _confirm(db_session, "biological_parent", brother.id, nephew.id, space_id=space.id)
    account_id = _account_id(db_session, viewer)
    personal_family_view.initialize_account_views(
        db_session, account_id=account_id, user_id=viewer.id
    )
    db_session.commit()
    return space, viewer, nephew


def test_expanded_sibling_path_shows_named_term_without_any_approval(db_session):
    """AC1：真实 job 发布后，查看者直接看到“侄子”，全程零称谓批准操作。

    这条路径此前只显示“兄弟的儿子”（registry 按折叠式编码，resolver 产出展开式）。
    改善必须由确定性规则自动生效，不依赖建议提交、通知或用户点击。
    """
    from app.models.steward_suggestion import StewardSuggestion

    space, viewer, nephew = _nephew_family(db_session)
    account_id = _account_id(db_session, viewer)
    _drain(db_session, space)
    payload = personal_family_view.current_view_payload(
        db_session, account=db_session.get(Account, account_id), space_id=space.id
    )
    assert payload["status"] == "current"
    edge = next(e for e in payload["edges"] if e["to_user_id"] == nephew.id)
    assert edge["term"] == "侄子"
    # 用户什么都没做：既没有待处理建议，也没有同值自我推荐。
    assert (
        db_session.scalar(
            select(StewardSuggestion.id).where(
                StewardSuggestion.space_id == space.id,
                StewardSuggestion.kind == "term_preference",
                StewardSuggestion.value_json["term"].as_string() == "侄子",
            )
        )
        is None
    )


def test_deterministic_term_refreshes_family_view_without_read_side_writes(db_session):
    space, gc, _mom, gm = _grandchild_family(db_session)
    _drain(db_session, space)
    _choose_grandmother_term(db_session, space, gc)
    payload = personal_family_view.current_view_payload(
        db_session, account=db_session.get(Account, _account_id(db_session, gc)), space_id=space.id
    )
    assert payload["status"] == "current"
    assert next(e for e in payload["edges"] if e["to_user_id"] == gm.id)["term"] == "姥姥"


def test_disabled_model_term_is_not_served(db_session, monkeypatch):
    space, gc, _mom, gm = _grandchild_family(db_session)
    _model_grandmother_term(db_session, space)
    account_id = _account_id(db_session, gc)
    assert (
        terms.compose_resolution_view(
            db_session,
            account_id=account_id,
            viewer_user_id=gc.id,
            target_user_id=gm.id,
            space_id=space.id,
        )["term"]
        == "姥姥"
    )
    monkeypatch.setattr(config, "STEWARD_ASSIST_TERMINOLOGY", False)
    assert (
        terms.compose_resolution_view(
            db_session,
            account_id=account_id,
            viewer_user_id=gc.id,
            target_user_id=gm.id,
            space_id=space.id,
        )["term"]
        == "外婆"
    )


def test_revised_path_invalidates_automatic_term_even_if_baseline_is_unchanged(db_session):
    space, gc, _mom, gm = _grandchild_family(db_session)
    _drain(db_session, space)
    _choose_grandmother_term(db_session, space, gc)
    fact = db_session.scalar(select(SourceFact).where(SourceFact.space_id == space.id))
    fact.revision += 1
    db_session.commit()
    assert (
        terms.compose_resolution_view(
            db_session,
            account_id=_account_id(db_session, gc),
            viewer_user_id=gc.id,
            target_user_id=gm.id,
            space_id=space.id,
        )["term"]
        == "外婆"
    )


@pytest.mark.parametrize("target_ref", ["t000", "t0", "t02", "t-1"])
def test_model_cannot_alias_or_negative_index_a_target(db_session, target_ref):
    space, gc, _mom, _gm = _grandchild_family(db_session)
    _drain(db_session, space)
    group = next(
        g
        for g in steward_terminology.collect_model_groups(
            db_session,
            space_id=space.id,
            max_groups=10,
            max_targets=8,
        )
        if g["viewer_account_id"] == _account_id(db_session, gc)
    )
    prompt = json.loads(steward_terminology.project_terminology_input(db_session, group))
    result = steward_terminology.validate_model_output(
        db_session,
        text=json.dumps(
            {
                "version": 1,
                "context_hash": prompt.get("context_hash"),
                "items": [
                    {
                        "target_ref": target_ref,
                        "concept_code": "Uf-Uf",
                        "term": "姥姥",
                        "reason_code": "synonym",
                    }
                ],
            }
        ),
        group=group,
        space_id=space.id,
    )
    assert result is None or result["items"] == []


def test_prior_sibling_usage_cannot_invent_age_for_another_target(db_session):
    space = _space(db_session, "sibling-autonomy", kind="household")
    viewer = _person(db_session, space.id, "sib-viewer", gender="m")
    sibling = _person(db_session, space.id, "sib-target", gender="m")
    _confirm(db_session, "direct_sibling", viewer.id, sibling.id, space_id=space.id)
    account_id = _account_id(db_session, viewer)
    personal_family_view.initialize_account_views(
        db_session,
        account_id=account_id,
        user_id=viewer.id,
    )
    terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Bm",
        term="哥哥",
        account_id=account_id,
        profile_id=viewer.id,
        source_event="manual_select",
    )
    db_session.commit()
    _drain(db_session, space)
    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == account_id,
            StewardTermProjection.target_user_id == sibling.id,
        )
    )
    assert projection is None or projection.term != "哥哥"
    assert (
        terms.compose_resolution_view(
            db_session,
            account_id=account_id,
            viewer_user_id=viewer.id,
            target_user_id=sibling.id,
            space_id=space.id,
        )["term"]
        == "兄弟"
    )
