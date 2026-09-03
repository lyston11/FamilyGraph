from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FamilyRecommendationItem(BaseModel):
    category: str
    target_user_id: int = Field(gt=0)
    display: dict[str, Any]
    reason_code: str
    term: str | None = None
    concept_code: str | None = None
    path_class: str | None = None
    path_summary: list[str] | None = None
    proposed_fact_type: str | None = None


class FamilyRecommendationsOut(BaseModel):
    space_id: int
    view_status: str
    view_version: int
    generated_from_view_version: int
    items: list[FamilyRecommendationItem]
    truncated: bool


class FamilyRecommendationDismissIn(BaseModel):
    space_id: int = Field(gt=0)
    target_user_id: int = Field(gt=0)
    category: str
