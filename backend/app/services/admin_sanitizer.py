"""后台响应脱敏器（09-04 RM-F2 / design §3.4）。

在 response schema 之前运行：字段黑名单直接丢弃；字符串值清理 credential-like、
Bearer、JWT、URL query secret 与已知 PII pattern。任何无法可靠脱敏的输入
fail-closed：调用方只输出 error_code + 安全位置，不输出摘要。

原始 Agent error_json/result_json 只保留在服务端受限诊断通道，API 永不直接
序列化（调用方必须经 ``sanitize_error_payload`` 投影）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

# 键黑名单：键名（小写化后）包含任一片段即整键丢弃（design §3.4）。
BLOCKED_KEY_PARTS: tuple[str, ...] = (
    "token",
    "secret",
    "key",
    "authorization",
    "auth",
    "password",
    "passwd",
    "credential",
    "cookie",
    "pin",
    "jwt",
    "bearer",
    "prompt",
    "message",
    "msg",
    "content",
    "email",
    "phone",
    "mobile",
    "address",
    "school",
    "health",
    "note",
    "private",
    "memory",
    "context",
    "text",
    "body",
    "session",
    "signature",
    "salt",
    "otp",
)

# 字符串值模式（依序替换；替换后重扫仍命中视为不可靠脱敏）。
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|secret|key|password|passwd|access_token|api_key|apikey|sig|signature|code|pin)=)[^&\s]+"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_LONG_DIGITS_RE = re.compile(r"(?<!\d)\d{7,}(?!\d)")
# 明文 kv 秘密（token=xxx / password: xxx）：强敏感信号，触发 fail-closed。
_KV_SECRET_RE = re.compile(
    r"(?i)\b(token|secret|api[_-]?key|access[_-]?token|password|passwd|pin|authorization|sig)\s*[=:]\s*\S+"
)
# credential-like：24+ 的 base64/hex 风格连续串（须含数字；下划线/纯大写标识符不算）。
_CREDENTIAL_CANDIDATE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9+/\-]{24,}(?![A-Za-z0-9])")
_STACK_RE = re.compile(r"(?i)traceback \(most recent call last\)|\bat [\w$.]+\(.*\)")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

REDACTED = "[REDACTED]"
# 安全位置/组件名：短标识符，不含空白与路径穿越片段。
_SAFE_LOCATION_RE = re.compile(r"^[A-Za-z0-9_.:/\\-]{1,160}$")
_MAX_SUMMARY_LENGTH = 200


def _redact_credential_like(value: str) -> str:
    def _sub(match: re.Match[str]) -> str:
        candidate = match.group(0)
        return REDACTED if any(ch.isdigit() for ch in candidate) else candidate

    return _CREDENTIAL_CANDIDATE_RE.sub(_sub, value)


def _credential_like_hit(value: str) -> bool:
    return any(
        any(ch.isdigit() for ch in match.group(0))
        for match in _CREDENTIAL_CANDIDATE_RE.finditer(value)
    )


@dataclass(frozen=True)
class SanitizeOutcome:
    """脱敏结果：``reliable=False`` 时调用方必须降级为 error_code + 位置。"""

    value: Any
    reliable: bool


def is_blocked_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in BLOCKED_KEY_PARTS)


def sanitize_text(value: str) -> SanitizeOutcome:
    """清理单条字符串；出现秘密/PII 模式即判为不可靠（fail-closed）。"""
    if _STACK_RE.search(value):
        return SanitizeOutcome(REDACTED, False)
    cleaned = _CONTROL_CHARS_RE.sub("", value)
    found_secret = bool(
        _BEARER_RE.search(cleaned)
        or _JWT_RE.search(cleaned)
        or _URL_SECRET_RE.search(cleaned)
        or _KV_SECRET_RE.search(cleaned)
        or _credential_like_hit(cleaned)
    )
    found_pii = bool(
        _EMAIL_RE.search(cleaned) or _PHONE_RE.search(cleaned) or _LONG_DIGITS_RE.search(cleaned)
    )
    cleaned = _BEARER_RE.sub(REDACTED, cleaned)
    cleaned = _JWT_RE.sub(REDACTED, cleaned)
    cleaned = _URL_SECRET_RE.sub(REDACTED, cleaned)
    cleaned = _KV_SECRET_RE.sub(REDACTED, cleaned)
    cleaned = _EMAIL_RE.sub("[REDACTED_EMAIL]", cleaned)
    cleaned = _PHONE_RE.sub("[REDACTED_PHONE]", cleaned)
    cleaned = _LONG_DIGITS_RE.sub("[REDACTED_DIGITS]", cleaned)
    cleaned = _redact_credential_like(cleaned)
    return SanitizeOutcome(cleaned, not (found_secret or found_pii))


def sanitize_value(value: Any) -> SanitizeOutcome:
    """递归脱敏：黑名单键整键丢弃；叶子字符串逐条清理。"""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, bool) or value is None or isinstance(value, int | float):
        return SanitizeOutcome(value, True)
    if isinstance(value, datetime | date):
        return SanitizeOutcome(value.isoformat(), True)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        reliable = True
        for key, item in value.items():
            if is_blocked_key(str(key)):
                reliable = False
                continue
            inner = sanitize_value(item)
            reliable = reliable and inner.reliable
            result[str(key)] = inner.value
        return SanitizeOutcome(result, reliable)
    if isinstance(value, list | tuple):
        items = [sanitize_value(item) for item in value]
        return SanitizeOutcome(
            [item.value for item in items],
            all(item.reliable for item in items),
        )
    # 未知类型（bytes、对象等）一律不可靠。
    return SanitizeOutcome(None, False)


def _safe_location(value: Any) -> str | None:
    """安全位置：短标识符（文件:行、组件名），含敏感模式的值直接丢弃。"""
    if not isinstance(value, str):
        return None
    if len(value) > 160 or not _SAFE_LOCATION_RE.match(value):
        return None
    outcome = sanitize_text(value)
    if not outcome.reliable or not isinstance(outcome.value, str):
        return None
    return outcome.value


def sanitize_error_payload(
    raw_error: dict[str, Any] | list[Any] | str | None,
    *,
    error_code: str | None,
) -> dict[str, Any] | None:
    """Agent 原始 error_json → 二次脱敏诊断投影。

    只输出 error_code / component / stack_location / summary；summary 依赖
    整体可靠脱敏，任何键被黑名单丢弃或值清理不可靠时 summary 置 None
    （fail-closed：只返回 error_code + 安全位置）。
    """
    component: str | None = None
    stack_location: str | None = None
    summary_parts: list[str] = []
    reliable = True

    if isinstance(raw_error, str):
        outcome = sanitize_text(raw_error)
        reliable = reliable and outcome.reliable
        if outcome.reliable:
            summary_parts.append(outcome.value[:_MAX_SUMMARY_LENGTH])
    elif isinstance(raw_error, dict):
        for key, value in raw_error.items():
            lowered = str(key).lower()
            if is_blocked_key(str(key)):
                reliable = False
                continue
            if lowered == "error_code":
                # 错误码单独抽取，不重复进摘要。
                continue
            if lowered in ("component", "component_name") and component is None:
                component = _safe_location(value)
                continue
            if lowered in ("stack_location", "location", "file") and stack_location is None:
                stack_location = _safe_location(value)
                continue
            if lowered in ("stack", "traceback"):
                # 完整堆栈永不进入响应；只有位置（文件:行）允许保留。
                reliable = False
                continue
            if isinstance(value, str | int | float | bool) and value is not None:
                outcome = sanitize_value(value)
                reliable = reliable and outcome.reliable
                if outcome.reliable and isinstance(outcome.value, str) and outcome.value:
                    summary_parts.append(outcome.value[:_MAX_SUMMARY_LENGTH])
    # list/None 原文：不生成摘要，仅保留错误码与位置。
    summary = "；".join(summary_parts)[:_MAX_SUMMARY_LENGTH] if reliable and summary_parts else None
    if error_code is None and component is None and stack_location is None and summary is None:
        return None
    return {
        "error_code": error_code,
        "component": component,
        "stack_location": stack_location,
        "summary": summary,
    }


def sanitize_filters(filters: dict[str, Any]) -> dict[str, Any]:
    """审计 filters 摘要：只保留标量安全值；黑名单键/复杂结构一律不落审计。"""
    safe: dict[str, Any] = {}
    for key, value in filters.items():
        if is_blocked_key(str(key)) or value is None:
            continue
        if isinstance(value, bool):
            safe[str(key)] = value
        elif isinstance(value, int):
            safe[str(key)] = value
        elif isinstance(value, str):
            outcome = sanitize_text(value)
            if outcome.reliable and outcome.value:
                safe[str(key)] = outcome.value[:120]
    return safe
