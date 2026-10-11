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
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.services import platform_features
from app.utils.timeutil import utcnow

if TYPE_CHECKING:
    from app.models.agent import AgentRun
    from app.models.memory import MemoryCandidate
    from app.services.memory_rag import MemoryCandidateInput

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "memory-extractor-v2"
MAX_CANDIDATES_PER_MESSAGE = 3
# 与消息长度上限对齐；防御超长输入拖慢正则。
_INPUT_MAX_CHARS = config.AGENT_MESSAGE_MAX_LENGTH

# ---- 后台片段抽取（U4）----
#
# settle 路径只看**当前这一条**用户消息，因此一条事实若分散在多条消息里（先说
# 「我妈住在杭州」、下一轮才说「是西湖区」），单条匹配永远拼不出它。这不是正则
# 不够好，而是**观察窗口**太窄。
#
# 片段 pass 读一段会话而不是一条消息。它仍然只用确定性规则（不引入模型幻觉），
# 产物仍然是 **pending 候选**（确认门不变）。
SEGMENT_EXTRACTOR_VERSION = "memory-segment-extractor-v1"
#: 一次读多少条**用户**消息。有界：不读整段历史。
SEGMENT_WINDOW_MESSAGES = 20
#: 片段文本总长上限（拼接后）。
SEGMENT_MAX_CHARS = 12_000
#: 一个 tick 最多处理多少个会话。
SEGMENT_MAX_SESSIONS_PER_TICK = 20
#: 只处理最近活跃过的会话（分钟）。重复扫描同一窗口是幂等的（见去重），
#: 所以这个窗口只是工作量的上界，不是正确性依赖。
SEGMENT_ACTIVITY_MINUTES = 180

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


def extract_session_segments(db: Session, *, limit: int | None = None) -> dict[str, int]:
    """后台按**会话片段**抽取候选。返回计数，永不抛错。

    ## 与 settle 路径的关系

    settle 路径（`extract_after_settle`）在 run 终态时对**当轮那条用户消息**跑规则，
    它快、与终态同事务、失败不影响 run。片段 pass 是**补充**：它读一段会话，因此能
    看到需要跨消息上下文的事实。两者共存，不互相替代。

    ## 为什么不引入模型

    现有抽取器的价值在于「同输入逐字节同输出」——可审计、不幻觉。片段 pass 扩大的是
    **观察窗口**，不是判断能力；换成模型会让同一条事实的抽取结果不可复现，而 RAG
    语料的错误后果是持久的。模型驱动的抽取应由独立的验证机制配套（U5），不在本切片。

    ## 幂等

    去重不看「从哪条消息来的」，而看**事实本身**：同 `(author_account_id, summary)`
    的 pending/confirmed 候选或记忆已存在就跳过。因此：

    - 重复跑同一窗口不产生重复候选（第二次全是 no-op）；
    - settle 路径已提过的同一事实不会被片段 pass 再提一遍。

    ## 确认门不变

    产物是 `status="pending"` 的 `MemoryCandidate`，用户仍需在记忆面板确认。
    """
    from app.models.agent import AgentMessage

    counters = {"sessions_scanned": 0, "candidates_proposed": 0, "candidates_skipped": 0}
    try:
        if not platform_features.is_memory_enabled(db):
            return counters
        since = utcnow() - timedelta(minutes=SEGMENT_ACTIVITY_MINUTES)
        session_ids = list(
            db.scalars(
                select(AgentMessage.session_id)
                .where(AgentMessage.created_at >= since, AgentMessage.role == "user")
                .group_by(AgentMessage.session_id)
                .order_by(func.max(AgentMessage.id).desc())
                .limit(limit or SEGMENT_MAX_SESSIONS_PER_TICK)
            ).all()
        )
        for session_id in session_ids:
            counters["sessions_scanned"] += 1
            try:
                proposed, skipped = _extract_one_session(db, session_id)
            except Exception:  # noqa: BLE001 — 单个会话失败不影响其余
                logger.warning(
                    "segment extraction failed session=%s error swallowed",
                    session_id,
                    exc_info=True,
                )
                db.rollback()
                continue
            counters["candidates_proposed"] += proposed
            counters["candidates_skipped"] += skipped
        db.commit()
    except Exception:  # noqa: BLE001 — 后台 pass 不得影响 tick 其余部分
        logger.warning("segment extraction pass failed error swallowed", exc_info=True)
        db.rollback()
    return counters


