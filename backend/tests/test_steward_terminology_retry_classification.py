"""守护 terminology 目标的「临时失败不是终态」分类。

## 为什么这是正确性要求（实测生产回归）

`terminology_target_retryable` 用 `failed` 作为「已尝试过」的判据，但 `failed` 混着
两类完全不同的结果：

  - **永久失败**：输出不合规（`invalid_output`）、提示词/响应过大、策略拒绝。
    同一输入重发必然再失败，判为终态是对的。
  - **临时失败**：上游 5xx、流中断、本 run 的出站重试预算耗尽。重发**可能**成功。

把临时失败当终态，等于让**一次上游抖动永久封死一个目标**。

实测（生产，2026-10-09）：space 2 的 33 个完全合格目标（例如
`爸爸 -> 父亲`、`妈妈 -> 母亲`、`哥哥 -> 兄弟`）全部被 `failed` 拦下，其中 33 个
都是临时失败（`http_503` 10、`PROVIDER_STREAM_ERROR` 7、
`PROVIDER_RETRY_BUDGET_EXHAUSTED` 16）。结果是 terminology 静默停摆 6 小时、
零 attempt、无任何错误日志。
"""

from __future__ import annotations

import pytest

from app.services.steward_assist import _is_transient_error

_TRANSIENT = [
    "http_500",
    "http_502",
    "http_503",
    "http_504",
    "http_599",  # 未知 5xx 也必须算临时，否则新状态码会静默退化为终态
    "PROVIDER_STREAM_ERROR",
    "PROVIDER_RETRY_BUDGET_EXHAUSTED",
    "PROVIDER_PROXY_UNAVAILABLE",
    "timeout",
    "network_unknown",
    "transport_failed",
    "provider_unavailable",
    "lease_lost",
]

_PERMANENT = [
    "invalid_output",
    "prompt_too_large",
    "response_too_large",
    "policy_blocked",
    # provider 策略/配置拒绝由配置决定，重发同一请求不会变好。
    "PROVIDER_UNRESOLVED",
    "PROVIDER_MODEL_NOT_ALLOWED",
    "connect_failed",  # 已有独立的 unsent 语义，不走 transient 分支
    "http_400",
    "http_404",
    "http_422",
    "http_429",  # 客户端侧限流：重发同一请求不会因时间推移而通过
    "assist_disabled",
    "budget_exhausted",
    "insufficient_budget",
    None,
    "",
]


@pytest.mark.parametrize("code", _TRANSIENT)
def test_transient_codes_are_retryable(code: str) -> None:
    assert _is_transient_error(code), f"{code} 应判为临时失败（重发可能成功）"


@pytest.mark.parametrize("code", _PERMANENT)
def test_permanent_codes_are_not_retryable(code: str | None) -> None:
    assert not _is_transient_error(code), f"{code} 应判为永久失败（不得无限重发）"


def test_unknown_5xx_shape_is_transient() -> None:
    """按形状判定：未来新增的 5xx 不得静默退化成终态。"""
    for status in range(500, 600):
        assert _is_transient_error(f"http_{status}"), f"http_{status} 应判为临时"


def test_non_5xx_http_shape_is_permanent() -> None:
    """4xx 不得被判为临时（否则会无限重发一个必然失败的请求）。"""
    for status in (400, 401, 403, 404, 409, 422, 429):
        assert not _is_transient_error(f"http_{status}"), f"http_{status} 不应判为临时"
