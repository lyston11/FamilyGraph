"""TermRegistry 四级称谓解析与学习（V2.3 Block E3，KI-4/KI-5）。

四级解析优先级：personal（账号偏好）> space（空间词典，active 别名）>
locale（按空间配置的语言包，默认 zh-CN）> system（标准称谓兜底）。
无任何词条命中时回退结构描述——由组合层（compose_resolution_view）用
E2 resolver 的确定性描述函数完成，本服务返回 source_level=None 表示未命中。

写入治理合同：
- set_personal_term 只创建/更新 personal 词条并写 term.personal_updated
  领域事件（AC-KI6）；不触碰 SourceFact 与 raw_relation_inputs 原文。
- 展示侧实时 resolve_term：称谓变更立即生效且无需失效 DerivedFact 缓存
  （缓存行只承载结构真值 path/concept；derived_facts.term_version 保持
  NULL，不落称谓快照——落快照反而需要随每次个人修改失效全空间缓存）。
- 两人晋升规则：同一 space 候选词收集到 ≥2 个不同 identity_confirmed 且
  为该空间 active 成员的账号 usage 时自动晋升 level=space status=active；
  任一支撑资格丧失后重算不足则降级 superseded。管理员无审批路径，
  系统也不复制到 locale/system。由纯函数 recompute_space_promotion 实现，
  每次 usage 变更后调用。

由调用方事务统一提交（与 source_facts/derived_facts 同一纪律）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import (
    CONCEPT_CODE_INVALID,
    TERM_INVALID,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models.relationship_facts import SourceFact
from app.models.term_registry import (
    BUILTIN_TERM_SEEDS,
    USAGE_SOURCE_EVENTS,
    TermEntry,
    TermUsage,
)
from app.models.user import User
from app.services.derived_facts import (
    DerivedFactResult,
    describe_result,
    get_or_compute,
    steps_from_json,
)
from app.services.domain_events import emit as emit_domain_event
from app.services.relationship_graph import load_birth_years
from app.services.relationship_resolver import concept_code_for_path, describe_path
from app.services.space_fsm import is_active_member
from app.utils.timeutil import utcnow

# ---- 合同常量 ----
TERM_LEVEL_PERSONAL = "personal"
TERM_LEVEL_SPACE = "space"
TERM_LEVEL_LOCALE = "locale"
TERM_LEVEL_SYSTEM = "system"

EVENT_PERSONAL_UPDATED = "term.personal_updated"
EVENT_SPACE_PROMOTED = "term.space_promoted"
EVENT_SPACE_DEMOTED = "term.space_demoted"
AGGREGATE_TYPE = "term_entry"

# 结构回退在输出中的来源标记（非存储层级）
SOURCE_LEVEL_STRUCTURAL = "structural"

# 空间 locale 配置扩展点：v2 空间模型尚无 locale 列，恒用默认包
DEFAULT_SPACE_LOCALE = "zh-CN"

# 晋升门槛：≥2 个不同合格账号的使用证据（KI-4）
PROMOTION_MIN_ACCOUNTS = 2

_CONCEPT_CODE_RE = re.compile(r"^SELF$|^[A-Z][sga]?[mf]?(?:-[A-Z][sga]?[mf]?)*$")
_TERM_MAX_LENGTH = 64


@dataclass(frozen=True)
class TermResolution:
    """resolve_term 结果。source_level=None 表示四级均未命中（结构回退）。"""

    term: str | None
    source_level: str | None
    entry_id: int | None


# ---- 输入校验 ----


def validate_concept_code(concept_code: str) -> str:
    """校验 E2 concept_code 编码格式（编码合同见 relationship_resolver docstring）。"""
    code = concept_code.strip()
    if not code or len(code) > 128:
        raise_api_error(
            422,
            CONCEPT_CODE_INVALID,
            "概念码格式非法",
            detail={"concept_code": code, "max_length": 128},
        )
    if not _CONCEPT_CODE_RE.match(code):
        raise_api_error(422, CONCEPT_CODE_INVALID, "概念码格式非法", detail={"concept_code": code})
    return code


def validate_term_text(term: str) -> str:
    """称谓文本校验：去首尾空白后 1..64 字。"""
    cleaned = term.strip()
    if not cleaned:
        raise_api_error(422, TERM_INVALID, "称谓不能为空")
    if len(cleaned) > _TERM_MAX_LENGTH:
        raise_api_error(422, TERM_INVALID, "称谓不能超过 64 字", detail={"max_length": 64})
    return cleaned


def space_locale(session: Session, space_id: int) -> str:
    """空间语言包选择扩展点；当前恒返回默认包 zh-CN。"""
    _ = session, space_id
    return DEFAULT_SPACE_LOCALE


def seed_builtin_packs(session: Session) -> int:
    """幂等重灌内置 system/locale 种子（迁移已含同份清单）。

    生产环境由迁移 0012 写入；测试的清表夹具会连带清掉种子行，用本函数
    在测试内恢复。只补缺失行，不碰用户/空间层词条。返回新增行数。
    """
    existing = {
        (row.level, row.locale, row.concept_code, row.term)
        for row in session.scalars(
            select(TermEntry).where(TermEntry.level.in_((TERM_LEVEL_SYSTEM, TERM_LEVEL_LOCALE)))
        )
    }
    now = utcnow()
    added = 0
    for level, locale, code, term in BUILTIN_TERM_SEEDS:
        if (level, locale, code, term) in existing:
            continue
        session.add(
            TermEntry(
                concept_code=code,
                level=level,
                space_id=None,
                owner_account_id=None,
                locale=locale,
                term=term,
                status="active",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        added += 1
    if added:
        session.flush()
    return added


# ---- 解析 ----


def resolve_term(
    session: Session,
    *,
    account_id: int,
    space_id: int,
    concept_code: str,
) -> TermResolution:
    """四级优先级解析（personal > space > locale > system）。

    全部未命中返回 source_level=None，由组合层回退结构描述；
    绝不静默使用其他概念的词条。
    """
    code = validate_concept_code(concept_code)

    personal = session.scalar(
        select(TermEntry).where(
            TermEntry.level == TERM_LEVEL_PERSONAL,
            TermEntry.owner_account_id == account_id,
            TermEntry.concept_code == code,
            TermEntry.status == "active",
        )
    )
    if personal is not None:
        return TermResolution(personal.term, TERM_LEVEL_PERSONAL, personal.id)

    space_entry = session.scalar(
        select(TermEntry).where(
            TermEntry.level == TERM_LEVEL_SPACE,
            TermEntry.space_id == space_id,
            TermEntry.concept_code == code,
            TermEntry.status == "active",
        )
    )
    if space_entry is not None:
        return TermResolution(space_entry.term, TERM_LEVEL_SPACE, space_entry.id)

    locale_entry = session.scalar(
        select(TermEntry).where(
            TermEntry.level == TERM_LEVEL_LOCALE,
            TermEntry.locale == space_locale(session, space_id),
            TermEntry.concept_code == code,
            TermEntry.status == "active",
        )
    )
    if locale_entry is not None:
        return TermResolution(locale_entry.term, TERM_LEVEL_LOCALE, locale_entry.id)

    system_entry = session.scalar(
        select(TermEntry).where(
            TermEntry.level == TERM_LEVEL_SYSTEM,
            TermEntry.concept_code == code,
            TermEntry.status == "active",
        )
    )
    if system_entry is not None:
        return TermResolution(system_entry.term, TERM_LEVEL_SYSTEM, system_entry.id)

    return TermResolution(None, None, None)


def resolve_term_alias(
    session: Session, *, account_id: int, space_id: int, term: str
) -> str | None:
    """Reverse lookup a user/space/locale/system alias to a concept code.

    Alias lookup is intentionally deterministic: the same precedence used for
    display is applied in reverse, and ties are ordered by concept/id.  Raw
    relation input remains append-only; this function only reads the registry.
    """
    cleaned = term.strip()
    if not cleaned:
        return None
    locale = space_locale(session, space_id)
    rows = list(
        session.scalars(
            select(TermEntry).where(
                TermEntry.term == cleaned,
                TermEntry.status == "active",
                (
                    (
                        (TermEntry.level == TERM_LEVEL_PERSONAL)
                        & (TermEntry.owner_account_id == account_id)
                    )
                    | ((TermEntry.level == TERM_LEVEL_SPACE) & (TermEntry.space_id == space_id))
                    | ((TermEntry.level == TERM_LEVEL_LOCALE) & (TermEntry.locale == locale))
                    | (TermEntry.level == TERM_LEVEL_SYSTEM)
                ),
            )
        )
    )
    rank = {
        TERM_LEVEL_PERSONAL: 0,
        TERM_LEVEL_SPACE: 1,
        TERM_LEVEL_LOCALE: 2,
        TERM_LEVEL_SYSTEM: 3,
    }
    rows.sort(key=lambda row: (rank[row.level], row.concept_code, row.id))
    return rows[0].concept_code if rows else None


# ---- 个人称谓纠正（AC-KI6：DomainEvent + 立即生效，不需要记忆卡）----


def set_personal_term(
    session: Session,
    *,
    account_id: int,
    space_id: int,
    concept_code: str,
    term: str,
) -> TermEntry:
    """创建/更新个人显示称谓：旧值置 superseded 保留 revision 链。

    - 幂等：与当前 active 词条同文本时原样返回（不重复发事件）；
    - 改回历史用词时复用既有 superseded 行（避免重复历史行），
      active 行 revision 单调递增（链延续语义）；
    - 只写 term_entries 与一条 term.personal_updated 领域事件；
      不产生 SourceFact 变更、不改写原文。
    """
    code = validate_concept_code(concept_code)
    cleaned = validate_term_text(term)
    now = utcnow()

    current = session.scalar(
        select(TermEntry).where(
            TermEntry.level == TERM_LEVEL_PERSONAL,
            TermEntry.owner_account_id == account_id,
            TermEntry.concept_code == code,
            TermEntry.status == "active",
        )
    )
    if current is not None and current.term == cleaned:
        return current

    # 先降级旧 active 行并落库，避免 partial unique 在 INSERT 先于 UPDATE
    # 执行时误判冲突
    next_revision = 1
    if current is not None:
        current.status = "superseded"
        current.revision += 1
        current.updated_at = now
        next_revision = current.revision + 1
        session.flush()

    entry = session.scalar(
        select(TermEntry)
        .where(
            TermEntry.level == TERM_LEVEL_PERSONAL,
            TermEntry.owner_account_id == account_id,
            TermEntry.concept_code == code,
            TermEntry.term == cleaned,
            TermEntry.status == "superseded",
        )
        .order_by(TermEntry.updated_at.desc(), TermEntry.id.desc())
        .limit(1)
    )
    if entry is not None:
        entry.status = "active"
    else:
        entry = TermEntry(
            concept_code=code,
            level=TERM_LEVEL_PERSONAL,
            space_id=None,
            owner_account_id=account_id,
            locale=None,
            term=cleaned,
            status="active",
            revision=1,
            created_at=now,
            updated_at=now,
        )
        session.add(entry)
    entry.revision = next_revision
    entry.updated_at = now
    session.flush()

    emit_domain_event(
        session,
        event_type=EVENT_PERSONAL_UPDATED,
        aggregate_type=AGGREGATE_TYPE,
        aggregate_id=entry.id,
        payload={
            "account_id": account_id,
            "space_id": space_id,
            "concept_code": code,
            "entry_id": entry.id,
        },
        space_id=space_id,
        actor_account_id=account_id,
    )
    return entry


def list_personal_terms(
    session: Session,
    *,
    account_id: int,
    space_id: int | None = None,
) -> list[dict[str, Any]]:
    """本人 personal 词条列表；给定 space 时附带该空间语境的实时生效解析。"""
    rows = list(
        session.scalars(
            select(TermEntry)
            .where(
                TermEntry.level == TERM_LEVEL_PERSONAL,
                TermEntry.owner_account_id == account_id,
                TermEntry.status == "active",
            )
            .order_by(TermEntry.concept_code.asc())
        )
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {
            "entry_id": row.id,
            "concept_code": row.concept_code,
            "term": row.term,
            "revision": row.revision,
            "updated_at": row.updated_at,
        }
        if space_id is not None:
            resolved = resolve_term(
                session, account_id=account_id, space_id=space_id, concept_code=row.concept_code
            )
            item["resolved"] = {
                "term": resolved.term,
                "source_level": resolved.source_level,
                "entry_id": resolved.entry_id,
            }
        out.append(item)
    return out


# ---- 使用证据与两人晋升 ----


def ensure_space_candidate(
    session: Session,
    *,
    space_id: int,
    concept_code: str,
    term: str,
) -> TermEntry:
    """定位/创建某空间候选词的唯一载体行（level=space）。

    active 词条直接复用；否则复用既有 superseded 载体；都没有才新建
    superseded 载体行（晋升即原地激活，usage 证据始终挂同一行上计数）。
    """
    code = validate_concept_code(concept_code)
    cleaned = validate_term_text(term)
    entry = session.scalar(
        select(TermEntry).where(
            TermEntry.level == TERM_LEVEL_SPACE,
            TermEntry.space_id == space_id,
            TermEntry.concept_code == code,
            TermEntry.term == cleaned,
        )
    )
    if entry is not None:
        return entry
    now = utcnow()
    entry = TermEntry(
        concept_code=code,
        level=TERM_LEVEL_SPACE,
        space_id=space_id,
        owner_account_id=None,
        locale=None,
        term=cleaned,
        status="superseded",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(entry)
    session.flush()
    return entry


def record_usage(
    session: Session,
    *,
    entry_id: int,
    account_id: int,
    profile_id: int,
    space_id: int,
    source_event: str,
) -> TermUsage:
    """记录使用证据；同 (entry, account, space) 幂等去重（同账号只计一次）。"""
    if source_event not in USAGE_SOURCE_EVENTS:
        raise_api_error(422, VALIDATION_ERROR, f"未知使用来源 {source_event}")
    existing = session.scalar(
        select(TermUsage).where(
            TermUsage.term_entry_id == entry_id,
            TermUsage.account_id == account_id,
            TermUsage.space_id == space_id,
        )
    )
    if existing is not None:
        return existing
    usage = TermUsage(
        term_entry_id=entry_id,
        account_id=account_id,
        profile_id=profile_id,
        space_id=space_id,
        source_event=source_event,
        created_at=utcnow(),
    )
    session.add(usage)
    session.flush()
    return usage


def _eligible_supporter_ids(session: Session, *, space_id: int, entry_id: int) -> set[int]:
    """支撑账号集合：profile 已确档且仍为该空间 active 成员的 usage 提交者。"""
    usages = list(
        session.scalars(
            select(TermUsage).where(
                TermUsage.term_entry_id == entry_id, TermUsage.space_id == space_id
            )
        )
    )
    eligible: set[int] = set()
    for usage in usages:
        profile = session.get(User, usage.profile_id)
        if profile is None or profile.profile_status != "identity_confirmed":
            continue
        if not is_active_member(session, space_id, usage.profile_id):
            continue
        eligible.add(usage.account_id)
    return eligible


def recompute_space_promotion(
    session: Session,
    *,
    space_id: int,
    concept_code: str,
    term: str,
) -> dict[str, Any]:
    """两人晋升规则重算（usage 变更后调用；管理员无关、无发布动作）。

    - ≥2 个合格账号 → 该 (space_id, concept_code, term) 载体行原地激活
      （superseded → active，revision+1），写 term.space_promoted 事件，
      payload 记录支撑 usage/account ids 作为来源证据；
    - <2 个合格账号且载体为 active → 降级 superseded（revision+1），
      写 term.space_demoted 事件；
    - 载体不存在或状态已一致 → 无操作。
    """
    code = validate_concept_code(concept_code)
    cleaned = validate_term_text(term)
    holder = session.scalar(
        select(TermEntry).where(
            TermEntry.level == TERM_LEVEL_SPACE,
            TermEntry.space_id == space_id,
            TermEntry.concept_code == code,
            TermEntry.term == cleaned,
        )
    )
    if holder is None:
        return {"promoted": False, "demoted": False, "eligible_accounts": 0}

    eligible = _eligible_supporter_ids(session, space_id=space_id, entry_id=holder.id)
    usage_ids = list(
        session.scalars(
            select(TermUsage.id).where(
                TermUsage.term_entry_id == holder.id, TermUsage.space_id == space_id
            )
        )
    )
    now = utcnow()
    if len(eligible) >= PROMOTION_MIN_ACCOUNTS and holder.status != "active":
        holder.status = "active"
        holder.revision += 1
        holder.updated_at = now
        session.flush()
        emit_domain_event(
            session,
            event_type=EVENT_SPACE_PROMOTED,
            aggregate_type=AGGREGATE_TYPE,
            aggregate_id=holder.id,
            payload={
                "space_id": space_id,
                "concept_code": code,
                "term": cleaned,
                "supporter_account_ids": sorted(eligible),
                "usage_ids": usage_ids,
                "revision": holder.revision,
            },
            space_id=space_id,
            actor_account_id=None,
        )
    elif len(eligible) < PROMOTION_MIN_ACCOUNTS and holder.status == "active":
        holder.status = "superseded"
        holder.revision += 1
        holder.updated_at = now
        session.flush()
        emit_domain_event(
            session,
            event_type=EVENT_SPACE_DEMOTED,
            aggregate_type=AGGREGATE_TYPE,
            aggregate_id=holder.id,
            payload={
                "space_id": space_id,
                "concept_code": code,
                "term": cleaned,
                "supporter_account_ids": sorted(eligible),
                "revision": holder.revision,
            },
            space_id=space_id,
            actor_account_id=None,
        )
    return {
        "promoted": holder.status == "active" and len(eligible) >= PROMOTION_MIN_ACCOUNTS,
        "demoted": holder.status != "active",
        "eligible_accounts": len(eligible),
    }


def record_usage_and_promote(
    session: Session,
    *,
    space_id: int,
    concept_code: str,
    term: str,
    account_id: int,
    profile_id: int,
    source_event: str,
) -> tuple[TermUsage, bool, dict[str, Any]]:
    """API 组合入口：载体定位 → 幂等记 usage → 重算晋升。

    返回 (usage, created, promotion_summary)；由调用方事务统一提交。
    """
    holder = ensure_space_candidate(
        session, space_id=space_id, concept_code=concept_code, term=term
    )
    prior = session.scalar(
        select(TermUsage).where(
            TermUsage.term_entry_id == holder.id,
            TermUsage.account_id == account_id,
            TermUsage.space_id == space_id,
        )
    )
    usage = record_usage(
        session,
        entry_id=holder.id,
        account_id=account_id,
        profile_id=profile_id,
        space_id=space_id,
        source_event=source_event,
    )
    summary = recompute_space_promotion(
        session, space_id=space_id, concept_code=holder.concept_code, term=holder.term
    )
    created = prior is None
    if created:
        emit_domain_event(
            session,
            event_type="term.usage_recorded",
            aggregate_type="term_usage",
            aggregate_id=usage.id,
            payload={
                "concept_code": holder.concept_code,
                "term": holder.term,
                "source_event": source_event,
            },
            space_id=space_id,
            actor_account_id=account_id,
        )
    return usage, created, summary


def list_term_alternatives(
    session: Session,
    *,
    account_id: int,
    space_id: int,
    concept_code: str,
    limit: int = 5,
) -> dict[str, Any]:
    """某概念码的可用叫法清单（E4a Agent 工具 get_term_alternatives 的实现）。

    - personal 单列（当前账号的个人偏好，最高优先级）；
    - alternatives 按来源层级稳定排序：space（space_suggested=true，两人
      晋升产物）→ locale（按空间语言包）→ system，同层按 term 字典序；
    - 只返回 active 词条；limit 截断（1..10，调用方负责范围校验）。
    """
    code = validate_concept_code(concept_code)
    personal_row = session.scalar(
        select(TermEntry).where(
            TermEntry.level == TERM_LEVEL_PERSONAL,
            TermEntry.owner_account_id == account_id,
            TermEntry.concept_code == code,
            TermEntry.status == "active",
        )
    )
    rows = list(
        session.scalars(
            select(TermEntry).where(
                TermEntry.concept_code == code,
                TermEntry.status == "active",
                (
                    (TermEntry.level == TERM_LEVEL_SPACE) & (TermEntry.space_id == space_id)
                    | (TermEntry.level == TERM_LEVEL_LOCALE)
                    & (TermEntry.locale == space_locale(session, space_id))
                    | (TermEntry.level == TERM_LEVEL_SYSTEM)
                ),
            )
        )
    )
    level_rank = {TERM_LEVEL_SPACE: 0, TERM_LEVEL_LOCALE: 1, TERM_LEVEL_SYSTEM: 2}
    rows.sort(key=lambda row: (level_rank[row.level], row.term, row.id))
    return {
        "concept_code": code,
        "personal": (
            {"term": personal_row.term, "source": TERM_LEVEL_PERSONAL}
            if personal_row is not None
            else None
        ),
        "alternatives": [
            {
                "term": row.term,
                "source_level": row.level,
                "space_suggested": row.level == TERM_LEVEL_SPACE,
            }
            for row in rows[:limit]
        ],
    }


# ---- resolve 视图合成（GET /api/kinship/resolve 的服务层实现）----


def _genders_for_paths(session: Session, paths_json: list[list[dict[str, Any]]]) -> dict[int, str]:
    node_ids: set[int] = set()
    for path in paths_json:
        for step in path:
            node_ids.add(int(step["from"]))
            node_ids.add(int(step["to"]))
    genders: dict[int, str] = {}
    for uid in sorted(node_ids):
        user = session.get(User, uid)
        if user is not None:
            genders[uid] = user.gender
    return genders


def _pair_fact_state(
    session: Session, *, viewer_user_id: int, target_user_id: int, space_id: int
) -> dict[str, Any]:
    """两人之间的事实状态摘要（双向、含全局事实；供 UI 展示不确定性）。"""
    stmt = (
        select(SourceFact.state, func.count())
        .where(
            (
                (SourceFact.subject_user_id == viewer_user_id)
                & (SourceFact.object_user_id == target_user_id)
            )
            | (
                (SourceFact.subject_user_id == target_user_id)
                & (SourceFact.object_user_id == viewer_user_id)
            ),
            (SourceFact.space_id == space_id) | SourceFact.space_id.is_(None),
        )
        .group_by(SourceFact.state)
    )
    counts = {state: int(count) for state, count in session.execute(stmt).all()}
    return {
        "confirmed": counts.get("confirmed", 0),
        "proposed": counts.get("proposed", 0),
        "disputed": counts.get("disputed", 0),
        "revoked": counts.get("revoked", 0),
    }


# ---- 长幼消歧与长链泛化（09-13-steward-term-autofix；全程确定性，零模型调用）----

# 消歧只升级 locale/system 内置词；personal/space 是用户自定义叫法，永不覆盖
_VARIANT_ELIGIBLE_LEVELS = (TERM_LEVEL_LOCALE, TERM_LEVEL_SYSTEM)
# 泛化产物（最长命名前缀 + 残链）的来源标记（非存储层级）
SOURCE_LEVEL_DERIVED = "derived"
# 管家自动投影来源（09-13 terminology；非存储层级，同 derived 语义）
SOURCE_LEVEL_STEWARD = "steward"

# 长幼可比的出生数据：(cal_type, year)；混合日历不比较（保守回退泛化词）
BirthPair = tuple[str, int]


@dataclass(frozen=True)
class VariantContext:
    """长幼消歧上下文：viewer→target 主路径步 + 相关人员的出生数据。

    path 为 steps_to_json 形状（{"from","to","edge_type","subtype","direction",
    "fact_id"}）；births 为 uid → (cal_type, year) | None（birth 字段对 viewer
    脱敏或不可解析时为 None）。缺省 None 的调用方行为与升级前逐字节一致。
    """

    viewer_user_id: int
    path: list[dict[str, Any]]
    births: dict[int, BirthPair | None]


def _parse_token(token: str) -> tuple[str, str | None, str | None]:
    """概念码单步 token → (域字母, 亚型字母, 性别字母)。

    token 合同（relationship_resolver._step_token）：[A-Z][sga]?[mf]?。
    """
    domain = token[0]
    subtype: str | None = None
    gender: str | None = None
    rest = token[1:]
    if rest[:1] in ("s", "a", "g"):
        subtype = rest[0]
        rest = rest[1:]
    if rest[:1] in ("m", "f"):
        gender = rest[0]
    return domain, subtype, gender


# 残链小词表：泛化时把未命名 hop 翻译为「关系词」（「妹夫的父亲」的「父亲」）
_RESIDUAL_WORDS: dict[tuple[str, str | None, str | None], str] = {
    ("U", None, "m"): "父亲",
    ("U", None, "f"): "母亲",
    ("U", "s", "m"): "继父",
    ("U", "s", "f"): "继母",
    ("U", "a", "m"): "养父",
    ("U", "a", "f"): "养母",
    ("U", "g", None): "监护人",
    ("U", None, None): "家长",
    ("D", None, "m"): "儿子",
    ("D", None, "f"): "女儿",
    ("D", "s", "m"): "继子",
    ("D", "s", "f"): "继女",
    ("D", "a", "m"): "养子",
    ("D", "a", "f"): "养女",
    ("D", None, None): "子女",
    ("B", None, "m"): "兄弟",
    ("B", None, "f"): "姐妹",
    ("B", None, None): "兄弟姐妹",
    ("S", None, "m"): "丈夫",
    ("S", None, "f"): "妻子",
    ("S", None, None): "配偶",
    ("P", None, "m"): "伴侣",
    ("P", None, "f"): "伴侣",
    ("P", None, None): "伴侣",
    ("X", None, None): "跨空间亲人",
}

# 兄弟/姐妹类基码 → 长幼具体词（基码泛化词已入内置包，消歧命中即升级）
_SIBLING_AGE_TERMS: dict[str, dict[str, str]] = {
    "m": {"elder": "哥哥", "younger": "弟弟"},
    "f": {"elder": "姐姐", "younger": "妹妹"},
}
# 基码性别 × 配偶后缀 → 长幼具体词（嫂子/弟媳、姐夫/妹夫）
_SIBLING_SPOUSE_AGE_TERMS: dict[tuple[str, str], dict[str, str]] = {
    ("m", "Sf"): {"elder": "嫂子", "younger": "弟媳"},
    ("f", "Sm"): {"elder": "姐夫", "younger": "妹夫"},
}

_SUFFIX_GENDERS = ("Sm", "Sf")


def _sibling_base_hop(
    code_tokens: list[str],
) -> tuple[int, bool] | None:
    """识别长幼消歧类基跳：返回 (基跳下标, 是否带配偶后缀)；非消歧类返回 None。

    消歧类（与内置包泛化词条一一对应）：
    - 1 跳 B（直接同胞事实）：Bm/Bf；
    - 2 跳 U-D（经父母链的同胞）：Um-Dm/Um-Df/Uf-Dm/Uf-Df；
    - 2 跳 B-S（同胞的配偶）：Bm-Sf/Bf-Sm/...；
    - 3 跳 U-D-S（经父母链同胞的配偶）：Um-Df-Sm/...
    伯/叔、堂表长幼依赖父辈或旁支长幼链，v1 不消歧（保持泛化词）。
    """
    if any(_parse_token(token)[1] is not None for token in code_tokens):
        return None  # Do not erase adoptive/step/guardian distinctions with sibling age terms.
    n = len(code_tokens)
    if n == 1:
        domain, _, _ = _parse_token(code_tokens[0])
        return (0, False) if domain == "B" else None
    if n == 2:
        first_domain, _, _ = _parse_token(code_tokens[0])
        last_domain, _, _ = _parse_token(code_tokens[1])
        if first_domain == "U" and last_domain == "D":
            return (1, False)
        if first_domain == "B" and code_tokens[1] in _SUFFIX_GENDERS:
            return (0, True)
        return None
    if n == 3:
        d0, _, _ = _parse_token(code_tokens[0])
        d1, _, _ = _parse_token(code_tokens[1])
        d2, _, _ = _parse_token(code_tokens[2])
        if d0 == "U" and d1 == "U" and d2 == "D":
            return None  # 曾祖辈长幼链，不消歧
        if d0 == "U" and d1 == "D" and code_tokens[2] in _SUFFIX_GENDERS:
            return (1, True)
    return None


def _age_order(births: dict[int, BirthPair | None], a_id: int, b_id: int) -> str | None:
    """比较两人出生数据：返回 **b 相对 a** 的长幼——b 年长 → "elder"，b 年幼 →
    "younger"，不可比 → None。

    两人日历类型必须一致（混合 solar/lunar 比较可能差一岁，保守放弃）；
    同年（无论日历）不判长幼。
    """
    a = births.get(a_id)
    b = births.get(b_id)
    if a is None or b is None:
        return None
    if a[0] != b[0] or a[1] == b[1]:
        return None
    return "younger" if a[1] < b[1] else "elder"


def _sibling_variant_term(code: str, context: VariantContext) -> str | None:
    """长幼消歧：消歧类概念码 + 可比出生数据 → 具体长幼称谓；否则 None。

    纯函数：相同 (码, 路径, 出生数据) 恒产出相同结果。
    """
    code_tokens = code.split("-")
    base = _sibling_base_hop(code_tokens)
    if base is None:
        return None
    base_index, has_spouse_suffix = base
    if base_index >= len(context.path):
        return None
    step = context.path[base_index]
    sibling_id = step.get("to")
    reference_id = context.viewer_user_id if base_index > 0 else step.get("from")
    if sibling_id is None or reference_id is None:
        return None
    base_token = code_tokens[base_index]
    _, _, base_gender = _parse_token(base_token)
    suffix_token = code_tokens[base_index + 1] if has_spouse_suffix else None
    if base_gender is None:
        return None
    if has_spouse_suffix:
        assert suffix_token is not None  # _sibling_base_hop 合同保证
        table = _SIBLING_SPOUSE_AGE_TERMS.get((base_gender, suffix_token))
    else:
        table = _SIBLING_AGE_TERMS.get(base_gender)
    if table is None:
        return None
    order = _age_order(context.births, reference_id, int(sibling_id))
    if order is None:
        return None
    return table[order]


def _generalized_term(
    session: Session, *, account_id: int, space_id: int, concept_code: str
) -> str | None:
    """第 5 级泛化：最长已命名前缀 + 残链小词（「妹夫的父亲」）。

    - 前缀沿用四级解析优先级（用户 personal/space 词条若命中前缀，同样尊重）；
    - 残链逐跳经 _RESIDUAL_WORDS 翻译，以「的」连接；
    - 组合超长（>64 字）或残链存在不可命名 hop → None（维持结构描述）。
    """
    code_tokens = concept_code.split("-")
    for cut in range(len(code_tokens) - 1, 0, -1):
        prefix = "-".join(code_tokens[:cut])
        if prefix == "SELF":  # pragma: no cover - SELF 只可能是全码
            continue
        resolved = resolve_term(
            session, account_id=account_id, space_id=space_id, concept_code=prefix
        )
        if resolved.term is None:
            continue
        residual_words: list[str] = []
        for token in code_tokens[cut:]:
            word = _RESIDUAL_WORDS.get(_parse_token(token))
            if word is None:
                return None
            residual_words.append(word)
        term = "的".join([resolved.term, *residual_words])
        if len(term) > _TERM_MAX_LENGTH:
            return None
        return term
    return None


def residual_word_for(token: str) -> str | None:
    """概念码单步 token 的残链小词（公开封装；无词返回 None）。"""
    return _RESIDUAL_WORDS.get(_parse_token(token))


def sibling_base_hop(code_tokens: list[str]) -> tuple[int, bool] | None:
    """长幼消歧类基跳识别（公开封装；非消歧类返回 None）。"""
    return _sibling_base_hop(code_tokens)


def term_registry_hash(
    session: Session,
    *,
    space_id: int,
    account_id: int | None = None,
    concept_codes: set[str] | None = None,
) -> str:
    """适用词典版本指纹：system/locale/space（本空间）+ 可选本人 personal 词条。"""
    import hashlib

    from sqlalchemy import or_

    scopes = [
        TermEntry.level == TERM_LEVEL_SYSTEM,
        (TermEntry.level == TERM_LEVEL_LOCALE)
        & (TermEntry.locale == space_locale(session, space_id)),
        (TermEntry.level == TERM_LEVEL_SPACE) & (TermEntry.space_id == space_id),
    ]
    if account_id is not None:
        scopes.append(
            (TermEntry.level == TERM_LEVEL_PERSONAL) & (TermEntry.owner_account_id == account_id)
        )
    stmt = select(TermEntry.id, TermEntry.revision, TermEntry.concept_code, TermEntry.term).where(
        TermEntry.status == "active", or_(*scopes)
    )
    if concept_codes is not None:
        stmt = stmt.where(TermEntry.concept_code.in_(concept_codes))
    parts = [tuple(row) for row in session.execute(stmt.order_by(TermEntry.id)).all()]
    canonical = repr(parts).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def resolve_term_or_structural(
    session: Session,
    *,
    account_id: int,
    space_id: int,
    concept_code: str | None,
    structural_description: str,
    variant_context: VariantContext | None = None,
) -> dict[str, Any]:
    """词条解析 + 长幼消歧 + 长链泛化 + 结构回退。

    解析顺序（09-13-steward-term-autofix）：
    1. 四级词典（personal > space > locale > system）；命中且属于长幼消歧类、
       且 variant_context 提供了可比出生数据 → 升级为具体长幼称谓
       （哥哥/弟弟/姐姐/妹妹/嫂子/弟媳/姐夫/妹夫）。personal/space 层用户的
       自定义词条永不升级（用户叫法优先）。
    2. 词典未命中 → 第 5 级泛化：最长已命名前缀 + 残链小词（「妹夫的父亲」）。
    3. 仍无法命名 → 结构描述（SOURCE_LEVEL_STRUCTURAL，与既有行为一致）。
    """
    if concept_code is None:
        return {
            "term": structural_description,
            "source_level": SOURCE_LEVEL_STRUCTURAL,
            "entry_id": None,
        }
    resolved = resolve_term(
        session, account_id=account_id, space_id=space_id, concept_code=concept_code
    )
    if resolved.source_level is not None and resolved.term is not None:
        variant = (
            _sibling_variant_term(concept_code, variant_context)
            if variant_context is not None
            else None
        )
        if variant is not None and resolved.source_level in _VARIANT_ELIGIBLE_LEVELS:
            return {
                "term": variant,
                "source_level": resolved.source_level,
                "entry_id": resolved.entry_id,
            }
        return {
            "term": resolved.term,
            "source_level": resolved.source_level,
            "entry_id": resolved.entry_id,
        }
    generalized = _generalized_term(
        session, account_id=account_id, space_id=space_id, concept_code=concept_code
    )
    if generalized is not None:
        return {
            "term": generalized,
            "source_level": SOURCE_LEVEL_DERIVED,
            "entry_id": None,
        }
    return {
        "term": structural_description,
        "source_level": SOURCE_LEVEL_STRUCTURAL,
        "entry_id": None,
    }


def compose_resolution_view(
    session: Session,
    *,
    viewer_user_id: int,
    target_user_id: int,
    space_id: int,
    account_id: int,
) -> dict[str, Any]:
    """合成 resolve 输出：DerivedFact 缓存读取 + 四级称谓实时解析。

    缓存只承载结构真值（path/concept/evidence_hash）；称谓在展示时实时
    resolve——个人纠正即时生效，无需失效缓存（AC-KI6）。
    """
    result: DerivedFactResult = get_or_compute(
        session,
        viewer_user_id=viewer_user_id,
        target_user_id=target_user_id,
        space_id=space_id,
    )

    if not result.found:
        # 不泄露存在性：不可见/不存在/超深一律同一形状——fact_state 也必须
        # 归零，否则按 id 探测即可得知与不可见人物之间存在事实及其状态。
        return {
            "found": False,
            "viewer_user_id": viewer_user_id,
            "target_user_id": target_user_id,
            "space_id": space_id,
            "path_class": result.path_class,
            "concept_code": None,
            "explanation_structural": None,
            "term": None,
            "term_source_level": None,
            "term_entry_id": None,
            "main_path": [],
            "alt_paths": [],
            "fact_state": {
                "confirmed": 0,
                "proposed": 0,
                "disputed": 0,
                "revoked": 0,
                "evidence_fact_ids": [],
            },
            "cache_hit": result.cache_hit,
            "algorithm_version": result.algorithm_version,
        }

    fact_state = _pair_fact_state(
        session, viewer_user_id=viewer_user_id, target_user_id=target_user_id, space_id=space_id
    )
    main_description, alt_descriptions = describe_result(session, result)

    all_paths_json = [result.main_path_json, *result.alt_paths_json]
    genders = _genders_for_paths(session, all_paths_json)
    # 长幼消歧上下文：路径涉及人员的出生数据（脱敏/不可解析 → None）。
    person_ids: set[int] = {viewer_user_id, target_user_id}
    for path_json in all_paths_json:
        for step_json in path_json:
            person_ids.add(int(step_json["from"]))
            person_ids.add(int(step_json["to"]))
    births = load_birth_years(
        session, viewer_user_id=viewer_user_id, space_id=space_id, user_ids=person_ids
    )

    main_view = resolve_term_or_structural(
        session,
        account_id=account_id,
        space_id=space_id,
        concept_code=result.concept_code,
        structural_description=main_description,
        variant_context=VariantContext(
            viewer_user_id=viewer_user_id,
            path=result.main_path_json,
            births=births,
        ),
    )

    # 09-13 terminology：有效自动词覆盖（个人/空间显式词条无条件优先，
    # 选择器只对 locale/system/derived/structural baseline 生效）。
    def _apply_override(
        view: dict[str, Any],
        path_json: list[dict[str, Any]],
        code: str | None,
    ) -> dict[str, Any]:
        from app.services import steward_terminology

        override = steward_terminology.effective_override(
            session,
            account_id=account_id,
            root_user_id=viewer_user_id,
            space_id=space_id,
            target_user_id=target_user_id,
            concept_code=code,
            baseline_term=view["term"],
            baseline_source=view["source_level"],
            path=path_json,
        )
        if override is not None and override != view["term"]:
            return {**view, "term": override, "source_level": SOURCE_LEVEL_STEWARD}
        return view

    main_view = _apply_override(main_view, result.main_path_json, result.concept_code)
    alt_views: list[dict[str, Any]] = []
    for index, path_json in enumerate(result.alt_paths_json):
        steps = steps_from_json(path_json)
        alt_code = concept_code_for_path(steps, genders)
        alt_description = (
            alt_descriptions[index]
            if index < len(alt_descriptions)
            else describe_path(steps, genders)
        )
        alt_term_view = resolve_term_or_structural(
            session,
            account_id=account_id,
            space_id=space_id,
            concept_code=alt_code,
            structural_description=alt_description,
            variant_context=VariantContext(
                viewer_user_id=viewer_user_id,
                path=path_json,
                births=births,
            ),
        )
        alt_term_view = _apply_override(alt_term_view, path_json, alt_code)
        alt_views.append(
            {
                "path": path_json,
                "description": alt_description,
                "concept_code": alt_code,
                "term": alt_term_view["term"],
                "term_source_level": alt_term_view["source_level"],
                "term_entry_id": alt_term_view["entry_id"],
            }
        )

    return {
        "found": True,
        "viewer_user_id": viewer_user_id,
        "target_user_id": target_user_id,
        "space_id": space_id,
        "path_class": result.path_class,
        "concept_code": result.concept_code,
        "explanation_structural": main_description,
        "term": main_view["term"],
        "term_source_level": main_view["source_level"],
        "term_entry_id": main_view["entry_id"],
        "main_path": result.main_path_json,
        "alt_paths": alt_views,
        "fact_state": {
            **fact_state,
            "evidence_fact_ids": result.evidence_fact_ids,
        },
        "cache_hit": result.cache_hit,
        "algorithm_version": result.algorithm_version,
    }
