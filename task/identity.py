"""Resolve which JobSearchTask a user turn belongs to. No guessing when ambiguous."""

from __future__ import annotations

from dataclasses import dataclass

from task.schema import CONTROL_TASK_KINDS, NEW_SEARCH_TASK_KINDS, JobSearchTask
from task.store import JobSearchTaskStore
from understanding.schema import has_user_goal, is_follow_up_task

INTENT_NEW = "new_task"
INTENT_RESUME = "resume_task"
INTENT_FOLLOW_UP = "follow_up"
INTENT_CLARIFY = "clarification"


@dataclass
class TaskIntent:
    kind: str
    control: str | None = None
    task: JobSearchTask | None = None
    error_code: str | None = None
    message: str | None = None


def task_intent_payload(intent: TaskIntent) -> dict:
    """JSON-safe view for Observation / Reasoner payload. Not a reduce switch."""
    return {
        "kind": intent.kind,
        "control": intent.control,
        "task_id": intent.task.task_id if intent.task is not None else None,
        "error_code": intent.error_code,
        "message": intent.message,
    }


def resolve_task_intent(
    understanding: dict | None,
    store: JobSearchTaskStore | None,
    *,
    conversation_id: str | None,
    resolved_job_key: str | None = None,
) -> TaskIntent:
    """Map Understanding task_kind onto create / resume / follow-up / clarify.

    Pure function. Result is payload material. Does not read user text.
    Does not pick a task when more than one is active.
    """
    fields = understanding if isinstance(understanding, dict) else {}
    kind = fields.get("task_kind")
    active = store.list_active(conversation_id) if store is not None else []

    if kind in NEW_SEARCH_TASK_KINDS or _is_new_search(fields, kind):
        return TaskIntent(kind=INTENT_NEW)

    if kind in CONTROL_TASK_KINDS:
        return _resume_control(kind, active)

    if is_follow_up_task(fields):
        return _follow_up(active, resolved_job_key)

    if has_user_goal(fields) and kind is None:
        return TaskIntent(kind=INTENT_NEW)

    if len(active) == 1 and kind is None:
        return TaskIntent(kind=INTENT_CLARIFY, error_code="clarification_needed", message="请说明你是要继续当前求职，还是开始一轮新的搜索。")

    if len(active) > 1:
        return TaskIntent(
            kind=INTENT_CLARIFY,
            error_code="clarification_needed",
            message="目前有不止一个进行中的求职任务，请说明你指的是哪一次。",
        )
    return TaskIntent(kind=INTENT_NEW)


def _is_new_search(fields: dict, kind: str | None) -> bool:
    if kind == "new_job_search":
        return True
    if kind == "update_goal" and has_user_goal(fields):
        return True
    return False


def _resume_control(control: str, active: list[JobSearchTask]) -> TaskIntent:
    if len(active) == 0:
        return TaskIntent(
            kind=INTENT_CLARIFY,
            control=control,
            error_code="clarification_needed",
            message="当前没有进行中的求职任务可以执行这个操作。",
        )
    if len(active) > 1:
        return TaskIntent(
            kind=INTENT_CLARIFY,
            control=control,
            error_code="clarification_needed",
            message="目前有不止一个进行中的求职任务，请说明你指的是哪一次。",
        )
    return TaskIntent(kind=INTENT_RESUME, control=control, task=active[0])


def _follow_up(active: list[JobSearchTask], resolved_job_key: str | None) -> TaskIntent:
    if resolved_job_key:
        owning = [task for task in active if _task_owns_job(task, resolved_job_key)]
        if len(owning) == 1:
            return TaskIntent(kind=INTENT_FOLLOW_UP, task=owning[0])
        if len(owning) > 1:
            return TaskIntent(
                kind=INTENT_CLARIFY,
                error_code="clarification_needed",
                message="目前有不止一个进行中的求职任务包含这个职位，请说明你指的是哪一次。",
            )
    if len(active) == 1:
        return TaskIntent(kind=INTENT_FOLLOW_UP, task=active[0])
    return TaskIntent(kind=INTENT_FOLLOW_UP, task=None)


def _task_owns_job(task: JobSearchTask, job_key: str) -> bool:
    wanted = (job_key or "").strip()
    if not wanted:
        return False
    if task.current_job_context_id == wanted:
        return True
    return any(item.job_context_id == wanted or item.job_key == wanted for item in task.explored_jobs)
