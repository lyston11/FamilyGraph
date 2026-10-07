"""Embedding 分段：把 RAG 块切成不超过模型上限的片段。

## 为什么需要这一层

模型上限是 **512 tokens，不是 512 个汉字**。实测：1200 字的 `家庭`×600 输入
报 `tokens=512, truncated=True`——即约 2.3 字符/token。

而现有 RAG 块是 **1200 字符固定切分**（`memory_rag` 的 v1 算法），因此：

- 一个 1200 字符的块 ≈ 500+ tokens，处在截断边缘；
- 更长或信息密度更高的块会被**静默截断**，后半段内容完全没进向量。

截断报告（`truncated=True`）能暴露问题，但暴露不等于解决：直接截断会漏掉内容，
而「把截断文本冒充完整来源」会让检索声称覆盖了实际没覆盖的内容。

## 解决方式：受版本管理的分段

```text
rag_chunks（业务块，不可变，引用指向它）
    └── rag_embedding_segments（派生的 embedding 分段，可重建）
```

分段是**派生数据**：可以随时重建，不改变旧引用指向，也不改变业务块的文本。
检索返回分段后映射回父 chunk，因此对外仍以 chunk 为引用单位。

## 切分规则

1. **优先在句边界切**：`。！？；!?;` 与换行。跨句切断会破坏语义，降低向量质量。
2. **贪心累积到 `target_chars`** 再落段，避免产生大量极短分段。
3. **保留重叠**：相邻分段重叠 `overlap_chars`，避免「答案正好被切在边界」而
   两边都检索不到。
4. **超长单句硬切**：一个没有标点的长句（例如粘贴的一整段）必须硬切，
   否则它自己就超过模型上限并被静默截断。

## 为什么 target 是 400 字符

按实测约 2.3 字符/token 估算，400 字符 ≈ 175 tokens，对 512 上限有充足余量。
即使遇到「1 字符 = 1 token」的极端文本（生僻字、逐字拆分），400 字符也仍在
上限内。**余量是刻意的**：宁可多切几段，也不要依赖截断。

## 确定性

同一输入必须产生同一分段（`(char_start, char_end, text)` 完全一致）。
否则「重建索引」会产生不同的分段集合，导致旧引用与新分段对不上。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 目标分段长度（字符）。见模块文档的余量说明。
DEFAULT_TARGET_CHARS = 400

#: 相邻分段的重叠字符数。
DEFAULT_OVERLAP_CHARS = 60

#: 单次切分的分段数上界。防止异常输入（例如数百万字符）产生海量分段，
#: 让后台索引在一次调用里做不完。
DEFAULT_MAX_SEGMENTS = 64

#: 句边界。句末标点保留在前一句里（否则语义会断开）。
_SENTENCE_END = re.compile(r"(?<=[。！？；!?;])|(?<=\n)")

#: 分段算法版本。写入分段记录，使换算法后可识别旧分段。
SEGMENT_ALGORITHM = "sentence-greedy-v1"


@dataclass(frozen=True)
class Segment:
    """一个分段。`char_start/char_end` 是相对**规范化后文本**的偏移。"""

    index: int
    text: str
    char_start: int
    char_end: int

    @property
    def char_count(self) -> int:
        return self.char_end - self.char_start


def normalize(text: str) -> str:
    """规范化空白。

    - 把 `\\r\\n` 统一为 `\\n`；
    - 折叠连续空格/制表符（但**保留**换行，换行是句边界）；
    - 折叠 3 个以上连续换行为 2 个（保留段落结构，去掉无意义空白）。

    不做 NFKC：那会改变字符数与偏移，使 `char_start/char_end` 与原文对不上。
    规范化是**分段的输入**，原文本仍保留在 `rag_chunks.text`。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _sentences(text: str) -> list[tuple[int, int]]:
    """切出句子区间 `(start, end)`。空句被丢弃。"""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for piece in _SENTENCE_END.split(text):
        if not piece:
            continue
        start = cursor
        end = cursor + len(piece)
        if piece.strip():
            spans.append((start, end))
        cursor = end
    if cursor < len(text) and text[cursor:].strip():
        spans.append((cursor, len(text)))
    return spans


