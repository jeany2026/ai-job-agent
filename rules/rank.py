"""Deterministic ranking keys from MatchResult. No keyword-based open ordering.

Platform is not a quality signal: the same MatchResult ranks the same on any site.
listed_order then job_key are stable tiebreakers only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.state import JobRecord

RECOMMENDATION_RANK = {
    "yes": 0,
    "weak": 1,
    "insufficient_evidence": 2,
    "no": 3,
}

FIT_RANK = {
    "strong": 0,
    "moderate": 1,
    "weak": 2,
    "none": 3,
    "unknown": 4,
}


def rank_key(record: JobRecord) -> tuple:
    match = record.match_result or {}
    recommendation = match.get("recommendation")
    overall = match.get("overall_fit")
    hard = match.get("hard_requirements_met")
    hard_rank = 0 if hard is True else 1 if hard is None else 2
    return (
        RECOMMENDATION_RANK.get(recommendation, 9),
        FIT_RANK.get(overall, 9),
        hard_rank,
        record.listed_order,
        record.job_key or "",
    )


def rank_records(records: list) -> list:
    ordered = sorted(records, key=rank_key)
    for record in ordered:
        if hasattr(record, "rank_score"):
            record.rank_score = rank_key(record)
    return ordered
