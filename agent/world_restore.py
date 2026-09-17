"""Restore Live World continuity from Persistence.

Identity / session continuity only. Never chooses the next business Action.
Never auto open / analyze / match / surface / search.
"""

from __future__ import annotations

import copy
from typing import Any

from agent.human_gate_continuity import (
    human_gate_snapshot_from_session,
    pending_human_gate_from_session,
    restore_world_from_human_gate_snapshot,
)
from agent.state import DECISION_SURFACED, AgentState, JobRecord, note_job_decision
from conversation.job_context import (
    JobContext,
    get_job_context,
    hydrate_job_record,
    job_contexts_from_session,
)
from task.schema import JobSearchTask


def restore_continuity_world(state: AgentState, task_store: Any = None) -> dict[str, Any]:
    """Restore continuity into live World before the Reasoner runs.

    Priority:
    1. Pending Human Gate World snapshot (same Task after login/CAPTCHA pause)
    2. Exactly one WAITING_USER JobSearchTask current job

    Returns a report for Observation annotation. Does not invoke Tools.
    """
    report: dict[str, Any] = {
        "restored_job_keys": [],
        "bound_task_id": None,
        "reason": "no_restore",
        "waiting_task_count": 0,
    }
    conversation_id = (state.conversation_id or "").strip() or None
    _refresh_active_tasks(state, task_store, conversation_id)

    pending = pending_human_gate_from_session(state.session_context)
    snapshot = human_gate_snapshot_from_session(state.session_context)
    if pending is not None and snapshot is not None:
        report.update(restore_world_from_human_gate_snapshot(state, snapshot))
        _refresh_active_tasks(state, task_store, conversation_id)
        report["pending_human_gate"] = {
            "reason": pending.get("reason"),
            "source": pending.get("source"),
        }
        return report

    if task_store is None or not conversation_id:
        return report

    active = list(task_store.list_active(conversation_id) or [])
    waiting = [task for task in active if task.task_status == "WAITING_USER"]
    report["waiting_task_count"] = len(waiting)
    _refresh_active_tasks(state, task_store, conversation_id, active=active)

    if len(waiting) == 0:
        report["reason"] = "no_waiting_task"
        return report
    if len(waiting) > 1:
        report["reason"] = "ambiguous_waiting_tasks"
        return report

    task = waiting[0]
    # Do NOT bind_task here: choosing the current work unit is Reasoner's decision.
    # Only restore the continuity Job entity into Live World.
    current_id = (task.current_job_context_id or "").strip()
    if not current_id:
        report["reason"] = "waiting_without_current_job"
        return report

    ctx = get_job_context(job_contexts_from_session(state.session_context), current_id)
    if ctx is None:
        progress = task.job_progress(current_id)
        if progress is None:
            report["reason"] = "persistence_job_missing"
            return report
        record = _record_from_progress(state, progress)
    else:
        record = restore_job_record(state, ctx, task=task)

    report["restored_job_keys"] = [record.job_key]
    report["bound_task_id"] = None
    report["continuity_task_id"] = task.task_id
    report["reason"] = "waiting_user_continuity"
    return report


def restore_job_record(state: AgentState, ctx: JobContext, *, task: JobSearchTask | None = None) -> JobRecord:
    """Install one persisted Job into live World. Interpretations stay restored/unverified.

    Does not upgrade Live stage to analyzed/matched from historical match_result/job_profile.
    """
    from agent.epistemic import observed_stage_from_record_fields, stamp_restored

    record = hydrate_job_record(state, ctx)
    if isinstance(ctx.match_result, dict):
        record.match_result = stamp_restored(copy.deepcopy(ctx.match_result), source="persistence")
    if isinstance(record.job_profile, dict):
        record.job_profile = stamp_restored(record.job_profile, source="persistence")
    if isinstance(record.interpret_result, dict):
        record.interpret_result = stamp_restored(record.interpret_result, source="persistence")
    record.stage = observed_stage_from_record_fields(
        opened=record.opened,
        listed=record.listed,
        persisted_stage=ctx.stage,
    )
    if task is not None:
        progress = task.job_progress(ctx.context_id) or task.job_progress(ctx.job_key)
        if progress is not None and progress.status in {"SURFACED", "WAITING_USER"}:
            if not any(isinstance(item, dict) and item.get("kind") == DECISION_SURFACED for item in record.decisions):
                note_job_decision(
                    record,
                    kind=DECISION_SURFACED,
                    source="world_restore",
                    payload={"restored": True, "provenance": "restored", "verified": False},
                )
    return record


