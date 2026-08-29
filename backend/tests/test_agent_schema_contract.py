"""Internal Agent 协议 schema 合同快照（R3）。

后端 `app/schemas/agent.py` 是权威合同；sidecar `worker.integration.test.ts` 与
`client.test.ts` 以该形状为 mock。这里的快照逐字段锁定 wire 模型的字段名、
required、类型与关键长度/数值/枚举约束，防止无意漂移破坏 sidecar 兼容。

只锁定内部线缆合同形状（字段名/required/约束），不锁 Python 类型对象的 repr，
避免 pydantic 版本噪声。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas import agent


def _fields(model: type[BaseModel]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, f in model.model_fields.items():
        out[name] = {
            "required": f.is_required(),
            "min_length": None,
            "max_length": None,
            "ge": None,
            "max_items": None,
            "min_items": None,
            "pattern": None,
        }
        for meta in f.metadata:
            m = getattr(meta, "min_length", None)
            if m is not None:
                out[name]["min_length"] = m
            m = getattr(meta, "max_length", None)
            if m is not None:
                out[name]["max_length"] = m
            m = getattr(meta, "ge", None)
            if m is not None:
                out[name]["ge"] = m
            m = getattr(meta, "max_length", None)
            if m is not None:
                # arrays use max_length as max_items in pydantic v2
                out[name]["max_items"] = m
            m = getattr(meta, "min_length", None)
            if m is not None and "list" in str(f.annotation):
                out[name]["min_items"] = m
            m = getattr(meta, "pattern", None)
            if m is not None:
                out[name]["pattern"] = m
    return out


def _signatures(model: type[BaseModel]) -> dict[str, dict[str, Any]]:
    """字段名 + required + 约束的稳定签名（去掉 None 噪声）。"""
    compact: dict[str, dict[str, Any]] = {}
    for name, spec in _fields(model).items():
        compact[name] = {k: v for k, v in spec.items() if v is not None}
        compact[name]["required"] = spec["required"]
    return compact


def _names(model: type[BaseModel]) -> list[str]:
    return list(model.model_fields.keys())


def test_internal_input_models_fail_closed_on_extra_fields() -> None:
    """所有内部线缆输入模型必须 extra=forbid（未知字段拒绝）。"""
    strict_inputs = [
        agent.LeaseRequest,
        agent.HeartbeatRequest,
        agent.EventIn,
        agent.EventAppendRequest,
        agent.ToolExecuteRequest,
        agent.SettleRequest,
    ]
    for model in strict_inputs:
        assert model.model_config.get("extra") == "forbid", f"{model.__name__} 未 fail-closed"


def test_lease_out_field_contract() -> None:
    assert _names(agent.LeaseOut) == [
        "job_id",
        "run_id",
        "agent_kind",
        "attempt",
        "tool_allowlist",
        "policy_version",
        "run_token",
    ]


def test_context_provider_out_field_contract() -> None:
    assert _names(agent.ContextProviderOut) == [
        "provider_id",
        "model",
        "kind",
        "policy_result",
        "secret_ref",
        "base_url",
        "api_key",
    ]


def test_context_out_field_contract() -> None:
    assert _names(agent.ContextOut) == [
        "run_id",
        "session_id",
        "agent_kind",
        "account_id",
        "space_id",
        "status",
        "attempt",
        "policy_version",
        "tool_allowlist",
        "messages",
        "provider",
        "context_build_id",
        "context_blocks",
        "cancel_requested",
    ]


def test_event_wire_constraints() -> None:
    event = _signatures(agent.EventIn)
    assert event["seq"]["ge"] == 0
    assert event["type"]["min_length"] == 1
    assert event["type"]["max_length"] == 64

    batch = _signatures(agent.EventAppendRequest)
    assert batch["events"]["min_items"] == 1
    assert batch["events"]["max_items"] == 100


def test_tool_wire_constraints() -> None:
    tool = _signatures(agent.ToolExecuteRequest)
    assert tool["version"]["ge"] == 1
    assert tool["tool_call_id"]["max_length"] == 128


def test_settle_wire_constraints() -> None:
    settle = _signatures(agent.SettleRequest)
    assert settle["error_code"]["max_length"] == 64
    # status 为 Literal["succeeded", "failed"]（枚举不允许第三值）
    literal = getattr(agent.SettleRequest.model_fields["status"].annotation, "__args__", None)
    assert literal is not None and set(literal) == {"succeeded", "failed"}
