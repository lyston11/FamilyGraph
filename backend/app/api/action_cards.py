"""ActionCard 生命周期 API（V2.4 Block S2；前缀 /api/action-cards）。

信任边界与风格对齐 api/kinship.py：
- 只服务浏览器用户：JWT 认证（require_authenticated_user）；feature flag 关闭时
  全部端点 503 ACTION_CARD_FLAG_DISABLED；
- 列表强制 space_id 必填、recipient_account_id == 当前 account、且当前账号为该空间
  active 成员（防枚举：不存在/无权同一 403/404）；
- view/dismiss/accept 单卡转换：非本人 403/404、终态 410 CARD_EXPIRED、
  compare-and-set 冲突 409 CARD_STATE_CONFLICT；
- execute 是唯一会调用 Foundation domain command 的入口：在单事务内重新校验
  actor/space/SourceFact revision/目标 Profile identity_confirmed/目标 membership
  仍未 active/VisibilityPolicy 与 cooldown；任一失败 409 CARD_EXECUTE_REJECTED
  且卡片保持 accepted 可重试；成功才 transition execute（ST-6 红线）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.api.deps import get_db, require_authenticated_user
from app.commands.context import ActorContext, command_transaction
from app.commands.spaces import create_shared_household, request_lineage_membership
from app.errors import (
    ACTION_CARD_FLAG_DISABLED,
    CARD_EXECUTE_REJECTED,
    CARD_EXPIRED,
    CARD_NOT_FOUND,
    CARD_REVISION_CONFLICT,
    CARD_STATE_CONFLICT,
    SPACE_FORBIDDEN_ACTOR,
    SPACE_NOT_FOUND,
    VALIDATION_ERROR,
    extract_api_error,
    raise_api_error,
)
from app.models.account import Account
from app.models.relationship_facts import SourceFact
from app.models.space import FamilySpace
from app.models.steward import ActionCard
from app.models.user import User
from app.schemas.action_card import (
    CardOut,
    CardStateOut,
    ExecuteRequest,
    ExecuteResponse,
    is_valid_state,
)
from app.services import action_cards, audit
from app.services import steward as steward_service
from app.services.action_cards import (
    ACTION_ACCEPT,
    ACTION_DISMISS,
    ACTION_EXECUTE,
    ACTION_VIEW,
)
from app.services.space_fsm import is_active_member
from app.services.visibility import evaluate as evaluate_visibility
from app.utils.timeutil import utcnow

# 终态：对 view/dismiss/accept/execute 一律 410 CARD_EXPIRED（不可复活）
_TERMINAL_STATES = frozenset({"executed", "dismissed", "expired", "superseded"})
# compare-and-set 冲突码（services/action_cards 内部用），用于 execute 路径转译
_CARD_REVISION_CONFLICT_CODE = CARD_REVISION_CONFLICT


def _require_action_cards_enabled() -> None:
    """V2.4 ActionCard 浏览器面 feature flag 总开关，默认关闭。"""
    if not config.STEWARD_ENABLED:
        raise_api_error(503, ACTION_CARD_FLAG_DISABLED, "ActionCard 功能未启用")


router = APIRouter(
    prefix="/action-cards",
    tags=["action-cards"],
    dependencies=[Depends(_require_action_cards_enabled)],
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


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _user_ref(db: Session, user_id: int | None) -> dict[str, Any] | None:
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if user is None:
        return None
    return {"id": user.id, "name": user.name}


def _evidence_out(card: ActionCard) -> dict[str, Any]:
    """把存储的 evidence_json 归一为前端 ActionCardEvidence 形状。

    S1 evidence_json = {primary_fact_id, facts:[{id,type,revision}], inputs:{...}}；
    不含 masked 原值；path_summary 为 null（S1 不落路径摘要）。
    """
    facts = card.evidence_json.get("facts")
    fact_ids: list[int] = []
    if isinstance(facts, list):
        for item in facts:
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                fact_ids.append(int(item["id"]))
    return {
        "fact_ids": fact_ids,
        "path_summary": None,
        "evidence_version": card.evidence_version,
    }


def _proposed_action_out(card: ActionCard) -> dict[str, Any]:
    """把存储的 proposed_action_json（{"action": <verb>, ...}）归一为前端形状。

    type = 存储的 action（create_household / request_lineage）；params = 其余键。
    """
    raw = dict(card.proposed_action_json)
    action_verb = raw.pop("action", None)
    return {"type": str(action_verb) if action_verb is not None else "", "params": raw}


def _card_out(db: Session, card: ActionCard) -> CardOut:
    return CardOut(
        id=card.id,
        kind=card.kind,
        space_id=card.space_id,
        subject_user=_user_ref(db, card.subject_user_id),  # type: ignore[arg-type]
        object_user=_user_ref(db, card.object_user_id),  # type: ignore[arg-type]
        reason_text=card.reason_text,
        evidence=_evidence_out(card),  # type: ignore[arg-type]
        proposed_action=_proposed_action_out(card),  # type: ignore[arg-type]
        privacy_effect=card.privacy_effect,
        state=card.state,
        expires_at=card.expires_at.isoformat() if card.expires_at is not None else None,
        created_at=card.created_at.isoformat(),
        revision=card.revision,
    )


def _load_card_for_recipient(db: Session, card_id: int, account_id: int) -> ActionCard:
    """加载卡片并校验归属：不存在或非本人同一 404 CARD_NOT_FOUND（防枚举）。"""
    card = action_cards.get_card(db, card_id)
    if card is None or card.recipient_account_id != account_id:
        raise_api_error(404, CARD_NOT_FOUND, "卡片不存在", detail={"card_id": card_id})
    return card


def _reject_if_terminal(card: ActionCard) -> None:
    """终态卡片对 view/dismiss/accept/execute 一律 410 CARD_EXPIRED（不可复活）。"""
    if card.state in _TERMINAL_STATES:
        raise_api_error(
            410,
            CARD_EXPIRED,
            "该卡片已失效，不可操作",
            detail={"card_id": card.id, "state": card.state},
        )


# ---- 列表 ----


@router.get("", response_model=list[CardOut])
def list_cards(
    space_id: int = Query(ge=1),
    state: str | None = Query(default=None),
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[CardOut]:
    """列出当前账号在该空间的推荐卡。

    - space_id 必填；state 可选过滤（无效值 422）；
    - 当前账号必须是该空间 active 成员，否则 403 SPACE_FORBIDDEN_ACTOR；
    - 仅返回 recipient_account_id == 当前 account 的卡。
    """
    user, account = identity
    _space_or_404(db, space_id)
    _require_active_member(db, user.id, space_id)
    if state is not None and not is_valid_state(state):
        raise_api_error(422, VALIDATION_ERROR, "未知的卡片状态", detail={"state": state})
    stmt = select(ActionCard).where(
        ActionCard.space_id == space_id,
        ActionCard.recipient_account_id == account.id,
    )
    if state is not None:
        stmt = stmt.where(ActionCard.state == state)
    rows = list(db.scalars(stmt.order_by(ActionCard.created_at.desc())))
    return [_card_out(db, c) for c in rows]


# ---- 状态转换：view / dismiss / accept ----


def _transition_card_endpoint(
    request: Request,
    card_id: int,
    action: str,
    db: Session,
    identity: tuple[User, Account],
) -> CardStateOut:
    user, account = identity
    card = _load_card_for_recipient(db, card_id, account.id)
    # 校验当前账号仍是卡片所属空间的 active 成员（撤权后禁止任何操作）
    _require_active_member(db, user.id, card.space_id)
    _reject_if_terminal(card)
    audit.write_audit(
        db,
        action=f"card_{action}",
        actor_id=user.id,
        target_id=card.id,
        ip=_client_ip(request),
        detail={"card_id": card.id, "kind": card.kind, "from_state": card.state},
    )
    try:
        updated = action_cards.transition_card(
            db, card, action, expected_revision=card.revision, actor_account_id=account.id
        )
        if action == ACTION_DISMISS:
            steward_service.set_kind_cooldown(
                db,
                space_id=card.space_id,
                account_id=account.id,
                kind=card.kind,
            )
    except HTTPException as exc:
        # compare-and-set 失败/非法转换统一转译为浏览器面 CARD_STATE_CONFLICT
        # （终态已在上方 _reject_if_terminal 拦截为 410，此处仅并发冲突可达）
        db.rollback()
        _ = exc  # transition_card 失败仅存在冲突路径，转译统一文案
        raise_api_error(
            409,
            CARD_STATE_CONFLICT,
            "卡片状态刚被其他操作更新",
            detail={"card_id": card.id, "action": action},
        )
    db.commit()
    return CardStateOut(id=updated.id, state=updated.state, revision=updated.revision)


@router.post("/{card_id}/view", response_model=CardStateOut)
def view_card(
    card_id: int,
    request: Request,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> CardStateOut:
    return _transition_card_endpoint(request, card_id, ACTION_VIEW, db, identity)


@router.post("/{card_id}/dismiss", response_model=CardStateOut)
def dismiss_card(
    card_id: int,
    request: Request,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> CardStateOut:
    return _transition_card_endpoint(request, card_id, ACTION_DISMISS, db, identity)


@router.post("/{card_id}/accept", response_model=CardStateOut)
def accept_card(
    card_id: int,
    request: Request,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> CardStateOut:
    return _transition_card_endpoint(request, card_id, ACTION_ACCEPT, db, identity)


# ---- execute：唯一调用 Foundation domain command 的入口 ----


def _execute_reject(card: ActionCard, reason: str) -> None:
    """重校验失败：409 CARD_EXECUTE_REJECTED，卡片保持 accepted（可重试）。

    红线：绝不在此路径写 SourceFact、绝不自动发送申请、绝不改 space 成员。
    """
    raise_api_error(
        409,
        CARD_EXECUTE_REJECTED,
        "当前条件已发生变化，暂时无法执行",
        detail={"card_id": card.id, "reason": reason},
    )


def _recorded_fact_revision(card: ActionCard) -> int | None:
    """从证据快照取 primary_fact 的 revision（S1 evidence.facts[0].revision）。"""
    primary_id = card.evidence_json.get("primary_fact_id")
    facts = card.evidence_json.get("facts")
    if not isinstance(facts, list):
        return None
    for item in facts:
        if isinstance(item, dict) and item.get("id") == primary_id:
            rev = item.get("revision")
            if isinstance(rev, int):
                return rev
    return None


def _confirmed_fact(db: Session, fact_id: int) -> SourceFact | None:
    fact = db.get(SourceFact, fact_id)
    if fact is None or fact.state != "confirmed":
        return None
    return fact


def _create_shared_household(
    db: Session,
    card: ActionCard,
    actor: User,
    account: Account,
    subject: User,
    obj: User,
    *,
    name: str | None,
    ip: str | None,
) -> None:
    """调用 Foundation 空间命令，再在同一事务内完成卡片 execute。"""
    if actor.id == subject.id:
        other = obj
    elif actor.id == obj.id:
        other = subject
    else:
        _execute_reject(card, "actor_not_subject_or_object")
        return
    ctx = ActorContext.from_identity(actor, account, ip=ip)
    _space, executed_event_id = create_shared_household(
        db,
        ctx,
        other_user_id=other.id,
        name=name,
        commit=False,
    )
    action_cards.transition_card(
        db,
        card,
        ACTION_EXECUTE,
        expected_revision=card.revision,
        actor_account_id=account.id,
        executed_event_id=executed_event_id,
    )


def _request_lineage_join(
    db: Session,
    card: ActionCard,
    actor: User,
    account: Account,
    subject: User,
    obj: User,
    *,
    target_space_id: int | None,
    ip: str | None,
) -> None:
    """调用 Foundation 加入命令；目标空间只能来自已接受卡片的快照。"""
    raw_target = card.proposed_action_json.get("space_id")
    if not isinstance(raw_target, int):
        _execute_reject(card, "no_target_space")
        return
    if target_space_id is not None and target_space_id != raw_target:
        _execute_reject(card, "target_space_changed")
        return
    if actor.id == subject.id:
        other = obj
    elif actor.id == obj.id:
        other = subject
    else:
        _execute_reject(card, "actor_not_subject_or_object")
        return
    target_space = db.get(FamilySpace, raw_target)
    if target_space is None or target_space.kind != "lineage":
        _execute_reject(card, "target_space_not_lineage")
        return
    if not is_active_member(db, target_space.id, other.id):
        _execute_reject(card, "target_member_no_longer_active")
        return
    if is_active_member(db, target_space.id, actor.id):
        _execute_reject(card, "already_member")
        return
    ctx = ActorContext.from_identity(actor, account, ip=ip)
    _member, executed_event_id = request_lineage_membership(
        db,
        ctx,
        target_space_id=raw_target,
        target_user_id=other.id,
        commit=False,
    )
    action_cards.transition_card(
        db,
        card,
        ACTION_EXECUTE,
        expected_revision=card.revision,
        actor_account_id=account.id,
        executed_event_id=executed_event_id,
    )


@router.post("/{card_id}/execute", response_model=ExecuteResponse)
def execute_card(
    card_id: int,
    body: ExecuteRequest,
    request: Request,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> ExecuteResponse:
    """执行已 accepted 卡片：重校验全部前置条件，成功才 transition execute。

    重校验清单（单事务，ST-6）：
    1. actor 仍是卡片所属空间 active 成员；
    2. 卡片状态为 accepted（终态/其他态 410 CARD_EXPIRED）；
    3. 卡片未过期（accepted 过期亦拒绝）；
    4. 相关 SourceFact revision 未变且仍 confirmed；
    5. subject/object Profile 仍 identity_confirmed；
    6. 目标 membership 仍未 active（household：新建空间不存在成员冲突；
       lineage：actor 未 active）；
    7. VisibilityPolicy 仍允许（actor 对对方 visible）；
    8. kind cooldown 仍允许。

    任一失败 → 409 CARD_EXECUTE_REJECTED{detail.reason}，卡片保持 accepted。
    成功 → 调用对应 Foundation domain command，产生 DomainEvent，把 executed_event_id
    落卡并 transition execute；绝不静默写 SourceFact 或自动发送（ST-6 红线）。
    """
    user, account = identity
    with command_transaction(db):
        card = _load_card_for_recipient(db, card_id, account.id)
        _require_active_member(db, user.id, card.space_id)
        if card.state != "accepted":
            # 终态或非 accepted 一律按失效处理（410）
            _reject_if_terminal(card)
            raise_api_error(
                410,
                CARD_EXPIRED,
                "卡片当前状态不可执行",
                detail={"card_id": card.id, "state": card.state},
            )
        # 过期复核（accepted 过期亦拒绝执行）
        if card.expires_at is not None and card.expires_at < utcnow():
            raise_api_error(410, CARD_EXPIRED, "卡片已过期", detail={"card_id": card.id})

        subject = db.get(User, card.subject_user_id)
        obj = db.get(User, card.object_user_id) if card.object_user_id is not None else None
        if subject is None or obj is None:
            _execute_reject(card, "profile_missing")
        assert subject is not None and obj is not None
        if user.id not in (subject.id, obj.id):
            _execute_reject(card, "actor_not_subject_or_object")
        # 4. SourceFact revision 未变且仍 confirmed
        primary_fact_id = card.evidence_json.get("primary_fact_id")
        fact = (
            _confirmed_fact(db, int(primary_fact_id)) if isinstance(primary_fact_id, int) else None
        )
        if fact is None:
            _execute_reject(card, "evidence_invalidated")
        assert fact is not None
        recorded_revision = _recorded_fact_revision(card)
        if recorded_revision is None or fact.revision != recorded_revision:
            _execute_reject(card, "evidence_changed")
        # 5. 双方 Profile 仍 identity_confirmed
        if subject.profile_status != "identity_confirmed":
            _execute_reject(card, "subject_not_confirmed")
        if obj.profile_status != "identity_confirmed":
            _execute_reject(card, "object_not_confirmed")
        if fact.fact_type == "partner" and not steward_service.mutual_disclosure_allowed(
            db, subject, obj, card.space_id
        ):
            _execute_reject(card, "disclosure_revoked")
        # 7. VisibilityPolicy 仍允许 actor 对对方可见
        counterpart = obj if user.id == subject.id else subject
        if not evaluate_visibility(db, user, counterpart, space_context=card.space_id).visible:
            _execute_reject(card, "visibility_denied")
        # 8. kind cooldown 仍允许
        if steward_service.kind_in_cooldown(
            db, space_id=card.space_id, account_id=account.id, kind=card.kind
        ):
            _execute_reject(card, "cooldown_active")

        audit.write_audit(
            db,
            action="card_execute",
            actor_id=user.id,
            target_id=card.id,
            ip=_client_ip(request),
            detail={"card_id": card.id, "kind": card.kind},
        )

        action_verb = card.proposed_action_json.get("action")
        try:
            if action_verb == "create_household":
                _create_shared_household(
                    db,
                    card,
                    user,
                    account,
                    subject,
                    obj,
                    name=body.name,
                    ip=_client_ip(request),
                )
            elif action_verb == "request_lineage":
                _request_lineage_join(
                    db,
                    card,
                    user,
                    account,
                    subject,
                    obj,
                    target_space_id=body.target_space_id,
                    ip=_client_ip(request),
                )
            else:
                _execute_reject(card, "unknown_action")
        except HTTPException as exc:
            api_err = extract_api_error(exc.detail)
            # 仅 compare-and-set 冲突回滚中间写入并转译为 CARD_STATE_CONFLICT；
            # 重校验失败（CARD_EXECUTE_REJECTED）与其他业务错误原样抛出。
            if api_err is not None and api_err.get("code") == _CARD_REVISION_CONFLICT_CODE:
                db.rollback()
                raise_api_error(
                    409,
                    CARD_STATE_CONFLICT,
                    "卡片状态刚被其他操作更新",
                    detail={"card_id": card.id, "action": "execute"},
                )
            raise
    db.refresh(card)
    return ExecuteResponse(id=card.id, state=card.state)
