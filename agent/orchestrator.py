"""Agent Orchestrator: read inputs, run Loop, return State/output.

Does not browse BOSS, does not apply, does not keyword-enrich.
"""

from __future__ import annotations

from typing import Any

from agent.loop import run_loop
from agent.state import SUPPORTED_DATA_SOURCES, AgentState, new_agent_state
from storage.candidate_profile import CandidateProfileStore
from storage.errors import MissingStorageError
from storage.history import HistoryStore
from storage.tracker import AppliedTracker, merge_already_applied
from task.store import JobSearchTaskStore
from tools.registry import build_registry


def run_agent(
    *,
    goal: Any = None,
    message: Any = None,
    resume: str | dict | None = None,
    attachments: list[dict] | None = None,
    candidate_profile: dict | None = None,
    profile_dir: str | None = None,
    candidate_id: str = "cand-1",
    constraints: dict | None = None,
    llm_provider=None,
    data_source: str = "mock",
    registry=None,
    max_steps: int | None = None,
    history_dir: str | None = None,
    tracker_path: str | None = None,
    session_context: dict | None = None,
    on_status=None,
    task_store=None,
    conversation_id: str | None = None,
) -> AgentState:
    """resume= is a compatibility wrap: raw text intake, not pre-labeled resume Evidence."""
    if data_source not in SUPPORTED_DATA_SOURCES:
        raise ValueError(f"unsupported data_source: {data_source}")
    pending = message if message is not None else goal
    tracker = AppliedTracker(tracker_path) if tracker_path else None
    merged = dict(constraints or {})
    merged["already_applied"] = merge_already_applied(merged.get("already_applied"), tracker)
    profile_record = None
    loaded_profile = candidate_profile
    if loaded_profile is None and profile_dir:
        store = CandidateProfileStore(profile_dir)
        if store.exists(candidate_id):
            try:
                profile_record = store.load(candidate_id)
                loaded_profile = profile_record.get("profile")
                if isinstance(loaded_profile, dict):
                    loaded_profile = dict(loaded_profile)
                    loaded_profile["kind"] = "interpretation_projection"
                    loaded_profile.setdefault("derived_from", [
                        item.get("id")
                        for item in (profile_record.get("evidence") or [])
                        if isinstance(item, dict) and item.get("id")
                    ])
            except MissingStorageError:
                profile_record = None
                loaded_profile = None
    state = new_agent_state(
        resume=resume,
        goal_input=pending,
        constraints=merged,
        data_source=data_source,
        candidate_id=candidate_id,
        attachments=attachments,
        candidate_profile=loaded_profile,
        profile_record=profile_record,
        profile_dir=profile_dir,
        session_context=session_context,
        conversation_id=conversation_id,
    )
    tool_registry = registry or build_registry(data_source=data_source)
    kwargs = {"llm_provider": llm_provider}
    if max_steps is not None:
        kwargs["max_steps"] = max_steps
    if on_status is not None:
        kwargs["on_status"] = on_status
    store = task_store if task_store is not None else JobSearchTaskStore()
    kwargs["task_store"] = store
    kwargs["conversation_id"] = conversation_id
    from agent.world_restore import restore_continuity_world

    # Persistence → Live World (identity continuity only). Not a business pipeline step.
    world_restore = restore_continuity_world(state, task_store=store)
    kwargs["world_restore"] = world_restore
    state = run_loop(state, tool_registry, **kwargs)
    if tracker is not None:
        tracker.record_applied(
            record.job_key
            for record in state.jobs.values()
            if record.exclude_reason == "already_applied"
        )
    if history_dir:
        HistoryStore(history_dir).save_run(state)
    return state
