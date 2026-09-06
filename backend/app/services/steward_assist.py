"""Steward 模型辅助层（09-06 子任务 B；候选/排序/解释，child run 审计）。

兑现 09-01 决策记录的约定："未来模型只能辅助候选、排序和解释"、"模型 child
run/context 审计另立任务"。三类辅助点全部默认关闭（平台 config AND 空间
settings 行），关闭时零调用、行为与确定性基线逐字节等价。

红线（R3，全部有测试）：
- 确定性内核（dirty 重算/冲突检测/资格矩阵/checkpoint 幂等）不因本层改变；
  本层任何失败只留审计行与日志，绝不拖垮流水线（degrade-to-template）。
- 候选只落内部池 steward_llm_candidates，不经过确定性矩阵绝不进卡片、
  绝不产生任何正式写入（SourceFact/成员/可见权/申请）。
- 排序只改呈现顺序（presentation_rank），必须通过"严格排列"校验，
  绝不改变卡片集合成员。
- 解释只写 reason_text_llm，只允许复述卡内已确认事实。

child run 审计（B2）：每次模型调用一行 StewardModelCall——space/job/
policy_version/assist_kind/provider/model/prompt sha256 摘要与长度/token
usage/status/latency；prompt 明文永不落库。per (job_id, assist_kind, seq)
唯一：job crash 重试据此幂等跳过，不重复花费 token（B4）。

egress（B1）：进程内直连 Provider（resolve_runtime 唯一解密出口），
不经 sidecar、不伪造 AgentRun；api 容器仍是唯一 egress 点。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.models.agent_provider import AgentSpaceProviderSetting
from app.models.steward import ActionCard, StewardJob, StewardLlmCandidate, StewardModelCall
from app.services import action_cards, agent_provider
from app.utils import timeutil

logger = logging.getLogger(__name__)

ASSIST_KINDS: tuple[str, ...] = ("candidate", "ranking", "explanation")

# 与 provider_proxy._API_PATHS 对齐（egress 路径单点语义）
_API_PATHS = {
    "openai-completions": "/chat/completions",
    "openai-responses": "/responses",
}

_EXPLAIN_MAX_CHARS = 500

Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


def _post_json(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    """默认 transport：httpx 同步 POST（测试 monkeypatch 本函数注入 fake）。"""
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise ValueError("provider response is not a JSON object")
    return data


# ---- 开关与预算 ----


def _platform_flag(kind: str) -> bool:
    return bool(getattr(config, f"STEWARD_ASSIST_{kind.upper()}"))


def _space_flag(db: Session, space_id: int, kind: str) -> bool:
    row = db.scalar(
        select(AgentSpaceProviderSetting).where(
            AgentSpaceProviderSetting.space_id == space_id,
            AgentSpaceProviderSetting.agent_kind == agent_provider.AGENT_KIND_STEWARD,
        )
    )
    if row is None or not row.enabled:
        return False
    return bool(getattr(row, f"assist_{kind}"))


def assist_enabled(db: Session, space_id: int, kind: str) -> bool:
    """有效开关 = 平台级 AND 空间级（steward 维度显式行）；默认全关。"""
    if kind not in ASSIST_KINDS:
        raise ValueError(f"unknown assist kind: {kind}")
    return _platform_flag(kind) and _space_flag(db, space_id, kind)


def _budget_state(db: Session, job_id: int) -> tuple[int, int]:
    calls, tokens = db.execute(
        select(func.count(StewardModelCall.id), func.coalesce(func.sum(StewardModelCall.total_tokens), 0)).where(  # type: ignore[arg-type]
            StewardModelCall.job_id == job_id,
            StewardModelCall.status == "succeeded",
        )
    ).one()
    return int(calls), int(tokens)


# ---- child run 审计 ----


def _record_call(
    db: Session,
    *,
    job: StewardJob,
    assist_kind: str,
    seq: int,
    provider_id: int | None,
    model: str | None,
    prompt: str,
    completion: str,
    usage: dict[str, int] | None,
    status: str,
    error_code: str | None,
    latency_ms: int,
) -> StewardModelCall:
    row = StewardModelCall(
        space_id=job.space_id,
        job_id=job.id,
        policy_version=job.policy_version,
        assist_kind=assist_kind,
        provider_id=provider_id,
        model=model,
        prompt_digest=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        prompt_chars=len(prompt),
        completion_chars=len(completion or ""),
        prompt_tokens=usage.get("prompt_tokens") if usage else None,
        completion_tokens=usage.get("completion_tokens") if usage else None,
        total_tokens=usage.get("total_tokens") if usage else None,
        status=status,
        error_code=error_code,
        latency_ms=latency_ms,
        seq=seq,
        created_at=timeutil.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _seq_done(db: Session, job_id: int, assist_kind: str, seq: int) -> bool:
    """该 (job, kind, seq) 审计行已存在 → 本轮跳过（同事务重入/部分重试幂等）。"""
    return (
        db.scalar(
            select(StewardModelCall.id).where(
                StewardModelCall.job_id == job_id,
                StewardModelCall.assist_kind == assist_kind,
                StewardModelCall.seq == seq,
            )
        )
        is not None
    )


def _parse_response(api: str, data: dict[str, Any]) -> tuple[str, dict[str, int] | None]:
    """防御式解析两种协议的文本与 usage；解析失败抛错由调用方记 failed。"""
    usage: dict[str, int] | None = None
    raw_usage = data.get("usage") or {}
    if api == "openai-responses":
        text_parts: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text_parts.append(str(part.get("text") or ""))
        text = "".join(text_parts)
        if "input_tokens" in raw_usage or "output_tokens" in raw_usage:
            usage = {
                "prompt_tokens": int(raw_usage.get("input_tokens") or 0),
                "completion_tokens": int(raw_usage.get("output_tokens") or 0),
                "total_tokens": int(raw_usage.get("total_tokens") or 0),
            }
        return text, usage
    choices = data.get("choices") or []
    text = str((choices[0].get("message") or {}).get("content") or "") if choices else ""
    if "total_tokens" in raw_usage or "prompt_tokens" in raw_usage:
        usage = {
            "prompt_tokens": int(raw_usage.get("prompt_tokens") or 0),
            "completion_tokens": int(raw_usage.get("completion_tokens") or 0),
            "total_tokens": int(raw_usage.get("total_tokens") or 0),
        }
    return text, usage


def _call_model_impl(
    db: Session,
    *,
    job: StewardJob,
    assist_kind: str,
    seq: int,
    system: str,
    user: str,
    max_out_tokens: int,
    transport: Transport | None,
) -> tuple[str | None, StewardModelCall | None]:
    if _seq_done(db, job.id, assist_kind, seq):
        return None, None
    calls_used, tokens_used = _budget_state(db, job.id)
    if calls_used >= config.STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB or (
        tokens_used >= config.STEWARD_ASSIST_MAX_TOKENS_PER_JOB
    ):
        row = _record_call(
            db,
            job=job,
            assist_kind=assist_kind,
            seq=seq,
            provider_id=None,
            model=None,
            prompt=f"{system}\n{user}",
            completion="",
            usage=None,
            status="skipped",
            error_code="budget_exhausted",
            latency_ms=0,
        )
        return None, row

    runtime = agent_provider.resolve_runtime(db, job.space_id, agent_kind=agent_provider.AGENT_KIND_STEWARD)
    prompt = f"{system}\n{user}"
    if runtime is None or not (runtime.base_url or "").rstrip("/"):
        row = _record_call(
            db,
            job=job,
            assist_kind=assist_kind,
            seq=seq,
            provider_id=None,
            model=None,
            prompt=prompt,
            completion="",
            usage=None,
            status="degraded",
            error_code="provider_unavailable",
            latency_ms=0,
        )
        return None, row

    api_path = _API_PATHS.get(runtime.api)
    if api_path is None:
        row = _record_call(
            db,
            job=job,
            assist_kind=assist_kind,
            seq=seq,
            provider_id=runtime.provider_id,
            model=runtime.model,
            prompt=prompt,
            completion="",
            usage=None,
            status="degraded",
            error_code="provider_api_unsupported",
            latency_ms=0,
        )
        return None, row

    if runtime.api == "openai-responses":
        payload: dict[str, Any] = {
            "model": runtime.model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_output_tokens": max_out_tokens,
            "stream": False,
        }
    else:
        payload = {
            "model": runtime.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_out_tokens,
            "stream": False,
        }
    url = f"{runtime.base_url.rstrip('/')}{api_path}"
    headers = {"Authorization": f"Bearer {runtime.api_key}"} if runtime.api_key else {}
    started = time.monotonic()
    try:
        data = (transport or _post_json)(
            url, headers, payload, config.STEWARD_ASSIST_TIMEOUT_SECONDS
        )
        text, usage = _parse_response(runtime.api, data)
    except Exception as exc:  # noqa: BLE001 — 任何 transport/解析失败只记 failed 行
        row = _record_call(
            db,
            job=job,
            assist_kind=assist_kind,
            seq=seq,
            provider_id=runtime.provider_id,
            model=runtime.model,
            prompt=prompt,
            completion="",
            usage=None,
            status="failed",
            error_code=type(exc).__name__[:64],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return None, row

    row = _record_call(
        db,
        job=job,
        assist_kind=assist_kind,
        seq=seq,
        provider_id=runtime.provider_id,
        model=runtime.model,
        prompt=prompt,
        completion=text,
        usage=usage,
        status="succeeded",
        error_code=None,
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    return text, row


# ---- 三个辅助点 ----


def _extract_json_array(text: str) -> list[Any] | None:
    """从模型文本提取 JSON 数组（容忍 ```json 围栏）；失败返回 None。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    try:
        parsed = json.loads(stripped.strip())
    except json.JSONDecodeError:
        start, end = stripped.find("["), stripped.rfind("]")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, list) else None


def maybe_generate_candidates(
    db: Session,
    *,
    space_id: int,
    job: StewardJob,
    facts_brief: list[dict[str, Any]],
    visible: set[int],
    transport: Transport | None = None,
) -> None:
    """候选生成：LLM 提议只落内部池；非法项丢弃；绝不触发任何卡片/正式写入。"""
    if not assist_enabled(db, space_id, "candidate") or not facts_brief:
        return
    system = (
        "你是家庭空间管家助手。只基于给定的已确认事实清单，提出可能的关系候选建议"
        "（如可补充的称谓/分支假设）。输出 JSON 数组，每项为"
        ' {"kind": 关系种类, "subject_user_id": 整数, "object_user_id": 整数,'
        ' "rationale": 简短理由}。subject/object 必须来自清单中出现的用户 id，'
        "绝不编造其他 id；不得断言未确认的事实。"
    )
    user = json.dumps(facts_brief, ensure_ascii=False)
    text, _row = _call_model_impl(
        db,
        job=job,
        assist_kind="candidate",
        seq=1,
        system=system,
        user=user,
        max_out_tokens=2000,
        transport=transport,
    )
    if not text:
        return
    items = _extract_json_array(text)
    if items is None:
        logger.warning("steward assist candidate: unparseable output; dropped")
        return
    now = timeutil.utcnow()
    accepted = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()[:48]
        subject_id, object_id = item.get("subject_user_id"), item.get("object_user_id")
        rationale = str(item.get("rationale") or "").strip()[:500]
        if not kind or not isinstance(subject_id, int) or isinstance(subject_id, bool):
            continue
        if not isinstance(object_id, int) or isinstance(object_id, bool):
            continue
        if subject_id not in visible or object_id not in visible or subject_id == object_id:
            continue
        payload = {
            "kind": kind,
            "subject_user_id": subject_id,
            "object_user_id": object_id,
            "rationale": rationale,
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        exists = db.scalar(
            select(StewardLlmCandidate.id).where(
                StewardLlmCandidate.space_id == space_id,
                StewardLlmCandidate.candidate_digest == digest,
            )
        )
        if exists is not None:
            continue
        db.add(
            StewardLlmCandidate(
                space_id=space_id,
                job_id=job.id,
                candidate_kind=kind,
                payload_json=payload,
                candidate_digest=digest,
                status="proposed",
                created_at=now,
            )
        )
        accepted += 1
    db.flush()
    logger.info("steward assist candidate: %d accepted / %d proposed", accepted, len(items))


def maybe_rank_cards(
    db: Session,
    *,
    job: StewardJob,
    cards: list[ActionCard],
    transport: Transport | None = None,
) -> None:
    """排序：只对给定卡集合的呈现顺序重排；非法输出一律丢弃（AC-2）。"""
    space_id = job.space_id
    if not assist_enabled(db, space_id, "ranking"):
        return
    targets = [c for c in cards if c.state in ("pending", "viewed")][
        : config.STEWARD_ASSIST_MAX_CARDS_PER_JOB
    ]
    if len(targets) < 2:
        return
    ids = [int(c.id) for c in targets]
    system = (
        "你是家庭空间管家助手。对给定的推荐卡按对用户的实际有用程度排序。"
        "输出 JSON 数组：仅包含给定 card id 的整数，每个 id 恰好出现一次，"
        "不得新增、遗漏或重复。"
    )
    user = json.dumps(
        [{"card_id": int(c.id), "kind": c.kind, "reason": c.reason_text} for c in targets],
        ensure_ascii=False,
    )
    text, row = _call_model_impl(
        db,
        job=job,
        assist_kind="ranking",
        seq=1,
        system=system,
        user=user,
        max_out_tokens=1000,
        transport=transport,
    )
    if not text:
        return
    parsed = _extract_json_array(text)
    valid = (
        parsed is not None
        and len(parsed) == len(ids)
        and all(isinstance(v, int) and not isinstance(v, bool) for v in parsed)
        and set(parsed) == set(ids)
    )
    if not valid or parsed is None:
        # 非严格排列：绝不应用（AC-2）；审计行标注 degraded
        if row is not None:
            row.status = "degraded"
            row.error_code = "invalid_permutation"
        logger.warning("steward assist ranking: invalid permutation; ignored")
        return
    for rank_value, card_id in enumerate(parsed, 1):
        card = next(c for c in targets if int(c.id) == card_id)
        card.presentation_rank = rank_value
    db.flush()


def maybe_explain_cards(
    db: Session,
    *,
    job: StewardJob,
    cards: list[ActionCard],
    transport: Transport | None = None,
) -> None:
    """解释：只复述卡内已确认事实；失败保持 NULL（模板兜底）。"""
    if not assist_enabled(db, job.space_id, "explanation"):
        return
    targets = [
        c
        for c in cards
        if c.state in ("pending", "viewed") and not c.reason_text_llm
    ][: config.STEWARD_ASSIST_MAX_CARDS_PER_JOB]
    if not targets:
        return
    system = (
        "你是家庭空间管家助手。把给定推荐卡的结构化信息改写成一段面向普通用户的"
        "自然语言解释（为什么推荐、隐私影响是什么）。只允许复述给定的已确认事实，"
        "绝不编造或推断新事实；输出纯文本，不超过 300 字。"
    )
    for card in targets:
        user = json.dumps(
            {
                "card_id": int(card.id),
                "kind": card.kind,
                "reason": card.reason_text,
                "privacy_effect": card.privacy_effect,
                "proposed_action": card.proposed_action_json,
                "evidence_fact_types": [
                    f.get("type") for f in card.evidence_json.get("facts", []) if isinstance(f, dict)
                ],
            },
            ensure_ascii=False,
        )
        text, _row = _call_model_impl(
            db,
            job=job,
            assist_kind="explanation",
            seq=int(card.id),
            system=system,
            user=user,
            max_out_tokens=800,
            transport=transport,
        )
        if text and text.strip():
            card.reason_text_llm = text.strip()[:_EXPLAIN_MAX_CHARS]
    db.flush()


def run_assists(
    db: Session,
    *,
    job: StewardJob,
    facts_brief: list[dict[str, Any]],
    visible: set[int],
    cards: list[ActionCard],
    transport: Transport | None = None,
) -> None:
    """编排三个辅助点；单点异常经 SAVEPOINT 隔离回滚，绝不外抛、绝不波及
    主流水线已写入的确定性结果（AC-5）。"""
    assists = (
        (
            "candidate",
            lambda: maybe_generate_candidates(
                db,
                space_id=job.space_id,
                job=job,
                facts_brief=facts_brief,
                visible=visible,
                transport=transport,
            ),
        ),
        ("ranking", lambda: maybe_rank_cards(db, job=job, cards=cards, transport=transport)),
        (
            "explanation",
            lambda: maybe_explain_cards(db, job=job, cards=cards, transport=transport),
        ),
    )
    for name, runner in assists:
        try:
            with db.begin_nested():
                runner()
        except Exception:  # noqa: BLE001 — 单点失败只记日志，SAVEPOINT 已局部回滚
            logger.exception("steward assist %s failed; degraded to deterministic baseline", name)


__all__ = [
    "ASSIST_KINDS",
    "assist_enabled",
    "maybe_explain_cards",
    "maybe_generate_candidates",
    "maybe_rank_cards",
    "run_assists",
]
