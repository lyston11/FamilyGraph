"""Deterministic lexical query planning for memory RAG (workstream B).

The planner turns one natural-language question into a bounded set of FTS
terms, an exact phrase and a degradation record.  It is a versioned lexical
contract, not semantic understanding: alias expansion comes only from the
explicit table below, and nothing here widens authorization (eligibility is
always re-applied downstream in ``memory_rag.search_rag``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

QUERY_PLAN_VERSION = "lex-v1"

MAX_QUERY_CHARS = 500
MAX_TERMS = 8
# Terms shorter than this cannot trigram-match; they take the bounded
# short-word fallback path instead of the FTS path.
FTS_MIN_CHARS = 3

# Versioned domain alias table (v1).  Keys are normalized exact words; values
# are additional search terms.  This is a closed, reviewable list — never a
# model call and never per-fixture hardcoding.
ALIAS_TABLE_V1: dict[str, tuple[str, ...]] = {
    "过年": ("春节",),
    "聚会": ("聚餐",),
}

# Interrogative / function words that must never be the sole hit basis.
_QUESTION_STOPWORDS = frozenset(
    {
        "哪里",
        "什么",
        "怎么",
        "怎样",
        "怎么样",
        "什么时候",
        "多少",
        "哪个",
        "哪些",
        "谁",
        "为什么",
        "请问",
        "the",
        "a",
        "an",
        "of",
        "is",
        "are",
        "what",
        "where",
        "when",
        "how",
        "who",
    }
)

_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


@dataclass(frozen=True)
class QueryPlan:
    """Bounded, loggable query plan (no raw text, no person names in logs)."""

    version: str
    normalized_query: str
    phrase: str | None
    fts_terms: tuple[str, ...]
    fallback_terms: tuple[str, ...]
    degradation: tuple[str, ...] = field(default=())
    term_count: int = 0

    def log_summary(self) -> dict[str, object]:
        """Version/count/duration shape only; raw question text stays out."""
        return {
            "plan_version": self.version,
            "term_count": self.term_count,
            "fts_terms": len(self.fts_terms),
            "fallback_terms": len(self.fallback_terms),
            "degradation": list(self.degradation),
        }


def normalize_query(raw: str) -> str:
    """Unicode NFKC, collapse whitespace/punctuation into single spaces."""
    normalized = unicodedata.normalize("NFKC", raw)
    return " ".join(_PUNCT_RE.split(normalized)).strip()


def _segment_terms(normalized: str) -> list[str]:
    """Split normalized text into candidate terms.

    CJK runs are further split into overlapping 2-character windows plus the
    full run when short enough; latin/digit runs become lowercase words.
    """
    terms: list[str] = []
    for token in normalized.split():
        if not token:
            continue
        if token.isascii() and token.isalnum():
            if len(token) >= 2 and token not in _QUESTION_STOPWORDS:
                terms.append(token)
            continue
        # CJK (or mixed CJK) run.
        if token not in _QUESTION_STOPWORDS:
            terms.append(token)
        if len(token) > 2:
            for pos in range(len(token) - 1):
                bigram = token[pos : pos + 2]
                if bigram not in _QUESTION_STOPWORDS:
                    terms.append(bigram)
    return terms


def plan_query(raw: str) -> QueryPlan:
    """Build the bounded query plan; never raises on odd input."""
    degradation: list[str] = []
    normalized = normalize_query(raw or "")
    if not normalized:
        return QueryPlan(
            version=QUERY_PLAN_VERSION,
            normalized_query="",
            phrase=None,
            fts_terms=(),
            fallback_terms=(),
            degradation=("empty_query",),
        )
    truncated = normalized[:MAX_QUERY_CHARS]
    if len(normalized) > MAX_QUERY_CHARS:
        degradation.append("query_truncated")

    # Exact phrase keeps whole-question regression for FTS trigram matching.
    phrase = truncated if " " not in truncated and len(truncated) >= FTS_MIN_CHARS else None

    candidates: list[str] = []
    seen: set[str] = set()
    for term in _segment_terms(truncated):
        if term in seen:
            continue
        seen.add(term)
        candidates.append(term)
        # Controlled alias expansion: each alias adds one more bounded term.
        for alias in ALIAS_TABLE_V1.get(term, ()):
            if alias not in seen:
                seen.add(alias)
                candidates.append(alias)

    fts_terms = [t for t in candidates if len(t) >= FTS_MIN_CHARS]
    fallback_terms = [t for t in candidates if FTS_MIN_CHARS > len(t) >= 2]
    if len(candidates) > MAX_TERMS:
        degradation.append("term_cap_applied")
    fts_terms = fts_terms[:MAX_TERMS]
    fallback_terms = fallback_terms[:MAX_TERMS]
    if not fts_terms and not fallback_terms:
        degradation.append("no_usable_terms")
    return QueryPlan(
        version=QUERY_PLAN_VERSION,
        normalized_query=truncated,
        phrase=phrase,
        fts_terms=tuple(fts_terms),
        fallback_terms=tuple(fallback_terms),
        degradation=tuple(degradation),
        term_count=len(fts_terms) + len(fallback_terms),
    )


__all__ = [
    "ALIAS_TABLE_V1",
    "FTS_MIN_CHARS",
    "MAX_QUERY_CHARS",
    "MAX_TERMS",
    "QUERY_PLAN_VERSION",
    "QueryPlan",
    "normalize_query",
    "plan_query",
]
