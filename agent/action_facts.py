"""Action / Observation fact projections for the Reasoner.

Facts only — never chooses the next Tool, never ranks Actions, never forbids
schema-legal proposals. Program validate + loop_safety remain the execution gates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent.epistemic import LAYER_FACT

if TYPE_CHECKING:
    from agent.state import AgentState

# Unified execution-result classes on Observation (compatible add-on field).
OUTCOME_EXECUTED_PROGRESSED = "executed_progressed"
OUTCOME_EXECUTED_NO_PROGRESS = "executed_no_progress"
OUTCOME_NOT_EXECUTED_REJECTED = "not_executed_rejected"
OUTCOME_NOT_EXECUTED_KNOWN_NO_PROGRESS = "not_executed_known_no_progress"
OUTCOME_FAILED = "failed"
OUTCOME_HUMAN_BLOCKED = "human_blocked"

EXECUTION_OUTCOMES = frozenset(
    {
        OUTCOME_EXECUTED_PROGRESSED,
        OUTCOME_EXECUTED_NO_PROGRESS,
        OUTCOME_NOT_EXECUTED_REJECTED,
        OUTCOME_NOT_EXECUTED_KNOWN_NO_PROGRESS,
        OUTCOME_FAILED,
        OUTCOME_HUMAN_BLOCKED,
    }
)

# action_state_facts.status values (World/history facts — not recommendations).
STATUS_PROGRESSED = "progressed"
STATUS_NO_PROGRESS = "no_progress"
STATUS_REJECTED = "rejected"
STATUS_KNOWN_NO_PROGRESS = "known_no_progress"


def classify_observation_outcome(obs: dict[str, Any] | None) -> str | None:
    """Map an Observation dict to a unified execution_outcome. None if not classifiable."""
    if not isinstance(obs, dict):
        return None
    kind = str(obs.get("kind") or "")

    if kind == "repeated_no_progress":
        return OUTCOME_NOT_EXECUTED_KNOWN_NO_PROGRESS
    if kind == "action_rejected":
        return OUTCOME_NOT_EXECUTED_REJECTED
    if kind in {"thrash_re_reason"}:
        # Structural reject pressure — Action was not executed as proposed.
        return OUTCOME_NOT_EXECUTED_REJECTED
    if kind in {
        "insufficient_candidate_material",
        "missing_candidate_material",
        "search_plan_incomplete",
    }:
        return OUTCOME_NOT_EXECUTED_REJECTED

    if kind in {"human_gate", "human_gate_pending"}:
        return OUTCOME_HUMAN_BLOCKED
    if kind == "user_turn" and isinstance(obs.get("pending_human_gate"), dict):
        return OUTCOME_HUMAN_BLOCKED

    if kind == "tool_result" or obs.get("search_executed") is True:
        if "progress" in obs:
            return (
                OUTCOME_EXECUTED_PROGRESSED
                if obs.get("progress") is True
                else OUTCOME_EXECUTED_NO_PROGRESS
            )
        # Non-search tool_result without progress: treat as executed; infer from flags.
        if obs.get("ok") is False or obs.get("error"):
            return OUTCOME_FAILED
        return OUTCOME_EXECUTED_PROGRESSED

    if kind in {"llm_error", "analysis_failed", "tool_failed"}:
        return OUTCOME_FAILED
    if obs.get("ok") is False and obs.get("error"):
        return OUTCOME_FAILED

    return None


def stamp_execution_outcome(obs: dict[str, Any]) -> dict[str, Any]:
    """Attach execution_outcome without removing existing fields."""
    view = dict(obs)
    outcome = classify_observation_outcome(view)
    if outcome is not None:
        view["execution_outcome"] = outcome
        # Explicit inequality helpers for Reasoner (facts, not next_action).
        view["execution_outcome_semantics"] = {
            "layer": LAYER_FACT,
            "action_rejected_means": (
                "Action was rejected by Program/World conditions and was not executed"
            ),
            "repeated_no_progress_means": (
                "Identical Action was not executed because it already ran without "
                "World progress (known_no_progress)"
            ),
            "note": (
                "action_rejected != repeated_no_progress; "
                "neither field chooses the next Tool"
            ),
        }
    return view


def build_action_state_facts(state: "AgentState") -> list[dict[str, Any]]:
    """Historical Action-state facts for Context. No ranking, no next_action."""
    facts: list[dict[str, Any]] = []
    facts.extend(_opened_job_reopen_facts(state))
    facts.extend(_recent_search_no_progress_facts(state))
    facts.extend(_observation_derived_action_facts(state))
    return facts


def _jd_text_present(record: Any) -> bool:
    opened = getattr(record, "opened", None)
    if not isinstance(opened, dict):
        return False
    jd = opened.get("job_description")
    return isinstance(jd, str) and bool(jd.strip())


def _opened_job_reopen_facts(state: "AgentState") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, record in (getattr(state, "jobs", None) or {}).items():
        stage = getattr(record, "stage", None)
        opened = getattr(record, "opened", None)
        if stage not in {"opened", "analyzed", "matched"} and not isinstance(opened, dict):
            continue
        if not _jd_text_present(record):
            continue
        out.append(
            {
                "layer": LAYER_FACT,
                "kind": "action_state_fact",
                "tool_name": "open_job",
                "job_key": str(key),
                "status": STATUS_REJECTED,
                "reason": (
                    "job already opened with JD text in World; "
                    "a further open_job/inspect_job for the same identity is rejected"
                ),
                "identity": {"job_key": str(key), "stage": stage},
            }
        )
    return out[:20]


def _recent_search_no_progress_facts(state: "AgentState") -> list[dict[str, Any]]:
    stats = getattr(getattr(state, "search", None), "stats", None) or {}
    if not isinstance(stats, dict):
        return []
    fp = stats.get("last_action_fingerprint")
    if not fp or stats.get("last_action_progress") is not False:
        return []
    # Fingerprint string is opaque identity for the Reasoner.
    attempted_tool = "search_jobs"
    args: dict[str, Any] = {}
    try:
        import json

        parsed = json.loads(fp) if isinstance(fp, str) else None
        if isinstance(parsed, dict) and parsed.get("tool_name"):
            attempted_tool = str(parsed.get("tool_name"))
            if isinstance(parsed.get("arguments"), dict):
                args = dict(parsed.get("arguments") or {})
    except Exception:
        args = {}
    consecutive = int(stats.get("consecutive_identical_no_progress") or 0)
    # After an executed no-progress run, consecutive >= 1: further identical
    # proposals are known_no_progress (loop_safety will not execute them).
    status = STATUS_KNOWN_NO_PROGRESS if consecutive >= 1 else STATUS_NO_PROGRESS
    return [
        {
            "layer": LAYER_FACT,
            "kind": "action_state_fact",
            "tool_name": attempted_tool,
            "status": status,
            "reason": (
                "last executed Action with this fingerprint produced no World progress "
                "(e.g. search with new_jobs=0)"
            ),
            "identity": {
                "action_fingerprint": fp,
                "arguments": args,
            },
            "last_action_progress": False,
            "consecutive_identical_no_progress": consecutive,
        }
    ]


def _observation_derived_action_facts(state: "AgentState") -> list[dict[str, Any]]:
    """Mirror the latest Observation into action_state_facts when it names an attempt."""
    obs = state.last_raw_observation if isinstance(state.last_raw_observation, dict) else None
    if not obs:
        return []
    outcome = classify_observation_outcome(obs)
    if outcome not in {
        OUTCOME_NOT_EXECUTED_REJECTED,
        OUTCOME_NOT_EXECUTED_KNOWN_NO_PROGRESS,
        OUTCOME_EXECUTED_NO_PROGRESS,
    }:
        return []
    attempted = obs.get("attempted") if isinstance(obs.get("attempted"), dict) else {}
    tool_name = attempted.get("tool_name") or obs.get("tool_name")
    args = attempted.get("arguments") if isinstance(attempted.get("arguments"), dict) else {}
    if outcome == OUTCOME_NOT_EXECUTED_REJECTED:
        status = STATUS_REJECTED
        reason = str(obs.get("error") or obs.get("message") or "action rejected; not executed")
    elif outcome == OUTCOME_NOT_EXECUTED_KNOWN_NO_PROGRESS:
        status = STATUS_KNOWN_NO_PROGRESS
        reason = str(obs.get("message") or "identical action not re-executed (known no progress)")
    else:
        status = STATUS_NO_PROGRESS
        reason = (
            f"tool_result progress=false "
            f"(new_jobs={obs.get('new_jobs')}, duplicate_jobs={obs.get('duplicate_jobs')})"
        )
    job_key = attempted.get("job_key") or args.get("job_key")
    fact = {
        "layer": LAYER_FACT,
        "kind": "action_state_fact",
        "tool_name": tool_name,
        "status": status,
        "reason": reason,
        "observation_kind": obs.get("kind"),
        "execution_outcome": outcome,
        "identity": {
            "job_key": job_key,
            "arguments": args or None,
        },
    }
    return [fact]
