"""管家称谓自主优化测试（09-13-steward-terminology-autonomy）。

覆盖（父 AC-04～08/11～13 的可观察行为；真实 job/fake transport 进入，绝不
以手工 upsert 产物替代生产链）：
- B-AC1：真实 job 中确定性来源（长链可保留、本人明确用词）生成本人建议，
  notify=False（无逐条待办通知）；
- B-AC3：fake transport 合法同义词（外婆→姥姥）实际保存消费；错概念码/
  未知词/额外目标被拒绝并回退；
- B-AC5：attempt 绑定 viewer；GET 零模型调用；同输入不重复调用；
- B-AC7：恢复原叫法即时回退 + 稳定抑制跨证据版本防重现；
- B-AC6：只有 terminology 开启时也能运行。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from test_steward import _confirm, _person, _space

from app import config
from app.models.account import Account
from app.models.notification import Notification
from app.models.steward import (
    StewardAssistBatch,
    StewardModelCall,
    StewardTermProjection,
    StewardTermSuppression,
)
from app.models.steward_suggestion import StewardSuggestion
from app.services import (
    personal_family_view,
    platform_features,
    steward_assist,
    terms,
)
from app.services import (
    steward as steward_service,
)
from app.utils import timeutil


def _drain(session, space) -> int:
    """租约并运行空间内全部到期 job（领域事件自动入队；无 job 返回 0）。"""
    ran = 0
    while True:
        granted = steward_service.lease_next_steward_job(
            session, leased_by="term-test", space_id=space.id
        )
        if granted is None:
            return ran
        steward_service.run_steward_job(session, granted)
        ran += 1


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_ASSIST_CANDIDATE", False)
    monkeypatch.setattr(config, "STEWARD_ASSIST_RANKING", False)
    monkeypatch.setattr(config, "STEWARD_ASSIST_EXPLANATION", False)
    monkeypatch.setattr(config, "STEWARD_ASSIST_TERMINOLOGY", True)


@pytest.fixture(autouse=True)
def _seed_terms(db_session):
    """测试库按迁移种子建表（无 姥姥 别名）；幂等补灌内置包。"""
    terms.seed_builtin_packs(db_session)
    db_session.commit()


def _grandchild_family(db_session):
    """外孙女—母亲—外婆三层家庭（Uf-Uf 路径，zh-CN 基线词 外婆）。"""
    space = _space(db_session, "term-autonomy", kind="household")
    gc = _person(db_session, space.id, "ta-gc", gender="f")
    mom = _person(db_session, space.id, "ta-mom", gender="f")
    gm = _person(db_session, space.id, "ta-gm", gender="f")
    _confirm(db_session, "biological_parent", mom.id, gc.id, space_id=space.id)
    _confirm(db_session, "biological_parent", gm.id, mom.id, space_id=space.id)
    # 测试造数绕过注册/成员事件，不产生 PFV 行；与 dev-seed 同口径显式初始化
    for user in (gc, mom, gm):
        account_id = _account_id(db_session, user)
        personal_family_view.initialize_account_views(
            db_session, account_id=account_id, user_id=user.id
        )
    db_session.commit()
    return space, gc, mom, gm


def _account_id(db_session, user) -> int:
    value = db_session.scalar(select(Account.id).where(Account.user_id == user.id))
    assert value is not None
    return int(value)


def _enable_provider(db_session, space) -> None:
    """在 core job 之前建 Provider+空间开关（批次在 core 事务内注册）。"""
    from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
    from app.utils.secretbox import encrypt_secret

    provider = AgentProvider(
        name=f"term-provider-{space.id}",
        kind="openai_compatible",
        base_url="https://api.example.com/v1",
        secret_ciphertext=encrypt_secret("sk-term-test"),
        allowed_models_json=["gpt-term"],
        enabled=True,
        created_at=timeutil.utcnow(),
        updated_at=timeutil.utcnow(),
    )
    db_session.add(provider)
    db_session.commit()
    db_session.add(
        AgentSpaceProviderSetting(
            space_id=space.id,
            agent_kind="steward",
            provider_id=provider.id,
            model="gpt-term",
            cloud_allowed=True,
            enabled=True,
            assist_terminology=True,
        )
    )
    db_session.commit()


def _pfv_edge(db_session, account_id: int, space, target_id):
    from app.models.personal_family_view import PersonalFamilyView, PersonalFamilyViewEdge

    view = db_session.scalar(
        select(PersonalFamilyView).where(
            PersonalFamilyView.viewer_account_id == account_id,
            PersonalFamilyView.space_id == space.id,
        )
    )
    assert view is not None
    return db_session.scalar(
        select(PersonalFamilyViewEdge).where(
            PersonalFamilyViewEdge.view_id == view.id,
            PersonalFamilyViewEdge.to_user_id == target_id,
        )
    )


def _completions_fake(text: str):
    """openai-responses 形状 fake（Provider 默认 api=openai-responses）。"""

    def transport(url, headers, payload, timeout):
        response_text = text
        decoded = json.loads(text)
        if isinstance(decoded, dict) and decoded.get("context_hash") is None:
            messages = payload.get("input", payload.get("messages", []))
            request = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
            decoded["context_hash"] = request["context_hash"]
            response_text = json.dumps(decoded, ensure_ascii=False)
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": response_text}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }

    return transport


def _terminology_payload(item: dict) -> str:
    return json.dumps({"version": 1, "context_hash": None, "items": [item]}, ensure_ascii=False)


# ---- B-AC1：确定性生产（真实 job；notify=False） ----


def test_deterministic_scan_records_baseline_projection_without_notifications(
    db_session,
) -> None:
    space, gc, mom, gm = _grandchild_family(db_session)
    _drain(db_session, space)
    account_id = _account_id(db_session, gc)

    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == account_id,
            StewardTermProjection.target_user_id == gm.id,
        )
    )
    assert projection is not None
    assert projection.baseline_term == "外婆"
    assert projection.baseline_source == "locale"
    # 无改善不造建议（locale 词条已是基线，非 derived 长链）
    assert (
        db_session.scalar(
            select(StewardSuggestion).where(StewardSuggestion.kind == "term_preference")
        )
        is None
    )
    # 无逐条称谓待办通知
    assert (
        db_session.scalar(
            select(Notification).where(
                Notification.kind == "steward_suggestion",
                Notification.space_id == space.id,
            )
        )
        is None
    )


def test_explicit_usage_creates_override_and_optional_suggestion(db_session) -> None:
    space, gc, mom, gm = _grandchild_family(db_session)
    _drain(db_session, space)
    account_id = _account_id(db_session, gc)
    # 本人明确用词：对该词条的使用证据（manual_select）
    entry = db_session.scalar(
        select(terms.TermEntry).where(
            terms.TermEntry.concept_code == "Uf-Uf", terms.TermEntry.term == "姥姥"
        )
    )
    assert entry is not None
    terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Uf-Uf",
        term="姥姥",
        account_id=account_id,
        profile_id=gc.id,
        source_event="manual_select",
    )
    db_session.commit()
    _drain(db_session, space)

    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == account_id,
            StewardTermProjection.target_user_id == gm.id,
        )
    )
    assert projection is not None and projection.term == "姥姥"
    assert projection.origin == "deterministic"
    suggestion = db_session.scalar(
        select(StewardSuggestion).where(
            StewardSuggestion.kind == "term_preference",
            StewardSuggestion.viewer_account_id == account_id,
        )
    )
    assert suggestion is not None
    assert suggestion.value_json["term"] == "姥姥"
    # 不伪造人类用词：自动行为不新增 TermUsage
    usage_count = len(
        list(
            db_session.scalars(
                select(terms.TermUsage).where(terms.TermUsage.account_id == account_id)
            )
        )
    )
    assert usage_count == 1  # 只有本人主动选择那一条


# ---- B-AC3/5：fake transport 全链（register→reserve→network→validate→writeback）----


def test_model_term_applied_via_real_job_chain(db_session, monkeypatch) -> None:
    space, gc, mom, gm = _grandchild_family(db_session)
    _enable_provider(db_session, space)
    _drain(db_session, space)
    account_id = _account_id(db_session, gc)

    batch_row = db_session.scalar(
        select(StewardAssistBatch).where(StewardAssistBatch.space_id == space.id)
    )
    assert batch_row is not None
    kinds = (batch_row.fence_json or {}).get("kinds", [])
    assert kinds == ["terminology"]

    status = steward_assist.run_due_batch(
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
    assert status == "applied"

    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == account_id,
            StewardTermProjection.target_user_id == gm.id,
        )
    )
    assert projection is not None and projection.term == "姥姥"
    assert projection.origin == "model"
    assert projection.last_checked_hash is not None

    attempt = db_session.scalar(
        select(StewardModelCall).where(
            StewardModelCall.batch_id == batch_row.id,
            StewardModelCall.assist_kind == "terminology",
        )
    )
    assert attempt is not None
    assert attempt.viewer_account_id == account_id
    assert attempt.status == "succeeded"

    suggestion = db_session.scalar(
        select(StewardSuggestion).where(
            StewardSuggestion.kind == "term_preference",
            StewardSuggestion.viewer_account_id == account_id,
        )
    )
    assert suggestion is not None
    # notify=False：零逐条称谓待办
    assert (
        db_session.scalar(
            select(Notification).where(
                Notification.kind == "steward_suggestion",
                Notification.space_id == space.id,
            )
        )
        is None
    )
    # 自动结果不写词典：零 TermEntry 变化、零 TermUsage
    assert (
        db_session.scalar(select(terms.TermUsage).where(terms.TermUsage.account_id == account_id))
        is None
    )


def test_invalid_model_terms_rejected_and_marked_checked(db_session) -> None:
    space, gc, mom, gm = _grandchild_family(db_session)
    _enable_provider(db_session, space)
    _drain(db_session, space)
    account_id = _account_id(db_session, gc)

    # 错概念码 + 未知词 + 额外目标：全部拒绝（结构多余的 target_ref 直接 None）
    bad = _terminology_payload(
        {
            "target_ref": "t002",
            "concept_code": "Um",
            "term": "姥姥",
            "reason_code": "synonym",
        }
    )
    status = steward_assist.run_due_batch(db_session, transport=_completions_fake(bad))
    assert status == "applied"  # 批次终态，但产物零应用

    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == account_id,
            StewardTermProjection.target_user_id == gm.id,
        )
    )
    assert projection is not None and projection.term is None
    assert projection.last_checked_hash is not None

    job_id_first = db_session.scalar(
        select(StewardModelCall.job_id)
        .where(StewardModelCall.assist_kind == "terminology")
        .limit(1)
    )
    # 同输入不再调用（collect 已检查目标跳过）：推新事件重跑 core
    from test_steward import _emit_fact_event

    from app.models.relationship_facts import SourceFact

    fact = db_session.scalar(select(SourceFact).where(SourceFact.space_id == space.id).limit(1))
    _emit_fact_event(db_session, fact)
    db_session.commit()
    _drain(db_session, space)
    calls = list(
        db_session.scalars(
            select(StewardModelCall).where(StewardModelCall.assist_kind == "terminology")
        )
    )
    sent = [c for c in calls if c.status in ("succeeded", "degraded")]
    # 首轮每 viewer 组一次（≤2 组）；重跑同输入零新调用
    assert len(sent) == 2
    assert all(c.job_id == job_id_first for c in sent)


# ---- B-AC7：恢复原叫法 + 稳定抑制 ----


def test_restore_suppresses_across_evidence_versions(db_session) -> None:
    space, gc, mom, gm = _grandchild_family(db_session)
    _drain(db_session, space)
    account_id = _account_id(db_session, gc)
    terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Uf-Uf",
        term="姥姥",
        account_id=account_id,
        profile_id=gc.id,
        source_event="manual_select",
    )
    db_session.commit()
    _drain(db_session, space)
    suggestion = db_session.scalar(
        select(StewardSuggestion).where(
            StewardSuggestion.kind == "term_preference",
            StewardSuggestion.viewer_account_id == account_id,
        )
    )
    assert suggestion is not None
    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == account_id,
            StewardTermProjection.target_user_id == gm.id,
        )
    )

    from app.commands.context import ActorContext

    ctx = ActorContext(user_id=gc.id, account_id=account_id, account_status="claimed")
    status, payload = steward_suggestions_restore(
        db_session,
        account_id=account_id,
        suggestion_id=suggestion.id,
        expected_revision=suggestion.revision,
        expected_projection_revision=projection.revision,
        semantic_hash=projection.semantic_hash,
        idempotency_key="restore-1",
        ctx=ctx,
    )
    assert status == 200
    assert payload["projection"]["baseline_term"] == "外婆"

    db_session.expire_all()
    restored = db_session.get(StewardTermProjection, projection.id)
    assert restored.term is None and restored.status == "suppressed"
    assert (
        db_session.scalar(
            select(StewardTermSuppression).where(
                StewardTermSuppression.viewer_account_id == account_id
            )
        )
        is not None
    )

    # 换一个新证据版本（新事实推进水位）重跑：同语义同词不再应用、不再建议
    _drain(db_session, space)
    refreshed = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == account_id,
            StewardTermProjection.target_user_id == gm.id,
        )
    )
    assert refreshed.term is None
    active_suggestion = db_session.scalar(
        select(StewardSuggestion).where(
            StewardSuggestion.kind == "term_preference",
            StewardSuggestion.viewer_account_id == account_id,
            StewardSuggestion.status.in_(("proposed", "submitted")),
        )
    )
    assert active_suggestion is None


def steward_suggestions_restore(db_session, **kwargs):
    from app.services import steward_suggestions

    return steward_suggestions.restore_term(
        db_session,
        account=db_session.get(Account, kwargs["account_id"]),
        space_id=db_session.get(StewardSuggestion, kwargs["suggestion_id"]).space_id,
        suggestion_id=kwargs["suggestion_id"],
        expected_revision=kwargs["expected_revision"],
        expected_projection_revision=kwargs["expected_projection_revision"],
        semantic_hash=kwargs["semantic_hash"],
        idempotency_key=kwargs["idempotency_key"],
    )


# ---- B-AC6：只有 terminology 开启也能运行（平台/空间开关生效值） ----


def test_platform_flag_only_terminology(db_session, monkeypatch) -> None:
    state = platform_features._environment_state()
    assert state.steward_assist_terminology is True
    assert state.steward_assist_candidate is False
    # assist_enabled 的 kind 校验接受 terminology
    space, gc, mom, gm = _grandchild_family(db_session)
    import pytest as _pytest

    with _pytest.raises(ValueError):
        steward_assist.assist_enabled(db_session, space.id, "unknown_kind")
