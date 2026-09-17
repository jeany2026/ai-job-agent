"""JobSearchTask lifecycle helpers. Update the task; do not persist AgentState."""

from __future__ import annotations

import copy
from typing import Any

from agent.state import (
    DECISION_SKIPPED,
    DECISION_SURFACED,
    AgentState,
    JobRecord,
    SearchPlan,
    job_has_decision,
    note_job_decision,
    utc_now,
)
from conversation.job_context import get_job_context, job_contexts_from_session
from task.schema import (
    STOP_REASON_QUOTA,
    STOP_REASON_USER,
    TERMINAL_JOB_PROGRESS,
    JobSearchTask,
    TaskJobProgress,
    new_job_search_task,
    set_task_status,
)
from task.store import JobSearchTaskStore

AGENT_STAGE_TO_PROGRESS = {
    "listed": "DISCOVERED",
    "opened": "OPENED",
    "analyzed": "ANALYZED",
    "matched": "MATCHED",
}

def bind_task(state: AgentState, task: JobSearchTask) -> None:
    state.job_search_task_id = task.task_id
    state.task_view = task.public_view()
    state.application_history = copy.deepcopy(task.application_history)


def create_bound_task(state: AgentState, store: JobSearchTaskStore | None) -> JobSearchTask:
    task = new_job_search_task(
        user_goal=state.goal if isinstance(state.goal, dict) else {},
        candidate_context=state.candidate_context if isinstance(state.candidate_context, dict) else None,
        conversation_id=state.conversation_id,
        search_strategy=_search_strategy(state),
    )
    bind_task(state, task)
    _save(store, task)
    return task


def load_bound_task(state: AgentState, store: JobSearchTaskStore | None) -> JobSearchTask | None:
    if store is None or not state.job_search_task_id:
        return None
    task = store.get(state.job_search_task_id)
    if task is not None:
        state.task_view = task.public_view()
    return task


def sync_task_from_state(state: AgentState, store: JobSearchTaskStore | None) -> JobSearchTask | None:
    task = load_bound_task(state, store)
    if task is None:
        return None
    if isinstance(state.goal, dict):
        task.user_goal = copy.deepcopy(state.goal)
    if isinstance(state.candidate_context, dict):
        task.candidate_context = copy.deepcopy(state.candidate_context)
    task.search_strategy = _search_strategy(state)
    for record in state.jobs.values():
        _upsert_progress_from_record(task, record)
    task.updated_at = utc_now()
    bind_task(state, task)
    _save(store, task)
    return task


def hydrate_state_from_task(state: AgentState, task: JobSearchTask) -> None:
    """Bind Task and restore its exploratable Jobs into Live World.

    Called only after Reasoner chooses bind_task / task control — not on every HTTP turn.
    Startup continuity restore remains separate and only brings the waiting Job.
    Does not open / analyze / match / surface.
    """
    from agent.world_restore import restore_job_record

    bind_task(state, task)
    _restore_search(state, task)
    contexts = job_contexts_from_session(state.session_context)
    terminal = {"REJECTED", "SKIPPED_BY_USER", "APPLIED", "FAILED"}
    for progress in sorted(task.explored_jobs, key=lambda item: item.listed_order):
        if progress.status in terminal:
            continue
        ctx = get_job_context(contexts, progress.job_context_id) or get_job_context(
            contexts, progress.job_key
        )
        if ctx is not None and (ctx.job_profile or ctx.job_listing):
            record = restore_job_record(state, ctx, task=task)
            record.listed_order = progress.listed_order
            _apply_progress_stage(record, progress)
            continue
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
            listed_order=progress.listed_order,
            listed=listing,
        )
        _apply_progress_stage(record, progress)
        state.jobs[progress.job_key] = record


def mark_job_rejected(task: JobSearchTask, record: JobRecord, *, rationale: str | None = None) -> None:
    progress = _upsert_progress_from_record(task, record, status="REJECTED")
    progress.decision = "REJECT_JOB"
    progress.decision_rationale = rationale
    _remember_id(task.rejected_jobs, progress.job_context_id)
    task.updated_at = utc_now()


def mark_job_failed(task: JobSearchTask, record: JobRecord, *, rationale: str | None = None) -> None:
    progress = _upsert_progress_from_record(task, record, status="FAILED")
    progress.decision = "FAILED"
    progress.decision_rationale = rationale
    task.updated_at = utc_now()


def mark_job_continue(task: JobSearchTask, record: JobRecord, *, rationale: str | None = None) -> None:
    progress = _upsert_progress_from_record(task, record, status="MATCHED")
    progress.decision = "CONTINUE_EXPLORING"
    progress.decision_rationale = rationale
    task.updated_at = utc_now()


