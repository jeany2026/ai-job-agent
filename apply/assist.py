"""Reserved apply-assist surface. Auto apply/contact is NotImplemented.

prepare_apply_assist composes already-structured Agent fields into a draft
that still requires a human. It does not click, chat, or bypass verification.
"""

from __future__ import annotations

from typing import Any

from apply.schema import (
    APPLY_ASSIST_SCHEMA_VERSION,
    ASSIST_STATUS_AWAITING,
    AUTO_EXECUTION_NOT_IMPLEMENTED,
    confirmation_status,
    job_identity,
    structured_application_evidence,
    structured_proposed_actions,
    structured_recommendation,
)

NOT_IMPLEMENTED_APPLY = "auto apply is not implemented (Phase 11 reserved only)"
NOT_IMPLEMENTED_CONTACT = "auto contact is not implemented (Phase 11 reserved only)"

AUTO_APPLY_SPEC = {
    "name": "auto_apply",
    "description": (
        "RESERVED. Apply to a job only after explicit human confirmation. "
        "Not implemented in baseline v1. Must not be registered on the Agent Loop. "
        "Does not click apply, send chat, or bypass captcha."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plan": {"type": "object"},
            "confirmation": {"type": "object"},
        },
        "required": ["plan", "confirmation"],
    },
}

SEND_COMMUNICATION_SPEC = {
    "name": "send_communication",
    "description": (
        "RESERVED. Send recruiter communication after explicit human confirmation. "
        "Not implemented in baseline v1. Must not be registered on the Agent Loop."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plan": {"type": "object"},
            "confirmation": {"type": "object"},
            "message": {"type": "string"},
        },
        "required": ["plan", "confirmation"],
    },
}

RESERVED_TOOL_SPECS = (AUTO_APPLY_SPEC, SEND_COMMUNICATION_SPEC)


def prepare_apply_assist(request: dict | None) -> dict:
    """Build a human-confirmation draft from already-structured fields.

    Does not execute apply/contact. Illegal or missing intents stay unused;
    this function does not guess from button text or JD/resume wording.
    """
    if not isinstance(request, dict):
        raise TypeError("apply assist request must be a dict")
    job = request.get("job")
    interpret_result = request.get("interpret_result")
    match_result = request.get("match_result")
    identity = job_identity(job if isinstance(job, dict) else None)
    return {
        "schema_version": APPLY_ASSIST_SCHEMA_VERSION,
        "assist_status": ASSIST_STATUS_AWAITING,
        "confirmation_required": True,
        "auto_execution": AUTO_EXECUTION_NOT_IMPLEMENTED,
        "job_key": identity["job_key"],
        "platform": identity["platform"],
        "job_id": identity["job_id"],
        "job_url": identity["job_url"],
        "job_title": identity["job_title"],
        "company_name": identity["company_name"],
        "proposed_actions": structured_proposed_actions(
            interpret_result if isinstance(interpret_result, dict) else None
        ),
        "application_evidence": structured_application_evidence(
            interpret_result if isinstance(interpret_result, dict) else None
        ),
        "match_recommendation": structured_recommendation(
            match_result if isinstance(match_result, dict) else None
        ),
    }


def human_confirmation(
    *,
    confirmed: bool | None = None,
    confirmed_by: str | None = None,
    note: str | None = None,
) -> dict:
    """Structured confirmation payload. Presence of confirmed=True still cannot execute."""
    payload = {
        "confirmed": confirmed is True,
        "status": confirmation_status(confirmed=confirmed),
        "confirmed_by": (confirmed_by or "").strip() or None,
        "note": (note or "").strip() or None,
    }
    return payload


def auto_apply(
    plan: dict | None = None,
    confirmation: dict | None = None,
    **_: Any,
) -> dict:
    raise NotImplementedError(NOT_IMPLEMENTED_APPLY)


def click_apply(
    plan: dict | None = None,
    confirmation: dict | None = None,
    **_: Any,
) -> dict:
    raise NotImplementedError(NOT_IMPLEMENTED_APPLY)


def auto_contact(
    plan: dict | None = None,
    confirmation: dict | None = None,
    **_: Any,
) -> dict:
    raise NotImplementedError(NOT_IMPLEMENTED_CONTACT)


def send_communication(
    plan: dict | None = None,
    confirmation: dict | None = None,
    message: str | None = None,
    **_: Any,
) -> dict:
    raise NotImplementedError(NOT_IMPLEMENTED_CONTACT)


def execute_apply_assist(
    plan: dict | None = None,
    confirmation: dict | None = None,
    **_: Any,
) -> dict:
    """Unified auto-delivery entry. Always reserved, even after human confirmation."""
    raise NotImplementedError(NOT_IMPLEMENTED_APPLY)
