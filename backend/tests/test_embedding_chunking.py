"""Embedding 分段测试。

## 本文件守护的核心性质：**不丢内容**

静默截断是本项目最容易犯且最难发现的错误：向量覆盖了段落的前半部分，检索
看起来正常，但后半段内容永远不会被命中。因此覆盖完整性有**显式断言**——
不能只测「分段数量合理」。

## 其余性质

- 优先在句边界切（跨句切断会破坏语义）；
- 相邻分段有重叠（避免答案正好落在边界两边都检索不到）；
- 超长无标点句必须硬切（否则单段就超过模型上限）；
- 确定性（同输入同分段，否则重建索引会让旧引用对不上）；
- 规范化不改偏移（`char_start/char_end` 必须与规范化文本一致）。
"""

from __future__ import annotations

import dataclasses

import pytest

from app.services.embedding_chunking import (
    DEFAULT_TARGET_CHARS,
    Segment,
    coverage_of,
    normalize,
    segment_text,
)

# ---------------------------------------------------------------- 覆盖完整性


def test_all_content_is_covered_no_silent_loss():
    """**最重要的一条**：所有非空白内容都被某个分段覆盖。

    用真实规模的中文文本（家庭关系描述），检查合并区间后无空洞。
    """
    body = "".join(
        f"第{i}个人是第{i - 1}个人的弟弟。他住在{city}，今年{20 + i}岁。"
        for i, city in enumerate(["北京", "上海", "广州", "深圳", "杭州"] * 20)
    )
    segments = segment_text(body)
    assert segments, "未产生分段"

    covered, gaps = coverage_of(body, segments)
    assert gaps == [], f"存在未覆盖区间（内容会被静默丢失）：{gaps[:3]}"
    normalized_len = len(normalize(body))
    assert covered == normalized_len, f"覆盖 {covered} != 规范化长度 {normalized_len}"


def test_coverage_holds_for_hard_wrapped_text():
    """无标点长文本（会被硬切）也必须全覆盖。"""
    body = "家庭关系" * 500  # 无任何标点
    segments = segment_text(body)
    _covered, gaps = coverage_of(body, segments)
    assert gaps == [], f"硬切后仍有未覆盖：{gaps[:3]}"


def test_coverage_holds_for_mixed_content():
    """混合内容：短句、长句、空行、超长无标点段落。"""
    body = "\n\n".join(
        [
            "短句。",
            "这是一个稍长的句子，包含多个分句，用于测试贪心累积是否正确。" * 3,
            "无标点长段落" * 200,
            "结尾。",
        ]
    )
    segments = segment_text(body)
    _covered, gaps = coverage_of(body, segments)
    assert gaps == [], f"混合内容存在未覆盖：{gaps[:3]}"


# ---------------------------------------------------------------- 分段上界


def test_no_segment_exceeds_the_target():
    """任何分段都不得超过 target_chars。

    超过就意味着可能被模型截断——而截断是静默的。
    """
    body = "".join(f"第{i}句内容。" for i in range(300))
    for segment in segment_text(body, target_chars=200):
        assert (
            segment.char_count <= 200
        ), f"分段 {segment.index} 长度 {segment.char_count} 超过上限 200"


def test_oversized_single_sentence_is_hard_split():
    """超长无标点句必须硬切，不能整段保留。

    若整段保留，它自己就超过模型上限并被静默截断——这正是要避免的。
    """
    body = "字" * 1000  # 无标点，远超 target
    segments = segment_text(body, target_chars=200, overlap_chars=20)
    assert len(segments) > 1, "超长无标点文本未硬切"
    for segment in segments:
        assert segment.char_count <= 200


def test_max_segments_is_enforced():
    """分段数上界：异常输入不得产生海量分段（一次索引调用做不完）。"""
    body = "句子内容。" * 5000
    segments = segment_text(body, target_chars=100, max_segments=10)
    assert len(segments) <= 10


def test_invalid_parameters_fail_loud():
    """参数错误必须拒绝，不能静默产生错误分段。"""
    with pytest.raises(ValueError):
        segment_text("x", target_chars=0)
    with pytest.raises(ValueError):
        segment_text("x", overlap_chars=-1)
    # 重叠 >= 目标长度会让分段数无限增长
    with pytest.raises(ValueError):
        segment_text("x" * 100, target_chars=50, overlap_chars=50)


# ---------------------------------------------------------------- 句边界与重叠


def test_sentences_are_not_split_across_segments_when_possible():
    """句边界优先：短句不应被从中间切开。

    跨句切断会破坏语义，降低向量质量——而且不会报错。
    """
    sentences = ["第一句话内容。", "第二句话内容。", "第三句话内容。", "第四句话内容。"]
    body = "".join(sentences)
    segments = segment_text(body, target_chars=20, overlap_chars=2)
    # 每个分段要么以句末标点结尾，要么是硬切（本用例无硬切）
    for segment in segments:
        assert (
            segment.text.endswith(("。", "\n")) or segment.char_count == 20
        ), f"分段未在句边界结束：{segment.text!r}"


