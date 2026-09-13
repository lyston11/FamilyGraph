"""Platform-level Memory and RAG feature configuration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models.platform_features import PlatformFeatureConfig
from app.utils.timeutil import utcnow

FeatureSource = Literal["environment", "platform", "deployment"]


StewardAssistKind = Literal["candidate", "ranking", "explanation"]


@dataclass(frozen=True)
class PlatformFeatureState:
    memory_enabled: bool
    rag_enabled: bool
    memory_source: FeatureSource
    rag_source: FeatureSource
    # 09-13：平台级 Steward 辅助三开关（candidate/ranking/explanation）
    steward_assist_candidate: bool
    steward_assist_ranking: bool
    steward_assist_explanation: bool
    steward_assist_candidate_source: FeatureSource
    steward_assist_ranking_source: FeatureSource
    steward_assist_explanation_source: FeatureSource
    updated_at: datetime | None


def _steward_assist_env(kind: str) -> bool:
    return bool(getattr(config, f"STEWARD_ASSIST_{kind.upper()}"))


def _environment_state() -> PlatformFeatureState:
    return PlatformFeatureState(
        memory_enabled=config.MEMORY_ENABLED,
        rag_enabled=config.RAG_ENABLED,
        memory_source="environment",
        rag_source="environment",
        steward_assist_candidate=_steward_assist_env("candidate"),
        steward_assist_ranking=_steward_assist_env("ranking"),
        steward_assist_explanation=_steward_assist_env("explanation"),
        steward_assist_candidate_source="environment",
        steward_assist_ranking_source="environment",
        steward_assist_explanation_source="environment",
        updated_at=None,
    )


def _steward_assist_effective(db_value: bool, env_value: bool) -> tuple[bool, FeatureSource]:
    """DB 值 ∧ 环境部署兜底；env 关闭时为部署级 kill-switch（与 memory/rag 同语义）。"""
    if not env_value:
        return False, "deployment"
    return bool(db_value), "platform"


def get_platform_feature_state(db: Session) -> PlatformFeatureState:
    """Read the current state; an absent row preserves legacy env bootstrap semantics."""
    row = db.scalar(select(PlatformFeatureConfig).where(PlatformFeatureConfig.id == 1))
    if row is None:
        return _environment_state()
    candidate = _steward_assist_effective(
        bool(row.steward_assist_candidate), _steward_assist_env("candidate")
    )
    ranking = _steward_assist_effective(
        bool(row.steward_assist_ranking), _steward_assist_env("ranking")
    )
    explanation = _steward_assist_effective(
        bool(row.steward_assist_explanation), _steward_assist_env("explanation")
    )
    return PlatformFeatureState(
        memory_enabled=bool(row.memory_enabled) and config.MEMORY_ENABLED,
        rag_enabled=bool(row.rag_enabled) and config.RAG_ENABLED,
        memory_source="deployment" if not config.MEMORY_ENABLED else "platform",
        rag_source="deployment" if not config.RAG_ENABLED else "platform",
        steward_assist_candidate=candidate[0],
        steward_assist_ranking=ranking[0],
        steward_assist_explanation=explanation[0],
        steward_assist_candidate_source=candidate[1],
        steward_assist_ranking_source=ranking[1],
        steward_assist_explanation_source=explanation[1],
        updated_at=row.updated_at,
    )


def is_memory_enabled(db: Session) -> bool:
    return get_platform_feature_state(db).memory_enabled


def is_rag_enabled(db: Session) -> bool:
    return get_platform_feature_state(db).rag_enabled


def set_platform_feature_state(
    db: Session,
    *,
    memory_enabled: bool,
    rag_enabled: bool,
    steward_assist_candidate: bool | None,
    steward_assist_ranking: bool | None,
    steward_assist_explanation: bool | None,
    system_admin_id: int,
) -> PlatformFeatureState:
    """Persist both explicit platform values in the singleton row.

    Steward 三开关缺省 None = 保留现值（行缺失时以 env 初始化），避免旧客户端
    只写 memory/rag 时静默重置辅助开关（09-13 治理语义）。
    """
    row = db.get(PlatformFeatureConfig, 1)
    now = utcnow()

    def _resolve(kind: str, provided: bool | None) -> bool:
        if provided is not None:
            return provided
        if row is not None:
            return bool(getattr(row, f"steward_assist_{kind}"))
        return _steward_assist_env(kind)

    resolved_candidate = _resolve("candidate", steward_assist_candidate)
    resolved_ranking = _resolve("ranking", steward_assist_ranking)
    resolved_explanation = _resolve("explanation", steward_assist_explanation)
    if row is None:
        row = PlatformFeatureConfig(
            id=1,
            memory_enabled=memory_enabled,
            rag_enabled=rag_enabled,
            steward_assist_candidate=resolved_candidate,
            steward_assist_ranking=resolved_ranking,
            steward_assist_explanation=resolved_explanation,
            updated_at=now,
            updated_by_system_admin_id=system_admin_id,
        )
        db.add(row)
    else:
        row.memory_enabled = memory_enabled
        row.rag_enabled = rag_enabled
        row.steward_assist_candidate = resolved_candidate
        row.steward_assist_ranking = resolved_ranking
        row.steward_assist_explanation = resolved_explanation
        row.updated_at = now
        row.updated_by_system_admin_id = system_admin_id
    db.flush()
    steward_assist_candidate = resolved_candidate
    steward_assist_ranking = resolved_ranking
    steward_assist_explanation = resolved_explanation
    candidate = _steward_assist_effective(
        steward_assist_candidate, _steward_assist_env("candidate")
    )
    ranking = _steward_assist_effective(steward_assist_ranking, _steward_assist_env("ranking"))
    explanation = _steward_assist_effective(
        steward_assist_explanation, _steward_assist_env("explanation")
    )
    return PlatformFeatureState(
        memory_enabled=memory_enabled and config.MEMORY_ENABLED,
        rag_enabled=rag_enabled and config.RAG_ENABLED,
        memory_source="deployment" if not config.MEMORY_ENABLED else "platform",
        rag_source="deployment" if not config.RAG_ENABLED else "platform",
        steward_assist_candidate=candidate[0],
        steward_assist_ranking=ranking[0],
        steward_assist_explanation=explanation[0],
        steward_assist_candidate_source=candidate[1],
        steward_assist_ranking_source=ranking[1],
        steward_assist_explanation_source=explanation[1],
        updated_at=now,
    )


def is_steward_assist_platform_enabled(db: Session, kind: str) -> bool:
    """平台级辅助开关生效值（DB 治理 ∧ env 部署兜底；kind ∈ candidate/ranking/explanation）。"""
    state = get_platform_feature_state(db)
    return bool(getattr(state, f"steward_assist_{kind}"))


__all__ = [
    "PlatformFeatureState",
    "get_platform_feature_state",
    "is_memory_enabled",
    "is_rag_enabled",
    "is_steward_assist_platform_enabled",
    "set_platform_feature_state",
]
