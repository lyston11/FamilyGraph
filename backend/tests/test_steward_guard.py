"""steward_guard 单元测试（09-11 quality-security；R1/R2/R3 纯函数合同）。"""

from __future__ import annotations

import json

from app.services import steward_guard

_CTX = steward_guard.ProjectionContext({1, 2, 3}, minor_ids={3})


def test_projection_never_contains_raw_names() -> None:
    facts = [
        {
            "fact_id": 11,
            "fact_type": "spouse",
            "revision": 1,
            "subject_user_id": 1,
            "object_user_id": 2,
        }
    ]
    text = steward_guard.project_candidate_input(facts, _CTX)
    body = json.loads(text)
    assert [n["node"] for n in body["nodes"]] == ["n001", "n002", "n003"]
    assert body["facts"][0]["subject"] == "n001"
    assert body["facts"][0]["object"] == "n002"
    # 端点越出本次授权输入 → 不投影
    facts.append(
        {
            "fact_id": 12,
            "fact_type": "spouse",
            "revision": 1,
            "subject_user_id": 1,
            "object_user_id": 424242,
        }
    )
    body2 = json.loads(steward_guard.project_candidate_input(facts, _CTX))
    assert len(body2["facts"]) == 1


def test_candidate_validator_rejects_and_maps() -> None:
    text = json.dumps(
        [
            {"kind": "spouse", "subject": "n001", "object": "n002", "rationale": "自由文本"},
            {"kind": "grandparent", "subject": "n001", "object": "n003"},
            {"kind": "spouse", "subject": "n001", "object": "n999"},
            {"kind": "spouse", "subject": "n001", "object": "n001"},
            {"kind": "guardian", "subject": "n001", "object": "n003"},  # minor
            {"kind": "spouse", "subject": 1, "object": 2},  # 原始 id：必须拒绝
        ],
        ensure_ascii=False,
    )
    items = steward_guard.validate_candidate_output(text, _CTX)
    assert items == [{"kind": "spouse", "subject_user_id": 1, "object_user_id": 2}]
    assert steward_guard.validate_candidate_output("不是 JSON", _CTX) is None


def test_candidate_digest_ignores_wording() -> None:
    a = steward_guard.candidate_digest("spouse", 1, 2)
    b = steward_guard.candidate_digest("spouse", 1, 2)
    c = steward_guard.candidate_digest("partner", 1, 2)
    assert a == b and a != c


def test_explanation_validator_evidence_fence() -> None:
    ok = json.dumps(
        {
            "reason_code": "household_link_available",
            "supporting_fact_ids": [11],
            "template_slots": {"partner_node": "n002"},
        }
    )
    assert steward_guard.validate_explanation_output(
        ok,
        reason_code="household_link_available",
        evidence_fact_ids=[11],
        slot_values_allowed={"n002"},
    ) == {
        "schema_version": steward_guard.EXPLANATION_SCHEMA_VERSION,
        "reason_code": "household_link_available",
        "supporting_fact_ids": [11],
        "template_slots": {"partner_node": "n002"},
    }
    bad_reason = ok.replace("household_link_available", "auto_join_family_promise")
    assert (
        steward_guard.validate_explanation_output(
            bad_reason,
            reason_code="household_link_available",
            evidence_fact_ids=[11],
            slot_values_allowed={"n002"},
        )
        is None
    )
    bad_fact = ok.replace("[11]", "[11, 999]")
    assert (
        steward_guard.validate_explanation_output(
            bad_fact,
            reason_code="household_link_available",
            evidence_fact_ids=[11],
            slot_values_allowed={"n002"},
        )
        is None
    )
    bad_slot = ok.replace("n002", "我们会自动帮你入族")
    assert (
        steward_guard.validate_explanation_output(
            bad_slot,
            reason_code="household_link_available",
            evidence_fact_ids=[11],
            slot_values_allowed={"n002"},
        )
        is None
    )
    bad_key = json.dumps(
        {
            "reason_code": "household_link_available",
            "supporting_fact_ids": [11],
            "template_slots": {"extra": "n002"},
        }
    )
    assert (
        steward_guard.validate_explanation_output(
            bad_key,
            reason_code="household_link_available",
            evidence_fact_ids=[11],
            slot_values_allowed={"n002"},
        )
        is None
    )


def test_render_explanation_is_deterministic() -> None:
    structured = {
        "schema_version": 2,
        "reason_code": "household_link_available",
        "supporting_fact_ids": [11],
        "template_slots": {"partner_node": "n002"},
    }
    text = steward_guard.render_explanation(structured, counterpart_name="李四")
    assert text == steward_guard.render_explanation(structured, counterpart_name="李四")
    assert "1 条事实" in text and "李四" in text
    assert "n002" not in text  # 代号不进入呈现文案


def test_outbound_check_blocks_secrets_and_enforces_provider_policy() -> None:
    decision = steward_guard.outbound_check(
        {"messages": [{"content": "token=sk-live-abcdefgh1234"}]},
        provider_kind="cloud",
        local_required=False,
        cloud_allowed=True,
    )
    assert decision.action == "block"
    # 秘密被 classify 为 local_required（本地必需），两种安全原因码均拒绝出站
    assert decision.reason in ("secret_in_provider_payload", "local_provider_required")
    # 云同意撤销 → block，绝不自动切云
    decision = steward_guard.outbound_check(
        {"messages": [{"content": "普通内容"}]},
        provider_kind="cloud",
        local_required=False,
        cloud_allowed=False,
    )
    assert decision.action == "block" and decision.reason == "cloud_provider_forbidden"
    # 敏感必须本地：local_required + 云 → block
    decision = steward_guard.outbound_check(
        {"messages": [{"content": "普通内容"}]},
        provider_kind="cloud",
        local_required=True,
        cloud_allowed=True,
    )
    assert decision.action == "block" and decision.reason == "local_provider_required"
    # 本地 provider 满足 local_required → allow
    decision = steward_guard.outbound_check(
        {"messages": [{"content": "普通内容"}]},
        provider_kind="local",
        local_required=True,
        cloud_allowed=False,
    )
    assert decision.allowed


def test_ranking_validator_strict_permutation() -> None:
    assert steward_guard.validate_ranking_output("[2,1]", [1, 2]) == [2, 1]
    assert steward_guard.validate_ranking_output("[2,1,3]", [1, 2]) is None
    assert steward_guard.validate_ranking_output("[1.0,2]", [1, 2]) is None
    assert steward_guard.validate_ranking_output('["1","2"]', [1, 2]) is None
