"""Kinds used by policy and retrieval consumers.

These are deliberately separate from :mod:`app.models.agent`: ``steward`` is
an independent Agent identity for shared-data policy decisions, not a generic
session/run/job runtime kind.
"""

from typing import Literal

PolicyConsumerKind = Literal["assistant", "steward"]
POLICY_CONSUMER_KINDS: tuple[PolicyConsumerKind, ...] = ("assistant", "steward")


def is_policy_consumer_kind(value: str | None) -> bool:
    return value in POLICY_CONSUMER_KINDS


__all__ = ["POLICY_CONSUMER_KINDS", "PolicyConsumerKind", "is_policy_consumer_kind"]
