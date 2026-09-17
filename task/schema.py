"""JobSearchTask: long-lived job-search orchestration. Not AgentState. Not Conversation."""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

TASK_STATUSES = (
    "RUNNING",
    "WAITING_USER",
    "EXECUTING",
    "STOPPED",
    "COMPLETED",
)

ACTIVE_TASK_STATUSES = {"RUNNING", "WAITING_USER", "EXECUTING"}

JOB_PROGRESS_STATUSES = (
    "DISCOVERED",
    "OPENED",
    "ANALYZED",
    "MATCHED",
    "REJECTED",
    "SURFACED",
    "WAITING_USER",
    "SKIPPED_BY_USER",
    "EXECUTING",
    "APPLIED",
    "FAILED",
)

TERMINAL_JOB_PROGRESS = {
    "REJECTED",
    "SKIPPED_BY_USER",
    "APPLIED",
    "FAILED",
}

STOP_REASON_USER = "user_requested"
STOP_REASON_QUOTA = "exploration_limit"
STOP_REASON_NO_NEXT = "no_further_exploration"

CONTROL_TASK_KINDS = {"apply_job", "skip_job", "stop_task", "continue_task"}
NEW_SEARCH_TASK_KINDS = {"new_job_search", "update_goal"}

SCHEMA_VERSION = 1


@dataclass
class TaskJobProgress:
    """This job's progress inside one JobSearchTask. Does not copy JobProfile / MatchResult."""

    job_context_id: str
    job_key: str
    status: str
    job_id: str | None = None
    job_url: str | None = None
    title: str | None = None
    company: str | None = None
    platform: str | None = None
    listed_order: int = 0
    decision: str | None = None
    decision_rationale: str | None = None
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_context_id": self.job_context_id,
            "job_key": self.job_key,
            "status": self.status,
            "job_id": self.job_id,
            "job_url": self.job_url,
            "title": self.title,
            "company": self.company,
            "platform": self.platform,
            "listed_order": self.listed_order,
            "decision": self.decision,
            "decision_rationale": self.decision_rationale,
            "updated_at": self.updated_at,
        }


def job_progress_from_dict(raw: Any) -> TaskJobProgress | None:
    if not isinstance(raw, dict):
        return None
    job_key = _optional_text(raw.get("job_key")) or _optional_text(raw.get("job_context_id"))
    if not job_key:
        return None
    status = _optional_text(raw.get("status")) or "DISCOVERED"
    if status not in JOB_PROGRESS_STATUSES:
        status = "DISCOVERED"
    return TaskJobProgress(
        job_context_id=_optional_text(raw.get("job_context_id")) or job_key,
        job_key=job_key,
        status=status,
        job_id=_optional_text(raw.get("job_id")),
        job_url=_optional_text(raw.get("job_url")),
        title=_optional_text(raw.get("title")),
        company=_optional_text(raw.get("company")),
        platform=_optional_text(raw.get("platform")),
        listed_order=_optional_int(raw.get("listed_order")) or 0,
        decision=_optional_text(raw.get("decision")),
        decision_rationale=_optional_text(raw.get("decision_rationale")),
        updated_at=_optional_text(raw.get("updated_at")) or utc_now(),
    )


@dataclass
class JobSearchTask:
    """Long-running job search. Survives one HTTP AgentState run."""

    task_id: str
    task_status: str
    user_goal: dict
    candidate_context: dict | None = None
    search_strategy: dict = field(default_factory=dict)
    explored_jobs: list[TaskJobProgress] = field(default_factory=list)
    rejected_jobs: list[str] = field(default_factory=list)
    surfaced_jobs: list[str] = field(default_factory=list)
    user_decisions: list[dict] = field(default_factory=list)
    application_history: list[dict] = field(default_factory=list)
    current_job_context_id: str | None = None
    current_gate: dict | None = None
    stopping_reason: str | None = None
    conversation_id: str | None = None
    status_history: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "task_status": self.task_status,
            "user_goal": copy.deepcopy(self.user_goal) if isinstance(self.user_goal, dict) else {},
            "candidate_context": copy.deepcopy(self.candidate_context)
            if isinstance(self.candidate_context, dict)
            else None,
            "search_strategy": copy.deepcopy(self.search_strategy)
            if isinstance(self.search_strategy, dict)
            else {},
            "explored_jobs": [item.to_dict() for item in self.explored_jobs],
            "rejected_jobs": list(self.rejected_jobs),
            "surfaced_jobs": list(self.surfaced_jobs),
            "user_decisions": copy.deepcopy(self.user_decisions),
            "application_history": copy.deepcopy(self.application_history),
            "current_job_context_id": self.current_job_context_id,
            "current_gate": copy.deepcopy(self.current_gate) if isinstance(self.current_gate, dict) else None,
            "stopping_reason": self.stopping_reason,
            "conversation_id": self.conversation_id,
            "status_history": copy.deepcopy(self.status_history),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def public_view(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_status": self.task_status,
            "current_job_context_id": self.current_job_context_id,
            "current_gate": copy.deepcopy(self.current_gate) if isinstance(self.current_gate, dict) else None,
            "stopping_reason": self.stopping_reason,
            "conversation_id": self.conversation_id,
        }

    def job_progress(self, job_context_id: str | None) -> TaskJobProgress | None:
        wanted = (job_context_id or "").strip()
        if not wanted:
            return None
        for item in self.explored_jobs:
            if item.job_context_id == wanted or item.job_key == wanted:
                return item
        return None

    def is_active(self) -> bool:
        return self.task_status in ACTIVE_TASK_STATUSES


