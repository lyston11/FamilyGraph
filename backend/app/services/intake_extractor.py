"""ProfileIntakeExtractor：确定性自由文本关系解析（V2.3 Block E4a，KI-3）。

纯词素解析器，无 LLM 参与（红线）：相同 (原文, 图快照) 产出逐字节相同结果
（AC-KI7 的解析侧延伸）。职责边界：

- 只把自由文本翻译为 resolver 概念码候选并用当前空间确认事实图验证；
- **永不写 SourceFact**：supported/conflicting 只产出提案文本（proposal），
  写入必须经用户在后续流程显式确认；
- 每次解析先把原文 append-only 写入 raw_relation_inputs（数据库触发器保证
  不可覆盖），原文与解析产物互不污染（AC-KI3）。

## resolution class 判定（KI-3 / AC-KI4）

1. conflicting：候选与已确认事实矛盾——候选为生理学唯一角色（Um/Uf）且当前
   空间已确认 ≥2 位同角色人物；输出冲突列表 + 原子事实提案（标注冲突）。
2. determined：候选概念码被确认路径完全证明（存在可见人物的主路径编码与候
   选匹配）；附 compose_resolution_view 同构称谓（四级实时解析 + 结构回退）。
3. ambiguous：无法唯一确定——模板化追问恰好一个问题。触发情形：
   a) 存在未识别词素段（含整体不可解析、超过 4 层链）；
   b) 候选码的中间环节缺少记录（如「叔叔」需要父亲记录才能定位兄弟分支，
      而图中尚无 viewer 的父亲——追问而不是替用户虚构中间亲属）。
4. supported：码合法、全部中间环节均有记录、仅最后一跳无对应人物——生成
   一句话确认提案；绝不写 SourceFact。

词素表是单义映射（一词一码）；口语多义词（如「阿姨」）不收表，由 ambiguous
追问消解。「伯父/叔叔」同映射 Um-Bm：概念词汇表不区分兄弟长幼。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.errors import VALIDATION_ERROR, raise_api_error
from app.services import source_facts as sf
from app.services import terms
from app.services.relationship_graph import load_graph
from app.services.relationship_resolver import (
    PathStep,
    bulk_concept_codes,
    describe_path,
    resolve_relationship,
)

# ---- 合同常量 ----

SURFACE_BROWSER = "browser_parse"
SURFACE_ASSISTANT = "assistant_tool"

RESOLUTION_DETERMINED = "determined"
RESOLUTION_SUPPORTED = "supported"
RESOLUTION_AMBIGUOUS = "ambiguous"
RESOLUTION_CONFLICTING = "conflicting"

# 链式深度上限（合同值）：「的」分段 ≤4 层（如 奶奶的兄弟的儿子）
MAX_CHAIN_SEGMENTS = 4

_TEXT_MAX_LENGTH = 80

# 生理学唯一角色：出现 ≥2 位被确认的同角色人物即构成事实模型冲突
_UNIQUE_CONCEPT_CODES = {"Um": "爸爸", "Uf": "妈妈"}

# ---- 词素表（单义映射；token 编码合同见 relationship_resolver docstring）----

_MORPHEME_CODES: dict[str, str] = {
    # 父母
    "爸爸": "Um",
    "老爸": "Um",
    "父亲": "Um",
    "爹": "Um",
    "妈妈": "Uf",
    "老妈": "Uf",
    "母亲": "Uf",
    "娘": "Uf",
    # 继亲 / 收养 / 监护
    "继父": "Usm",
    "继母": "Usf",
    "养父": "Uam",
    "养母": "Uaf",
    "监护人": "Ug",
    # 祖辈
    "爷爷": "Um-Um",
    "奶奶": "Um-Uf",
    "外公": "Uf-Um",
    "姥爷": "Uf-Um",
    "外婆": "Uf-Uf",
    "姥姥": "Uf-Uf",
    # 父母旁系
    "叔叔": "Um-Bm",
    "伯父": "Um-Bm",
    "伯伯": "Um-Bm",
    "姑妈": "Um-Bf",
    "姑姑": "Um-Bf",
    "舅舅": "Uf-Bm",
    "姨妈": "Uf-Bf",
    # 同辈
    "哥哥": "Bm",
    "弟弟": "Bm",
    "兄弟": "Bm",
    "姐姐": "Bf",
    "妹妹": "Bf",
    "姐妹": "Bf",
    "兄弟姐妹": "B",
    # 晚辈
    "儿子": "Dm",
    "女儿": "Df",
    "孩子": "D",
    # 配偶与伴侣
    "丈夫": "Sm",
    "老公": "Sm",
    "妻子": "Sf",
    "老婆": "Sf",
    "太太": "Sf",
    "爱人": "S",
    "伴侣": "P",
}

_CHAIN_SEPARATOR = "的"
_LEADING_FILLER = ("我的",)
_TRAILING_PUNCT = "。.!！？?～~，,；;：:"
_WHITESPACE_RE = re.compile(r"\s+")

_EDGE_BY_LETTER = {"U": "parent", "D": "parent", "S": "spouse", "P": "partner", "B": "sibling"}
_DIRECTION_BY_LETTER = {"U": "up", "D": "down"}
_SUBTYPE_BY_LETTER = {"a": "adoptive", "s": "step", "g": "guardian"}

# 最后一步 token → 提案原子事实类型（SOURCE_FACT_TYPES 词汇）
_FACT_TYPE_BY_UP_SUBTYPE = {
    "": "biological_parent",
    "a": "adoptive_parent",
    "s": "step_parent",
    "g": "guardian",
}

# 追问文案里缺失环节的角色词（按缺失步 token 边字母）
_MISSING_ROLE_BY_TOKEN = {
    "U": "父亲或母亲",
    "D": "子女",
    "B": "兄弟或姐妹",
    "S": "配偶",
    "P": "伴侣",
}


@dataclass(frozen=True)
class _ChainParse:
    """归一化文本的分段结果。"""

    segments: tuple[str, ...]
    codes: tuple[str, ...]  # 与 segments 一一对应；未识别段的码不存在（列表截断）
    morphemes: tuple[str, ...]
    unknown_segment: str | None


# ---- 文本归一化与分链（纯函数，确定性）----


def normalize_text(text: str) -> str:
    """确定性归一：去全部空白 → 去尾部标点 → 去「我的」前缀。"""
    cleaned = _WHITESPACE_RE.sub("", text.strip()).rstrip(_TRAILING_PUNCT)
    for filler in _LEADING_FILLER:
        if cleaned.startswith(filler):
            cleaned = cleaned[len(filler) :]
    return cleaned


def split_chain(normalized: str) -> _ChainParse:
    """按「的」拆链并逐段查词素表；任一段未识别则整链视为未解析。"""
    segments = tuple(segment for segment in normalized.split(_CHAIN_SEPARATOR) if segment)
    codes: list[str] = []
    morphemes: list[str] = []
    unknown: str | None = None
    for segment in segments:
        code = _MORPHEME_CODES.get(segment)
        if code is None:
            unknown = segment
            break
        codes.append(code)
        morphemes.append(segment)
    return _ChainParse(
        segments=segments,
        codes=tuple(codes),
        morphemes=tuple(morphemes),
        unknown_segment=unknown,
    )


def _validate_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise_api_error(422, VALIDATION_ERROR, "关系描述不能为空")
    if len(stripped) > _TEXT_MAX_LENGTH:
        raise_api_error(422, VALIDATION_ERROR, f"关系描述不能超过 {_TEXT_MAX_LENGTH} 字")
    return stripped


# ---- 概念码工具（描述 / 匹配 / 事实类型映射，均复用 resolver 语义）----


def _split_token(token: str) -> tuple[str, str]:
    """token → (边字母+亚型字母, 性别字母)。性别缺省表示未知/通配。"""
    gender = token[-1] if token[-1] in ("m", "f") else ""
    core = token[:-1] if gender else token
    return core, gender


def describe_code(code: str) -> str:
    """概念码 → 确定性中文层级描述（合成 PathStep 复用 describe_path 单一真相）。"""
    steps: list[PathStep] = []
    genders: dict[int, str] = {}
    for index, token in enumerate(code.split("-")):
        core, gender = _split_token(token)
        edge = core[0]
        subtype = core[1:] or None
        steps.append(
            PathStep(
                from_id=-index,
                to_id=-index - 1,
                edge_type=_EDGE_BY_LETTER[edge],
                subtype=subtype if edge in ("U", "D") else None,
                direction=_DIRECTION_BY_LETTER.get(edge, "sym"),
                fact_id=0,
            )
        )
        if gender:
            genders[-index - 1] = gender
    return describe_path(tuple(steps), genders)


def code_matches(candidate: str, actual: str | None) -> bool:
    """候选码与实际码匹配：逐 token 比边+亚型；候选性别缺省时通配实际性别。

    例：候选 ``P``（伴侣，未指性别）匹配实际 ``Pm``/``Pf``/``P``；
    候选 ``Bm``（哥哥/弟弟）只匹配 ``Bm``，不匹配 ``Bf``。
    """
    if actual is None:
        return False
    cand_tokens = candidate.split("-")
    act_tokens = actual.split("-")
    if len(cand_tokens) != len(act_tokens):
        return False
    for cand_token, act_token in zip(cand_tokens, act_tokens, strict=True):
        cand_core, cand_gender = _split_token(cand_token)
        act_core, act_gender = _split_token(act_token)
        if cand_core != act_core:
            return False
        if cand_gender and cand_gender != act_gender:
            return False
    return True


def _fact_type_for_code(code: str) -> str:
    """候选码最后一步 → 提案原子事实类型（SOURCE_FACT_TYPES 词汇）。"""
    core, _ = _split_token(code.split("-")[-1])
    edge, subtype_letter = core[0], core[1:]
    if edge == "U":
        return _FACT_TYPE_BY_UP_SUBTYPE[subtype_letter]
    if edge == "D":
        return "biological_parent"
    if edge == "B":
        return "direct_sibling"
    if edge == "S":
        return "spouse"
    return "partner"


# ---- 图验证 ----


def _matched_people(
    session: Session, *, user_id: int, space_id: int, candidate: str
) -> tuple[list[int], dict[int, str | None]]:
    """返回 (与候选码匹配的可见人 id 升序列, 全部可见人的实际码表)。"""
    graph = load_graph(session, viewer_user_id=user_id, space_id=space_id)
    actuals = bulk_concept_codes(
        graph,
        viewer_user_id=user_id,
        target_ids=[uid for uid in graph.node_genders if uid != user_id],
    )
    matched = [uid for uid in sorted(actuals) if code_matches(candidate, actuals[uid])]
    return matched, actuals


def _uniqueness_conflicts(
    candidate: str, matched_ids: list[int], actuals: dict[int, str | None]
) -> list[str]:
    """生理学唯一角色冲突检测：≥2 位已确认同角色人物即矛盾（AC-KI4）。"""
    role = _UNIQUE_CONCEPT_CODES.get(candidate)
    if role is None:
        return []
    count = sum(1 for uid in matched_ids if actuals.get(uid) == candidate)
    if count >= 2:
        return [
            f"当前空间资料中已有 {count} 位被确认的『{role}』，而一位{role}只能有一位，"
            "请先核对既有档案。"
        ]
    return []


def _longest_proven_prefix(actuals: dict[int, str | None], tokens: list[str]) -> int:
    """从 SELF 出发连续可证明的最长前缀 token 数（0 = 连第一跳都无人可证）。"""
    longest = 0
    for length in range(1, len(tokens) + 1):
        prefix = "-".join(tokens[:length])
        if any(code_matches(prefix, actual) for actual in actuals.values()):
            longest = length
        else:
            break
    return longest


# ---- 结果构造 ----


def _candidate_view(
    session: Session,
    *,
    account_id: int,
    space_id: int,
    concept_code: str | None,
) -> dict[str, Any]:
    """compose_resolution_view 同构的候选称谓：四级实时解析 + 结构回退。"""
    if concept_code is None:
        return {"concept_code": None, "term": None, "term_source_level": None}
    view = terms.resolve_term_or_structural(
        session,
        account_id=account_id,
        space_id=space_id,
        concept_code=concept_code,
        structural_description=describe_code(concept_code),
    )
    return {
        "concept_code": concept_code,
        "term": view["term"],
        "term_source_level": view["source_level"],
    }


def _base_result(
    *,
    raw_text_id: int,
    normalized_text: str,
    evidence_morphemes: list[str],
) -> dict[str, Any]:
    return {
        "raw_text_id": raw_text_id,
        "normalized_text": normalized_text,
        "resolution_class": "",
        "candidate": {"concept_code": None, "term": None, "term_source_level": None},
        "graph_proof": {"found": False, "explanation_structural": None},
        "proposals": [],
        "conflicts": [],
        "clarifying_question": None,
        "evidence_morphemes": evidence_morphemes,
    }


def _proposal(fact_type: str, summary: str) -> dict[str, Any]:
    return {"kind": "source_fact", "fact_type": fact_type, "summary": summary}


def _atomic_proposal(*, normalized_text: str, code: str, conflicting: bool) -> dict[str, Any]:
    """supported/conflicting 共用的原子事实提案（只出文本，绝不写 SourceFact）。"""
    fact_type = _fact_type_for_code(code)
    suffix = "；但当前存在冲突，需先解决冲突" if conflicting else ""
    summary = (
        f"提案：把「{normalized_text}」（{describe_code(code)}）对应的原子事实"
        f"（{fact_type}）补充到当前空间{suffix}。在你明确确认之前不会写入任何家庭事实。"
    )
    return _proposal(fact_type, summary)


# ---- 入口 ----


def parse_free_text_relation(
    session: Session,
    *,
    account_id: int,
    user_id: int,
    space_id: int,
    text: str,
    surface: str,
) -> dict[str, Any]:
    """解析自由文本关系输入（浏览器 parse API 与 Agent 工具共用实现）。

    先把原文 append-only 落库（KI-3 红线），再执行确定性词素解析与图验证。
    由调用方事务统一提交。
    """
    stripped = _validate_text(text)
    raw = sf.create_raw_relation_input(
        session,
        author_account_id=account_id,
        text=stripped,
        context={"space_id": space_id, "surface": surface},
    )
    normalized = normalize_text(stripped)
    chain = split_chain(normalized)
    evidence = list(chain.morphemes)
    result = _base_result(
        raw_text_id=raw.id, normalized_text=normalized, evidence_morphemes=evidence
    )

    def finish(resolution_class: str) -> dict[str, Any]:
        result["resolution_class"] = resolution_class
        return result

    # 未识别：整体不可解析 / 含未知词素段 / 超过链深上限 → 恰好一个追问
    if chain.unknown_segment is not None:
        assert chain.unknown_segment  # 分段过滤空串后不会为空
        result["candidate"] = _candidate_view(
            session, account_id=account_id, space_id=space_id, concept_code=None
        )
        result["clarifying_question"] = (
            f"「{chain.unknown_segment}」还没有被识别为具体的亲属称呼。"
            "你能换成更常用的说法吗，比如『妈妈的哥哥』？"
        )
        return finish(RESOLUTION_AMBIGUOUS)
    if len(chain.segments) > MAX_CHAIN_SEGMENTS:
        result["clarifying_question"] = (
            f"关系说法最多支持 {MAX_CHAIN_SEGMENTS} 层（例如『奶奶的兄弟』）。"
            f"你能简化一下「{normalized}」的说法吗？"
        )
        return finish(RESOLUTION_AMBIGUOUS)
    if not chain.codes:
        result["clarifying_question"] = "这句话里没能识别出具体的亲属称呼，可以换一种说法吗？"
        return finish(RESOLUTION_AMBIGUOUS)

    code = "-".join(chain.codes)
    result["candidate"] = _candidate_view(
        session, account_id=account_id, space_id=space_id, concept_code=code
    )

    matched, actuals = _matched_people(session, user_id=user_id, space_id=space_id, candidate=code)

    # 1) 冲突优先：候选与已确认事实矛盾
    conflicts = _uniqueness_conflicts(code, matched, actuals)
    if conflicts:
        result["conflicts"] = conflicts
        result["proposals"] = [
            _atomic_proposal(normalized_text=normalized, code=code, conflicting=True)
        ]
        return finish(RESOLUTION_CONFLICTING)

    # 2) 完全证明：存在可见人物的主路径编码与候选匹配
    if matched:
        resolution = resolve_relationship(
            session, viewer_user_id=user_id, target_user_id=matched[0], space_id=space_id
        )
        result["graph_proof"] = {
            "found": True,
            "explanation_structural": resolution.explanation_structural,
        }
        return finish(RESOLUTION_DETERMINED)

    # 3) 中间环节缺失 → 追问；仅最后一跳缺失 → supported 提案
    tokens = code.split("-")
    longest = _longest_proven_prefix(actuals, tokens)
    missing_role = _MISSING_ROLE_BY_TOKEN[tokens[longest][0]]
    full_desc = describe_code(code)
    if longest < len(tokens) - 1:
        result["clarifying_question"] = (
            f"目前资料里还没有关于你的{missing_role}的记录，「{normalized}」通常指{full_desc}。"
            f"你说的「{normalized}」是指{full_desc}吗？"
        )
        return finish(RESOLUTION_AMBIGUOUS)
    result["proposals"] = [
        _atomic_proposal(normalized_text=normalized, code=code, conflicting=False)
    ]
    return finish(RESOLUTION_SUPPORTED)


__all__ = [
    "MAX_CHAIN_SEGMENTS",
    "RESOLUTION_AMBIGUOUS",
    "RESOLUTION_CONFLICTING",
    "RESOLUTION_DETERMINED",
    "RESOLUTION_SUPPORTED",
    "SURFACE_ASSISTANT",
    "SURFACE_BROWSER",
    "code_matches",
    "describe_code",
    "normalize_text",
    "parse_free_text_relation",
    "split_chain",
]
