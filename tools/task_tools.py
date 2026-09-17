"""Internal Task Tools. Reasoner selects them; Program only records and persists."""

from __future__ import annotations

from typing import Any

BIND_TASK_SPEC = {
    "name": "bind_task",
    "description": (
        "Create or bind a JobSearchTask. create must be explicitly true to create. "
        "Does not search, analyze, ask the user, or finish the Agent session."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "create": {"type": "boolean"},
        },
    },
}

SKIP_JOB_SPEC = {
    "name": "skip_job",
    "description": (
        "Record that the user skipped the named waiting job. "
        "Does not search, open, recommend, or finish."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "job_key": {"type": "string"},
        },
    },
}

STOP_TASK_SPEC = {
    "name": "stop_task",
    "description": (
        "Stop the bound JobSearchTask. This is not finish: the Agent session stays "
        "running until Reasoner chooses ask_user or finish."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
        },
    },
}

HYDRATE_JOB_REFERENCE_SPEC = {
    "name": "hydrate_job_reference",
    "description": (
        "Resolve a job_key / conversation reference and install that Job into Memory. "
        "Does not analyze, match, open, or search."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "job_key": {"type": "string"},
        },
    },
}


def bind_task_tool(arguments: dict, **_extra: Any) -> dict:
    return {
        "ok": True,
        "tool_name": "bind_task",
        "create": arguments.get("create"),
        "task_id": arguments.get("task_id"),
    }


def skip_job_tool(arguments: dict, **_extra: Any) -> dict:
    return {
        "ok": True,
        "tool_name": "skip_job",
        "task_id": arguments.get("task_id"),
        "job_key": arguments.get("job_key"),
    }


def stop_task_tool(arguments: dict, **_extra: Any) -> dict:
    return {
        "ok": True,
        "tool_name": "stop_task",
        "task_id": arguments.get("task_id"),
    }


def hydrate_job_reference_tool(arguments: dict, **_extra: Any) -> dict:
    return {
        "ok": True,
        "tool_name": "hydrate_job_reference",
        "task_id": arguments.get("task_id"),
        "job_key": arguments.get("job_key"),
    }