def test_adjacent_segments_overlap():
    """相邻分段必须重叠，避免答案落在边界两边都检索不到。"""
    body = "".join(f"第{i}句内容。" for i in range(40))
    segments = segment_text(body, target_chars=120, overlap_chars=40)
    assert len(segments) >= 2

    # 分段文本有重叠：后一段的起点早于前一段的终点（因为重叠是拼接出来的）
    for previous, current in zip(segments, segments[1:], strict=False):
        assert current.char_start < previous.char_end, "相邻分段没有重叠"


def test_overlap_does_not_change_covered_range():
    """重叠不应造成「重复计算覆盖」的假象——覆盖是按合并区间算的。"""
    body = "".join(f"第{i}句。" for i in range(50))
    segments = segment_text(body, target_chars=100, overlap_chars=50)
    covered, gaps = coverage_of(body, segments)
    assert gaps == []
    assert covered <= len(normalize(body)), "覆盖数超过了文本长度（区间未合并）"


# ---------------------------------------------------------------- 确定性


def test_segmentation_is_deterministic():
    """同输入必须产生完全相同的分段。

    否则「重建索引」会产生不同分段集合，旧引用与新分段对不上。
    """
    body = "".join(f"第{i}句内容，用于测试确定性。" for i in range(60))
    first = segment_text(body)
    second = segment_text(body)
    assert [(s.index, s.char_start, s.char_end, s.text) for s in first] == [
        (s.index, s.char_start, s.char_end, s.text) for s in second
    ]


def test_char_offsets_match_the_normalized_text():
    """`char_start/char_end` 必须与规范化文本一致，否则引用会错位。"""
    body = "第一句。\r\n\r\n第二句。\t\t第三句。"
    normalized = normalize(body)
    for segment in segment_text(body):
        assert (
            segment.text == normalized[segment.char_start : segment.char_end]
        ), f"分段 {segment.index} 的偏移与文本不一致"


# ---------------------------------------------------------------- 规范化


def test_normalize_collapses_whitespace_but_keeps_newlines():
    """折叠空白但保留换行（换行是句边界）。"""
    assert normalize("a\r\nb") == "a\nb"
    assert normalize("a\t\tb") == "a b"
    assert normalize("a\u3000\u3000b") == "a b"
    assert normalize("a\n\n\n\nb") == "a\n\nb"
    assert normalize("  a  ") == "a"


def test_normalize_does_not_change_character_classes():
    """不做 NFKC：那会改变字符数与偏移，使引用对不上原文。"""
    # NFKC 会把全角数字转半角；这里必须保持原样
    assert normalize("１２３") == "１２３"
    # 兼容字符（如 ㍿）在 NFKC 下会变化，必须保持
    assert normalize("㍿") == "㍿"


# ---------------------------------------------------------------- 边界


def test_empty_and_whitespace_only_input_produce_no_segments():
    """空输入不产生分段（没有内容需要编码）。"""
    assert segment_text("") == []
    assert segment_text("   ") == []
    assert segment_text("\n\n\t ") == []


def test_short_text_produces_single_segment():
    """短文本产生单个分段，不产生多余碎片。"""
    segments = segment_text("只有一句话。")
    assert len(segments) == 1
    assert segments[0].text == "只有一句话。"
    assert segments[0].char_start == 0
    assert segments[0].char_end == len("只有一句话。")


def test_segment_indices_are_sequential():
    """分段索引必须连续（0,1,2,…），便于按序重建。"""
    body = "".join(f"第{i}句。" for i in range(80))
    # 必须显式给 overlap：默认 60 与 target 60 冲突（overlap >= target 会让分段
    # 无限增长，因此函数 fail-loud）。这是刻意的 API 约束，不是测试特例。
    segments = segment_text(body, target_chars=60, overlap_chars=10)
    assert [s.index for s in segments] == list(range(len(segments)))


def test_default_target_leaves_room_for_the_token_limit():
    """默认 target 必须对 512 token 上限留有余量。

    实测约 2.3 字符/token，但极端文本可能接近 1 字符/token。默认 400 字符在
    「1 字符 = 1 token」的最坏情况下仍低于 512。
    """
    assert (
        DEFAULT_TARGET_CHARS <= 512
    ), "默认分段长度在「1 字符 = 1 token」的最坏情况下会超过模型上限"


def test_segment_type_is_immutable():
    """分段是不可变值，避免下游意外改写导致偏移与文本不一致。"""
    segment = Segment(index=0, text="x", char_start=0, char_end=1)
    # frozen dataclass 抛 FrozenInstanceError（AttributeError 的子类）；
    # 断言具体类型而不是裸 Exception。
    with pytest.raises(dataclasses.FrozenInstanceError):
        segment.text = "y"  # type: ignore[misc]
