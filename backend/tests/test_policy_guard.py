from app.services import policy_guard


def test_context_hook_rejects_masked_data() -> None:
    decision = policy_guard.context_hook(
        [
            {
                "kind": "data",
                "trust": "untrusted_data",
                "visibility": "masked",
                "content": "hidden",
            }
        ]
    )

    assert not decision.allowed
    assert decision.reason == "context_masked_data"


def test_tool_result_hook_annotates_nested_unconfirmed_fact() -> None:
    decision = policy_guard.tool_result_hook(
        {"result": {"fact_state": "proposed", "value": "candidate"}}
    )

    assert decision.action == "annotate"
    assert decision.value == {
        "data": {"result": {"fact_state": "proposed", "value": "candidate"}},
        "confirmed": False,
    }
