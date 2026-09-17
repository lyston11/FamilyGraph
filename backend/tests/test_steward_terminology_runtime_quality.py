"""Production-job regressions for terminology governance and refresh."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import delete, select
from test_steward_terminology import (
    _account_id,
    _completions_fake,
    _drain,
    _enable_provider,
    _grandchild_family,
    _terminology_payload,
)
from test_steward_terminology_quality import _model_grandmother_term
from test_steward_terminology_quality import terminology_setup as terminology_setup

from app import config
from app.db import SessionLocal
from app.models.account import Account
from app.models.agent_provider import AgentProvider
from app.models.relationship_facts import SourceFact
from app.models.space import SpaceMember
from app.models.steward import (
    ActionCard,
    StewardAssistBatch,
    StewardModelCall,
    StewardTermProjection,
)
from app.services import personal_family_view, steward_assist, steward_terminology, terms
from app.services import steward as steward_service
from app.utils import timeutil


def _integrity_scan(db_session, space):
    steward_service.enqueue_steward_job(
        db_session,
        space_id=space.id,
        cause="integrity_scan",
        trigger_cursor=steward_service.current_event_watermark(db_session),
    )
    _drain(db_session, space)


def test_model_output_refreshes_tree_and_survives_next_core_scan(db_session):
    space, gc, _mom, gm = _grandchild_family(db_session)
    _model_grandmother_term(db_session, space)
    _drain(db_session, space)
    _integrity_scan(db_session, space)
    account_id = _account_id(db_session, gc)
    payload = personal_family_view.current_view_payload(
        db_session,
        account=db_session.get(Account, account_id),
        space_id=space.id,
    )
    assert payload["status"] == "current"
    assert next(e for e in payload["edges"] if e["to_user_id"] == gm.id)["term"] == "姥姥"
    groups = steward_terminology.collect_model_groups(
        db_session,
        space_id=space.id,
        max_groups=10,
        max_targets=8,
    )
    assert not any(g["viewer_account_id"] == account_id for g in groups)


def test_builtin_model_alias_works_without_reseeding_existing_database(db_session):
    # Reproduce an existing database with the original migrated 外婆 vocabulary.
    db_session.execute(delete(terms.TermEntry).where(terms.TermEntry.term == "姥姥"))
    db_session.commit()
    space, gc, _mom, gm = _grandchild_family(db_session)
    _model_grandmother_term(db_session, space)
    assert (
        terms.compose_resolution_view(
            db_session,
            account_id=_account_id(db_session, gc),
            viewer_user_id=gc.id,
            target_user_id=gm.id,
            space_id=space.id,
        )["term"]
        == "姥姥"
    )


def test_unknown_result_is_not_resent_in_a_new_job_or_prompt_version(db_session, monkeypatch):
    space, gc, _mom, _gm = _grandchild_family(db_session)
    _enable_provider(db_session, space)
    _drain(db_session, space)

    def unknown(url, headers, payload, timeout):
        assert not db_session.in_transaction()
        raise httpx.ReadTimeout("synthetic timeout")

    assert steward_assist.run_due_batch(db_session, transport=unknown) == "failed"
    rows = list(db_session.scalars(select(StewardModelCall)))
    assert rows and all(row.status == "unknown" for row in rows)
    monkeypatch.setattr(steward_terminology, "PROMPT_VERSION", "synthetic-next-prompt")
    _integrity_scan(db_session, space)
    groups = steward_terminology.collect_model_groups(
        db_session,
        space_id=space.id,
        max_groups=10,
        max_targets=8,
    )
    assert not any(g["viewer_account_id"] == _account_id(db_session, gc) for g in groups)
    assert steward_assist.run_due_batch(db_session, transport=unknown) is None


def test_unsent_failures_have_a_cross_job_retry_limit_and_delay(db_session, monkeypatch):
    space, gc, _mom, _gm = _grandchild_family(db_session)
    _enable_provider(db_session, space)
    _drain(db_session, space)

    def unsent(url, headers, payload, timeout):
        raise httpx.ConnectTimeout("synthetic unsent timeout")

    assert steward_assist.run_due_batch(db_session, transport=unsent) == "failed"
    assert (
        steward_terminology.collect_model_groups(
            db_session,
            space_id=space.id,
            max_groups=10,
            max_targets=8,
        )
        == []
    )
    later = timeutil.utcnow() + timedelta(seconds=61)
    monkeypatch.setattr(timeutil, "utcnow", lambda: later)
    _integrity_scan(db_session, space)
    assert steward_assist.run_due_batch(db_session, transport=unsent) == "failed"
    later += timedelta(seconds=61)
    _integrity_scan(db_session, space)
    assert steward_assist.run_due_batch(db_session, transport=unsent) is None
    viewer_calls = list(
        db_session.scalars(
            select(StewardModelCall).where(
                StewardModelCall.viewer_account_id == _account_id(db_session, gc),
            )
        )
    )
    assert len(viewer_calls) == 2


def test_one_call_budget_rotates_from_candidate_to_terminology(db_session, monkeypatch):
    from app.models.agent_provider import AgentSpaceProviderSetting

    space, _gc, _mom, _gm = _grandchild_family(db_session)
    _enable_provider(db_session, space)
    setting = db_session.scalar(
        select(AgentSpaceProviderSetting).where(
            AgentSpaceProviderSetting.space_id == space.id,
        )
    )
    setting.assist_candidate = True
    db_session.commit()
    monkeypatch.setattr(config, "STEWARD_ASSIST_CANDIDATE", True)
    monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB", 1)
    _drain(db_session, space)
    batch = steward_assist.schedule_due_batch(db_session)
    assert batch is not None
    assert list(
        db_session.scalars(
            select(StewardModelCall.assist_kind).where(
                StewardModelCall.batch_id == batch.id,
                StewardModelCall.status == "reserved",
            )
        )
    ) == ["candidate"]

    def empty_candidate(url, headers, payload, timeout):
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "[]"}]}]}

    assert (
        steward_assist.execute_batch(db_session, batch.id, transport=empty_candidate) == "applied"
    )
    _integrity_scan(db_session, space)
    next_batch = steward_assist.schedule_due_batch(db_session)
    assert next_batch is not None
    assert list(
        db_session.scalars(
            select(StewardModelCall.assist_kind).where(
                StewardModelCall.batch_id == next_batch.id,
                StewardModelCall.status == "reserved",
            )
        )
    ) == ["terminology"]


@pytest.mark.parametrize("change", ["fact_revision", "personal_preference", "revoke_membership"])
def test_midflight_input_change_discards_model_writeback(db_session, change):
    from app.models.space import SpaceMember

    space, gc, _mom, gm = _grandchild_family(db_session)
    _enable_provider(db_session, space)
    _drain(db_session, space)
    batch = steward_assist.schedule_due_batch(db_session)
    assert batch is not None

    def changed(session, _batch):
        if change == "fact_revision":
            fact = session.scalar(select(SourceFact).where(SourceFact.space_id == space.id))
            fact.revision += 1
        elif change == "personal_preference":
            terms.set_personal_term(
                session,
                account_id=_account_id(session, gc),
                space_id=space.id,
                concept_code="Uf-Uf",
                term="阿婆",
            )
        else:
            member = session.scalar(
                select(SpaceMember).where(
                    SpaceMember.space_id == space.id,
                    SpaceMember.user_id == gc.id,
                )
            )
            member.status = "removed"
        session.commit()

    status = steward_assist.execute_batch(
        db_session,
        batch.id,
        after_send=changed,
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
    assert status == "superseded"
    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == _account_id(db_session, gc),
            StewardTermProjection.target_user_id == gm.id,
        )
    )
    assert projection is None or projection.term is None


def test_age_variant_never_erases_adoptive_subtype(db_session):
    context = terms.VariantContext(
        viewer_user_id=1,
        path=[{"from": 1, "to": 2}, {"from": 2, "to": 3}],
        births={1: ("solar", 1990), 3: ("solar", 1980)},
    )
    assert not steward_terminology.term_semantics_valid(
        db_session,
        account_id=1,
        space_id=1,
        concept_code="Uam-Dm",
        term="哥哥",
        variant_context=context,
    )


@pytest.mark.parametrize("change", ["fact_revision", "revoke_membership", "provider_endpoint"])
def test_writeback_observes_changes_from_another_database_session(db_session, change):
    space, gc, _mom, gm = _grandchild_family(db_session)
    _enable_provider(db_session, space)
    _drain(db_session, space)
    batch = steward_assist.schedule_due_batch(db_session)
    assert batch is not None
    # Keep strong references: SessionLocal deliberately retains its identity map
    # across commits, so a new transaction alone is not a freshness guarantee.
    cached = {
        "fact_revision": db_session.scalar(
            select(SourceFact).where(SourceFact.space_id == space.id)
        ),
        "revoke_membership": db_session.scalar(
            select(SpaceMember).where(
                SpaceMember.space_id == space.id,
                SpaceMember.user_id == gc.id,
            )
        ),
        "provider_endpoint": db_session.scalar(select(AgentProvider)),
    }

    def changed(_session, _batch):
        with SessionLocal() as writer:
            row = writer.get(type(cached[change]), cached[change].id)
            if change == "fact_revision":
                row.revision += 1
            elif change == "revoke_membership":
                row.status = "removed"
            else:
                row.base_url = "https://changed.example.invalid/v1"
            writer.commit()

    status = steward_assist.execute_batch(
        db_session,
        batch.id,
        after_send=changed,
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
    assert status == "superseded"
    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == _account_id(db_session, gc),
            StewardTermProjection.target_user_id == gm.id,
        )
    )
    assert projection is None or projection.term is None


def test_old_executor_cannot_audit_after_another_session_takes_the_lease(db_session):
    space, _gc, _mom, _gm = _grandchild_family(db_session)
    _enable_provider(db_session, space)
    _drain(db_session, space)
    batch = steward_assist.schedule_due_batch(db_session)
    assert batch is not None
    original_attempt = batch.attempt

    def take_over(_session, old_batch):
        with SessionLocal() as writer:
            current = writer.get(StewardAssistBatch, old_batch.id)
            current.lease_owner = "replacement-worker"
            current.attempt += 1
            writer.commit()

    # 另一会话在本笔请求在飞期间接管租约：旧执行者随后的逐笔结算必须被拒，
    # 行保持 in_flight 且无 output_json，交由恢复器/新 owner 处理。
    answer = _completions_fake(
        _terminology_payload(
            {
                "target_ref": "t002",
                "concept_code": "Uf-Uf",
                "term": "姥姥",
                "reason_code": "synonym",
            }
        )
    )
    took_over: list[bool] = []

    def transport(url, headers, payload, timeout):
        if not took_over:
            took_over.append(True)
            take_over(db_session, batch)
        return answer(url, headers, payload, timeout)

    assert (
        steward_assist.execute_batch(
            db_session,
            batch.id,
            transport=transport,
        )
        == "applying"
    )
    assert took_over == [True]
    assert batch.lease_owner == "replacement-worker"
    assert batch.attempt == original_attempt + 1
    calls = list(
        db_session.scalars(
            select(StewardModelCall).where(
                StewardModelCall.batch_id == batch.id,
            )
        )
    )
    # 旧执行者零结算：本笔保持 in_flight（不落 output_json/计费），接管后的下一笔
    # 因租约身份不符而未发送。
    assert calls and all(
        call.status in ("in_flight", "reserved") and call.output_json is None for call in calls
    )
    assert any(call.status == "in_flight" for call in calls)
    assert all(call.billed_tokens is None for call in calls)


@pytest.mark.parametrize("lease_change", ["expired", "taken_over"])
def test_recovery_alone_applies_persisted_output_after_lease_loss(
    db_session, monkeypatch, lease_change
):
    space, gc, _mom, gm = _grandchild_family(db_session)
    _enable_provider(db_session, space)
    _drain(db_session, space)
    batch = steward_assist.schedule_due_batch(db_session)
    assert batch is not None
    apply = steward_assist._apply_batch
    held = {}

    def pause_after_audit(_session, batch_id, **kwargs):
        held.update(kwargs)
        return "applying"

    monkeypatch.setattr(steward_assist, "_apply_batch", pause_after_audit)
    assert (
        steward_assist.execute_batch(
            db_session,
            batch.id,
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
        == "applying"
    )
    monkeypatch.setattr(steward_assist, "_apply_batch", apply)
    with SessionLocal() as writer:
        current = writer.get(StewardAssistBatch, batch.id)
        if lease_change == "expired":
            current.lease_until = timeutil.utcnow() - timedelta(seconds=1)
        else:
            current.lease_owner = "replacement-worker"
            current.attempt += 1
        writer.commit()
    assert apply(db_session, batch.id, **{**held, "now": timeutil.utcnow()}) == "applying"
    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == _account_id(db_session, gc),
            StewardTermProjection.target_user_id == gm.id,
        )
    )
    assert projection is None or projection.term is None
    with SessionLocal() as writer:
        current = writer.get(StewardAssistBatch, batch.id)
        current.lease_until = timeutil.utcnow() - timedelta(seconds=1)
        writer.commit()
    assert steward_assist.recover_stuck_batches(db_session) >= 1
    assert batch.status == "applied"
    assert batch.lease_owner.startswith("recovery:")
    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == _account_id(db_session, gc),
            StewardTermProjection.target_user_id == gm.id,
        )
    )
    assert projection is not None and projection.term == "姥姥"
    assert steward_assist.recover_stuck_batches(db_session) == 0


def test_model_recreates_a_missing_projection_with_its_real_baseline(db_session):
    space, gc, _mom, gm = _grandchild_family(db_session)
    _enable_provider(db_session, space)
    _drain(db_session, space)
    batch = steward_assist.schedule_due_batch(db_session)
    assert batch is not None
    account_id = _account_id(db_session, gc)

    def remove_projection(_session, _batch):
        with SessionLocal() as writer:
            writer.execute(
                delete(StewardTermProjection).where(
                    StewardTermProjection.viewer_account_id == account_id,
                    StewardTermProjection.target_user_id == gm.id,
                )
            )
            writer.commit()

    assert (
        steward_assist.execute_batch(
            db_session,
            batch.id,
            after_send=remove_projection,
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
    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == account_id,
            StewardTermProjection.target_user_id == gm.id,
        )
    )
    assert projection.term == "姥姥"
    assert projection.baseline_term == "外婆"
    assert projection.baseline_source == "locale"


def test_reading_an_unrelated_card_does_not_discard_terminology_output(db_session):
    from test_steward import _confirm, _person

    from app.services import action_cards

    space, gc, _mom, gm = _grandchild_family(db_session)
    # A disconnected parent-child pair legitimately produces a household card,
    # without changing any input to the grandchild's terminology request.
    parent = _person(db_session, space.id, "term-unrelated-parent", member=False, ref=True)
    child = _person(db_session, space.id, "term-unrelated-child", member=False, ref=True)
    _confirm(db_session, "biological_parent", parent.id, child.id, space_id=space.id)
    _enable_provider(db_session, space)
    _drain(db_session, space)
    batch = steward_assist.schedule_due_batch(db_session)
    assert batch is not None
    card = db_session.scalar(
        select(ActionCard).where(
            ActionCard.space_id == space.id,
            ActionCard.state == "pending",
        )
    )
    assert card is not None

    def read_card(_session, _batch):
        with SessionLocal() as reader:
            current = reader.get(ActionCard, card.id)
            action_cards.transition_card(
                reader,
                current,
                "view",
                expected_revision=current.revision,
                actor_account_id=current.recipient_account_id,
            )
            reader.commit()

    assert (
        steward_assist.execute_batch(
            db_session,
            batch.id,
            after_send=read_card,
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
    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == _account_id(db_session, gc),
            StewardTermProjection.target_user_id == gm.id,
        )
    )
    assert projection.term == "姥姥"
