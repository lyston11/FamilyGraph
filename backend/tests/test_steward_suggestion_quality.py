"""Authorization, semantic direction and stale-action regressions for kinship suggestions."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import create_agent_fixture, create_space_member, create_user_with_pin
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_kinship_presentation import _login_header
from test_steward_inferred import _candidate, _make_job, _member
from test_steward_terminology import _drain, _grandchild_family

from app import config
from app.commands.context import ActorContext
from app.commands.relationship_proposals import (
    confirm_relationship_proposal,
    create_relationship_proposal,
)
from app.models.relationship_facts import SourceFact
from app.models.steward import StewardTermProjection, StewardTermSuppression
from app.models.steward_inferred import StewardInferredEdge
from app.models.steward_suggestion import StewardSuggestion
from app.services import (
    kinship_presentation,
    notifications,
    personal_family_view,
    source_facts,
    steward_inferred,
    steward_suggestions,
    steward_terminology,
    terms,
)
from app.utils.timeutil import utcnow


@pytest.fixture(autouse=True)
def _environment(db_session, monkeypatch):
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    terms.seed_builtin_packs(db_session)
    db_session.commit()


def _ctx(user):
    return ActorContext(user_id=user.id, account_id=user.account.id, account_status="claimed")


def _suggestion(
    session,
    space,
    subject,
    target,
    *,
    kind="relation_proposal",
    value=None,
    viewer=None,
    evidence=None,
    source_candidate_id=None,
):
    row, _ = steward_suggestions.upsert_suggestion(
        session,
        space_id=space.id,
        origin="model" if kind == "relation_proposal" else "deterministic",
        kind=kind,
        subject_user_id=subject.id,
        object_user_id=target.id,
        value_json=value or {"fact_type": "biological_parent"},
        evidence_json=evidence or {"facts": []},
        policy_version="quality-test",
        viewer_account_id=viewer.account.id if viewer is not None else None,
        recipient_account_ids=[subject.account.id, target.account.id],
        source_candidate_id=source_candidate_id,
    )
    session.commit()
    return row


def _detail(session, space, viewer, suggestion):
    return steward_suggestions.get_suggestion_detail(
        session,
        account=viewer.account,
        space_id=space.id,
        suggestion_id=suggestion.id,
    )


def _submit(session, space, viewer, suggestion, key="submit"):
    return steward_suggestions.submit_suggestion(
        session,
        _ctx(viewer),
        account=viewer.account,
        space_id=space.id,
        suggestion_id=suggestion.id,
        expected_revision=suggestion.revision,
        evidence_hash=suggestion.evidence_hash,
        confirm=True,
        idempotency_key=key,
    )


def _term_fixture(session):
    space, child, _mother, grandmother = _grandchild_family(session)
    context = steward_terminology.current_target_context(
        session,
        viewer_account_id=child.account.id,
        root_user_id=child.id,
        space_id=space.id,
        target_user_id=grandmother.id,
    )
    assert context is not None
    projection, _ = steward_terminology.upsert_projection(
        session,
        space_id=space.id,
        viewer_account_id=child.account.id,
        root_user_id=child.id,
        target_user_id=grandmother.id,
        concept_code="Uf-Uf",
        semantic_hash=context["semantic_hash"],
        baseline_term=context["baseline_term"],
        baseline_source=context["baseline_source"],
        term="姥姥",
        origin="deterministic",
    )
    suggestion, created = steward_terminology.upsert_term_preference_suggestion(
        session,
        space_id=space.id,
        viewer_account_id=child.account.id,
        subject_user_id=child.id,
        object_user_id=grandmother.id,
        concept_code="Uf-Uf",
        term="姥姥",
        projection_id=projection.id,
        projection_revision=projection.revision,
        semantic_hash=projection.semantic_hash,
        reason_code="preferred_usage",
        policy_version="quality-test",
        origin="deterministic",
    )
    assert created
    session.commit()
    return space, child, grandmother, suggestion, projection


def test_personal_suggestions_filter_viewer_and_target_before_pagination(client, db_session):
    owner, space = create_agent_fixture(db_session, name="sq-private")
    other = _member(db_session, space, "sq-private-other", "f")
    target = _member(db_session, space, "sq-private-target", "m")
    own = _suggestion(
        db_session,
        space,
        owner,
        target,
        kind="term_preference",
        viewer=owner,
        value={"concept_code": "Um", "term": "我的私有叫法"},
    )
    for index in range(24):
        _suggestion(
            db_session,
            space,
            other,
            target,
            kind="term_preference",
            viewer=other,
            value={"concept_code": "Um", "term": f"其他人的私有叫法{index}"},
        )
        _suggestion(
            db_session,
            space,
            owner,
            other,
            kind="term_preference",
            viewer=owner,
            value={"concept_code": "Uf", "term": f"不同目标{index}"},
        )
    headers = _login_header(client, owner.name)
    response = client.get(
        "/api/steward-suggestions",
        headers=headers,
        params={
            "space_id": space.id,
            "kind": "term_preference",
            "target_user_id": target.id,
            "limit": 1,
        },
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [own.id]
    assert response.json()["next_cursor"] is None
    denied = client.get(
        f"/api/steward-suggestions/{own.id}",
        headers=_login_header(client, other.name),
        params={"space_id": space.id},
    )
    assert denied.status_code == 404
    # Deliberately misaddressed historical notification must not leak the value.
    notifications.record_suggestion_notification(
        db_session, suggestion=own, recipient_account_id=other.account.id
    )
    db_session.commit()
    notice = notifications.list_notifications_page(
        db_session, account=other.account, space_id=space.id
    )
    assert all(item["suggestion"]["suggestion_id"] != own.id for item in notice["items"])


@pytest.mark.parametrize(
    ("kind", "up", "down"),
    [
        ("biological_parent", "爸爸", "儿子"),
        ("adoptive_parent", "养父", "养子"),
        ("step_parent", "继父", "继子"),
        ("guardian", "监护人", "受监护子女"),
    ],
)
def test_candidate_direction_uses_actual_fact_for_all_viewers(db_session, kind, up, down):
    parent, space = create_agent_fixture(db_session, name=f"sq-direction-{kind}")
    parent.gender = "m"
    child = _member(db_session, space, f"sq-direction-child-{kind}", "m")
    other = _member(db_session, space, f"sq-direction-other-{kind}", "f")
    fact = source_facts.create_source_fact(
        db_session,
        fact_type="direct_sibling",
        subject_user_id=parent.id,
        object_user_id=child.id,
        provenance="manual_entry",
        space_id=space.id,
        state="confirmed",
    )
    assert fact.state == "confirmed"
    personal_family_view.rebuild_view(db_session, account=child.account, space_id=space.id)
    suggestion = _suggestion(db_session, space, parent, child, value={"fact_type": kind})
    for viewer, expected in (
        (parent, f"{child.name}可能是你的{down}"),
        (child, f"{parent.name}可能是你的{up}"),
        (other, f"{parent.name}可能是{child.name}的{up}"),
    ):
        presentation = _detail(db_session, space, viewer, suggestion)["presentation"]
        reference = parent.id if viewer.id == parent.id else child.id
        target = child.id if viewer.id == parent.id else parent.id
        assert presentation["summary"] == expected
        assert presentation["reference_user_id"] == reference
        assert presentation["target_user_id"] == target
        inferred = kinship_presentation.build_inferred_edge_presentation(
            db_session,
            viewer=viewer,
            space_id=space.id,
            subject_user_id=parent.id,
            object_user_id=child.id,
            fact_type=kind,
            term=down,
            evidence_fact_count=20,
        )
        assert inferred["summary"] == expected
        assert inferred["reference_user_id"] == reference
        assert inferred["target_user_id"] == target
        assert inferred["evidence"]["kind"] == "unverified_candidate"
    notice = notifications.list_notifications_page(
        db_session, account=child.account, space_id=space.id
    )
    assert notice["items"][0]["payload"]["summary"] == f"{parent.name}可能是你的{up}"
    if kind == "biological_parent":
        for person in (child, other):
            terms.set_personal_term(
                db_session,
                account_id=person.account.id,
                space_id=space.id,
                concept_code="Um",
                term="我的老爸",
            )
        db_session.commit()
        assert _detail(db_session, space, child, suggestion)["presentation"]["term"] == "我的老爸"
        assert _detail(db_session, space, other, suggestion)["presentation"]["term"] == "爸爸"


@pytest.mark.parametrize("bad_evidence", ["adjacent", "revoked", "stale_revision", "invisible"])
def test_space_snapshot_is_not_supporting_evidence(db_session, bad_evidence):
    owner, space = create_agent_fixture(db_session, name=f"sq-evidence-{bad_evidence}")
    target = _member(db_session, space, f"sq-evidence-target-{bad_evidence}", "m")
    third = _member(db_session, space, f"sq-evidence-third-{bad_evidence}", "m")
    if bad_evidence == "invisible":
        third = create_user_with_pin(db_session, "sq-invisible", "123456")
    endpoint = third if bad_evidence in ("adjacent", "invisible") else target
    fact = source_facts.create_source_fact(
        db_session,
        fact_type="biological_parent",
        subject_user_id=owner.id,
        object_user_id=endpoint.id,
        provenance="manual_entry",
        space_id=space.id,
        state="confirmed",
    )
    snapshot = {
        "id": fact.id,
        "revision": fact.revision,
        "subject_user_id": owner.id,
        "object_user_id": endpoint.id,
    }
    if bad_evidence == "revoked":
        fact.state = "revoked"
    elif bad_evidence == "stale_revision":
        fact.fact_type = "direct_sibling"
        fact.revision += 1
    db_session.commit()
    suggestion = _suggestion(db_session, space, owner, target, evidence={"facts": [snapshot]})
    detail = _detail(db_session, space, target, suggestion)
    assert detail["evidence_summary"] == {
        "kind": "unverified_candidate",
        "fact_count": 0,
        "related_fact_count": 0,
        "facts": [],
    }
    assert detail["presentation"]["evidence"]["kind"] == "unverified_candidate"


@pytest.mark.parametrize(
    ("raw_state", "elapsed", "state", "domain_status"),
    [
        ("proposed", True, "expired", "expired"),
        ("submitted", True, "expired", "expired"),
        ("submitted", False, "submitted", "accepted"),
        ("resolved", False, "resolved", "done"),
        ("superseded", False, "superseded", "revoked"),
    ],
)
def test_terminal_actions_and_notification_effective_status(
    db_session, raw_state, elapsed, state, domain_status
):
    owner, space = create_agent_fixture(db_session, name=f"sq-terminal-{raw_state}")
    target = _member(db_session, space, f"sq-terminal-target-{raw_state}", "m")
    suggestion = _suggestion(db_session, space, owner, target)
    suggestion.status = raw_state
    if elapsed:
        suggestion.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    detail = _detail(db_session, space, owner, suggestion)
    assert detail["state"] == state
    assert detail["allowed_actions"] == ["open_details"]
    notice = notifications.list_notifications_page(
        db_session, account=owner.account, space_id=space.id
    )
    assert notice["items"][0]["suggestion"]["state"] == state
    assert notice["items"][0]["domain_status"] == domain_status


def test_hidden_endpoint_suggestion_also_disappears_from_notifications(db_session):
    owner, space = create_agent_fixture(db_session, name="sq-hidden")
    hidden = create_user_with_pin(db_session, "sq-hidden-outsider", "123456")
    suggestion = _suggestion(db_session, space, owner, hidden)
    assert (
        notifications.list_notifications_page(db_session, account=owner.account, space_id=space.id)[
            "items"
        ]
        == []
    )
    with pytest.raises(HTTPException) as exc:
        _detail(db_session, space, owner, suggestion)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("scope", ["current", "global", "foreign"])
def test_exact_confirmed_relation_retires_existing_todo_and_skips_new_projection(db_session, scope):
    owner, space = create_agent_fixture(db_session, name=f"sq-redundant-{scope}")
    owner.gender = "m"
    target = _member(db_session, space, f"sq-redundant-target-{scope}", "m")
    suggestion = _suggestion(db_session, space, owner, target)
    fact_space = space.id if scope == "current" else None
    if scope == "foreign":
        _foreign_owner, foreign = create_agent_fixture(
            db_session, name="sq-redundant-foreign-space"
        )
        fact_space = foreign.id
    fact = source_facts.create_source_fact(
        db_session,
        fact_type="biological_parent",
        subject_user_id=owner.id,
        object_user_id=target.id,
        space_id=fact_space,
        provenance="manual_entry",
        state="confirmed",
    )
    db_session.commit()
    detail = _detail(db_session, space, target, suggestion)
    if scope == "foreign":
        assert detail["state"] == "proposed"
        assert detail["presentation"]["inferred"] is True
    else:
        assert detail["state"] == "resolved"
        assert detail["allowed_actions"] == ["open_details"]
        assert detail["presentation"]["summary"] == f"{owner.name}是你的爸爸"
        assert detail["evidence_summary"]["facts"] == [
            {"fact_id": fact.id, "revision": fact.revision}
        ]
        notice = notifications.list_notifications_page(
            db_session, account=target.account, space_id=space.id
        )
        assert notice["items"][0]["domain_status"] == "done"
        reverse = _suggestion(db_session, space, target, owner)
        assert _detail(db_session, space, owner, reverse)["state"] == "proposed"
    suggestion.status = "expired"
    db_session.commit()
    job = _make_job(db_session, space)
    _candidate(db_session, job, kind="biological_parent", subject_id=owner.id, object_id=target.id)
    created = steward_suggestions.project_for_job(db_session, job, findings=[], facts=[])
    assert created == (1 if scope == "foreign" else 0)


@pytest.mark.parametrize(
    ("removal", "state", "domain"),
    [
        ("revoke", "superseded", "revoked"),
        ("delete", "rejected", "rejected"),
    ],
)
def test_confirmed_fact_link_survives_revocation_or_deletion(db_session, removal, state, domain):
    parent, space = create_agent_fixture(db_session, name=f"sq-durable-{removal}")
    child = _member(db_session, space, f"sq-durable-child-{removal}", "m")
    suggestion = _suggestion(db_session, space, parent, child)
    steward_suggestions.dismiss_suggestion(
        db_session,
        account=parent.account,
        space_id=space.id,
        suggestion_id=suggestion.id,
        expected_revision=suggestion.revision,
    )
    previous_revision = suggestion.revision
    fact = source_facts.create_source_fact(
        db_session,
        fact_type="biological_parent",
        subject_user_id=parent.id,
        object_user_id=child.id,
        space_id=space.id,
        provenance="manual_entry",
        state="confirmed",
    )
    db_session.commit()
    assert suggestion.status == "resolved"
    assert suggestion.linked_fact_id == fact.id
    assert suggestion.revision == previous_revision + 1
    assert _detail(db_session, space, child, suggestion)["linked_proposal"]["state"] == "confirmed"
    assert steward_suggestions.resolve_for_linked_fact(db_session, fact_id=fact.id) == 0
    assert suggestion.revision == previous_revision + 1
    if removal == "revoke":
        source_facts.transition_source_fact(db_session, fact, source_facts.ACTION_REVOKE)
    else:
        db_session.delete(fact)
    db_session.commit()
    detail = _detail(db_session, space, child, suggestion)
    assert detail["state"] == state
    assert detail["allowed_actions"] == ["open_details"]
    assert _detail(db_session, space, parent, suggestion)["state"] == "dismissed"
    assert suggestion.status == "resolved"
    notice = notifications.list_notifications_page(
        db_session, account=child.account, space_id=space.id
    )
    assert notice["items"][0]["domain_status"] == domain


@pytest.mark.parametrize("global_fact", [False, True])
def test_confirmed_resolution_persists_exact_scope_type_and_direction(db_session, global_fact):
    parent, first_space = create_agent_fixture(db_session, name="sq-persist-first")
    child = _member(db_session, first_space, "sq-persist-child", "m")
    _other_owner, other_space = create_agent_fixture(db_session, name="sq-persist-other")
    for person in (parent, child):
        create_space_member(db_session, other_space.id, person.id)
    db_session.commit()
    matched = _suggestion(db_session, first_space, parent, child)
    foreign = _suggestion(db_session, other_space, parent, child)
    reverse = _suggestion(db_session, first_space, child, parent)
    sibling = _suggestion(
        db_session, first_space, parent, child, value={"fact_type": "direct_sibling"}
    )
    fact = source_facts.create_source_fact(
        db_session,
        fact_type="biological_parent",
        subject_user_id=parent.id,
        object_user_id=child.id,
        space_id=None if global_fact else first_space.id,
        provenance="manual_entry",
        state="confirmed",
    )
    db_session.commit()
    assert matched.status == "resolved" and matched.linked_fact_id == fact.id
    assert foreign.status == ("resolved" if global_fact else "proposed")
    assert foreign.linked_fact_id == (fact.id if global_fact else None)
    assert reverse.status == sibling.status == "proposed"
    assert reverse.linked_fact_id is None and sibling.linked_fact_id is None


def test_job_backfills_historical_resolution_once(db_session):
    parent, space = create_agent_fixture(db_session, name="sq-backfill")
    child = _member(db_session, space, "sq-backfill-child", "m")
    suggestion = _suggestion(db_session, space, parent, child)
    # An old imported confirmed row predates the source_fact.confirmed consumer.
    fact = SourceFact(
        fact_type="biological_parent",
        subject_user_id=parent.id,
        object_user_id=child.id,
        space_id=space.id,
        provenance="import",
        state="confirmed",
        revision=1,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db_session.add(fact)
    db_session.commit()
    assert suggestion.status == "proposed"
    original_revision = suggestion.revision
    job = _make_job(db_session, space)
    for _ in range(2):
        assert steward_suggestions.project_for_job(db_session, job, findings=[], facts=[fact]) == 0
    assert suggestion.status == "resolved" and suggestion.linked_fact_id == fact.id
    assert suggestion.revision == original_revision + 1


def _same_origin(session):
    owner, space = create_agent_fixture(session, name="sq-origin")
    parent = _member(session, space, "sq-origin-parent", "m")
    child = _member(session, space, "sq-origin-child", "m")
    job = _make_job(session, space)
    candidate = _candidate(
        session, job, kind="biological_parent", subject_id=parent.id, object_id=child.id
    )
    suggestion = _suggestion(session, space, parent, child, source_candidate_id=candidate.id)
    edge = StewardInferredEdge(
        space_id=space.id,
        subject_user_id=parent.id,
        object_user_id=child.id,
        relation_kind="biological_parent",
        status="proposed",
        origin="llm",
        source_candidate_id=candidate.id,
        evidence_hash="a" * 64,
        evidence_json={"facts": []},
        revision=1,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(edge)
    session.commit()
    return owner, space, parent, child, suggestion, edge


def test_tree_rejection_is_shared_and_notification_dismissal_stays_private(db_session):
    owner, space, parent, child, suggestion, edge = _same_origin(db_session)
    steward_suggestions.dismiss_suggestion(
        db_session,
        account=parent.account,
        space_id=space.id,
        suggestion_id=suggestion.id,
        expected_revision=suggestion.revision,
    )
    assert edge.status == "proposed"
    assert _detail(db_session, space, child, suggestion)["state"] == "proposed"
    steward_inferred.dismiss_edge(
        db_session,
        account=owner.account,
        space_id=space.id,
        edge_id=edge.id,
        expected_revision=edge.revision,
    )
    assert _detail(db_session, space, parent, suggestion)["state"] == "dismissed"
    other = _detail(db_session, space, child, suggestion)
    assert other["state"] == other["source_state"] == "rejected"
    assert other["allowed_actions"] == ["open_details"]
    with pytest.raises(HTTPException) as exc:
        _submit(db_session, space, child, suggestion)
    assert exc.value.status_code == 409
    steward_inferred.reinstate_edge(
        db_session,
        account=owner.account,
        space_id=space.id,
        edge_id=edge.id,
        expected_revision=edge.revision,
    )
    assert _detail(db_session, space, child, suggestion)["state"] == "proposed"
    assert _detail(db_session, space, parent, suggestion)["state"] == "dismissed"


@pytest.mark.parametrize("first", ["tree", "notification"])
def test_two_entrypoints_share_one_proposal_and_detail_has_live_state(db_session, first):
    owner, space, parent, child, suggestion, edge = _same_origin(db_session)

    def via_tree():
        return steward_inferred.confirm_edge(
            db_session,
            _ctx(owner),
            account=owner.account,
            space_id=space.id,
            edge_id=edge.id,
            expected_revision=edge.revision,
        )

    status, initial = (
        via_tree() if first == "tree" else _submit(db_session, space, owner, suggestion)
    )
    assert status == 202
    status, repeated = (
        _submit(db_session, space, owner, suggestion, "other-entry")
        if first == "tree"
        else via_tree()
    )
    assert status == 202
    fact_id = initial["linked_proposal"]["source_fact_id"]
    assert repeated["linked_proposal"]["source_fact_id"] == fact_id
    assert db_session.scalar(select(SourceFact.id)) == fact_id
    detail = _detail(db_session, space, child, suggestion)
    assert detail["state"] == "submitted"
    assert detail["allowed_actions"] == ["open_details"]
    assert detail["linked_proposal"]["state"] == "proposed"
    assert detail["pending_confirmations"]
    fact = db_session.get(SourceFact, fact_id)
    confirm_relationship_proposal(
        db_session, _ctx(parent), fact_id, expected_revision=fact.revision
    )
    detail = _detail(db_session, space, child, suggestion)
    assert detail["state"] == "resolved"
    assert detail["linked_proposal"]["state"] == "confirmed"
    assert detail["pending_confirmations"] == []


@pytest.mark.parametrize("provenance", ["manual_entry", "agent_proposal"])
@pytest.mark.parametrize("is_endpoint", [False, True])
def test_old_tree_entry_reuses_already_confirmed_fact(db_session, provenance, is_endpoint):
    owner, space, parent, child, suggestion, edge = _same_origin(db_session)
    if provenance == "agent_proposal":
        _status, result = _submit(db_session, space, owner, suggestion)
        fact = db_session.get(SourceFact, result["linked_proposal"]["source_fact_id"])
        confirm_relationship_proposal(
            db_session, _ctx(parent), fact.id, expected_revision=fact.revision
        )
    else:
        fact = source_facts.create_source_fact(
            db_session,
            fact_type="biological_parent",
            subject_user_id=parent.id,
            object_user_id=child.id,
            space_id=space.id,
            provenance=provenance,
            state="confirmed",
        )
        db_session.commit()
    actor = parent if is_endpoint else owner
    for _ in range(2):
        status, result = steward_inferred.confirm_edge(
            db_session,
            _ctx(actor),
            account=actor.account,
            space_id=space.id,
            edge_id=edge.id,
            expected_revision=edge.revision,
        )
        assert status == 200
        assert result["edge"]["status"] == "confirmed"
        assert result["linked_proposal"]["source_fact_id"] == fact.id
        assert result["linked_proposal"]["state"] == "confirmed"
        assert result["pending_confirmations"] == []
    assert len(db_session.scalars(select(SourceFact)).all()) == 1
    assert suggestion.status == "resolved" and suggestion.linked_fact_id == fact.id


def test_proposal_from_other_space_is_not_reused(db_session):
    owner, space, parent, child, suggestion, _edge = _same_origin(db_session)
    other_owner, other_space = create_agent_fixture(db_session, name="sq-other-space")
    for user in (parent, child, owner):
        create_space_member(db_session, other_space.id, user.id)
    db_session.commit()
    foreign = create_relationship_proposal(
        db_session,
        _ctx(other_owner),
        space_id=other_space.id,
        fact_type="biological_parent",
        subject_user_id=parent.id,
        object_user_id=child.id,
        evidence_json={},
    )
    detail = _detail(db_session, space, owner, suggestion)
    assert detail["linked_proposal"] is None
    assert detail["state"] == "proposed"
    with pytest.raises(HTTPException) as exc:
        _submit(db_session, space, owner, suggestion)
    assert exc.value.status_code == 409
    assert db_session.get(StewardSuggestion, suggestion.id).linked_fact_id is None
    assert db_session.get(SourceFact, foreign.id).state == "proposed"


def test_valid_term_can_be_kept_and_targets_the_relative(db_session):
    space, viewer, target, suggestion, projection = _term_fixture(db_session)
    detail = _detail(db_session, space, viewer, suggestion)
    assert detail["presentation"]["target_user_id"] == target.id
    assert detail["value"]["can_restore"] is True
    assert detail["value"]["projection_revision"] == projection.revision
    assert "submit" in detail["allowed_actions"]
    status, payload = _submit(db_session, space, viewer, suggestion)
    assert status == 200
    assert payload["linked_preference"]["term"] == "姥姥"
    assert (
        terms.resolve_term(
            db_session, account_id=viewer.account.id, space_id=space.id, concept_code="Uf-Uf"
        ).source_level
        == "personal"
    )
    assert _detail(db_session, space, viewer, suggestion)["value"]["can_restore"] is False


def test_real_job_long_chain_baseline_can_be_kept_without_override(client, db_session):
    viewer, space = create_agent_fixture(db_session, name="sq-long-chain")
    ancestors = [_member(db_session, space, f"sq-long-ancestor-{index}", "m") for index in range(5)]
    for parent, child in zip(ancestors, [viewer, *ancestors[:-1]], strict=True):
        source_facts.create_source_fact(
            db_session,
            fact_type="biological_parent",
            subject_user_id=parent.id,
            object_user_id=child.id,
            provenance="manual_entry",
            space_id=space.id,
            state="confirmed",
        )
    personal_family_view.initialize_account_views(
        db_session, account_id=viewer.account.id, user_id=viewer.id
    )
    db_session.commit()
    assert _drain(db_session, space) > 0
    target = ancestors[-1]
    projection = db_session.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.viewer_account_id == viewer.account.id,
            StewardTermProjection.space_id == space.id,
            StewardTermProjection.target_user_id == target.id,
        )
    )
    assert projection is not None
    assert projection.baseline_source == "derived"
    assert projection.status == "unchanged" and projection.term is None
    suggestion = db_session.scalar(
        select(StewardSuggestion).where(
            StewardSuggestion.viewer_account_id == viewer.account.id,
            StewardSuggestion.object_user_id == target.id,
            StewardSuggestion.kind == "term_preference",
        )
    )
    assert suggestion is not None
    headers = _login_header(client, viewer.name)
    response = client.get(
        f"/api/steward-suggestions/{suggestion.id}", params={"space_id": space.id}, headers=headers
    )
    assert response.status_code == 200
    detail = response.json()
    assert detail["state"] == "proposed"
    assert detail["value"]["can_restore"] is False
    assert detail["presentation"]["term"] == projection.baseline_term
    assert detail["presentation"]["term_source_level"] == "derived"
    assert "submit" in detail["allowed_actions"]
    baseline_term = projection.baseline_term
    response = client.post(
        f"/api/steward-suggestions/{suggestion.id}/submit",
        params={"space_id": space.id},
        headers={**headers, "Idempotency-Key": "keep-long-chain"},
        json={
            "expected_revision": detail["revision"],
            "evidence_hash": detail["evidence_hash"],
            "confirm": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["linked_preference"]["term"] == baseline_term
    db_session.expire_all()
    preference = terms.resolve_term(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code=projection.concept_code,
    )
    assert preference.term == baseline_term and preference.source_level == "personal"
    assert len(db_session.scalars(select(SourceFact)).all()) == len(ancestors)


@pytest.mark.parametrize("operation", ["keep", "restore"])
def test_term_actions_refresh_objects_cached_before_another_session_commit(db_session, operation):
    space, viewer, _target, suggestion, projection = _term_fixture(db_session)
    detail = _detail(db_session, space, viewer, suggestion)
    assert detail["value"]["can_restore"] is True
    stale_revision = projection.revision
    stale_semantic = projection.semantic_hash
    with Session(bind=db_session.get_bind()) as other_session:
        fresh = other_session.get(StewardTermProjection, projection.id)
        fresh.term = "外祖母"
        fresh.revision += 1
        other_session.commit()
    assert projection.term == "姥姥"  # The caller deliberately retains its old identity map.
    with pytest.raises(HTTPException) as exc:
        if operation == "keep":
            _submit(db_session, space, viewer, suggestion)
        else:
            steward_suggestions.restore_term(
                db_session,
                account=viewer.account,
                space_id=space.id,
                suggestion_id=suggestion.id,
                expected_revision=detail["revision"],
                expected_projection_revision=stale_revision,
                semantic_hash=stale_semantic,
                idempotency_key="stale-cached-restore",
            )
    assert exc.value.status_code == 409
    assert projection.term == "外祖母"
    assert db_session.scalar(select(StewardTermSuppression.id)) is None


@pytest.mark.parametrize("change", ["personal", "space", "replacement", "semantic", "target"])
def test_old_term_suggestion_cannot_keep_or_restore_replacement(db_session, change):
    space, viewer, target, suggestion, projection = _term_fixture(db_session)
    if change == "personal":
        terms.set_personal_term(
            db_session,
            account_id=viewer.account.id,
            space_id=space.id,
            concept_code="Uf-Uf",
            term="亲爱的姥姥",
        )
    elif change == "space":
        db_session.add(
            terms.TermEntry(
                concept_code="Uf-Uf",
                level="space",
                space_id=space.id,
                term="家庭外婆",
                status="active",
                revision=1,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    elif change == "replacement":
        projection.term = "外祖母"
    elif change == "semantic":
        projection.semantic_hash = "b" * 64
    else:
        suggestion.value_json = {**suggestion.value_json, "target_user_id": viewer.id}
    projection.revision += 1
    db_session.commit()
    expected = projection.term
    detail = _detail(db_session, space, viewer, suggestion)
    assert detail["allowed_actions"] == ["open_details"]
    assert detail["value"]["can_restore"] is False
    with pytest.raises(HTTPException) as exc:
        _submit(db_session, space, viewer, suggestion)
    assert exc.value.status_code == 409
    # Supplying a fresh live revision/hash cannot grant an old suggestion authority.
    with pytest.raises(HTTPException) as exc:
        steward_suggestions.restore_term(
            db_session,
            account=viewer.account,
            space_id=space.id,
            suggestion_id=suggestion.id,
            expected_revision=suggestion.revision,
            expected_projection_revision=projection.revision,
            semantic_hash=projection.semantic_hash,
            idempotency_key="stale-restore",
        )
    assert exc.value.status_code == 409
    assert projection.term == expected
    assert db_session.scalar(select(StewardTermSuppression.id)) is None
    if change == "personal":
        assert (
            terms.resolve_term(
                db_session, account_id=viewer.account.id, space_id=space.id, concept_code="Uf-Uf"
            ).term
            == "亲爱的姥姥"
        )


def test_same_value_baseline_has_no_restore_action(db_session):
    space, viewer, target, _old, projection = _term_fixture(db_session)
    projection.term = projection.baseline_term
    projection.revision += 1
    suggestion, created = steward_terminology.upsert_term_preference_suggestion(
        db_session,
        space_id=space.id,
        viewer_account_id=viewer.account.id,
        subject_user_id=viewer.id,
        object_user_id=target.id,
        concept_code=projection.concept_code,
        term=projection.term,
        projection_id=projection.id,
        projection_revision=projection.revision,
        semantic_hash=projection.semantic_hash,
        reason_code="shorter_chain",
        policy_version="quality-test",
        origin="deterministic",
    )
    assert created
    db_session.commit()
    detail = _detail(db_session, space, viewer, suggestion)
    assert detail["state"] == "proposed"
    assert detail["value"]["can_restore"] is False
    with pytest.raises(HTTPException) as exc:
        steward_suggestions.restore_term(
            db_session,
            account=viewer.account,
            space_id=space.id,
            suggestion_id=suggestion.id,
            expected_revision=suggestion.revision,
            expected_projection_revision=projection.revision,
            semantic_hash=projection.semantic_hash,
            idempotency_key="same-value-restore",
        )
    assert exc.value.status_code == 409


def test_valid_restore_falls_back_without_second_session_or_replayed_effect(db_session):
    space, viewer, _target, suggestion, projection = _term_fixture(db_session)
    kwargs = dict(
        account=viewer.account,
        space_id=space.id,
        suggestion_id=suggestion.id,
        expected_revision=suggestion.revision,
        expected_projection_revision=projection.revision,
        semantic_hash=projection.semantic_hash,
        idempotency_key="valid-restore",
    )
    status, payload = steward_suggestions.restore_term(db_session, **kwargs)
    assert status == 200
    assert payload["projection"]["baseline_term"] == "外婆"
    assert projection.term is None and projection.status == "suppressed"
    retry_status, retry = steward_suggestions.restore_term(db_session, **kwargs)
    assert retry_status == status
    assert retry["projection"] == payload["projection"]
    assert retry["suggestion"]["revision"] == payload["suggestion"]["revision"]
    assert len(db_session.scalars(select(StewardTermSuppression)).all()) == 1
