"""跨语言工具契约快照的新鲜度门禁。

## 这组测试守住什么

工具注册表住在两个不能互相 import 的地方：`backend/app/services/agent_tools.py`
（Python，强制方）与 `agent/src/tools.ts`（TypeScript，模型看到的一侧）。
手工维护它们曾经造成真实故障：`familygraph.propose_memory` 只存在于后端，
于是平台记忆开启时**每一次 assistant run** 都在 sidecar 的 allowlist 校验处失败。

门禁是两半，各自能独立失败：

1. 这里——快照必须与 `REGISTRY` 一致（改了注册表没重生成 → 红）；
2. `agent/test/tool-contract-parity.test.ts`——sidecar 声明必须与快照一致。

只有第 2 半时，快照可以过期（改了 Python 没重生成），TS 比对陈旧快照仍然"一致"。
只有第 1 半时，快照与 TS 可以一起漂移（改了 Python + 重生成快照，但没改 TS）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import agent_tools

SNAPSHOT = Path(__file__).resolve().parents[2] / "shared" / "agent-tool-contract.json"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_snapshot_exists() -> None:
    assert SNAPSHOT.exists(), (
        f"缺少 {SNAPSHOT}；运行 python3 scripts/export-agent-tool-contract.py"
    )


def test_snapshot_is_fresh() -> None:
    """注册表变了而快照没重生成 → 失败。

    反证：改任一 `input_schema` 的边界值（如 `maxLength`）后不重生成，此用例必须失败。
    """
    body = _snapshot()
    assert body["schema_version"] == agent_tools.CONTRACT_SCHEMA_VERSION, (
        "快照格式版本与 CONTRACT_SCHEMA_VERSION 不一致；"
        "改变规范化规则时须升版本并重生成"
    )
    expected = agent_tools.canonical_contract()
    actual = body["tools"]
    assert set(actual) == set(expected), (
        "工具集合不一致："
        f"仅快照有={sorted(set(actual) - set(expected))} "
        f"仅注册表有={sorted(set(expected) - set(actual))}"
    )
    for name in sorted(expected):
        assert actual[name] == expected[name], (
            f"{name} 的契约不一致（重生成快照：python3 scripts/export-agent-tool-contract.py）\n"
            f"  快照={json.dumps(actual[name], ensure_ascii=False, sort_keys=True)}\n"
            f"  注册表={json.dumps(expected[name], ensure_ascii=False, sort_keys=True)}"
        )


def test_every_registered_tool_appears_in_the_contract() -> None:
    """防止"注册表有工具但导出漏掉"这类静默缺口。"""
    assert set(agent_tools.canonical_contract()) == set(agent_tools.REGISTRY)


def test_contract_carries_no_description() -> None:
    """`description` 只面向模型，两侧文案必然不同，不属于契约。

    它是规范化规则的第 1 条；若它漏进契约，parity 门禁会永远红——那会诱使人
    把门禁放宽，而不是把描述对齐。
    """
    text = json.dumps(_snapshot()["tools"], ensure_ascii=False)
    assert '"description"' not in text


def test_contract_records_boundaries_and_additional_properties() -> None:
    """边界关键字与 `additionalProperties` 必须在契约里。

    前者是真正的受理边界（当前两侧并不一致，必须被钉住）；后者是
    "模型不能发送 space_id/account_id 等任意权限字段"这条边界在 schema 层的表达。
    """
    tools = _snapshot()["tools"]
    assert tools["familygraph.echo"]["schema"]["properties"]["text"]["maxLength"] == 1000
    for name, entry in tools.items():
        schema = entry["schema"]
        assert schema["type"] == "object", name
        assert schema.get("additionalProperties") is False, (
            f"{name} 的 schema 必须显式 additionalProperties=false："
            "未声明不等于允许，两侧表达同一个边界"
        )
        assert "required" in schema, f"{name} 缺少 required（规范化应补空列表）"


def test_unknown_schema_keyword_is_rejected() -> None:
    """未知 schema 关键字必须报错，而不是被静默忽略。

    反证：去掉 `_validate_value` 里的 `_SCHEMA_ALLOWED_KEYWORDS` 检查后，此用例失败
    （`maxLenght` 会被当成不存在的键跳过，约束静默失效）。
    """
    bogus = agent_tools.ToolSpec(
        name="familygraph.test_bogus",
        version=1,
        description="test only",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "maxLenght": 5}},
            "required": [],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
    )
    with pytest.raises(agent_tools.ToolProtocolError) as excinfo:
        agent_tools.validate_input(bogus, {"text": "abcdefgh"})
    # 500 而非 422：注册表自身配置错误，不是调用方问题。
    assert excinfo.value.status_code == 500
    assert excinfo.value.code == "INTERNAL_ERROR"
    assert excinfo.value.detail is not None
    assert excinfo.value.detail["unknown"] == ["maxLenght"]


def test_every_registered_schema_uses_only_known_keywords() -> None:
    """全量自检：现有注册表没有用集合外的关键字（新增检查不得误伤既有 schema）。"""
    for name, spec in agent_tools.REGISTRY.items():
        agent_tools.validate_input(spec, _minimal_valid_payload(spec))


def _minimal_valid_payload(spec: agent_tools.ToolSpec) -> dict:
    """构造一个只含必填字段的最小合法输入，用于触发全量 schema 自检。"""
    payload: dict = {}
    for key in spec.input_schema.get("required", []):
        sub = spec.input_schema["properties"][key]
        kind = sub.get("type")
        if kind == "string":
            payload[key] = "x"
        elif kind == "integer":
            payload[key] = sub.get("minimum", 1)
        elif kind == "boolean":
            payload[key] = True
        elif kind == "array":
            payload[key] = []
        else:  # pragma: no cover - 注册表当前没有 object 型必填字段
            raise AssertionError(f"{spec.name}: 无法构造 {kind!r} 的最小值")
    return payload
