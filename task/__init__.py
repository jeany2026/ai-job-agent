"""JobSearchTask long-running orchestration. Not AgentState. Not Conversation."""

from __future__ import annotations

from task.identity import TaskIntent, resolve_task_intent, task_intent_payload
from task.lifecycle import (
    bind_task,
    complete_task,
    create_bound_task,
    hydrate_state_from_task,
    load_bound_task,
    mark_job_continue,
    mark_job_failed,
    mark_job_rejected,
    record_application,
    skip_current_job,
    stop_task,
    surface_job,
    sync_task_from_state,
)
from task.schema import (
    ACTIVE_TASK_STATUSES,
    CONTROL_TASK_KINDS,
    JOB_PROGRESS_STATUSES,
    NEW_SEARCH_TASK_KINDS,
    TASK_STATUSES,
    JobSearchTask,
    TaskJobProgress,
    job_search_task_from_dict,
    new_job_search_task,
)
from task.store import JobSearchTaskStore

__all__ = [
    "ACTIVE_TASK_STATUSES",
    "CONTROL_TASK_KINDS",
    "JOB_PROGRESS_STATUSES",
    "NEW_SEARCH_TASK_KINDS",
    "TASK_STATUSES",
    "JobSearchTask",
    "JobSearchTaskStore",
    "TaskIntent",
    "TaskJobProgress",
    "bind_task",
    "complete_task",
    "create_bound_task",
    "hydrate_state_from_task",
    "job_search_task_from_dict",
    "load_bound_task",
    "mark_job_continue",
    "mark_job_failed",
    "mark_job_rejected",
    "new_job_search_task",
    "record_application",
    "resolve_task_intent",
    "task_intent_payload",
    "skip_current_job",
    "stop_task",
    "surface_job",
    "sync_task_from_state",
]