def new_job_search_task(
    *,
    user_goal: dict | None = None,
    candidate_context: dict | None = None,
    conversation_id: str | None = None,
    search_strategy: dict | None = None,
) -> JobSearchTask:
    now = utc_now()
    task_id = str(uuid.uuid4())
    return JobSearchTask(
        task_id=task_id,
        task_status="RUNNING",
        user_goal=copy.deepcopy(user_goal) if isinstance(user_goal, dict) else {},
        candidate_context=copy.deepcopy(candidate_context) if isinstance(candidate_context, dict) else None,
        search_strategy=copy.deepcopy(search_strategy) if isinstance(search_strategy, dict) else {},
        conversation_id=conversation_id,
        status_history=[{"status": "RUNNING", "at": now}],
        created_at=now,
        updated_at=now,
    )


def job_search_task_from_dict(raw: Any) -> JobSearchTask | None:
    if not isinstance(raw, dict):
        return None
    task_id = _optional_text(raw.get("task_id"))
    if not task_id:
        return None
    status = _optional_text(raw.get("task_status")) or "RUNNING"
    if status not in TASK_STATUSES:
        return None
    explored: list[TaskJobProgress] = []
    for item in raw.get("explored_jobs") or []:
        progress = job_progress_from_dict(item)
        if progress is not None:
            explored.append(progress)
    return JobSearchTask(
        task_id=task_id,
        task_status=status,
        user_goal=copy.deepcopy(raw.get("user_goal")) if isinstance(raw.get("user_goal"), dict) else {},
        candidate_context=copy.deepcopy(raw.get("candidate_context"))
        if isinstance(raw.get("candidate_context"), dict)
        else None,
        search_strategy=copy.deepcopy(raw.get("search_strategy"))
        if isinstance(raw.get("search_strategy"), dict)
        else {},
        explored_jobs=explored,
        rejected_jobs=_string_list(raw.get("rejected_jobs")),
        surfaced_jobs=_string_list(raw.get("surfaced_jobs")),
        user_decisions=_dict_list(raw.get("user_decisions")),
        application_history=_dict_list(raw.get("application_history")),
        current_job_context_id=_optional_text(raw.get("current_job_context_id")),
        current_gate=copy.deepcopy(raw.get("current_gate")) if isinstance(raw.get("current_gate"), dict) else None,
        stopping_reason=_optional_text(raw.get("stopping_reason")),
        conversation_id=_optional_text(raw.get("conversation_id")),
        status_history=_dict_list(raw.get("status_history")),
        created_at=_optional_text(raw.get("created_at")) or utc_now(),
        updated_at=_optional_text(raw.get("updated_at")) or utc_now(),
        schema_version=int(raw.get("schema_version") or SCHEMA_VERSION),
    )


def set_task_status(task: JobSearchTask, status: str, *, reason: str | None = None) -> None:
    if status not in TASK_STATUSES:
        raise ValueError(f"illegal JobSearchTask status: {status}")
    now = utc_now()
    task.task_status = status
    task.updated_at = now
    if reason is not None:
        task.stopping_reason = reason
    task.status_history.append({"status": status, "at": now, "reason": reason})


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip() if item is not None else ""
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _dict_list(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [copy.deepcopy(item) for item in value if isinstance(item, dict)]
