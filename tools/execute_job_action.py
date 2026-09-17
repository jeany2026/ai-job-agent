"""Execute a user-authorized action on the current JobContext. Does not search."""

from __future__ import annotations

from typing import Any

from apply.live_execute import (
    USER_AUTHORIZATION_APPLY,
    FixtureJobPage,
    already_executed_record,
    execute_authorized_page_action,
    execution_result,
    job_identity_from_payload,
)
from job.common_schema import job_key as make_job_key

EXECUTE_JOB_ACTION_SPEC = {
    "name": "execute_job_action",
    "description": (
        "Execute a user-authorized apply/contact action against a live World Job. "
        "Reasoner passes job_key + user_authorization; Binding loads job identity from World. "
        "Uses job_url, re-observes the live page, interprets semantic actions, "
        "and clicks the bound live element. Does not search or open a different job."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_key": {
                "type": "string",
                "description": "Identity of a live World Job. Binding loads listing/opened facts.",
            },
            "job": {
                "type": "object",
                "description": "Compatibility only. Loop Binding loads from World.",
            },
            "job_id": {"type": "string"},
            "job_url": {"type": "string"},
            "interpret_result": {"type": "object"},
            "user_authorization": {
                "type": "string",
                "description": "Human Gate grant. Must be apply_job; not a page semantic_intent.",
            },
            "job_context_id": {"type": "string"},
            "task_id": {"type": "string"},
            "application_history": {"type": "array"},
        },
        "required": ["job_key", "user_authorization"],
    },
}


def execute_job_action(arguments: dict | None = None, **extra: Any) -> dict:
    """Authorized JobContext execution. Live observe + interpret + click + verify."""
    payload = arguments if isinstance(arguments, dict) else {}
    identity = job_identity_from_payload(payload)
    task_id = payload.get("task_id")
    job_context_id = payload.get("job_context_id") or identity.get("job_key")
    user_authorization = payload.get("user_authorization")
    if not identity["job_key"] and not identity["job_id"] and not identity["job_url"]:
        return execution_result(
            ok=False,
            result="failed",
            execution_status="failed",
            verification="failed",
            error="missing JobContext identity",
            error_code="missing_identity",
            identity=identity,
            task_id=task_id,
            job_context_id=job_context_id,
        )

    prior = already_executed_record(
        payload.get("application_history"),
        job_context_id,
        identity.get("job_key"),
    )
    if prior is not None:
        return execution_result(
            ok=prior.get("result") == "applied",
            result="already_executed",
            execution_status="already_executed",
            executed_intent=prior.get("executed_intent") or "none",
            verification="confirmed" if prior.get("result") == "applied" else "skipped",
            application_evidence=prior.get("evidence") or prior.get("application_evidence"),
            identity=identity,
            task_id=task_id,
            job_context_id=job_context_id,
            extra={"prior_result": prior.get("result")},
        )

    if user_authorization != USER_AUTHORIZATION_APPLY:
        return execution_result(
            ok=False,
            result="failed",
            execution_status="failed",
            verification="failed",
            error="user authorization is missing or is not apply_job",
            error_code="not_authorized",
            identity=identity,
            task_id=task_id,
            job_context_id=job_context_id,
        )

    llm_provider = extra.get("llm_provider")
    click_log = extra.get("click_log")
    page = extra.get("page")
    platform = (identity.get("platform") or "").strip().lower()

    if page is not None:
        return execute_authorized_page_action(
            page,
            identity,
            user_authorization=user_authorization,
            llm_provider=llm_provider,
            task_id=task_id,
            job_context_id=job_context_id,
            click_log=click_log,
        )

    if platform == "boss":
        from platforms.boss.execute import execute_boss_job_action

        return execute_boss_job_action(
            payload,
            llm_provider=llm_provider,
            click_log=click_log,
        )

    return _execute_local_html(
        identity,
        payload,
        llm_provider=llm_provider,
        click_log=click_log,
        task_id=task_id,
        job_context_id=job_context_id,
        user_authorization=user_authorization,
    )


def _execute_local_html(
    identity: dict,
    payload: dict,
    *,
    llm_provider,
    click_log,
    task_id,
    job_context_id,
    user_authorization,
) -> dict:
    job = payload.get("job") if isinstance(payload.get("job"), dict) else {}
    buttons = job.get("raw_actions") if isinstance(job.get("raw_actions"), list) else []
    if not buttons:
        buttons = [
            {"text": "Submit your resume for this role", "tag": "button"},
            {"text": "Start a new chat with the recruiter", "tag": "button"},
        ]
    page = FixtureJobPage(identity, buttons=buttons, url=identity.get("job_url"))
    return execute_authorized_page_action(
        page,
        identity,
        user_authorization=user_authorization,
        llm_provider=llm_provider,
        task_id=task_id,
        job_context_id=job_context_id,
        click_log=click_log,
    )


# Re-export for tests that imported the helper name historically.
def _identity_job_key(job: dict) -> str | None:
    return make_job_key(job)
