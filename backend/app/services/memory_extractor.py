"""Deterministic rule-based memory candidate extraction (2026-09-15 audit fix).

红线（与 intake_extractor 一致）：纯词法、确定性、无 LLM、无 DB 读取——相同
输入逐字节相同输出。本模块只把会话原文翻译为 ``MemoryCandidateInput`` 提案；
落库仍走 ``memory_rag.propose_candidate`` 的完整校验链（来源归一化、幂等键、
fingerprint、领域事件），确认/索引永远等待用户显式操作。

设计取舍见 .trellis/tasks/09-15-memory-extractor-onboard/design.md：
- ``source_quote`` 必须是完整 user 消息原文（``memory_sources.resolve_source``
  对 agent_message 来源做全等校验，片段会导致 422）；
- 类别集合保守首发（生日/纪念日/饮食禁忌/职业/学校/居住地/偏好），单消息上限 3 条；
- settle 集成点全部容错：提取失败绝不影响 run 终态落库。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app import config
from app.services import platform_features

if TYPE_CHECKING:
    from app.models.agent import AgentRun
    from app.models.memory import MemoryCandidate
    from app.services.memory_rag import MemoryCandidateInput

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "memory-extractor-v1"
MAX_CANDIDATES_PER_MESSAGE = 3
# 与消息长度上限对齐；防御超长输入拖慢正则。
_INPUT_MAX_CHARS = config.AGENT_MESSAGE_MAX_LENGTH

# 否定/习语守卫：命中关键词前出现这些字时不产卡（避免「不喜欢」被判为偏好、
# 「哪怕」被判为「怕」）。
_NEGATION = re.compile(r"[不没别无哪]")

# ---- 类别规则（有序；命中即占一个候选名额）----

# 生日：句内须同时出现日期与「生日/出生/诞辰」语境词。
_BIRTHDAY_CONTEXT = re.compile(r"生日|出生|诞辰")
_BIRTHDAY_DATE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]")
_LUNAR_PREFIX = re.compile(r"(?:农历|阴历)")

# 纪念日：同样要求「X月X日」日期，但语境词是纪念类。
_ANNIVERSARY_CONTEXT = re.compile(r"纪念日|纪念|周年")

# 饮食禁忌：强信号（过敏/忌口/不能吃）+ 弱信号「不吃」（后接动词时不命中，如「不吃亏」「不喜欢」）。
_DIETARY_STRONG = re.compile(r"(过敏|忌口|不能吃)")
_DIETARY_AVOID = re.compile(r"不吃")
_DIETARY_AVOID_BLOCK = re.compile(r"^[亏消准着过喜爱想要会敢能得吃]")
_DIETARY = re.compile(r"(过敏|忌口|不能吃|不吃)")

# 职业：封闭词表 + 是/在做/在…当 连接词。
_OCCUPATION_WORDS = "老师|医生|工程师|护士|律师|公务员|程序员|厨师|司机|教授|会计|警察|消防员|学生"
_OCCUPATION = re.compile(rf"(?:是|在做|在[^，。；,;]{{0,6}}当)\s*({_OCCUPATION_WORDS})")

# 学校：在读/就读/上学/在…上学 + 校名（至少 3 字，避免「读大学」类噪声）。
_SCHOOL = re.compile(
    r"(?:在读|就读|上学|在|上|读)\s*([\u4e00-\u9fff]{0,10}(?:大学|学院|中学|高中|初中|小学))"
)

# 居住地：常见城市 + 通用「X市/X县」后缀。
_CITIES = (
    "北京|上海|广州|深圳|成都|杭州|重庆|武汉|西安|南京|天津|苏州|长沙|郑州|"
    "青岛|厦门|福州|合肥|昆明|贵阳|南昌|济南|太原|沈阳|长春|哈尔滨|石家庄|"
    "兰州|西宁|南宁|海口|呼和浩特|乌鲁木齐|拉萨|银川"
)
_RESIDENCE = re.compile(rf"(?:住在|家在|搬到了)\s*({_CITIES}|[\u4e00-\u9fff]{{1,6}}[市县])")

# 偏好：喜欢/最爱/讨厌/怕 + 短名词短语（长词优先，避免「最爱」被「喜欢」截断）。
_PREFERENCE = re.compile(r"(最喜欢|最爱|喜欢|讨厌|怕)([\u4e00-\u9fff\w]{1,20})")

_SENSITIVE_CATEGORIES = frozenset({"dietary"})


def _negated(text: str, index: int) -> bool:
    """True when the 2 characters before ``index`` negate the statement."""
    return bool(_NEGATION.search(text[max(0, index - 2) : index]))


def _birthday_summary(text: str) -> str | None:
    if not _BIRTHDAY_CONTEXT.search(text):
        return None
    match = _BIRTHDAY_DATE.search(text)
    if match is None:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    lunar = "（农历）" if _LUNAR_PREFIX.search(text[: match.start()]) else ""
    return f"生日：{month}月{day}日{lunar}"


def _anniversary_summary(text: str) -> str | None:
    if not _ANNIVERSARY_CONTEXT.search(text):
        return None
    match = _BIRTHDAY_DATE.search(text)
    if match is None:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return f"纪念日：{month}月{day}日"


_CLAUSE_SPLIT = re.compile(r"[，。；！？,;.!?\n]")


def _clause_around(text: str, start: int, end: int) -> str:
    """The punctuation-delimited clause containing [start, end)."""
    left = 0
    for match in _CLAUSE_SPLIT.finditer(text, 0, start):
        left = match.end()
    right = len(text)
    tail = _CLAUSE_SPLIT.search(text, end)
    if tail is not None:
        right = tail.start()
    return text[left:right].strip()


def _dietary_summary(text: str) -> str | None:
    strong = _DIETARY_STRONG.search(text)
    if strong is not None and not _negated(text, strong.start()):
        clause = _clause_around(text, strong.start(), strong.end())
        return f"饮食禁忌：{clause}" if clause else None
    avoid = _DIETARY_AVOID.search(text)
    if avoid is None or _DIETARY_AVOID_BLOCK.match(text[avoid.end() :]):
        return None
    clause = _clause_around(text, avoid.start(), avoid.end())
    return f"饮食禁忌：{clause}" if clause else None


def _occupation_summary(text: str) -> str | None:
    match = _OCCUPATION.search(text)
    if match is None or _negated(text, match.start()):
        return None
    return f"职业：{match.group(1)}"


def _school_summary(text: str) -> str | None:
    match = _SCHOOL.search(text)
    if match is None or _negated(text, match.start()):
        return None
    name = match.group(1)
    return f"学校：{name}" if len(name) >= 3 else None


def _residence_summary(text: str) -> str | None:
    match = _RESIDENCE.search(text)
    if match is None or _negated(text, match.start()):
        return None
    return f"居住地：{match.group(1)}"


def _preference_summary(text: str) -> str | None:
    match = _PREFERENCE.search(text)
    if match is None or _negated(text, match.start()):
        return None
    thing = match.group(2).strip()
    return f"偏好：{match.group(1)}{thing}" if thing else None


_PURPOSES = {
    "birthday": "家庭成员生日提醒与亲属问答",
    "anniversary": "家庭纪念日提醒",
    "dietary": "饮食安全提醒（过敏/忌口）",
    "occupation": "家庭成员背景问答",
    "school": "家庭成员就学背景问答",
    "residence": "家庭成员所在地问答",
    "preference": "家庭成员偏好问答",
}

_CATEGORY_SUMMARIZERS = (
    ("birthday", _birthday_summary),
    ("anniversary", _anniversary_summary),
    ("dietary", _dietary_summary),
    ("occupation", _occupation_summary),
    ("school", _school_summary),
    ("residence", _residence_summary),
    ("preference", _preference_summary),
)


def rule_detector_with_stats(text_value: str) -> tuple[list[MemoryCandidateInput], int]:
    """Detector plus the number of over-limit candidates dropped.

    Dropped counts feed audit logging so silent truncation stays observable.
    """
    from app.services.memory_rag import MemoryCandidateInput

    clean = (text_value or "").strip()[:_INPUT_MAX_CHARS]
    if not clean:
        return [], 0
    findings: list[tuple[str, str]] = []
    for category, summarizer in _CATEGORY_SUMMARIZERS:
        summary = summarizer(clean)
        if summary is not None and len(summary) <= 20_000:
            findings.append((category, summary))
    kept = findings[:MAX_CANDIDATES_PER_MESSAGE]
    dropped = len(findings) - len(kept)
    return (
        [
            MemoryCandidateInput(
                source_quote=clean,
                summary=summary,
                suggested_scope="private",
                purpose=_PURPOSES[category],
                sensitivity="sensitive" if category in _SENSITIVE_CATEGORIES else "normal",
                extractor_category=category,
            )
            for category, summary in kept
        ],
        dropped,
    )


def rule_detector(text_value: str) -> list[MemoryCandidateInput]:
    """Pure deterministic detector: full user message text → candidate inputs.

    ``source_quote`` is always the full original text — the caller must pass
    the complete message body (agent_message sources are checked verbatim).
    """
    return rule_detector_with_stats(text_value)[0]


def extract_after_settle(db: Session, run: AgentRun) -> int:
    """Best-effort extraction hook for the settle success path.

    Never raises: the whole extraction runs inside a SAVEPOINT so any failure
    (feature disabled, source rules, deferred flush errors, unexpected bugs)
    rolls back only the candidates and leaves run settlement authoritative.
    Returns the number of candidates proposed this call.
    """
    from app.models.agent import AgentMessage, AgentSession
    from app.services.memory_rag import MemoryCandidateExtractor

    if run.message_id is None:
        return 0
    try:
        message = db.get(AgentMessage, run.message_id, populate_existing=True)
        if message is None or message.role != "user":
            return 0
        text = message.content_json.get("text")
        if not isinstance(text, str) or not text.strip():
            return 0
        if len(text) > _INPUT_MAX_CHARS:
            # 超限不截断：截断会使 source_quote 与消息原文不等而触发 422，
            # 这里直接跳过并留日志，避免静默失配。
            logger.info("memory extraction skipped over-length message run=%s", run.id)
            return 0
        session = db.get(AgentSession, run.session_id, populate_existing=True)
        if session is None or session.account_id is None:
            return 0
        if not platform_features.is_memory_enabled(db):
            return 0
        inputs, dropped = rule_detector_with_stats(text)
        if dropped:
            logger.info(
                "memory extraction dropped %s over-limit candidate(s) run=%s",
                dropped,
                run.id,
            )
        if not inputs:
            return 0
        with db.begin_nested():
            extractor = MemoryCandidateExtractor(detector=lambda _text: inputs)
            extractor.version = EXTRACTOR_VERSION
            proposed: list[MemoryCandidate] = extractor.extract(
                db,
                author_account_id=session.account_id,
                conversation_text=text,
                source_message_id=message.id,
            )
        return len(proposed)
    except Exception:
        logger.warning("memory extraction failed run=%s error swallowed", run.id, exc_info=True)
        return 0


__all__ = [
    "EXTRACTOR_VERSION",
    "MAX_CANDIDATES_PER_MESSAGE",
    "extract_after_settle",
    "rule_detector",
    "rule_detector_with_stats",
]
