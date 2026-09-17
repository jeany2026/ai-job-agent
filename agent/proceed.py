"""Read-only completeness helpers. Isolated. Not Program gates.

Do not import into validate_action / loop / reasoner_context.
Absence of Verification or CandidateProfile is not a search gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.state import AgentState


def can_search(state: AgentState) -> bool:
    from understanding.schema import has_user_goal

    if has_user_goal(state.understanding or {}):
        return True
    goal = state.goal or {}
    return bool(
        goal.get("target_roles")
        or goal.get("cities")
        or goal.get("focus_areas")
        or goal.get("platforms")
        or goal.get("salary_min") is not None
    )


def can_match(state: AgentState) -> bool:
    from candidate.context import context_has_candidate_evidence
    from candidate.memory import memory_is_usable

    if memory_is_usable(getattr(state.candidate, "memory", None)):
        return True
    return context_has_candidate_evidence(state.candidate_context)
