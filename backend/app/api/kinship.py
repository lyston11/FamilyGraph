"""Kinship TermRegistry 浏览器 API（V2.3 Block E3；前缀 /api/kinship）。

信任边界与风格对齐 api/agent.py：
- 只服务浏览器用户：JWT 认证；resolve/usages 要求当前用户为目标空间
  active 成员；from_user_id 强制等于本人（禁止以他人视角探测图）；
- feature flag 关闭时全部端点 503 KINSHIP_FLAG_DISABLED；
- 个人称谓纠正只写 term_entries + term.personal_updated 领域事件，
  绝不触碰 SourceFact 与 raw_relation_inputs 原文（KI-4 红线）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import config
from app.api.deps import get_db, require_authenticated_user
from app.errors import (
    KINSHIP_FLAG_DISABLED,
    SPACE_FORBIDDEN_ACTOR,
    SPACE_NOT_FOUND,
    raise_api_error,
)
from app.models.account import Account
from app.models.space import FamilySpace
from app.models.user import User
from app.schemas.kinship import (
    KinshipParseRequest,
    KinshipResolveOut,
    MyTermOut,
    ParseResultOut,
    PersonalTermPutRequest,
    ResolvedTermOut,
    SpacePromotionOut,
    UsageCreatedOut,
    UsagePostRequest,
)
from app.services import audit, intake_extractor, terms
from app.services.space_fsm import is_active_member


def _require_kinship_enabled() -> None:
    """V2.3 关系智能 feature flag 总开关，默认关闭。"""
    if not config.RELATIONSHIP_INTELLIGENCE_ENABLED:
        raise_api_error(503, KINSHIP_FLAG_DISABLED, "关系智能能力未启用")


router = APIRouter(
    prefix="/kinship", tags=["kinship"], dependencies=[Depends(_require_kinship_enabled)]
)


# ---- 共享辅助 ----


def _space_or_404(db: Session, space_id: int) -> FamilySpace:
    space = db.get(FamilySpace, space_id)
    if space is None:
        raise_api_error(404, SPACE_NOT_FOUND, "空间不存在")
    return space


def _require_active_member(db: Session, user_id: int, space_id: int) -> None:
    if not is_active_member(db, space_id, user_id):
        raise_api_error(403, SPACE_FORBIDDEN_ACTOR, "仅空间 active 成员可执行该操作")


# ---- 个人称谓（personal > space > locale > system 的最高层）----


@router.get("/terms/my", response_model=list[MyTermOut])
def list_my_terms(
    space_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[MyTermOut]:
    """列出本人 personal 词条；带 space_id 时附该空间语境的实时生效解析。"""
    _user, account = identity
    if space_id is not None:
        _space_or_404(db, space_id)
    rows = terms.list_personal_terms(db, account_id=account.id, space_id=space_id)
    return [
        MyTermOut(
            entry_id=row["entry_id"],
            concept_code=row["concept_code"],
            term=row["term"],
            revision=row["revision"],
            updated_at=row["updated_at"],
            resolved=(
                ResolvedTermOut(**row["resolved"]) if row.get("resolved") is not None else None
            ),
        )
        for row in rows
    ]


@router.put("/terms/my", response_model=MyTermOut)
def set_my_term(
    body: PersonalTermPutRequest,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MyTermOut:
    """个人称谓纠正：立即生效（展示侧实时解析），写领域事件，不改结构关系。"""
    user, account = identity
    _space_or_404(db, body.space_id)
    entry = terms.set_personal_term(
        db,
        account_id=account.id,
        space_id=body.space_id,
        concept_code=body.concept_code,
        term=body.term,
    )
    audit.write_audit(
        db,
        action="term_personal_updated",
        actor_id=user.id,
        target_id=entry.id,
        detail={
            "space_id": body.space_id,
            "concept_code": body.concept_code,
            "term": entry.term,
        },
    )
    db.commit()
    return MyTermOut(
        entry_id=entry.id,
        concept_code=entry.concept_code,
        term=entry.term,
        revision=entry.revision,
        updated_at=entry.updated_at,
    )


# ---- resolve 合成视图 ----


@router.get("/resolve", response_model=KinshipResolveOut)
def resolve_kinship(
    space_id: int = Query(ge=1),
    from_user_id: int = Query(ge=1),
    to_user_id: int = Query(ge=1),
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> KinshipResolveOut:
    """主路径 + 称谓 + 来源级别 + 替代路径 + 事实状态摘要。

    - viewer（from_user_id）必须是当前登录者本人（防以他人视角探测）；
    - 不可见/不存在/超深一律 found=false 同一形状（不泄露存在性）；
    - DerivedFact 缓存按 evidence_hash 守护，过期行自动重算后返回。
    """
    user, account = identity
    _space_or_404(db, space_id)
    _require_active_member(db, user.id, space_id)
    if from_user_id != user.id:
        raise_api_error(403, SPACE_FORBIDDEN_ACTOR, "只能以本人视角解析亲属关系")
    payload = terms.compose_resolution_view(
        db,
        viewer_user_id=from_user_id,
        target_user_id=to_user_id,
        space_id=space_id,
        account_id=account.id,
    )
    return KinshipResolveOut(**payload)


# ---- 自由文本关系解析（KI-3：原文 append-only，解析永不写 SourceFact）----


@router.post("/parse", response_model=ParseResultOut, status_code=201)
def parse_relation_text(
    body: KinshipParseRequest,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> ParseResultOut:
    """确定性词素解析：determined/supported/ambiguous/conflicting 四级结果。

    - 仅当前空间 active 成员可用（与 resolve/usages 同一授权口径）；
    - 原文先 append-only 写入 raw_relation_inputs 再解析（AC-KI3）；
    - 本端点绝不写 SourceFact；提案需用户在后续流程显式确认。
    """
    user, account = identity
    _space_or_404(db, body.space_id)
    _require_active_member(db, user.id, body.space_id)
    result = intake_extractor.parse_free_text_relation(
        db,
        account_id=account.id,
        user_id=user.id,
        space_id=body.space_id,
        text=body.text,
        surface=intake_extractor.SURFACE_BROWSER,
    )
    audit.write_audit(
        db,
        action="relation_text_parsed",
        actor_id=user.id,
        target_id=result["raw_text_id"],
        detail={
            "space_id": body.space_id,
            "resolution_class": result["resolution_class"],
        },
    )
    db.commit()
    return ParseResultOut(**result)


# ---- 使用证据（两人晋升输入）----


@router.post("/usages", response_model=UsageCreatedOut, status_code=201)
def record_term_usage(
    body: UsagePostRequest,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> UsageCreatedOut:
    """记录用户在某空间选择某叫法；同账号重复只计一次，随后重算晋升资格。"""
    user, account = identity
    _space_or_404(db, body.space_id)
    _require_active_member(db, user.id, body.space_id)
    usage, created, summary = terms.record_usage_and_promote(
        db,
        space_id=body.space_id,
        concept_code=body.concept_code,
        term=body.term,
        account_id=account.id,
        profile_id=user.id,
        source_event=body.source_event,
    )
    audit.write_audit(
        db,
        action="term_usage_recorded",
        actor_id=user.id,
        target_id=usage.id,
        detail={
            "space_id": body.space_id,
            "concept_code": body.concept_code,
            "term": body.term.strip(),
            "created": created,
            "promotion_promoted": summary["promoted"],
        },
    )
    db.commit()
    return UsageCreatedOut(
        usage_id=usage.id,
        entry_id=usage.term_entry_id,
        created=created,
        promotion=SpacePromotionOut(
            promoted=bool(summary["promoted"]),
            demoted=bool(summary["demoted"]),
            eligible_accounts=int(summary["eligible_accounts"]),
        ),
    )
