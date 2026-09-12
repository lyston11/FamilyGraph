"""Steward 建议审核投影（任务 09-11-steward-candidate-review）。

职责（design.md）：
- **投影**：把有证据的模型候选（steward_llm_candidates，payload 合同
  ``{"kind","subject_user_id","object_user_id"}``）与确定性 findings（冲突/
  缺失检测）投影为可审核 ``StewardSuggestion``。去重键含 space/kind/有向
  端点/结构化建议值（term_preference 另含 viewer account），**不含模型措辞**
  ——同结构候选只改 rationale 不生成第二待办；证据变化 → 新建议 + supersede
  旧活动行。并发生成由部分唯一索引收敛。
- **历史裸 JSON 候选隔离**：payload 缺少合同三键（或类型不合法）的旧候选
  视为缺少证据，永不投影为公开建议。
- **读取/操作**：列表（分页、安全显示字段，绝不回传 raw model payload）、
  按 revision CAS 的 dismiss（按收件人独立 + 同证据版本冷却）与 submit
  （Idempotency-Key 幂等；动作/对象完全由服务端建议决定）。

状态：proposed → submitted → resolved；proposed/submitted 可 expired /
superseded。submitted 的终局由关联领域对象决定；驳回/拒绝建议绝不撤销
已存在的正式事实。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.commands.context import ActorContext, command_transaction
from app.errors import (
    SUGGESTION_EVIDENCE_CHANGED,
    SUGGESTION_EXPIRED,
    SUGGESTION_NOT_FOUND,
    SUGGESTION_REVISION_CONFLICT,
    SUGGESTION_STATE_CONFLICT,
    SUGGESTION_SUBMIT_NOT_ALLOWED,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models.account import Account
from app.models.notification import Notification
from app.models.space import SpaceMember
from app.models.steward import StewardJob, StewardLlmCandidate
from app.models.steward_suggestion import (
    SUGGESTION_ACTIVE_STATES,
    StewardSuggestion,
    StewardSuggestionRecipient,
)
from app.models.user import User
from app.services import visibility
from app.services.action_cards import compute_evidence_hash
from app.services.family_projection import authorized_space_or_404
from app.utils.timeutil import utcnow

AGGREGATE_TYPE = "steward_suggestion"

# 状态 → 通知跨领域最小状态集
SUGGESTION_DOMAIN_STATUS: dict[str, str] = {
    "proposed": "pending",
    "submitted": "accepted",
    "resolved": "done",
    "expired": "expired",
    "superseded": "revoked",
}

_KIND_TITLES: dict[str, str] = {
    "relation_proposal": "Steward 有关系线索待核实",
    "term_preference": "Steward 有称谓偏好待确认",
    "identity_duplicate": "发现疑似重复档案待核实",
    "missing_information": "发现资料缺口待核实",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_dedupe_key(
    *,
    kind: str,
    subject_user_id: int,
    object_user_id: int | None,
    value_json: dict[str, Any],
    viewer_account_id: int | None,
) -> str:
    """去重键：kind + 有向端点 + 结构化建议值（term_preference 另含 viewer）。

    绝不包含模型措辞/rationale——相同结构建议换措辞不会绕过去重（F07/AC-1）。
    """
    canonical = _canonical(
        [
            str(kind),
            int(subject_user_id),
            int(object_user_id) if object_user_id is not None else None,
            value_json,
            int(viewer_account_id) if viewer_account_id is not None else None,
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def default_expires_at(now: datetime | None = None) -> datetime:
    return (now or utcnow()) + timedelta(days=config.STEWARD_SUGGESTION_TTL_DAYS)


def _account_ids_of_users(session: Session, user_ids: set[int]) -> list[int]:
    if not user_ids:
        return []
    rows = session.scalars(select(Account.id).where(Account.user_id.in_(user_ids))).all()
    return sorted({int(a) for a in rows})


def _space_active_member_user_ids(session: Session, space_id: int) -> set[int]:
    return {
        int(uid)
        for uid in session.scalars(
            select(SpaceMember.user_id).where(
                SpaceMember.space_id == space_id, SpaceMember.status == "active"
            )
        )
    }


def _supersede(session: Session, old: StewardSuggestion, new_id: int, now: datetime) -> None:
    old.status = "superseded"
    old.superseded_by_id = new_id
    old.revision += 1
    old.updated_at = now
    session.flush()


# ---- 投影（core 短事务内调用；同事务写通知）----


def upsert_suggestion(
    session: Session,
    *,
    space_id: int,
    origin: str,
    kind: str,
    subject_user_id: int,
    object_user_id: int | None,
    value_json: dict[str, Any],
    evidence_json: dict[str, Any],
    policy_version: str,
    source_candidate_id: int | None = None,
    source_job_id: int | None = None,
    expires_at: datetime | None = None,
    recipient_account_ids: list[int] | None = None,
    now: datetime | None = None,
) -> tuple[StewardSuggestion, bool]:
    """去重投影一条建议：同 (space, dedupe_key, evidence_hash) 收敛为一行。

    返回 (suggestion, created)。同 key 同证据 → 复用既有活跃行（并发生成
    收敛；唯一索引兜底）；同 key 证据变化 → 新建议 + supersede 旧活动行
    （驳回冷却只作用于旧收件人的旧证据版本）。
    """
    if kind not in (
        "relation_proposal",
        "term_preference",
        "identity_duplicate",
        "missing_information",
    ):
        raise ValueError(f"unknown suggestion kind: {kind}")
    if origin not in ("deterministic", "model"):
        raise ValueError(f"unknown suggestion origin: {origin}")
    now = now or utcnow()
    dedupe_key = compute_dedupe_key(
        kind=kind,
        subject_user_id=subject_user_id,
        object_user_id=object_user_id,
        value_json=value_json,
        viewer_account_id=None,  # v1 无个人维度生成来源；提交面保留该维度
    )
    evidence_hash = compute_evidence_hash(evidence_json)
    existing = session.scalar(
        select(StewardSuggestion)
        .where(
            StewardSuggestion.space_id == space_id,
            StewardSuggestion.dedupe_key == dedupe_key,
            StewardSuggestion.status.in_(SUGGESTION_ACTIVE_STATES),
        )
        .order_by(StewardSuggestion.id.desc())
        .limit(1)
    )
    if existing is not None and existing.evidence_hash == evidence_hash:
        _ensure_recipients(session, existing, recipient_account_ids or [], now)
        return existing, False
    suggestion = StewardSuggestion(
        space_id=space_id,
        origin=origin,
        kind=kind,
        subject_user_id=subject_user_id,
        object_user_id=object_user_id,
        value_json=value_json,
        evidence_json=evidence_json,
        evidence_hash=evidence_hash,
        dedupe_key=dedupe_key,
        policy_version=policy_version,
        status="proposed",
        revision=1,
        expires_at=expires_at or default_expires_at(now),
        source_candidate_id=source_candidate_id,
        source_job_id=source_job_id,
        created_at=now,
        updated_at=now,
    )
    session.add(suggestion)
    session.flush()
    if existing is not None:
        _supersede(session, existing, suggestion.id, now)
    _ensure_recipients(session, suggestion, recipient_account_ids or [], now)
    _record_suggestion_notifications(session, suggestion)
    return suggestion, True


def _ensure_recipients(
    session: Session, suggestion: StewardSuggestion, account_ids: list[int], now: datetime
) -> None:
    if not account_ids:
        return
    existing = set(
        session.scalars(
            select(StewardSuggestionRecipient.account_id).where(
                StewardSuggestionRecipient.suggestion_id == suggestion.id,
                StewardSuggestionRecipient.account_id.in_(account_ids),
            )
        )
    )
    for account_id in account_ids:
        if account_id in existing:
            continue
        session.add(
            StewardSuggestionRecipient(
                suggestion_id=suggestion.id,
                account_id=account_id,
                created_at=now,
            )
        )
    session.flush()


def _record_suggestion_notifications(session: Session, suggestion: StewardSuggestion) -> None:
    """建议产生 → 收件人通知（UNIQUE (recipient, space, suggestion) 去重）。"""
    from app.services import notifications as notifications_service

    recipients = session.scalars(
        select(StewardSuggestionRecipient.account_id).where(
            StewardSuggestionRecipient.suggestion_id == suggestion.id
        )
    ).all()
    for account_id in recipients:
        duplicate = session.scalar(
            select(Notification.id).where(
                Notification.recipient_account_id == account_id,
                Notification.space_id == suggestion.space_id,
                Notification.suggestion_id == suggestion.id,
            )
        )
        if duplicate is not None:
            continue
        notifications_service.record_suggestion_notification(
            session, suggestion=suggestion, recipient_account_id=int(account_id)
        )


def project_for_job(
    session: Session,
    job: StewardJob,
    *,
    findings: list[dict[str, Any]],
    facts: list[Any],
    now: datetime | None = None,
) -> int:
    """core 事务内的建议投影入口（确定性 findings + 本 job 的模型候选）。

    ``facts`` 是本次授权快照的 confirmed 事实行（用于证据 revision 快照）。
    返回新建建议数。绝不抛出阻断 core 的异常由调用方 SAVEPOINT 语义决定——
    本函数只在封闭 kind/origin 白名单内工作，结构异常直接跳过。
    """
    now = now or utcnow()
    created = 0
    fact_snapshots = [
        {
            "id": int(f.id),
            "revision": int(f.revision),
            "subject_user_id": int(f.subject_user_id),
            "object_user_id": int(f.object_user_id),
        }
        for f in facts
    ]

    # 模型候选 → relation_proposal（payload 合同三键；旧裸 JSON 永不公开）。
    # 09-11 E2E 修正：候选由辅助批次在 job 结算后写回（job_id=注册 job），
    # 本 job 的投影已随结算提交——若只看当前 job 的候选，模型候选永远投影
    # 不成建议。改为取本空间全部"尚未被任何建议引用"的 proposed 候选
    # （source_candidate_id 反连接保证幂等：已被投影的候选不再重复投影）。
    projected_ids = select(StewardSuggestion.source_candidate_id).where(
        StewardSuggestion.source_candidate_id.is_not(None)
    )
    candidates = list(
        session.scalars(
            select(StewardLlmCandidate).where(
                StewardLlmCandidate.space_id == job.space_id,
                StewardLlmCandidate.status == "proposed",
                StewardLlmCandidate.id.not_in(projected_ids),
            )
        )
    )
    for candidate in candidates:
        payload = candidate.payload_json if isinstance(candidate.payload_json, dict) else {}
        raw_kind = payload.get("kind")
        raw_subject = payload.get("subject_user_id")
        raw_object = payload.get("object_user_id")
        if not isinstance(raw_kind, str) or not raw_kind:
            continue  # 历史裸 JSON：缺证据/结构不合法 → 永不公开
        if isinstance(raw_subject, bool) or not isinstance(raw_subject, int):
            continue
        if isinstance(raw_object, bool) or not isinstance(raw_object, int):
            continue
        if raw_subject == raw_object:
            continue
        endpoints = {raw_subject, raw_object}
        # 证据指纹只含事实 revision 快照（候选内部行 id 不是证据：同结构候选
        # 换内部行/措辞不会产生新建议）；候选 id 仅在建行时作来源关联。
        evidence = {"facts": list(fact_snapshots)}
        recipient_users = endpoints & _space_active_member_user_ids(session, job.space_id)
        _, was_created = upsert_suggestion(
            session,
            space_id=job.space_id,
            origin="model",
            kind="relation_proposal",
            subject_user_id=raw_subject,
            object_user_id=raw_object,
            value_json={"fact_type": raw_kind},
            evidence_json=evidence,
            policy_version=job.policy_version,
            source_candidate_id=candidate.id,
            source_job_id=job.id,
            recipient_account_ids=_account_ids_of_users(session, recipient_users),
            now=now,
        )
        created += int(was_created)

    # 确定性 findings → identity_duplicate / missing_information（只指向人工处理）
    for finding in findings:
        raw_detail = finding.get("detail")
        detail: dict[str, Any] = raw_detail if isinstance(raw_detail, dict) else {}
        code = str(detail.get("code") or "")
        kind: str
        if finding.get("kind") == "conflict" and code.startswith("duplicate_person"):
            kind = "identity_duplicate"
        elif finding.get("kind") == "conflict":
            kind = "identity_duplicate"
        elif finding.get("kind") == "gap":
            kind = "missing_information"
        else:
            continue  # 未知种类：只保留在领域事件/隔离，不透传为新命令
        pair_raw = detail.get("pair")
        subject_id: int | None = None
        object_id: int | None = None
        try:
            if isinstance(pair_raw, list) and len(pair_raw) == 2:
                if any(isinstance(value, bool) or not isinstance(value, int) for value in pair_raw):
                    continue
                subject_id, object_id = int(pair_raw[0]), int(pair_raw[1])
            elif isinstance(detail.get("subject_user_id"), int):
                subject_id = int(detail["subject_user_id"])
                raw_object_id = detail.get("object_user_id")
                if isinstance(raw_object_id, int) and not isinstance(raw_object_id, bool):
                    object_id = int(raw_object_id)
        except (TypeError, ValueError):
            continue  # 畸形 pair：跳过本条 finding，不让单条坏数据阻断整批投影
        if subject_id is None:
            continue
        evidence = {
            # 证据引用与 pair 用户相关的事实（subject/object 端点匹配），
            # 不是 id 恰好等于用户 id 的事实——findings pair 是用户 id 维度。
            "facts": [
                f
                for f in fact_snapshots
                if subject_id in (f["subject_user_id"], f["object_user_id"])
                or (
                    object_id is not None
                    and object_id in (f["subject_user_id"], f["object_user_id"])
                )
            ]
        }
        member_users = _space_active_member_user_ids(session, job.space_id)
        _, was_created = upsert_suggestion(
            session,
            space_id=job.space_id,
            origin="deterministic",
            kind=kind,
            subject_user_id=subject_id,
            object_user_id=object_id,
            value_json={"code": code or finding.get("kind", ""), "signature": finding["signature"]},
            evidence_json=evidence,
            policy_version=job.policy_version,
            source_job_id=job.id,
            recipient_account_ids=_account_ids_of_users(session, member_users),
            now=now,
        )
        created += int(was_created)
    return created


# ---- 读取投影（安全显示字段；绝不回传 raw model payload）----


def _recipient_row(
    session: Session, suggestion_id: int, account_id: int
) -> StewardSuggestionRecipient | None:
    return session.scalar(
        select(StewardSuggestionRecipient).where(
            StewardSuggestionRecipient.suggestion_id == suggestion_id,
            StewardSuggestionRecipient.account_id == account_id,
        )
    )


def _endpoints_visible(session: Session, viewer: User, suggestion: StewardSuggestion) -> bool:
    """证据端点对当前用户必须可见（隐藏人物的建议不透出；列表/详情同口径）。"""
    for endpoint_id in (suggestion.subject_user_id, suggestion.object_user_id):
        if endpoint_id is None:
            continue
        endpoint = session.get(User, endpoint_id)
        if endpoint is None:
            return False
        decision = visibility.evaluate(
            session, viewer, endpoint, purpose=visibility.PURPOSE_PROFILE
        )
        if not decision.visible:
            return False
    return True


def visible_suggestion_or_404(
    session: Session, *, account: Account, space_id: int, suggestion_id: int
) -> tuple[Any, StewardSuggestion]:
    """统一 404 语义：未知建议/非 active 成员/证据端点不可见一律同形拒绝。"""
    space, viewer = authorized_space_or_404(session, account=account, space_id=space_id)
    suggestion = session.get(StewardSuggestion, suggestion_id)
    if (
        suggestion is None
        or suggestion.space_id != space.id
        or suggestion.kind
        not in (
            "relation_proposal",
            "term_preference",
            "identity_duplicate",
            "missing_information",
        )
    ):
        raise_api_error(404, SUGGESTION_NOT_FOUND, "建议不存在")
    if suggestion.kind == "term_preference" and suggestion.viewer_account_id != account.id:
        raise_api_error(404, SUGGESTION_NOT_FOUND, "建议不存在")
    # 证据端点对当前用户必须可见（隐藏人物的建议不透出）
    if not _endpoints_visible(session, viewer, suggestion):
        raise_api_error(404, SUGGESTION_NOT_FOUND, "建议不存在")
    return viewer, suggestion


def _jsonable(value: Any) -> Any:
    """datetime → ISO 字符串（submit_result_json JSON 列只存可序列化值）。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _evidence_summary(session: Session, suggestion: StewardSuggestion) -> dict[str, Any]:
    facts = [
        {"fact_id": int(f["id"]), "revision": int(f["revision"])}
        for f in suggestion.evidence_json.get("facts", [])
        if isinstance(f, dict) and isinstance(f.get("id"), int)
    ]
    return {"fact_count": len(facts), "facts": facts}