def surface_job(task: JobSearchTask, record: JobRecord, *, rationale: str | None = None) -> None:
    progress = _upsert_progress_from_record(task, record, status="WAITING_USER")
    progress.decision = "SURFACE_TO_USER"
    progress.decision_rationale = rationale
    _remember_id(task.surfaced_jobs, progress.job_context_id)
    task.current_job_context_id = progress.job_context_id
    task.current_gate = {
        "kind": "job_decision",
        "job_context_id": progress.job_context_id,
        "job_key": progress.job_key,
    }
    set_task_status(task, "WAITING_USER")


def skip_current_job(task: JobSearchTask, *, note: str | None = None) -> TaskJobProgress | None:
    current_id = task.current_job_context_id
    progress = task.job_progress(current_id)
    if progress is None:
        return None
    progress.status = "SKIPPED_BY_USER"
    progress.decision = "skip"
    progress.decision_rationale = note
    progress.updated_at = utc_now()
    task.user_decisions.append(
        {
            "at": utc_now(),
            "job_context_id": progress.job_context_id,
            "decision": "skip",
            "note": note,
        }
    )
    _clear_gate(task)
    set_task_status(task, "RUNNING")
    return progress


def restore_waiting_user(task: JobSearchTask, job_context_id: str | None = None) -> TaskJobProgress | None:
    """Return a blocked execute to WAITING_USER so the Human Gate remains recoverable."""
    current_id = job_context_id or task.current_job_context_id
    progress = task.job_progress(current_id)
    if progress is None:
        return None
    progress.status = "WAITING_USER"
    progress.updated_at = utc_now()
    task.current_job_context_id = progress.job_context_id
    task.current_gate = {
        "kind": "job_decision",
        "job_context_id": progress.job_context_id,
        "job_key": progress.job_key,
    }
    set_task_status(task, "WAITING_USER")
    return progress


def record_application(
    task: JobSearchTask,
    *,
    result: str,
    evidence: str | None,
    job_context_id: str | None = None,
) -> TaskJobProgress | None:
    progress = task.job_progress(job_context_id or task.current_job_context_id)
    if progress is None:
        return None
    applied = result == "applied"
    if result == "already_applied":
        progress.status = "REJECTED"
    elif result in {"already_executed"}:
        progress.status = "APPLIED" if any(
            item.get("result") == "applied" and item.get("job_context_id") == progress.job_context_id
            for item in task.application_history
        ) else progress.status
    else:
        progress.status = "APPLIED" if applied else "FAILED"
    progress.decision = "apply"
    progress.decision_rationale = result
    progress.updated_at = utc_now()
    if result != "already_executed":
        task.application_history.append(
            {
                "at": utc_now(),
                "job_context_id": progress.job_context_id,
                "result": result,
                "evidence": evidence,
            }
        )
    task.user_decisions.append(
        {
            "at": utc_now(),
            "job_context_id": progress.job_context_id,
            "decision": "apply",
            "note": result,
        }
    )
    _clear_gate(task)
    set_task_status(task, "RUNNING")
    return progress


def stop_task(task: JobSearchTask, *, reason: str = STOP_REASON_USER) -> None:
    _clear_gate(task)
    set_task_status(task, "STOPPED", reason=reason)


def complete_task(task: JobSearchTask, *, reason: str | None = None) -> None:
    _clear_gate(task)
    set_task_status(task, "COMPLETED", reason=reason or STOP_REASON_QUOTA)


def resume_running(task: JobSearchTask) -> None:
    if task.task_status in {"STOPPED", "COMPLETED"}:
        return
    _clear_gate(task)
    set_task_status(task, "RUNNING")


def remaining_unexplored(task: JobSearchTask) -> list[TaskJobProgress]:
    return [
        item
        for item in task.explored_jobs
        if item.status == "DISCOVERED"
    ]


def _search_strategy(state: AgentState) -> dict[str, Any]:
    return {
        "plans": [
            {
                "plan_id": plan.plan_id,
                "keyword": plan.keyword,
                "city": plan.city,
                "platform": plan.platform,
                "exhausted": plan.exhausted,
            }
            for plan in state.search.plans
        ],
        "active_plan_id": state.search.active_plan_id,
        "exhausted": list(state.search.exhausted),
        "stats": dict(state.search.stats),
        "stop_reason": state.search.stop_reason,
    }


