"""Public contracts for the controlled web gateway."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, field_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebPlatformConfigRequest(_Strict):
    enabled: bool = False
    search_provider: str = Field(default="configured", min_length=1, max_length=64)
    search_endpoint: AnyHttpUrl | None = None
    provider_secret: SecretStr | None = None
    allowed_domains: list[str] = Field(default_factory=list, max_length=200)
    denied_domains: list[str] = Field(default_factory=list, max_length=200)
    max_results: int = Field(default=10, ge=1, le=50)
    max_fetch_bytes: int = Field(default=1_000_000, ge=1_024, le=10_000_000)
    max_requests_per_minute: int = Field(default=30, ge=1, le=10_000)
    monthly_budget_cents: int = Field(default=0, ge=0, le=100_000_000)

    @field_validator("allowed_domains", "denied_domains")
    @classmethod
    def validate_domains(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip().lower().rstrip(".") for value in values]
        if any(not value or "/" in value or ":" in value for value in cleaned):
            raise ValueError("域名列表只能包含主机名")
        return cleaned


class WebPlatformConfigOut(_Strict):
    enabled: bool
    search_provider: str
    search_endpoint: str | None
    has_provider_secret: bool
    allowed_domains: list[str]
    denied_domains: list[str]
    max_results: int
    max_fetch_bytes: int
    max_requests_per_minute: int
    monthly_budget_cents: int
    updated_at: datetime


class WebSpaceConfigRequest(_Strict):
    enabled: bool = False
    allowed_use_cases: list[str] = Field(default_factory=list, max_length=10)
    max_results: int = Field(default=10, ge=1, le=50)
    max_fetch_bytes: int = Field(default=1_000_000, ge=1_024, le=10_000_000)
    max_requests_per_minute: int = Field(default=10, ge=1, le=10_000)
    monthly_budget_cents: int = Field(default=0, ge=0, le=100_000_000)

    @field_validator("allowed_use_cases")
    @classmethod
    def validate_use_cases(cls, values: list[str]) -> list[str]:
        allowed = {"research", "fact_check", "citation"}
        if any(value not in allowed for value in values):
            raise ValueError("联网用途无效")
        return list(dict.fromkeys(values))


class WebSpaceConfigOut(_Strict):
    space_id: int
    enabled: bool
    allowed_use_cases: list[str]
    max_results: int
    max_fetch_bytes: int
    max_requests_per_minute: int
    monthly_budget_cents: int
    updated_at: datetime


class WebSearchRequest(_Strict):
    query: str = Field(min_length=1, max_length=2_000)
    use_case: str = Field(default="research", min_length=1, max_length=32)
    limit: int = Field(default=5, ge=1, le=50)


class WebSearchResult(_Strict):
    title: str
    url: str
    snippet: str
    domain: str
    approved_token: str
    expires_at: datetime
    untrusted: bool = True


class WebSearchOut(_Strict):
    results: list[WebSearchResult]
    query_id: str
    provider: str
    expires_at: datetime


class WebFetchRequest(_Strict):
    approved_token: str = Field(min_length=20, max_length=200)


class WebCitationOut(_Strict):
    id: int | None
    url: str
    title: str
    excerpt: str
    content_hash: str
    fetched_at: datetime
    trust: str


class WebFetchOut(_Strict):
    content: str
    bytes_read: int
    citation: WebCitationOut
    untrusted: bool = True
    prompt_instructions: str


class WebUsageOut(_Strict):
    id: int
    tool: str
    provider: str | None
    domain: str | None
    result_count: int
    bytes_read: int
    cost_cents: int
    status: str
    policy_decision: str
    error_code: str | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
