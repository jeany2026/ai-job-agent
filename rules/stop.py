"""Isolated quota math. Not a next-step decisioner.

Loop / reduce / payload must not call should_continue_search or
should_stop_enriching to choose an Action or finish the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rules.quota import can_open, remaining_list_capacity, remaining_llm_calls, remaining_opens

if TYPE_CHECKING:
    from agent.state import AgentState

MAX_SEARCH_PLANS = 5

STOP_MIN_RECOMMEND = "min_recommend_met"
STOP_OPEN_QUOTA = "open_quota"
STOP_LLM_QUOTA = "llm_quota"
STOP_SEARCH_QUOTA = "search_quota"  # World list capacity exhausted (not search-call budget)
STOP_MAX_PLANS = "max_search_plans"
STOP_PLAN_SEARCH_FAILED = "plan_search_failed"
STOP_NO_NEXT_PLAN = "no_next_plan"


def recommended_count(state: AgentState) -> int:
    from agent.state import jobs_in_stage

    return len(jobs_in_stage(state, "recommended"))


def should_stop_enriching(state: AgentState) -> bool:
    """True when listed jobs cannot be opened, or open/LLM quotas are exhausted."""
    from agent.state import in_flight_job, next_job_to_open

    if remaining_llm_calls(state) <= 0:
        return True
    if in_flight_job(state) is not None:
        return False
    if next_job_to_open(state) is None:
        return True
    if not can_open(state):
        return True
    return False


def should_continue_exploring(state: AgentState) -> bool:
    """Quota/plan room remains. Surfacing a job for Human Gate is not a stop."""
    return next_explore_block_reason(state) is None


def next_explore_block_reason(state: AgentState) -> str | None:
    if remaining_opens(state) <= 0:
        return STOP_OPEN_QUOTA
    if remaining_llm_calls(state) <= 0:
        return STOP_LLM_QUOTA
    if remaining_list_capacity(state) <= 0:
        return STOP_SEARCH_QUOTA
    if len(state.search.plans) >= MAX_SEARCH_PLANS:
        return STOP_MAX_PLANS
    return None


def should_continue_search(state: AgentState) -> bool:
    """recommended < min_recommend and search/open/LLM quotas remain."""
    return next_search_block_reason(state) is None


def next_search_block_reason(state: AgentState) -> str | None:
    if recommended_count(state) >= int(state.constraints.min_recommend):
        return STOP_MIN_RECOMMEND
    if remaining_opens(state) <= 0:
        return STOP_OPEN_QUOTA
    if remaining_llm_calls(state) <= 0:
        return STOP_LLM_QUOTA
    if remaining_list_capacity(state) <= 0:
        return STOP_SEARCH_QUOTA
    if len(state.search.plans) >= MAX_SEARCH_PLANS:
        return STOP_MAX_PLANS
    return None
