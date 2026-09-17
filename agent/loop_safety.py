"""Generic Agent loop safety: identical Action + no World progress must not spin forever.

Program boundary only — does not choose the next business Tool. Emits Observation
`repeated_no_progress` for the Reasoner. Not a search-count or workflow rule.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.decide import Action
    from agent.state import AgentState

# After this many blocked identical no-progress attempts, fail closed (like reject thrash).
REPEATED_NO_PROGRESS_LIMIT = 8

# Fingerprint-irrelevant Reasoner prose fields.
_IGNORE_ARG_KEYS = frozenset(
    {
        "reason",
        "confidence",
        "rationale",
        "thinking",
        "explanation",
    }
)


def canonicalize_action_arguments(
    tool_name: str | None,
    args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Stable Tool args for fingerprints. search_jobs: omit/empty mode ≡ fresh."""
    raw = args if isinstance(args, dict) else {}
    stable = {
        key: raw[key]
        for key in sorted(raw.keys())
        if key not in _IGNORE_ARG_KEYS
    }
    if tool_name == "search_jobs":
        from platforms.search_contract import normalize_mode

        stable = dict(stable)
        stable["mode"] = normalize_mode(stable.get("mode"))
    return stable


def action_fingerprint(action: "Action") -> str:
    args = action.arguments if isinstance(action.arguments, dict) else {}
    stable_args = canonicalize_action_arguments(action.tool_name, args)
    payload = {
        "action_type": action.action_type,
        "tool_name": action.tool_name,
        "intent": action.intent,
        "job_key": action.job_key,
        "arguments": stable_args,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def world_progress_fingerprint(state: "AgentState") -> str:
    """Structural World digest. Stage / profile / match / JD presence — not browser URL."""
    jobs: list[tuple] = []
    for key in sorted((state.jobs or {}).keys()):
        record = state.jobs[key]
        opened = record.opened if isinstance(record.opened, dict) else {}
        jobs.append(
            (
                key,
                record.stage,
                bool(record.job_profile),
                bool(record.match_result),
                bool(opened.get("job_description")),
                tuple(sorted(str(x) for x in (record.constraint_flags or []))),
            )
        )
    candidate = state.candidate
    profile_ok = bool(
        isinstance(getattr(candidate, "profile", None), dict)
        and (candidate.profile or {}).get("analysis_status") == "ok"
    )
    payload = {
        "jobs": jobs,
        "job_count": len(jobs),
        "listed_count": int((state.search.stats or {}).get("listed_count")
                            or (state.search.stats or {}).get("listed")
                            or 0),
        "opened": int((state.search.stats or {}).get("opened") or 0),
        "understanding_status": getattr(state, "understanding_status", None),
        "profile_ok": profile_ok,
        "profile_version": getattr(candidate, "profile_version", None),
        "pending_job_key": state.pending_job_key,
        "job_search_task_id": state.job_search_task_id,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _search_observation_progress(state: "AgentState") -> bool | None:
    obs = state.last_raw_observation if isinstance(state.last_raw_observation, dict) else {}
    if obs.get("kind") != "tool_result":
        return None
    if "progress" not in obs:
        return None
    return bool(obs.get("progress"))


def note_tool_world_outcome(
    state: "AgentState",
    action: "Action",
    *,
    before_fingerprint: str,
) -> bool:
    """Update no-progress tracking after Reduce. Returns whether World progressed."""
    after = world_progress_fingerprint(state)
    progress = before_fingerprint != after
    search_progress = _search_observation_progress(state)
    if search_progress is not None:
        progress = search_progress

    stats = state.search.stats
    fp = action_fingerprint(action)
    prev_fp = stats.get("last_action_fingerprint")
    if not progress and fp == prev_fp:
        stats["consecutive_identical_no_progress"] = (
            int(stats.get("consecutive_identical_no_progress") or 0) + 1
        )
    elif not progress:
        stats["consecutive_identical_no_progress"] = 1
    else:
        stats["consecutive_identical_no_progress"] = 0
        stats["consecutive_no_progress_blocks"] = 0

    stats["last_action_fingerprint"] = fp
    stats["last_action_progress"] = progress
    return progress


def should_block_repeated_no_progress(state: "AgentState", action: "Action") -> bool:
    """True when the same Tool+args already ran without World progress."""
    if action.action_type != "tool" or not action.tool_name:
        return False
    stats = state.search.stats or {}
    fp = action_fingerprint(action)
    if stats.get("last_action_fingerprint") != fp:
        return False
    if stats.get("last_action_progress") is not False:
        return False
    return int(stats.get("consecutive_identical_no_progress") or 0) >= 1


def build_repeated_no_progress_observation(state: "AgentState", action: "Action") -> dict[str, Any]:
    args = action.arguments if isinstance(action.arguments, dict) else {}
    stable_args = canonicalize_action_arguments(action.tool_name, args)
    stats = state.search.stats or {}
    from agent.validate_action import _unexplored_listed_keys
    from rules.quota import remaining_opens

    unexplored_keys = _unexplored_listed_keys(state, limit=8)
    return {
        "kind": "repeated_no_progress",
        "layer": "observation",
        "search_executed": False,
        "tool_executed": False,
        "progress": False,
        "attempted": {
            "action_type": action.action_type,
            "tool_name": action.tool_name,
            "arguments": stable_args,
            "job_key": action.job_key,
            "intent": action.intent,
        },
        "message": (
            "identical action repeated without World progress; "
            "tool was not executed again — Reasoner must redecide from "
            "current Observation and World facts"
        ),
        "listed_count": int(stats.get("listed_count") or stats.get("listed") or 0),
        "search_executed_count": int(
            stats.get("search_executed_count") or stats.get("searches") or 0
        ),
        "zero_increment_count": int(stats.get("zero_increment_count") or 0),
        "last_search_newly_ingested": stats.get("last_search_newly_ingested"),
        "last_search_duplicates": stats.get("last_search_duplicates"),
        "consecutive_identical_no_progress": int(
            stats.get("consecutive_identical_no_progress") or 0
        ),
        "unexplored_listed_count": len(_unexplored_listed_keys(state, limit=10_000)),
        "unexplored_listed_job_keys": unexplored_keys,
        "remaining_opens": remaining_opens(state),
    }


def bump_no_progress_block(state: "AgentState") -> int:
    stats = state.search.stats
    n = int(stats.get("consecutive_no_progress_blocks") or 0) + 1
    stats["consecutive_no_progress_blocks"] = n
    return n
