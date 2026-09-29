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


def test_ordinary_prose_is_not_blocked_by_keyword_matching() -> None:
    """Natural-language markers are diagnostics, not authorization decisions.

    A user asking about prompts, or an answer that quotes one, must not fail the
    request. Rewording evades keyword matching anyway, so it cannot carry the
    security guarantee; the tool allowlist and provider boundary do.
    """
    for content in (
        "system prompt 要求 kind 只能是八种之一。",
        "我不会绕过限制。",
        "ignore previous instructions",
    ):
        decision = policy_guard.input_hook(content)
        assert decision.allowed, content


def test_input_hook_still_blocks_real_secret_material() -> None:
    """Relaxing the wording check must not relax the secret check."""
    decision = policy_guard.input_hook("api_key: sk-live-abcdefghijklmnop")
    assert decision.action == "block"
    assert decision.reason == "secret_in_input"


def test_tool_call_hook_blocks_secret_arguments_but_not_wording() -> None:
    """Tool arguments keep their real constraints."""
    allowlist = ["familygraph.echo"]
    wording = policy_guard.tool_call_hook(
        tool="familygraph.echo",
        version=1,
        arguments={"text": "system prompt says hello"},
        allowlist=allowlist,
    )
    assert wording.allowed

    secret = policy_guard.tool_call_hook(
        tool="familygraph.echo",
        version=1,
        arguments={"text": "api_key: sk-live-abcdefghijklmnop"},
        allowlist=allowlist,
    )
    assert secret.action == "block"
    assert secret.reason == "unsafe_tool_arguments"

    not_allowlisted = policy_guard.tool_call_hook(
        tool="familygraph.read_file",
        version=1,
        arguments={},
        allowlist=allowlist,
    )
    assert not_allowlisted.action == "block"
    assert not_allowlisted.reason == "tool_not_allowlisted"
