from app.services import policy_guard


def test_provider_payload_credential_keys_are_blocked_but_token_caps_allowed() -> None:
    blocked = policy_guard.before_provider_request(
        {"model": "gpt-5.6-sol", "api_key": "leak-me"},
        provider_kind="local",
    )
    assert blocked.action == "block"
    assert blocked.reason == "secret_in_provider_payload"

    allowed = policy_guard.before_provider_request(
        {
            "model": "gpt-5.6-sol",
            "max_tokens": 60000,
            "max_output_tokens": 60000,
            "stream_options": {"include_usage": True},
        },
        provider_kind="openai_compatible",
    )
    assert allowed.allowed


def test_provider_payload_header_credential_variants_are_blocked() -> None:
    for payload in (
        {"headers": {"x-api-key": "opaque"}},
        {"headers": {"X-Authorization": "opaque"}},
        {"headers": {"cookie": "opaque"}},
        {"private-key": "opaque"},
    ):
        decision = policy_guard.before_provider_request(payload, provider_kind="local")
        assert decision.action == "block"
        assert decision.reason == "secret_in_provider_payload"


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
