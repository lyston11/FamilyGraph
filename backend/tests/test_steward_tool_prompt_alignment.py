"""steward prompt 与工具集的对齐合同。

## 这条合同守什么

steward 的四个 assist kind 都有**完整的** user content 输入（候选=花名册+已确认事实、
排序=推荐卡、术语=目标组、解释=卡片证据），输出是封闭 JSON schema，服务端重算栅栏
（`target_ref` 集合、`semantic_hash`、`allowed_terms`、`supporting_fact_ids`）才是授权边界。

同时 `default_allowlist("steward", ...)` 会向这些 run 广告七个 `familygraph.steward.*`
只读工具，`agent/src/session.ts` 用 `adapterFor("steward").toolNames()` 校验通过。

两者共存，但 prompt **刻意不提任何工具**。理由是成本可核验的，不是偏好：

- `prompt_digest = sha256(f"{instructions}\\n{user_content}")`；
- `prompt_version() = sha256(json.dumps(_PROMPTS))`，它是评测报告字段
  （`test_steward_candidate_prompt_contract.py` R4）与 `input_hash` / 发送前 fence 的稳定依据。

在输入已完整的前提下给 `_PROMPTS` 加工具说明，只会移动 `prompt_version()` 与全部
digest 基线，不改变模型能产生的输出。因此本文件钉住"prompt 若提到工具，必须提到该
kind 真有的工具"，并把"当前四个 kind 都不提工具"作为显式状态而不是偶然。

要改成"prompt 里声明工具"时，必须同时更新本文件的第二条断言与 prompt 版本基线——
这正是它存在的意义：让那次改动成为可评审的，而不是顺手写一行。
"""

from __future__ import annotations

import re

import pytest

from app.services import agent_tools, steward_assist

#: prompt 里出现的 FamilyGraph 工具名（与 agent/test/prompt.test.ts 的检查同形）。
_TOOL_NAME_RE = re.compile(r"familygraph\.[a-z_]+(?:\.[a-z_]+)*")


def _prompt_tool_names() -> set[str]:
    found: set[str] = set()
    for text in steward_assist._PROMPTS.values():
        found.update(_TOOL_NAME_RE.findall(text))
    return found


def test_prompt_tool_names_belong_to_the_steward_registry() -> None:
    """prompt 提到的工具必须是该 kind 真有的工具。

    反证：把某个 assistant-only 工具名写进 `_PROMPTS`，此用例必须失败
    （否则 prompt 会指向一个 steward run 永远拿不到的工具）。
    """
    for name in sorted(_prompt_tool_names()):
        spec = agent_tools.REGISTRY.get(name)
        assert spec is not None, f"steward prompt 提到了未注册的工具 {name}"
        assert spec.required_kind == "steward", (
            f"steward prompt 提到了 required_kind={spec.required_kind!r} 的工具 {name}；"
            "steward run 的 allowlist 里不会有它"
        )


def test_no_assist_kind_declares_tools_in_its_prompt() -> None:
    """当前决定：四个 kind 都不在 prompt 里声明工具。

    这是**显式状态**，不是遗漏。理由见模块 docstring：输入已完整，加工具说明只移动
    `prompt_version()`。要改变它，请连同本用例与 prompt 版本基线一起改。
    """
    assert _prompt_tool_names() == set(), (
        "steward prompt 出现了工具名。若这是有意为之，请更新本用例并确认"
        "prompt_version()/prompt_digest 基线的迁移；否则它会让模型尝试调用"
        "它拿不到的工具（历史上实测 476 次 403 / 60 个 run）"
    )


def test_every_assist_kind_has_a_prompt() -> None:
    """四个 kind 各有一段文本；少一个会让该 kind 的 run 缺 system message。"""
    assert set(steward_assist._PROMPTS) == {
        "candidate",
        "ranking",
        "terminology",
        "explanation",
    }
    for kind, text in steward_assist._PROMPTS.items():
        assert text.strip(), kind


def test_steward_tools_are_advertised_but_unmentioned() -> None:
    """合同的两半同时成立：工具被广告，且 prompt 不提它们。

    反证：若有人"为了对齐"把工具从 allowlist 移除，或把工具说明写进 prompt，
    这个用例的某一半会失败——两种改动都是独立决定，不该由重构顺手做掉。
    """
    advertised = agent_tools.default_allowlist("steward")
    assert advertised, "steward allowlist 不应为空（它有七个只读投影工具）"
    assert _prompt_tool_names() == set()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
