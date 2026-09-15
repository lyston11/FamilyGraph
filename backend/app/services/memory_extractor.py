"""Deterministic rule-based memory candidate extraction (2026-09-15 audit fix).

红线（与 intake_extractor 一致）：纯词法、确定性、无 LLM、无 DB 读取——相同
输入逐字节相同输出。本模块只把会话原文翻译为 ``MemoryCandidateInput`` 提案；
落库仍走 ``memory_rag.propose_candidate`` 的完整校验链（来源归一化、幂等键、
fingerprint、领域事件），确认/索引永远等待用户显式操作。

设计取舍见 .trellis/tasks/09-15-memory-extractor-onboard/design.md：
- ``source_quote`` 必须是完整 user 消息原文（``memory_sources.resolve_source``
  对 agent_message 来源做全等校验，片段会导致 422）；
- 类别集合保守首发（生日/饮食禁忌/职业/居住地/偏好），单消息上限 3 条；
- settle 集成点全部容错：提取失败绝不影响 run 终态落库。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.services import platform_features

if TYPE_CHECKING:
    from app.models.agent import AgentRun
    from app.models.memory import MemoryCandidate
    from app.services.memory_rag import MemoryCandidateInput

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "memory-extractor-v1"
MAX_CANDIDATES_PER_MESSAGE = 3
# 与 AGENT_MESSAGE_MAX_LENGTH 对齐；防御超长输入拖慢正则。
_INPUT_MAX_CHARS = 8_000

# ---- 类别规则（有序；命中即占一个候选名额）----

# 生日：句内须同时出现日期与「生日/出生/诞辰」语境词。
_BIRTHDAY_CONTEXT = re.compile(r"生日|出生|诞辰")
_BIRTHDAY_DATE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]")
_LUNAR_PREFIX = re.compile(r"(?:农历|阴历)")

# 饮食禁忌：过敏/忌口/不能吃/不吃。
_DIETARY = re.compile(r"(过敏|忌口|不能吃|不吃)")

# 职业：封闭词表 + 是/在做/在…当 连接词。
_OCCUPATION_WORDS = "老师|医生|工程师|护士|律师|公务员|程序员|厨师|司机|教授|会计|警察|消防员"
_OCCUPATION = re.compile(rf"(?:是|在做|在[^，。；,;]{{0,6}}当)\s*({_OCCUPATION_WORDS})")

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


def _dietary_summary(text: str) -> str | None:
    match = _DIETARY.search(text)
    if match is None:
        return None
    start = max(0, match.start() - 10)
    clause = text[start : match.end() + 20].strip("，。；,;.、 ")
    return f"饮食禁忌：{clause}" if clause else None


def _occupation_summary(text: str) -> str | None:
    match = _OCCUPATION.search(text)
    if match is None:
        return None
    return f"职业：{match.group(1)}"


def _residence_summary(text: str) -> str | None:
    match = _RESIDENCE.search(text)
    if match is None:
        return None
    return f"居住地：{match.group(1)}"


def _preference_summary(text: str) -> str | None:
    match = _PREFERENCE.search(text)
    if match is None:
        return None
    thing = match.group(2).strip()
    return f"偏好：{match.group(1)}{thing}" if thing else None


_PURPOSES = {
    "birthday": "家庭成员生日提醒与亲属问答",
    "dietary": "饮食安全提醒（过敏/忌口）",
    "occupation": "家庭成员背景问答",
    "residence": "家庭成员所在地问答",
    "preference": "家庭成员偏好问答",
}


def rule_detector(text_value: str) -> list[MemoryCandidateInput]:
    """Pure deterministic detector: full user message text → candidate inputs.

    Returns ``MemoryCandidateInput`` instances (imported lazily to avoid a
    module-level cycle with memory_rag). ``source_quote`` is always the full
    original text — the caller must pass the complete message body.
    """
    from app.services.memory_rag import MemoryCandidateInput

    clean = (text_value or "").strip()[:_INPUT_MAX_CHARS]
    if not clean:
        return []
    findings: list[tuple[str, str]] = []
    for category, summarizer in (
        ("birthday", _birthday_summary),
        ("dietary", _dietary_summary),
        ("occupation", _occupation_summary),
        ("residence", _residence_summary),
        ("preference", _preference_summary),
    ):
        summary = summarizer(clean)
        if summary is not None and len(summary) <= 20_000:
            findings.append((category, summary))
    return [
        MemoryCandidateInput(
            source_quote=clean,
            summary=summary,
            suggested_scope="private",
            purpose=_PURPOSES[category],
            sensitivity="sensitive" if category in _SENSITIVE_CATEGORIES else "normal",
            extractor_category=category,
        )
        for category, summary in findings[:MAX_CANDIDATES_PER_MESSAGE]
    ]


def extract_after_settle(db: Session, run: AgentRun) -> int:
    """Best-effort extraction hook for the settle success path.

    Never raises: any failure (feature disabled, source rules, unexpected
    errors) is logged and swallowed so run settlement stays authoritative.
    Returns the number of candidates proposed this call.
    """
    from app.models.agent import AgentMessage, AgentSession
    from app.services.memory_rag import MemoryCandidateExtractor

    try:
        if run.message_id is None:
            return 0
        message = db.get(AgentMessage, run.message_id, populate_existing=True)
        if message is None or message.role != "user":
            return 0
        text = message.content_json.get("text")
        if not isinstance(text, str) or not text.strip():
            return 0
        session = db.get(AgentSession, run.session_id, populate_existing=True)
        if session is None or session.account_id is None:
            return 0
        if not platform_features.is_memory_enabled(db):
            return 0
        extractor = MemoryCandidateExtractor(detector=rule_detector)
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
]
