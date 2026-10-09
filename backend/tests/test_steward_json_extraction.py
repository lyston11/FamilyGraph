"""守护 `_extract_json` 的容器语义。

## 为什么这是安全要求（实测生产回归）

旧实现整体解析失败后**固定先找 `[`/`]`**，找不到才退回 `{`/`}`。对数组输出的
kind（candidate/ranking）无害，但对**对象输出的 kind 是致命的**：

```text
{"items":[{"target_ref":"t001"}]}              -> dict   （整体解析成功）
建议如下：{"items":[{"target_ref":"t001"}]}     -> list   （切出了内层数组）
```

模型只要在 JSON 前后写一句自然语言，对象就会被切成 `items` 的**内层数组**，
terminology/explanation 的 `isinstance(payload, dict)` 判定失败，整次有效调用被判
`degraded/invalid_output`。

实测（生产，2026-10-08/09）：terminology 11 次 `invalid_output`，诊断一致显示
`looks_like_json=false`（原文以说明文字开头）；同一提示词下 candidate/ranking
（数组输出）全部正常——正是「只有对象 kind 受害」的特征。
"""

from __future__ import annotations

from app.services.steward_guard import _extract_json

_OBJ = (
    '{"items":[{"target_ref":"t001","concept_code":"UNCLE","term":"叔叔","reason_code":"synonym"}]}'
)


def test_object_survives_surrounding_prose() -> None:
    """对象输出在带前后说明时必须仍解析为 dict。"""
    for name, text in {
        "纯对象": _OBJ,
        "前导说明": f"根据给定的关系路径，我建议：\n{_OBJ}",
        "后置说明": f"{_OBJ}\n以上为建议。",
        "围栏": f"```json\n{_OBJ}\n```",
        "前后说明+围栏": f"分析如下：\n```json\n{_OBJ}\n```\n完毕。",
    }.items():
        got = _extract_json(text)
        assert isinstance(got, dict), f"{name}: 解析为 {type(got).__name__}，应为 dict"
        assert "items" in got, f"{name}: 丢失外层 items"


def test_array_kinds_still_parse_as_list() -> None:
    """数组输出的 kind 行为必须保持不变。"""
    for name, text in {
        "纯数组": "[1,2]",
        "说明+数组": "排序结果：\n[1,2]",
        "对象数组": '[{"kind":"spouse","subject":"n001","object":"n002"}]',
        "围栏数组": "```json\n[1,2]\n```",
    }.items():
        got = _extract_json(text)
        assert isinstance(got, list), f"{name}: 解析为 {type(got).__name__}，应为 list"


def test_earliest_delimiter_wins_when_prose_contains_brackets() -> None:
    """说明文字里先出现无关方括号时，仍应取真正的对象。"""
    got = _extract_json('参考 [注] 后：{"items":[]}')
    assert isinstance(got, dict), f"解析为 {type(got).__name__}，应为 dict"


def test_unparseable_returns_none() -> None:
    """完全无法解析时返回 None（不得伪装成空成功）。"""
    assert _extract_json("完全没有 JSON 的一段话") is None
    assert _extract_json("") is None


def test_expect_disambiguates_nested_containers() -> None:
    """`expect` 必须消解嵌套歧义：`{"items":[...]}` 同时含对象与数组。

    两个方向都要成立，因为模型既可能把对象包起来，也可能把数组包起来：
      - terminology/explanation 要 dict，不能因内层数组而切错；
      - candidate/ranking 要 list，模型写 `{"candidates":[...]}` 时仍须取到数组。
    """
    nested_obj = '建议如下：\n{"items":[{"target_ref":"t001"}]}'
    nested_arr = '结果：\n{"candidates":[1,2]}'

    assert isinstance(_extract_json(nested_obj, dict), dict)
    assert isinstance(_extract_json(nested_obj, list), list)
    assert isinstance(_extract_json(nested_arr, list), list)
    assert isinstance(_extract_json(nested_arr, dict), dict)


def test_expect_filters_mismatched_type() -> None:
    """类型不符时必须返回 None（不得把数组当对象交付给校验器）。"""
    assert _extract_json('建议：\n{"items":[1]}', str) is None
    assert _extract_json("[1,2]", dict) is None