def allowed_actions(
    session: Session, viewer: User, account: Account, suggestion: StewardSuggestion
) -> list[str]:
    """按身份精确给出可执行动作（AC-2；未知/不可见建议根本到不了这里）。"""
    is_endpoint = viewer.id in (suggestion.subject_user_id, suggestion.object_user_id)
    if suggestion.kind == "term_preference":
        if suggestion.viewer_account_id != account.id:
            return ["open_details"]
        return ["open_details", "submit", "dismiss"]
    if suggestion.kind in ("identity_duplicate", "missing_information"):
        # v1 只指向人工处理（资料/去重流程），无 submit
        return ["open_details", "dismiss"]
    # relation_proposal：端点可提交/驳回；其他 active 成员（含 owner）可发起
    # 提案但绝不能代端点确认（确认在提案命令层，仅端点/合法代管人可做）。
    actions = ["open_details", "submit"]
    if is_endpoint or suggestion.viewer_account_id == account.id:
        actions.append("dismiss")
    return actions


def list_suggestions_page(
    session: Session,
    *,
    account: Account,
    space_id: int,
    cursor: int | None,
    limit: int,
) -> dict[str, Any]:
    """分页列表（keyset by id）；只返回 active 成员可见且证据可见的建议。

    可见性/动作过滤在取数后进行：过滤会造成页面欠返，因此按批 over-fetch
    继续向前取数，直到集满 limit 条或数据耗尽。next_cursor 指向最后一条
    已返回项的 id（客户端以其为 keyset 继续），保证不漏行。
    """
    space, viewer = authorized_space_or_404(session, account=account, space_id=space_id)
    limit = max(1, min(int(limit), 100))
    items: list[dict[str, Any]] = []
    fetch_cursor = cursor
    exhausted = False
    while len(items) < limit and not exhausted:
        stmt = (
            select(StewardSuggestion)
            .where(StewardSuggestion.space_id == space.id)
            .order_by(StewardSuggestion.id.desc())
            .limit(limit + 1)
        )
        if fetch_cursor is not None and fetch_cursor > 0:
            stmt = stmt.where(StewardSuggestion.id < fetch_cursor)
        rows = list(session.scalars(stmt))
        if len(rows) > limit:
            rows = rows[:limit]
        else:
            exhausted = True
        if not rows:
            break
        last_consumed_id: int | None = None
        for suggestion in rows:
            last_consumed_id = suggestion.id
            # 列表与详情同口径：证据端点对当前账号不可见（隐藏人物）→ 不透出
            if not _endpoints_visible(session, viewer, suggestion):
                continue
            try:
                allowed = allowed_actions(session, viewer, account, suggestion)
            except Exception:  # noqa: BLE001 — 端点消失等异常按 404 语义丢弃该行
                allowed = []
            if not allowed:
                continue
            if "dismiss" in allowed:
                recipient = _recipient_row(session, suggestion.id, account.id)
                if recipient is not None and recipient.dismissed_at is not None:
                    state = "dismissed"
                else:
                    state = suggestion.status
            else:
                state = suggestion.status
            items.append(_serialize(session, suggestion, allowed, state))
            if len(items) >= limit:
                break
        # 每批消费后都推进内部游标；否则整批被过滤时会重复查询同一批并循环。
        # 集满时游标落在最后消费行，后续未消费的尾行留给下一次查询。
        if last_consumed_id is not None:
            fetch_cursor = last_consumed_id
    # 还有未读尽的上游数据（exhausted=False）说明后面可能仍有可见行；
    # 否则到头了，不再给 cursor。
    next_cursor = items[-1]["id"] if (items and not exhausted) else None
    return {"space_id": space.id, "items": items, "next_cursor": next_cursor}


