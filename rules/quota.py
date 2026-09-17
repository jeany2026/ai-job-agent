"""Numeric quotas from AgentState.constraints. No keyword enrich.

Search *execution* is not gated by listed_count. listed / list capacity only
limits how many unique jobs World may still ingest. Zero-increment searches
are Observation + loop-safety concerns, not a search-count quota.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.state import AgentState


def remaining_opens(state: AgentState) -> int:
    opened = int(state.search.stats.get("opened") or 0)
    return max(0, int(state.constraints.max_open_jd) - opened)


def remaining_llm_calls(state: AgentState) -> int:
    used = int(state.search.stats.get("llm_calls") or 0)
    return max(0, int(state.constraints.max_llm_calls) - used)


def listed_count(state: AgentState) -> int:
    stats = state.search.stats or {}
    if "listed_count" in stats:
        return int(stats.get("listed_count") or 0)
    return int(stats.get("listed") or 0)


def remaining_list_capacity(state: AgentState) -> int:
    """How many more unique jobs World can still ingest (not a search-call budget)."""
    return max(0, int(state.constraints.max_search_results) - listed_count(state))


def remaining_search_slots(state: AgentState) -> int:
    """Compat alias for remaining_list_capacity. Not 'how many searches left'."""
    return remaining_list_capacity(state)


def can_open(state: AgentState) -> bool:
    """Browser open is not an LLM tool; do not gate it on remaining_llm_calls."""
    return remaining_opens(state) > 0


def note_llm_call(state: AgentState) -> None:
    state.search.stats["llm_calls"] = int(state.search.stats.get("llm_calls") or 0) + 1


def note_open(state: AgentState) -> None:
    state.search.stats["opened"] = int(state.search.stats.get("opened") or 0) + 1


def note_search(
    state: AgentState,
    *,
    newly_ingested: int,
    duplicates: int,
    capacity_skipped: int = 0,
) -> None:
    """Record one search execution and its World ingest outcome.

    search_executed_count always increments. listed_count only grows by newly_ingested.
    """
    stats = state.search.stats
    newly = max(0, int(newly_ingested))
    dups = max(0, int(duplicates))
    skipped = max(0, int(capacity_skipped))

    executed = int(stats.get("search_executed_count") or stats.get("searches") or 0) + 1
    stats["search_executed_count"] = executed
    stats["searches"] = executed  # backward-compatible alias

    listed = listed_count(state) + newly
    stats["listed_count"] = listed
    stats["listed"] = listed  # backward-compatible alias

    stats["newly_ingested_total"] = int(stats.get("newly_ingested_total") or 0) + newly
    stats["duplicate_total"] = int(stats.get("duplicate_total") or 0) + dups
    if newly == 0:
        stats["zero_increment_count"] = int(stats.get("zero_increment_count") or 0) + 1

    stats["last_search_newly_ingested"] = newly
    stats["last_search_duplicates"] = dups
    stats["last_search_capacity_skipped"] = skipped
    stats["last_search_progress"] = newly > 0
