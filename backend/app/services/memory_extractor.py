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

EXTRACTOR_VERSION = "memory-extractor-v2"
MAX_CANDIDATES_PER_MESSAGE = 3
# 与消息长度上限对齐；防御超长输入拖慢正则。
_INPUT_MAX_CHARS = config.AGENT_MESSAGE_MAX_LENGTH

# 否定/习语守卫：命中关键词前出现这些字时不产卡（避免「不喜欢」被判为偏好、
# 「哪怕」被判为「怕」）。
_NEGATION = re.compile(r"[不没别无哪]")

# ---- 类别规则（有序；命中即占一个候选名额）----

# 生日：句内须同时出现日期与「生日/出生/诞辰」语境词。
_BIRTHDAY_CONTEXT = re.compile(r"生日|出生|诞辰")
# 日期两种写法都要认：阿拉伯数字（3月5日）与中文数字（三月五日）。中文场合里
# 后者是常见写法，只认前者会静默漏掉整类生日/纪念日记忆（P0 基线 gap 用例）。
_CN_DIGITS = "零一二三四五六七八九十"
# 月份：`三`/`十`/`二十三`。日：`五`/`十五`，或日期专用的 `初二`/`初十`。
_CN_MONTH = f"[{_CN_DIGITS}]{{1,3}}"
_CN_DAY = f"(?:初[{_CN_DIGITS}]|[{_CN_DIGITS}]{{1,3}})"
_BIRTHDAY_DATE = re.compile(
    # 阿拉伯数字写法必须带 号/日；中文写法里 `八月初二` 本身就是完整日期，
    # 因此尾缀可省（少了这一点，「农历八月初二」整条漏掉）。
    rf"(\d{{1,2}})\s*月\s*(\d{{1,2}})\s*[号日]" rf"|({_CN_MONTH})\s*月\s*({_CN_DAY})\s*[号日]?"
)
_LUNAR_PREFIX = re.compile(r"(?:农历|阴历)")
# 「十月初二」这类以十起头的写法：`[零一二三四五六七八九十]{1,3}` 能整体匹配，
# 由 `_cn_number` 做语义解释。这里只负责切词，不负责语义。
_CN_NUM_VALUE = {
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _cn_number(value: str) -> int | None:
    """把中文数字（`三`/`十`/`十五`/`二十`/`二十三`/`初二`）转成整数。

    只支持 1~31 这一实际范围：日期之外的语义（百/千/万）不在这里猜。
    解析失败返回 None，由调用方当作「不命中」处理——宁可漏产候选，也不产出一个
    数字错的生日。
    """
    if not value:
        return None
    # 「初X」是日期专用写法，`初十` = 10，其余 `初X` 就是 X。
    if value.startswith("初"):
        return _cn_number(value[1:])
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        tens = 1 if left == "" else _CN_NUM_VALUE.get(left)
        units = 0 if right == "" else _CN_NUM_VALUE.get(right)
        if tens is None or units is None:
            return None
        return tens * 10 + units
    if len(value) != 1:
        return None
    return _CN_NUM_VALUE.get(value)


def _birthday_parts(match: re.Match[str]) -> tuple[int, int] | None:
    """Normalize both date spellings into `(month, day)`, or None if invalid."""
    if match.group(1) is not None:
        month: int | None = int(match.group(1))
        day: int | None = int(match.group(2))
    else:
        month = _cn_number(match.group(3) or "")
        day = _cn_number(match.group(4) or "")
    if month is None or day is None:
        return None
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return month, day


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
#
# 形式「在<校名>中学」既可能是在读（「妹妹在浙江大学」，也可能是「在中学读书」），
# 也可能是**职业**（「舅舅在南京的中学教书」）。区分靠后面的谓语：
# `_SCHOOL_WORK_VERB` 命中时该句是职业句，不产学校候选（P0 基线 gap 用例
# `x-determinism` 实测 `在南京的中学教书` 同时产出 occupation 与 school）。
_SCHOOL = re.compile(
    r"(?:在读|就读|上学|在|上|读)\s*([\u4e00-\u9fff]{0,10}(?:大学|学院|中学|高中|初中|小学))"
)
_SCHOOL_WORK_VERB = re.compile(r"教书|任教|上课|工作|上班|当老师|做老师")

# 居住地：常见城市 + 通用「X市/X县」后缀。
_CITIES = (
    "北京|上海|广州|深圳|成都|杭州|重庆|武汉|西安|南京|天津|苏州|长沙|郑州|"
    "青岛|厦门|福州|合肥|昆明|贵阳|南昌|济南|太原|沈阳|长春|哈尔滨|石家庄|"
    "兰州|西宁|南宁|海口|呼和浩特|乌鲁木齐|拉萨|银川"
)
_RESIDENCE = re.compile(rf"(?:住在|家在|搬到了?)\s*({_CITIES}|[\u4e00-\u9fff]{{1,6}}[市县])")

# 命名习惯（称呼偏好）：家里人怎么称呼某人，而不是某人喜欢什么。
# 与 `preference` 是**不同**的类别：前者是称谓约定（家族语境长期有效），
# 后者是好恶（个人口味）。混在一起会让「奶奶习惯被称为阿婆」变成一条口味记忆。
#
# 动词按**长优先**排列：`被` 排在 `被称为` 前面会把「被称为阿婆」的称谓读成
# 「称为阿婆」（实测）。
_TERM_VERB = re.compile(r"(?:被称为|被叫作|被叫做|被称作|称呼为|称作|叫做|叫|被)")
_TERM_CONTEXT = re.compile(r"在家|家里|我们|平时|都|一般|习惯|称呼")
_TERM_TRAILING = re.compile(r"(?:在家里|在家|家里|我们|平时|都|一般|习惯|的)+$")

# 迁居史：何时从哪里搬到哪里。时间与两端地点都是家族问答的高频事实。
# 只匹配**核心结构**「从 A 搬到 B」，主语与年份从句首回取——把主语写进正则会让
# 惰性量词在主语缺失时先匹配空串而整条漏掉（实测）。
_MIGRATION = re.compile(r"从([\u4e00-\u9fff]{1,8}?)(?:搬到|迁到|搬去|迁往)([\u4e00-\u9fff]{2,8})")
_MIGRATION_YEAR = re.compile(r"((?:\d{4}|[\u4e00-\u9fff]{4}))年")

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
    parts = _birthday_parts(match)
    if parts is None:
        return None
    month, day = parts
    lunar = "（农历）" if _LUNAR_PREFIX.search(text[: match.start()]) else ""
    return f"生日：{month}月{day}日{lunar}"


def _anniversary_summary(text: str) -> str | None:
    if not _ANNIVERSARY_CONTEXT.search(text):
        return None
    match = _BIRTHDAY_DATE.search(text)
    if match is None:
        return None
    parts = _birthday_parts(match)
    if parts is None:
        return None
    month, day = parts
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
    if len(name) < 3:
        return None
    # 校名之后、同一子句内出现「教书/任教/工作」等谓语时，这句说的是职业而不是就学。
    # 只看**校名之后**的部分：否则「妹妹在读浙江大学，爸爸在那教书」会因为后半句
    # 被整句拒绝。子句边界用同一个 `_CLAUSE_SPLIT`。
    tail = text[match.end() :]
    boundary = _CLAUSE_SPLIT.search(tail)
    if boundary is not None:
        tail = tail[: boundary.start()]
    if _SCHOOL_WORK_VERB.search(tail):
        return None
    return f"学校：{name}"


def _residence_summary(text: str) -> str | None:
    match = _RESIDENCE.search(text)
    if match is None or _negated(text, match.start()):
        return None
    if _MIGRATION.search(text):
        # 「从 A 搬到 B」由 `migration` 表达（含迁出地，信息更完整），
        # 不重复产一条只说目的地的居住地候选。
        return None
    return f"居住地：{match.group(1)}"


def _preference_summary(text: str) -> str | None:
    match = _PREFERENCE.search(text)
    if match is None or _negated(text, match.start()):
        return None
    thing = match.group(2).strip()
    return f"偏好：{match.group(1)}{thing}" if thing else None


def _clause_start(text: str, index: int) -> int:
    """Index of the start of the punctuation-delimited clause containing ``index``."""
    left = 0
    for match in _CLAUSE_SPLIT.finditer(text, 0, index):
        left = match.end()
    return left


def _term_summary(text: str) -> str | None:
    """称呼偏好：`X 在家里习惯被称为 Y`。

    只在**动词之前**出现称谓语境词（在家/家里/我们/平时/都/一般/习惯/称呼）时才
    产出。没有这个限定，「他叫我去吃饭」会被当成称呼偏好。
    """
    for verb in _TERM_VERB.finditer(text):
        if _negated(text, verb.start()):
            continue
        if not _TERM_CONTEXT.search(text[: verb.start()]):
            continue
        term_match = re.match(r"\s*([\u4e00-\u9fffA-Za-z0-9]{1,10})", text[verb.end() :])
        if term_match is None:
            continue
        term = term_match.group(1).strip()
        if not term:
            continue
        head = text[_clause_start(text, verb.start()) : verb.start()].strip()
        subject = _TERM_TRAILING.sub("", head).lstrip("在").strip()
        if not subject or len(subject) > 12:
            continue
        return f"称呼：{subject}被称为{term}"
    return None


def _migration_summary(text: str) -> str | None:
    """迁居史：`[某人] [某年] 从 A 搬到 B`。主语与年份可缺省（不猜）。"""
    match = _MIGRATION.search(text)
    if match is None or _negated(text, match.start()):
        return None
    origin, destination = match.group(1).strip(), match.group(2).strip()
    if not origin or not destination or origin == destination:
        return None
    head = text[_clause_start(text, match.start()) : match.start()]
    year_match = _MIGRATION_YEAR.search(head)
    year = year_match.group(1) if year_match else ""
    if year_match is not None:
        head = head[: year_match.start()] + head[year_match.end() :]
    subject = head.lstrip("在").strip()
    parts = [subject, f"{year}年" if year else "", f"从{origin}搬到{destination}"]
    return "迁居：" + "".join(part for part in parts if part)


_PURPOSES = {
    "birthday": "家庭成员生日提醒与亲属问答",
    "anniversary": "家庭纪念日提醒",
    "dietary": "饮食安全提醒（过敏/忌口）",
    "occupation": "家庭成员背景问答",
    "school": "家庭成员就学背景问答",
    "residence": "家庭成员所在地问答",
    "preference": "家庭成员偏好问答",
    "migration": "家族迁移史与所在地沿革问答",
    "term": "家族内称呼习惯问答",
}

# 顺序即优先级：`MAX_CANDIDATES_PER_MESSAGE = 3` 意味着靠后的类别在一条消息同时命中
# 多个规则时会被丢弃。排序依据是「漏掉这条事实的代价」：饮食禁忌是安全类，
# 生日/纪念日有提醒价值，迁居与称呼是家族问答的高频事实，好恶排最后。
_CATEGORY_SUMMARIZERS = (
    ("dietary", _dietary_summary),
    ("birthday", _birthday_summary),
    ("anniversary", _anniversary_summary),
    ("migration", _migration_summary),
    ("term", _term_summary),
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