def history_job_entries(state: AgentState) -> list[dict[str, Any]]:
    """Dormant persistence index for Reasoner. Not live World objects."""
    session = state.session_context if isinstance(state.session_context, dict) else {}
    live = set(state.jobs.keys())
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in session.get("job_contexts") or []:
        ctx = raw if isinstance(raw, JobContext) else None
        if ctx is None:
            from conversation.job_context import job_context_from_dict

            ctx = job_context_from_dict(raw)
        if ctx is None or ctx.job_key in seen:
            continue
        seen.add(ctx.job_key)
        entries.append(
            {
                "kind": "history_index",
                "job_key": ctx.job_key,
                "context_id": ctx.context_id,
                "title": ctx.title,
                "company": ctx.company,
                "stage": ctx.stage,
                "live": ctx.job_key in live,
            }
        )
    return entries


def persistence_view_for_reasoner(state: AgentState) -> dict[str, Any]:
    """Reasoner-facing persistence projection. Full job_contexts are not live World."""
    session = state.session_context if isinstance(state.session_context, dict) else {}
    pending = pending_human_gate_from_session(session)

    def _hist(label: str, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, list):
            return {
                "layer": "interpretation",
                "provenance": "restored",
                "evidence_status": "restored_unverified",
                "scope": "historical",
                "field": label,
                "items": copy.deepcopy(value),
            }
        if isinstance(value, dict):
            view = copy.deepcopy(value)
            view.setdefault("layer", "interpretation")
            view["provenance"] = "restored"
            view["evidence_status"] = "restored_unverified"
            view["scope"] = "historical"
            view["field"] = label
            return view
        return {
            "layer": "interpretation",
            "provenance": "restored",
            "evidence_status": "restored_unverified",
            "scope": "historical",
            "field": label,
            "value": value,
        }

    view: dict[str, Any] = {
        "previous_goal": _hist("previous_goal", session.get("previous_goal")),
        "previous_preferences": _hist("previous_preferences", list(session.get("previous_preferences") or [])),
        "previous_matching_context": _hist(
            "previous_matching_context", session.get("previous_matching_context")
        ),
        "task_ids": list(session.get("task_ids") or []),
        "last_recommended": _hist("last_recommended", list(session.get("last_recommended") or [])),
        "history_jobs": history_job_entries(state),
        "note": (
            "Persistence is historical storage — not Live World Fact. "
            "Restored interpretations stay unverified until new Observation/tools."
        ),
    }
    if pending is not None:
        view["pending_human_gate"] = {
            "reason": pending.get("reason"),
            "source": pending.get("source"),
            "message": pending.get("message"),
        }
    return view


def _record_from_progress(state: AgentState, progress) -> JobRecord:
    listing = {
        "platform": progress.platform,
        "job_id": progress.job_id,
        "job_url": progress.job_url,
        "job_title": progress.title,
        "company_name": progress.company,
    }
    record = JobRecord(
        job_key=progress.job_key,
        stage="listed",
        listed_order=int(progress.listed_order or 0),
        listed=listing,
        opened=None,
    )
    if progress.status in {"SURFACED", "WAITING_USER"}:
        note_job_decision(
            record,
            kind=DECISION_SURFACED,
            source="world_restore",
            payload={"restored": True},
        )
    state.jobs[record.job_key] = record
    return record


def _refresh_active_tasks(
    state: AgentState,
    task_store: Any,
    conversation_id: str | None,
    *,
    active: list | None = None,
) -> None:
    if task_store is None or not conversation_id:
        state.active_tasks = list(state.active_tasks or [])
        return
    items = active if active is not None else list(task_store.list_active(conversation_id) or [])
    state.active_tasks = [
        {
            "task_id": task.task_id,
            "task_status": task.task_status,
            "current_job_context_id": task.current_job_context_id,
        }
        for task in items
    ]