def _hard_split(start: int, end: int, target: int) -> list[tuple[int, int]]:
    """硬切一个超长区间（无句边界的文本）。"""
    return [(i, min(i + target, end)) for i in range(start, end, target)]


def segment_text(
    text: str,
    *,
    target_chars: int = DEFAULT_TARGET_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    max_segments: int = DEFAULT_MAX_SEGMENTS,
) -> list[Segment]:
    """把文本切成 embedding 分段。

    ## 覆盖保证

    所有分段（忽略重叠部分）合起来覆盖规范化文本的**全部非空白内容**。
    `test_embedding_chunking.py` 对此有显式断言——漏掉内容是静默失败，
    必须由测试守住。

    ## 重叠的实现

    重叠是「上一段的尾部」+「本段」的拼接。因此相邻分段共享 `overlap_chars`
    个字符，但分段边界本身仍是确定的（`char_start/char_end` 指向**不重叠**区间）。
    """
    if target_chars <= 0:
        raise ValueError("target_chars 必须为正")
    if overlap_chars < 0:
        raise ValueError("overlap_chars 不能为负")
    if overlap_chars >= target_chars:
        # 重叠不小于目标长度会让每一段都包含上一段全部内容——分段数无限增长。
        raise ValueError(f"overlap_chars({overlap_chars}) 必须小于 target_chars({target_chars})")

    body = normalize(text)
    if not body:
        return []

    spans = _sentences(body)
    if not spans:
        return []

    # 先硬切超长句：否则单句超过模型上限并被静默截断。
    expanded: list[tuple[int, int]] = []
    for start, end in spans:
        if end - start > target_chars:
            expanded.extend(_hard_split(start, end, target_chars))
        else:
            expanded.append((start, end))

    segments: list[Segment] = []
    current_start: int | None = None
    current_end: int | None = None

    for start, end in expanded:
        if current_start is None:
            current_start, current_end = start, end
            continue
        assert current_end is not None
        # 累积后仍在上限内则继续；否则落段。
        if end - current_start <= target_chars:
            current_end = end
        else:
            segments.append(
                Segment(
                    index=len(segments),
                    text=body[current_start:current_end],
                    char_start=current_start,
                    char_end=current_end,
                )
            )
            if len(segments) >= max_segments:
                break
            # 下一段从「本段末尾回退 overlap_chars」开始，形成重叠。
            #
            # **重叠必须计入目标长度预算。** 初版直接 `current_start = current_end - overlap`
            # 再 `current_end = end`，于是新分段长度 = 重叠 + 新句，会**超过 target**
            # （实测硬切场景得到 220 > 200）——正是本模块要防止的「超上限被静默截断」。
            # 因此当「新句 + 重叠」超限时，压缩重叠量，保证 `end - start <= target`。
            assert current_end is not None
            new_start = current_end - overlap_chars
            if end - new_start > target_chars:
                new_start = end - target_chars
            current_start = max(current_start, new_start)
            current_end = end

    if current_start is not None and current_end is not None and len(segments) < max_segments:
        segments.append(
            Segment(
                index=len(segments),
                text=body[current_start:current_end],
                char_start=current_start,
                char_end=current_end,
            )
        )

    return segments


def coverage_of(body: str, segments: list[Segment]) -> tuple[int, list[tuple[int, int]]]:
    """返回 `(覆盖字符数, 未覆盖区间)`，供测试断言覆盖完整性。"""
    normalized = normalize(body)
    if not segments:
        return (0, [(0, len(normalized))] if normalized else [])

    # 合并区间（分段有重叠，因此必须合并而不是求和）。
    ordered = sorted((s.char_start, s.char_end) for s in segments)
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    covered = 0
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for start, end in merged:
        if start > cursor:
            gaps.append((cursor, start))
        covered += end - max(start, cursor)
        cursor = max(cursor, end)
    if cursor < len(normalized):
        gaps.append((cursor, len(normalized)))

    # 只把**含非空白内容**的区间算作未覆盖。
    real_gaps = [(s, e) for s, e in gaps if normalized[s:e].strip()]
    return (covered, real_gaps)