def _restore_search(state: AgentState, task: JobSearchTask) -> None:
    strategy = task.search_strategy or {}
    plans = []
    for item in strategy.get("plans") or []:
        if not isinstance(item, dict) or not item.get("plan_id"):
            continue
        plans.append(
            SearchPlan(
                plan_id=str(item["plan_id"]),
                keyword=item.get("keyword"),
                city=item.get("city"),
                platform=item.get("platform") or state.constraints.data_source,
                exhausted=bool(item.get("exhausted")),
            )
        )
    state.search.plans = plans
    state.search.active_plan_id = strategy.get("active_plan_id")
    state.search.exhausted = list(strategy.get("exhausted") or [])
    if isinstance(strategy.get("stats"), dict):
        state.search.stats.update(strategy["stats"])
    state.search.stop_reason = strategy.get("stop_reason")


def _upsert_progress_from_record(
    task: JobSearchTask,
    record: JobRecord,
    status: str | None = None,
) -> TaskJobProgress:
    existing = task.job_progress(record.job_key)
    job = record.opened or record.listed or {}
    mapped = status or _progress_status_for_record(record, existing)
    if existing is None:
        existing = TaskJobProgress(
            job_context_id=record.job_key,
            job_key=record.job_key,
            status=mapped,
            listed_order=int(record.listed_order or 0),
        )
        task.explored_jobs.append(existing)
    if existing.status in TERMINAL_JOB_PROGRESS and (
        status is None or status not in TERMINAL_JOB_PROGRESS
    ):
        return existing
    if existing.status == "WAITING_USER" and status is None and mapped in {"MATCHED", "SURFACED"}:
        return existing
    decision = existing.decision
    rationale = existing.decision_rationale
    existing.status = mapped
    if status is None:
        existing.decision = decision
        existing.decision_rationale = rationale
    existing.job_id = job.get("job_id") or existing.job_id
    existing.job_url = job.get("job_url") or existing.job_url
    existing.title = job.get("job_title") or existing.title
    existing.company = job.get("company_name") or existing.company
    existing.platform = job.get("platform") or existing.platform
    existing.listed_order = int(record.listed_order if record.listed_order is not None else existing.listed_order)
    existing.updated_at = utc_now()
    return existing


def _progress_status_for_record(record: JobRecord, existing: TaskJobProgress | None) -> str:
    if job_has_decision(record, DECISION_SKIPPED):
        return "SKIPPED_BY_USER"
    if any(
        isinstance(item, dict)
        and (item.get("payload") or {}).get("application_evidence") == "applied"
        for item in (record.verifications or [])
    ):
        return "APPLIED"
    if job_has_decision(record, DECISION_SURFACED):
        if existing is not None and existing.status == "WAITING_USER":
            return "WAITING_USER"
        return "SURFACED"
    if existing is not None and existing.status in TERMINAL_JOB_PROGRESS | {"WAITING_USER", "EXECUTING"}:
        return existing.status
    return AGENT_STAGE_TO_PROGRESS.get(record.stage, "DISCOVERED")


def _inferred_world_stage(record: JobRecord, default: str = "listed") -> str:
    if record.match_result is not None:
        return "matched"
    if record.job_profile is not None:
        return "analyzed"
    if record.opened:
        return "opened"
    return default


def _apply_progress_stage(record: JobRecord, progress: TaskJobProgress) -> None:
    if progress.status == "DISCOVERED":
        record.stage = "listed"
        return
    if progress.status == "OPENED":
        record.stage = "opened"
        return
    if progress.status == "ANALYZED":
        record.stage = "analyzed"
        return
    if progress.status in {"MATCHED", "CONTINUE_EXPLORING", "EXECUTING"}:
        record.stage = "matched"
        return
    if progress.status in {"SURFACED", "WAITING_USER"}:
        record.stage = _inferred_world_stage(record, default="listed")
        if not job_has_decision(record, DECISION_SURFACED):
            note_job_decision(record, kind=DECISION_SURFACED, source="reasoner")
        return
    if progress.status == "SKIPPED_BY_USER":
        record.stage = _inferred_world_stage(record, default="listed")
        if not job_has_decision(record, DECISION_SKIPPED):
            note_job_decision(record, kind=DECISION_SKIPPED, source="user")
        return
    if progress.status == "APPLIED":
        record.stage = _inferred_world_stage(record, default="matched")
        return
    if progress.status in {"REJECTED", "FAILED"}:
        record.stage = _inferred_world_stage(record, default="listed")
        return


def _clear_gate(task: JobSearchTask) -> None:
    task.current_job_context_id = None
    task.current_gate = None


def _remember_id(bucket: list[str], value: str | None) -> None:
    text = (value or "").strip()
    if text and text not in bucket:
        bucket.append(text)


def _save(store: JobSearchTaskStore | None, task: JobSearchTask) -> None:
    if store is not None:
        store.save(task)