def _serialize(
    session: Session,
    suggestion: StewardSuggestion,
    allowed_actions_list: list[str],
    state: str,
) -> dict[str, Any]:
    subject = session.get(User, suggestion.subject_user_id)
    object_row = session.get(User, suggestion.object_user_id) if suggestion.object_user_id else None
    return {
        "id": suggestion.id,
        "space_id": suggestion.space_id,
        "kind": suggestion.kind,
        "origin": suggestion.origin,
        "state": state,
        "revision": suggestion.revision,
        "evidence_hash": suggestion.evidence_hash,
        "subject_user_id": suggestion.subject_user_id,
        "object_user_id": suggestion.object_user_id,
        "subject_name": subject.name if subject is not None else None,
        "object_name": object_row.name if object_row is not None else None,
        "value": dict(suggestion.value_json),
        "evidence_summary": _evidence_summary(session, suggestion),
        "allowed_actions": allowed_actions_list,
        "expires_at": suggestion.expires_at,
        "created_at": suggestion.created_at,
    }


# ---- dismiss（按收件人；CAS revision；同证据版本冷却）----


def dismiss_suggestion(
    session: Session,
    *,
    account: Account,
    space_id: int,
    suggestion_id: int,
    expected_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """驳回：收件人独立状态 + 冷却只作用于同一证据版本；幂等重入返回现状。"""
    now = now or utcnow()
    with command_transaction(session, immediate=True):
        viewer, suggestion = visible_suggestion_or_404(
            session, account=account, space_id=space_id, suggestion_id=suggestion_id
        )
        if "dismiss" not in allowed_actions(session, viewer, account, suggestion):
            raise_api_error(404, SUGGESTION_NOT_FOUND, "建议不存在")
        if suggestion.status == "expired" or (
            suggestion.expires_at is not None and suggestion.expires_at <= now
        ):
            raise_api_error(410, SUGGESTION_EXPIRED, "建议已过期")
        if suggestion.status not in SUGGESTION_ACTIVE_STATES:
            raise_api_error(409, SUGGESTION_STATE_CONFLICT, "建议已终结")
        if suggestion.revision != expected_revision:
            raise_api_error(409, SUGGESTION_REVISION_CONFLICT, "建议已被其他操作更新")
        recipient = _recipient_row(session, suggestion.id, account.id)
        if recipient is None:
            raise_api_error(404, SUGGESTION_NOT_FOUND, "建议不存在")
        if recipient.dismissed_at is None:
            recipient.dismissed_at = now
            recipient.cooldown_until = now + timedelta(days=config.STEWARD_SUGGESTION_COOLDOWN_DAYS)
            recipient.cooldown_evidence_hash = suggestion.evidence_hash
            suggestion.revision += 1
            suggestion.updated_at = now
            session.flush()
        return {
            "id": suggestion.id,
            "state": "dismissed",
            "revision": suggestion.revision,
            "dismissed_at": recipient.dismissed_at,
            "cooldown_until": recipient.cooldown_until,
        }


# ---- submit（Idempotency-Key 幂等；动作完全由服务端建议决定）----


def _endpoint_account_ids(session: Session, suggestion: StewardSuggestion) -> list[int]:
    ids = {suggestion.subject_user_id}
    if suggestion.object_user_id is not None:
        ids.add(suggestion.object_user_id)
    return _account_ids_of_users(session, ids)


# Note: 候选→正式关系唯一合法路径 —
# 见 .agent-notes/implemented/feature/2026-09-11-steward-suggestion-review-loop.md
def submit_suggestion(
    session: Session,
    ctx: ActorContext,
    *,
    account: Account,
    space_id: int,
    suggestion_id: int,
    expected_revision: int,
    evidence_hash: str,
    confirm: bool,
    idempotency_key: str,
    now: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    """提交建议对应的领域动作；返回 (http_status, payload)。

    - relation_proposal → 202 {suggestion, linked_proposal, pending_confirmations}
      （只生成 proposed SourceFact，provenance=agent_proposal；绝不直接
      confirmed，绝不把 submitted 显示为关系已确认）；
    - term_preference → 200 {suggestion, linked_preference}，仅本人显式提交；
    - identity_duplicate/missing_information → v1 无 submit（422）。
    同 Idempotency-Key 重试返回同一关联对象（不产生第二领域副作用）。
    """
    from app.commands import relationship_proposals
    from app.services import terms as terms_service

    now = now or utcnow()
    key = idempotency_key.strip()
    if not key or len(key) > 120:
        raise_api_error(422, VALIDATION_ERROR, "缺少合法的 Idempotency-Key")
    with command_transaction(session, immediate=True):
        viewer, suggestion = visible_suggestion_or_404(
            session, account=account, space_id=space_id, suggestion_id=suggestion_id
        )
        actions = allowed_actions(session, viewer, account, suggestion)
        if "submit" not in actions:
            raise_api_error(404, SUGGESTION_NOT_FOUND, "建议不存在")
        # 幂等重试：同 key 返回既有结果（含已提交状态），不产生第二副作用
        if suggestion.submit_key == key and suggestion.submit_result_json is not None:
            stored = dict(suggestion.submit_result_json)
            return int(stored.get("status_code", 200)), stored.get("payload", {})
        if suggestion.kind in ("identity_duplicate", "missing_information"):
            raise_api_error(422, SUGGESTION_SUBMIT_NOT_ALLOWED, "该类建议仅支持查看详情或驳回")
        if not confirm:
            raise_api_error(422, VALIDATION_ERROR, "必须显式 confirm=true 才能提交")
        if suggestion.status == "expired" or (
            suggestion.expires_at is not None and suggestion.expires_at <= now
        ):
            raise_api_error(410, SUGGESTION_EXPIRED, "建议已过期")
        if suggestion.status not in SUGGESTION_ACTIVE_STATES:
            raise_api_error(409, SUGGESTION_STATE_CONFLICT, "建议已终结")
        if suggestion.revision != expected_revision:
            raise_api_error(409, SUGGESTION_REVISION_CONFLICT, "建议已被其他操作更新")
        if evidence_hash != suggestion.evidence_hash:
            # 证据已变化：无正式写入；新证据版本会以新建议出现
            raise_api_error(409, SUGGESTION_EVIDENCE_CHANGED, "建议证据已变化，请基于新建议操作")

        if suggestion.kind == "relation_proposal":
            fact_type = str(suggestion.value_json.get("fact_type") or "")
            proposal = relationship_proposals.create_relationship_proposal(
                session,
                ctx,
                space_id=suggestion.space_id,
                fact_type=fact_type,
                subject_user_id=suggestion.subject_user_id,
                object_user_id=suggestion.object_user_id,
                evidence_json=dict(suggestion.evidence_json),
                suggestion_id=suggestion.id,
                commit=False,
            )
            confirmer_ids = relationship_proposals.eligible_confirmer_account_ids(
                session,
                subject_user_id=suggestion.subject_user_id,
                object_user_id=suggestion.object_user_id,
            )
            # 提案刚创建：所有合法确认主体都在待确认集合中；集合为空时提案保持
            # pending（绝不把空确认集合当作全部同意）
            pending = [{"account_id": a} for a in confirmer_ids]
            suggestion.status = "submitted"
            suggestion.revision += 1
            suggestion.updated_at = now
            suggestion.linked_fact_id = proposal.id
            suggestion.submit_key = key
            payload = {
                "suggestion": _serialize(session, suggestion, ["open_details"], suggestion.status),
                "linked_proposal": {
                    "source_fact_id": proposal.id,
                    "revision": proposal.revision,
                    "state": proposal.state,
                    "fact_type": proposal.fact_type,
                },
                "pending_confirmations": pending,
            }
            suggestion.submit_result_json = _jsonable({"status_code": 202, "payload": payload})
            session.flush()
            return 202, payload

        if suggestion.kind == "term_preference":
            if suggestion.viewer_account_id != account.id:
                raise_api_error(404, SUGGESTION_NOT_FOUND, "建议不存在")
            concept_code = str(suggestion.value_json.get("concept_code") or "")
            term_text = str(suggestion.value_json.get("term") or "")
            entry = terms_service.set_personal_term(
                session,
                account_id=account.id,
                space_id=suggestion.space_id,
                concept_code=concept_code,
                term=term_text,
            )
            suggestion.status = "resolved"
            suggestion.revision += 1
            suggestion.updated_at = now
            suggestion.linked_term_id = entry.id
            suggestion.submit_key = key
            payload = {
                "suggestion": _serialize(session, suggestion, ["open_details"], "resolved"),
                "linked_preference": {
                    "term_id": entry.id,
                    "concept_code": concept_code,
                    "term": entry.term,
                },
            }
            suggestion.submit_result_json = _jsonable({"status_code": 200, "payload": payload})
            session.flush()
            return 200, payload
        raise_api_error(422, SUGGESTION_SUBMIT_NOT_ALLOWED, "未知建议种类")  # pragma: no cover


def resolve_for_linked_fact(session: Session, *, fact_id: int) -> None:
    """关联提案被有权当事人确认入图 → submitted 建议 resolved（终局随领域对象）。"""
    rows = list(
        session.scalars(
            select(StewardSuggestion).where(
                StewardSuggestion.linked_fact_id == fact_id,
                StewardSuggestion.status == "submitted",
            )
        )
    )
    now = utcnow()
    for suggestion in rows:
        suggestion.status = "resolved"
        suggestion.revision += 1
        suggestion.updated_at = now
    if rows:
        session.flush()


__all__ = [
    "SUGGESTION_DOMAIN_STATUS",
    "allowed_actions",
    "compute_dedupe_key",
    "default_expires_at",
    "dismiss_suggestion",
    "list_suggestions_page",
    "project_for_job",
    "resolve_for_linked_fact",
    "submit_suggestion",
    "upsert_suggestion",
    "visible_suggestion_or_404",
]
