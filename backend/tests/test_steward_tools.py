from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services import agent_tools, steward_tools
from app.services.agent_execution import StewardExecution
from conftest import create_agent_fixture


def _execution(space_id: int, *, viewer_account_id: int | None = None) -> StewardExecution:
    return StewardExecution(
        run_id=999,
        steward_job_id=999,
        expected_attempt=1,
        space_id=space_id,
        viewer_account_id=viewer_account_id,
        agent_kind="steward",
        tool_allowlist=tuple(sorted(steward_tools.STEWARD_TOOL_NAMES)),
    )


def test_steward_registry_is_read_only_and_closed() -> None:
    assert set(steward_tools.STEWARD_TOOL_NAMES) == {
        steward_tools.TOOL_GET_SPACE_SNAPSHOT,
        steward_tools.TOOL_LIST_SPACE_NODES,
        steward_tools.TOOL_GET_VIEWER_TARGET,
        steward_tools.TOOL_GET_VIEWER_TERM,
        steward_tools.TOOL_GET_EVIDENCE,
        steward_tools.TOOL_GET_RELATIONSHIP_PATH,
    }
    assert all(
        agent_tools.REGISTRY[name].required_kind == "steward"
        for name in steward_tools.STEWARD_TOOL_NAMES
    )
    assert set(steward_tools.STEWARD_TOOL_NAMES).isdisjoint(
        {agent_tools.TOOL_ECHO, agent_tools.TOOL_PROBE_SCOPE}
    )
    assert all(
        schema["additionalProperties"] is False
        for schema in steward_tools.STEWARD_TOOL_INPUT_SCHEMAS.values()
    )


def test_steward_tools_fail_closed_without_published_projection(db_session) -> None:
    _user, space = create_agent_fixture(db_session, name="steward-tools")
    execution = _execution(space.id)

    for name, payload in (
        (steward_tools.TOOL_GET_SPACE_SNAPSHOT, {}),
        (steward_tools.TOOL_LIST_SPACE_NODES, {}),
        (steward_tools.TOOL_GET_VIEWER_TARGET, {"target_user_id": 1}),
        (
            steward_tools.TOOL_GET_VIEWER_TERM,
            {"root_user_id": 1, "target_user_id": 2},
        ),
        (steward_tools.TOOL_GET_EVIDENCE, {"target_user_id": 1}),
        (
            steward_tools.TOOL_GET_RELATIONSHIP_PATH,
            {"from_user_id": 1, "to_user_id": 2},
        ),
    ):
        if name in steward_tools.STEWARD_VIEWER_TOOL_NAMES:
            with pytest.raises(HTTPException) as exc_info:
                steward_tools.execute_steward_tool(
                    db_session, execution=execution, name=name, input_payload=payload
                )
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail["__api_error__"]["code"] == (
                "STEWARD_VIEWER_SCOPE_UNAVAILABLE"
            )
            continue
        output = steward_tools.execute_steward_tool(
            db_session, execution=execution, name=name, input_payload=payload
        )
        assert output["available"] is False
        assert output["reason"] in {"not_published", "not_available"}
        assert "space_id" not in output


def test_viewer_tools_require_viewer_scope(db_session) -> None:
    _user, space = create_agent_fixture(db_session, name="steward-no-viewer")
    execution = _execution(space.id)
    for name, payload in (
        (steward_tools.TOOL_GET_VIEWER_TARGET, {"target_user_id": 1}),
        (
            steward_tools.TOOL_GET_VIEWER_TERM,
            {"root_user_id": 1, "target_user_id": 2},
        ),
        (steward_tools.TOOL_GET_EVIDENCE, {"target_user_id": 1}),
        (
            steward_tools.TOOL_GET_RELATIONSHIP_PATH,
            {"from_user_id": 1, "to_user_id": 2},
        ),
    ):
        if name in steward_tools.STEWARD_VIEWER_TOOL_NAMES:
            with pytest.raises(HTTPException) as exc_info:
                steward_tools.execute_steward_tool(
                    db_session, execution=execution, name=name, input_payload=payload
                )
            assert exc_info.value.status_code == 403
            continue
        output = steward_tools.execute_steward_tool(
            db_session, execution=execution, name=name, input_payload=payload
        )
        assert output == {"available": False, "reason": "not_available"}


def test_steward_schema_rejects_scope_injection() -> None:
    spec = agent_tools.REGISTRY[steward_tools.TOOL_GET_SPACE_SNAPSHOT]
    with pytest.raises(agent_tools.ToolProtocolError) as exc_info:
        agent_tools.validate_input(spec, {"space_id": 123})
    assert exc_info.value.code == "AGENT_TOOL_SCHEMA_INVALID"

    evidence = agent_tools.REGISTRY[steward_tools.TOOL_GET_EVIDENCE]
    with pytest.raises(agent_tools.ToolProtocolError) as exc_info:
        agent_tools.validate_input(evidence, {"target_user_id": 1, "run_id": 2})
    assert exc_info.value.code == "AGENT_TOOL_SCHEMA_INVALID"


def test_default_allowlist_omits_viewer_tools_without_a_viewer_claim() -> None:
    """没有 viewer claim 的 steward run 不得被授予 viewer 绑定工具。

    为何是回归而不是细节：`get_viewer_target` / `get_viewer_term` 需要
    `viewer_account_id` claim，而只有 terminology attempt 带它。此前
    `default_allowlist("steward")` 无条件把两个工具放进**每个** run 的白名单，
    于是 candidate/ranking run 的模型能看到并调用一个必然被 403 拒绝的工具——
    实测 476 次拒绝、横跨 60 个 run。**不得向模型广告它无法使用的能力。**
    """
    from app.services import agent_tools, steward_tools

    without = agent_tools.default_allowlist("steward")
    for name in steward_tools.STEWARD_VIEWER_TOOL_NAMES:
        assert name not in without, f"无 viewer claim 时不应授予 {name}"

    with_viewer = agent_tools.default_allowlist("steward", viewer_scope=True)
    for name in steward_tools.STEWARD_VIEWER_TOOL_NAMES:
        assert name in with_viewer, f"有 viewer claim 时应授予 {name}"

    # 非 viewer 工具两种情况下都必须在，否则会误伤空间级工具。
    for name in steward_tools.STEWARD_TOOL_NAMES - steward_tools.STEWARD_VIEWER_TOOL_NAMES:
        assert name in without
        assert name in with_viewer


def test_assistant_allowlist_is_unaffected_by_viewer_scope() -> None:
    """viewer_scope 只影响 steward；assistant 白名单不得因它变化。"""
    from app.services import agent_tools

    assert agent_tools.default_allowlist("assistant") == agent_tools.default_allowlist(
        "assistant", viewer_scope=True
    )
