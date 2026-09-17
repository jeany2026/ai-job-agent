"""Draft contracts for human-confirmed apply assist. No JD/resume guessing."""

from __future__ import annotations

from typing import Any

from job.common_schema import job_key as make_job_key

APPLY_ASSIST_SCHEMA_VERSION = 1

# Future assist kinds. Values must already come from interpret_job_actions.
PROPOSED_ACTION_KINDS = ("apply_resume", "start_contact", "continue_contact")

CONFIRMATION_STATUSES = ("pending", "confirmed", "rejected")

ASSIST_STATUS_AWAITING = "awaiting_human_confirmation"
AUTO_EXECUTION_NOT_IMPLEMENTED = "not_implemented"

SUPPORTED_APPLY_PLATFORMS = ("mock", "boss", "liepin", "job51")

AUTO_APPLY_ENTRYPOINTS = (
    "auto_apply",
    "auto_contact",
    "click_apply",
    "send_communication",
    "execute_apply_assist",
    "apply_on_platform",
    "contact_on_platform",
)


def confirmation_status(*, confirmed: bool | None) -> str:
    if confirmed is True:
        return "confirmed"
    if confirmed is False:
        return "rejected"
    return "pending"


def job_identity(job: dict | None) -> dict[str, str | None]:
    """Copy identifiers already present on CommonJob / JobRecord. Do not invent."""
    record = job if isinstance(job, dict) else {}
    return {
        "job_key": _optional_text(record.get("job_key")) or make_job_key(record),
        "platform": _optional_text(record.get("platform")),
        "job_id": _optional_text(record.get("job_id")),
        "job_url": _optional_text(record.get("job_url")),
        "job_title": _optional_text(record.get("job_title")),
        "company_name": _optional_text(record.get("company_name")),
    }


def structured_proposed_actions(interpret_result: dict | None) -> list[dict[str, str]]:
    """Keep already-classified semantic_intent values. Do not read button copy."""
    if not isinstance(interpret_result, dict):
        return []
    actions = interpret_result.get("actions")
    if not isinstance(actions, list):
        return []
    proposed: list[dict[str, str]] = []
    seen: set[str] = set()
    allowed = set(PROPOSED_ACTION_KINDS)
    for item in actions:
        if not isinstance(item, dict):
            continue
        intent = item.get("semantic_intent")
        if not isinstance(intent, str) or intent not in allowed or intent in seen:
            continue
        seen.add(intent)
        proposed.append({"kind": intent, "status": AUTO_EXECUTION_NOT_IMPLEMENTED})
    return proposed


def structured_application_evidence(interpret_result: dict | None) -> str | None:
    if not isinstance(interpret_result, dict):
        return None
    inferred = interpret_result.get("inferred_context")
    if not isinstance(inferred, dict):
        return None
    evidence = inferred.get("application_evidence")
    if isinstance(evidence, str) and evidence.strip():
        return evidence.strip()
    return None


def structured_recommendation(match_result: dict | None) -> str | None:
    if not isinstance(match_result, dict):
        return None
    value = match_result.get("recommendation")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
