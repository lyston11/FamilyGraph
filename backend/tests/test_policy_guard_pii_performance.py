"""PII 检测必须在**线性**时间内完成。

为何是回归而不是性能优化：`policy_guard.before_provider_request` 跑在**事件循环**上，
输入是模型 payload（可达数百 KB）。此前的 phone 正则
`(?<!\\d)(?=(?:\\D*\\d){10,})(?:\\+?\\d[\\d -]{8,}\\d)(?!\\d)`
在长输入上超线性回溯（实测 4x 输入 → 15.7x 时间，66KB → 23 秒），把事件循环钉住，
使 provider 请求在 connect 阶段超时（10s）并被记为 `transport_timeout`——
2026-10-01 的进程级故障因此让全部出站失效 18.8 小时。

因此本文件断言的是**渐进复杂度**，不是某个具体毫秒数：输入翻倍，耗时不得
成倍恶化。这样它对机器性能不敏感，只对算法退化敏感。
"""

from __future__ import annotations

import json
import time

from app.services import policy_guard


def _elapsed(text: str) -> float:
    started = time.perf_counter()
    policy_guard.contains_pii(text)
    return time.perf_counter() - started


def test_pii_scanning_is_linear_in_input_size() -> None:
    """4x 输入不得导致远超 4x 的耗时（超线性即回归）。"""
    small = json.dumps({"input": [{"content": "x" * 4000}]})
    large = json.dumps({"input": [{"content": "x" * 16000}]})

    # 预热：排除首次编译/导入的固定开销，只比较增长趋势。
    _elapsed(small)

    t_small = max(_elapsed(small), 1e-6)
    t_large = _elapsed(large)
    ratio = t_large / t_small

    # 线性应为 ~4x；允许常数与噪声，但远低于超线性的 15x+。
    assert ratio < 10, (
        f"PII 扫描呈超线性：4x 输入耗时 {ratio:.1f}x（{t_small * 1000:.2f}ms -> "
        f"{t_large * 1000:.2f}ms）。这会把事件循环钉住，导致 provider 出站超时。"
    )


def test_large_payload_stays_fast_enough_for_the_provider_path() -> None:
    """接近真实 steward payload 的规模必须在亚秒级完成。

    真实 steward payload 约 17–18KB；此前该规模需 ~1s，且随规模急剧恶化。
    """
    payload = {
        "model": "deepseek-v4.1-flash",
        "input": [{"role": "user", "content": "x" * 8000}],
        "tools": [
            {
                "name": f"t{i}",
                "parameters": {
                    "type": "object",
                    "properties": {f"k{j}": {"type": "string"} for j in range(50)},
                },
            }
            for i in range(6)
        ],
    }
    started = time.perf_counter()
    decision = policy_guard.before_provider_request(
        payload, provider_kind="openai", cloud_allowed=True
    )
    elapsed = time.perf_counter() - started
    assert decision.action == "allow"
    assert elapsed < 0.5, (
        f"真实规模 payload 的 policy 检查耗时 {elapsed * 1000:.0f}ms；"
        "provider 请求预算不足以承受秒级检查"
    )


def test_phone_detection_still_works_after_the_rewrite() -> None:
    """线性化不得牺牲检测能力——否则就是用一个缺陷换另一个。"""
    positives = (
        "13800138000",
        "+86 138-0013-8000",
        "138 0013 8000",
        "010-12345678",
        "tel: 13912345678",
    )
    for text in positives:
        assert policy_guard.contains_pii(text), f"漏检电话号码: {text!r}"

    negatives = (
        "hello world",
        "12345",
        "订单号 2024",  # 数字不足 10 位
        "v4.1-flash",
        # ISO 日期只有 8 位数字：必须保持不误报，否则家族树里的生日会被脱敏。
        "1970-01-01",
        "2026-10-01T09:02:17",
    )
    for text in negatives:
        assert not policy_guard.contains_pii(text), f"误报为 PII: {text!r}"


def test_email_detection_is_unchanged() -> None:
    assert policy_guard.contains_pii("user@example.com")
    assert not policy_guard.contains_pii("not an email")