def _extract_one_session(db: Session, session_id: int) -> tuple[int, int]:
    """处理一个会话，返回 `(新提候选数, 因已存在而跳过数)`。"""
    from app.models.agent import AgentMessage, AgentSession
    from app.models.memory import Memory, MemoryCandidate
    from app.services.memory_rag import MemoryCandidateExtractor

    session = db.get(AgentSession, session_id, populate_existing=True)
    if session is None or session.account_id is None:
        return 0, 0
    # 只取**用户**消息：assistant 的回复不是用户的事实来源（它可能是模型的猜测，
    # 也可能是它自己的复述）——把模型输出当事实来源等于让幻觉进入记忆候选。
    messages = list(
        db.scalars(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id, AgentMessage.role == "user")
            .order_by(AgentMessage.id.desc())
            .limit(SEGMENT_WINDOW_MESSAGES)
        ).all()
    )
    if not messages:
        return 0, 0
    texts = [
        text
        for text in (m.content_json.get("text") for m in reversed(messages))
        if isinstance(text, str) and text.strip()
    ]
    if not texts:
        return 0, 0
    segment = "\n".join(texts)[:SEGMENT_MAX_CHARS]
    inputs, _dropped = rule_detector_with_stats(segment)
    if not inputs:
        return 0, 0

    # ---- 把每条 input 归回**一条真实消息**，并把原文换成该消息全文 ----
    #
    # 片段用来**扩大观察窗口**，不用来当来源。`propose_candidate` 对
    # `agent_message` 来源做**全等校验**（`source_quote` 必须等于该消息原文），所以：
    #
    # 1. 某条消息包含 quote → 归给它（单消息事实，与 settle 路径同形）；
    # 2. quote 跨多条消息（片段才拼得出的事实）→ 归给**被 quote 完整包含的最长那条
    #    消息**。证据确实来自它，只是判断时借了窗口里其它消息的上下文；
    # 3. 两者都不满足 → **丢弃**。无来源的记忆候选比没有候选更糟。
    ordered = [(m.id, m.content_json.get("text") or "") for m in reversed(messages)]
    by_message: dict[int, list[MemoryCandidateInput]] = {}
    unattributed = 0
    for item in inputs:
        quote = (item.source_quote or "").strip()
        if not quote:
            unattributed += 1
            continue
        containing = [(message_id, text) for message_id, text in ordered if quote in text.strip()]
        if containing:
            owner_id, owner_text = containing[0]
        else:
            covered = [
                (message_id, text)
                for message_id, text in ordered
                if text.strip() and text.strip() in quote
            ]
            if not covered:
                unattributed += 1
                continue
            owner_id, owner_text = max(covered, key=lambda pair: len(pair[1]))
        by_message.setdefault(owner_id, []).append(
            replace(item, source_quote=owner_text.strip(), source_message_id=owner_id)
        )
    if unattributed:
        logger.info(
            "segment extraction unattributed candidates session=%s count=%s",
            session_id,
            unattributed,
        )
    if not by_message:
        return 0, 0

    # 事实级去重：同账号下已经有同样 summary 的候选或记忆就跳过。
    summaries = [
        item.summary.strip()
        for group in by_message.values()
        for item in group
        if item.summary.strip()
    ]
    known: set[str] = set()
    if summaries:
        known.update(
            db.scalars(
                select(MemoryCandidate.summary).where(
                    MemoryCandidate.author_account_id == session.account_id,
                    MemoryCandidate.summary.in_(summaries),
                )
            ).all()
        )
        known.update(
            db.scalars(
                # `Memory` 的正文列叫 `content`（候选叫 `summary`）；两者是同一份文本
                # 在确认前后的不同列名。
                select(Memory.content).where(
                    Memory.author_account_id == session.account_id,
                    Memory.content.in_(summaries),
                )
            ).all()
        )

    proposed_total = 0
    skipped = 0
    for message_id, group in by_message.items():
        fresh = [item for item in group if item.summary.strip() not in known]
        skipped += len(group) - len(fresh)
        if not fresh:
            continue
        # 同一批次里也可能重复（同一事实在窗口内被说了两次）。
        known.update(item.summary.strip() for item in fresh)

        def _fixed_detector(
            _text: str, _group: list[MemoryCandidateInput] = fresh
        ) -> list[MemoryCandidateInput]:
            return _group

        with db.begin_nested():
            extractor = MemoryCandidateExtractor(detector=_fixed_detector)
            extractor.version = SEGMENT_EXTRACTOR_VERSION
            proposed = extractor.extract(
                db,
                author_account_id=session.account_id,
                conversation_text=segment,
                source_message_id=message_id,
            )
        proposed_total += len(proposed)
    return proposed_total, skipped


__all__ = [
    "EXTRACTOR_VERSION",
    "MAX_CANDIDATES_PER_MESSAGE",
    "SEGMENT_EXTRACTOR_VERSION",
    "SEGMENT_MAX_SESSIONS_PER_TICK",
    "SEGMENT_WINDOW_MESSAGES",
    "extract_after_settle",
    "extract_session_segments",
    "rule_detector",
    "rule_detector_with_stats",
]
