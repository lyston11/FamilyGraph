"""Backend half of the FamilyGraph policy guard.

The Node extension may call equivalent hooks, but these checks remain authoritative
at the data/provider boundary.  Decisions contain reasons and sanitized metadata,
never the inspected secret or source text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app import config
from app.errors import (
    POLICY_GUARD_DISABLED,
    POLICY_INPUT_BLOCKED,
    POLICY_LOCAL_REQUIRED,
    POLICY_PROVIDER_BLOCKED,
    POLICY_TOOL_BLOCKED,
    POLICY_TOOL_RESULT_BLOCKED,
    raise_api_error,
)

MAX_TOOL_RESULT_BYTES = 64_000
MAX_CONTEXT_BYTES = 256_000

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:sk|pk|api)[_-]?live[_-][a-z0-9]{12,}\b"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token|密碼|密码|密钥)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"),
)
# Credential-shaped keys are blocked even when their value does not resemble a
# provider key (for example ``{"api_key": "leak-me"}``). Token-cap fields are
# explicitly excluded because Pi/OpenAI request bodies legitimately contain
# ``max_tokens`` and ``max_output_tokens``.
_NON_CREDENTIAL_TOKEN_FIELDS = {
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
    "include_usage",
    "stream_options",
}
_CREDENTIAL_KEY_RE = re.compile(
    r"^(?:access|auth|bearer|refresh|id|session|api|provider|client|personal|customer|x)?[-_]?(?:tokens?|api[-_]?key|secret|password)$|^(?:authorization|x[-_]?authorization|cookie|set[-_]?cookie|private[-_]?key)$",
    re.IGNORECASE,
)
_PII_PATTERNS = (
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)(?=(?:\D*\d){10,})(?:\+?\d[\d -]{8,}\d)(?!\d)"),
)
_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all instructions",
    "system prompt",
    "developer message",
    "忽略之前的指令",
    "忽略系统提示",
    "你现在是系统",
)


@dataclass(frozen=True)
class PolicyDecision:
    action: str  # allow | block | redact | annotate
    reason: str
    sensitivity: str = "normal"
    provider: str | None = None
    value: Any = None

    @property
    def allowed(self) -> bool:
        return self.action == "allow"


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def _credential_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return normalized not in _NON_CREDENTIAL_TOKEN_FIELDS and bool(
        _CREDENTIAL_KEY_RE.fullmatch(normalized)
    )


def contains_credential_key(value: Any) -> bool:
    """Return true when any nested object key is credential-shaped."""
    if isinstance(value, dict):
        return any(
            _credential_key(key) or contains_credential_key(item) for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(contains_credential_key(item) for item in value)
    return False


def contains_secret(value: Any) -> bool:
    return contains_credential_key(value) or any(
        pattern.search(text) for text in _strings(value) for pattern in _SECRET_PATTERNS
    )


def contains_pii(value: Any) -> bool:
    return any(pattern.search(text) for text in _strings(value) for pattern in _PII_PATTERNS)


def contains_prompt_injection(value: Any) -> bool:
    return any(marker in text.lower() for text in _strings(value) for marker in _INJECTION_MARKERS)


def classify(value: Any) -> str:
    if contains_secret(value):
        return "local_required"
    if contains_pii(value):
        return "sensitive"
    return "normal"


def input_hook(content: Any) -> PolicyDecision:
    """Initial input screening for unsafe/injection/secret content."""
    if contains_prompt_injection(content):
        return PolicyDecision("block", "prompt_injection", "sensitive")
    if contains_secret(content):
        return PolicyDecision("block", "secret_in_input", "local_required")
    return PolicyDecision("allow", "input_checked", classify(content), value=content)


def tool_call_hook(
    *,
    tool: str,
    version: int,
    arguments: dict[str, Any],
    allowlist: Iterable[str],
    max_argument_bytes: int = 32_000,
) -> PolicyDecision:
    """Allowlist and bounded-argument check before FastAPI tool execution."""
    if tool not in set(allowlist):
        return PolicyDecision("block", "tool_not_allowlisted", value={"tool": tool})
    if version < 1:
        return PolicyDecision("block", "tool_version_invalid", value={"tool": tool})
    encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > max_argument_bytes:
        return PolicyDecision("block", "tool_arguments_too_large", value={"tool": tool})
    if contains_prompt_injection(arguments) or contains_secret(arguments):
        return PolicyDecision("block", "unsafe_tool_arguments", "local_required")
    return PolicyDecision("allow", "tool_call_checked", classify(arguments), value=arguments)


def _contains_unconfirmed(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized == "confirmed" and item is False:
                return True
            unconfirmed_statuses = {
                "pending",
                "proposed",
                "unconfirmed",
                "disputed",
            }
            if (
                normalized in {"confirmation_status", "fact_state", "status"}
                and str(item).lower() in unconfirmed_statuses
            ):
                return True
            if _contains_unconfirmed(item):
                return True
    elif isinstance(value, list | tuple):
        return any(_contains_unconfirmed(item) for item in value)
    return False


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        result = value
        for pattern in _SECRET_PATTERNS + _PII_PATTERNS:
            result = pattern.sub("[REDACTED]", result)
        return result
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _credential_key(key) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def tool_result_hook(
    result: Any,
    *,
    max_bytes: int = MAX_TOOL_RESULT_BYTES,
    confirmed: bool = True,
) -> PolicyDecision:
    """Second-pass result scrub and bounded output contract."""
    redacted = _redact(result)
    encoded = json.dumps(redacted, ensure_ascii=False, default=str).encode()
    if len(encoded) > max_bytes:
        return PolicyDecision("block", "tool_result_too_large", value={"max_bytes": max_bytes})
    if not confirmed or _contains_unconfirmed(redacted):
        return PolicyDecision(
            "annotate",
            "unconfirmed_fact",
            classify(redacted),
            value={"data": redacted, "confirmed": False},
        )
    if redacted != result:
        return PolicyDecision("redact", "secret_or_pii_removed", classify(result), value=redacted)
    return PolicyDecision("allow", "tool_result_checked", classify(result), value=redacted)


def context_hook(
    blocks: list[dict[str, Any]], *, max_bytes: int = MAX_CONTEXT_BYTES
) -> PolicyDecision:
    """Validate only prebuilt data blocks; this function has no DB dependency."""
    if not isinstance(blocks, list):
        return PolicyDecision("block", "context_not_list")
    safe: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("kind") != "data":
            return PolicyDecision("block", "context_instruction_block")
        if block.get("trust") != "untrusted_data":
            return PolicyDecision("block", "context_trust_missing")
        if block.get("visibility") == "masked" or block.get("masked") is True:
            return PolicyDecision("block", "context_masked_data")
        safe.append(dict(block))
    encoded = json.dumps(safe, ensure_ascii=False, default=str).encode()
    if len(encoded) > max_bytes:
        return PolicyDecision("block", "context_too_large", value={"max_bytes": max_bytes})
    return PolicyDecision("allow", "context_checked", classify(safe), value=safe)


def before_provider_request(
    payload: Any,
    *,
    provider_kind: str | None,
    local_required: bool = False,
    cloud_allowed: bool = True,
) -> PolicyDecision:
    """Final payload check immediately before provider transport."""
    required = local_required or classify(payload) == "local_required"
    if required and provider_kind != "local":
        return PolicyDecision("block", "local_provider_required", "local_required", provider_kind)
    if provider_kind != "local" and not cloud_allowed:
        return PolicyDecision("block", "cloud_provider_forbidden", classify(payload), provider_kind)
    if contains_secret(payload):
        return PolicyDecision(
            "block", "secret_in_provider_payload", "local_required", provider_kind
        )
    if provider_kind != "local" and contains_pii(payload):
        return PolicyDecision(
            "redact", "unnecessary_pii_removed", "sensitive", provider_kind, _redact(payload)
        )
    return PolicyDecision(
        "allow", "provider_request_checked", classify(payload), provider_kind, payload
    )


def agent_settled(*, status: str, usage: dict[str, Any] | None = None) -> PolicyDecision:
    """Return a log-safe settlement projection; hidden content is never accepted."""
    if status not in ("succeeded", "failed", "cancelled", "expired"):
        return PolicyDecision("block", "invalid_settlement_status")
    safe_usage = {
        key: value
        for key, value in (usage or {}).items()
        if key in {"input_tokens", "output_tokens", "total_tokens"} and isinstance(value, int)
    }
    return PolicyDecision(
        "allow", "settlement_checked", value={"status": status, "usage": safe_usage}
    )


# Explicit aliases make the six hook contract easy to consume from adapters.
policy_input = input_hook
policy_tool_call = tool_call_hook
policy_tool_result = tool_result_hook
policy_context = context_hook
policy_before_provider_request = before_provider_request
policy_agent_settled = agent_settled


def enforce(decision: PolicyDecision, *, code: str = POLICY_INPUT_BLOCKED) -> Any:
    """Turn a blocking decision into the common API error envelope."""
    if not config.POLICY_GUARD_ENABLED:
        raise_api_error(503, POLICY_GUARD_DISABLED, "Policy Guard 功能未开启")
    if decision.action == "block":
        code_by_reason = {
            "local_provider_required": POLICY_LOCAL_REQUIRED,
            "cloud_provider_forbidden": POLICY_PROVIDER_BLOCKED,
            "tool_not_allowlisted": POLICY_TOOL_BLOCKED,
            "tool_result_too_large": POLICY_TOOL_RESULT_BLOCKED,
        }
        raise_api_error(409, code_by_reason.get(decision.reason, code), "策略阻止了本次操作")
    return decision.value


__all__ = [
    "PolicyDecision",
    "agent_settled",
    "before_provider_request",
    "classify",
    "context_hook",
    "contains_pii",
    "contains_credential_key",
    "contains_prompt_injection",
    "contains_secret",
    "enforce",
    "input_hook",
    "policy_agent_settled",
    "policy_before_provider_request",
    "policy_context",
    "policy_input",
    "policy_tool_call",
    "policy_tool_result",
    "tool_call_hook",
    "tool_result_hook",
]
