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


@dataclass(frozen=True)
class PlatformFeatureState:
    memory_enabled: bool
    rag_enabled: bool
    memory_source: FeatureSource
    rag_source: FeatureSource
    updated_at: datetime | None


def _environment_state() -> PlatformFeatureState:
    return PlatformFeatureState(
        memory_enabled=config.MEMORY_ENABLED,
        rag_enabled=config.RAG_ENABLED,
        memory_source="environment",
        rag_source="environment",
        updated_at=None,
    )


def get_platform_feature_state(db: Session) -> PlatformFeatureState:
    """Read the current state; an absent row preserves legacy env bootstrap semantics."""
    row = db.scalar(select(PlatformFeatureConfig).where(PlatformFeatureConfig.id == 1))
    if row is None:
        return _environment_state()
    return PlatformFeatureState(
        memory_enabled=bool(row.memory_enabled) and config.MEMORY_ENABLED,
        rag_enabled=bool(row.rag_enabled) and config.RAG_ENABLED,
        memory_source="deployment" if not config.MEMORY_ENABLED else "platform",
        rag_source="deployment" if not config.RAG_ENABLED else "platform",
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
    system_admin_id: int,
) -> PlatformFeatureState:
    """Persist both explicit platform values in the singleton row."""
    row = db.get(PlatformFeatureConfig, 1)
    now = utcnow()
    if row is None:
        row = PlatformFeatureConfig(
            id=1,
            memory_enabled=memory_enabled,
            rag_enabled=rag_enabled,
            updated_at=now,
            updated_by_system_admin_id=system_admin_id,
        )
        db.add(row)
    else:
        row.memory_enabled = memory_enabled
        row.rag_enabled = rag_enabled
        row.updated_at = now
        row.updated_by_system_admin_id = system_admin_id
    db.flush()
    return PlatformFeatureState(
        memory_enabled=memory_enabled and config.MEMORY_ENABLED,
        rag_enabled=rag_enabled and config.RAG_ENABLED,
        memory_source="deployment" if not config.MEMORY_ENABLED else "platform",
        rag_source="deployment" if not config.RAG_ENABLED else "platform",
        updated_at=now,
    )


__all__ = [
    "PlatformFeatureState",
    "get_platform_feature_state",
    "is_memory_enabled",
    "is_rag_enabled",
    "set_platform_feature_state",
]
