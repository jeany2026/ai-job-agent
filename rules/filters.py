"""Deterministic list/match annotations. No JD/resume semantic guessing."""

from __future__ import annotations

import re
from typing import Any

from job.common_schema import job_key
from rules.application import application_exclude_reason, applied_key_set
from rules.blacklist import company_is_blacklisted

_SALARY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[-~—至到]?\s*(\d+(?:\.\d+)?)?\s*([kKwW万])?",
)


def list_exclude_reason(job: dict, constraints, *, tracker=None) -> str | None:
    """User-explicit blacklist may exclude at ingest. Other marks are flags."""
    if company_is_blacklisted(job.get("company_name"), constraints.blacklist):
        return "blacklist"
    return None


def list_constraint_flags(job: dict, constraints, *, tracker=None) -> list[str]:
    """Caller-explicit contract flags for Reasoner. Do not erase the Job from Memory."""
    flags: list[str] = []
    key = job_key(job)
    applied = applied_key_set(constraints.already_applied, tracker)
    if key and key in applied:
        flags.append("already_applied")
    if company_is_blacklisted(job.get("company_name"), constraints.blacklist):
        flags.append("blacklist")
    cities = [item.casefold() for item in (constraints.cities or [])]
    city = (job.get("city") or "").strip()
    if cities and city and city.casefold() not in cities:
        flags.append("city")
    if constraints.salary_min is not None:
        upper = salary_range_upper_yuan(job.get("salary"))
        if upper is not None and upper < int(constraints.salary_min):
            flags.append("salary")
    return flags


def match_exclude_reason(match_result: dict | None) -> str | None:
    """Readable match annotation. Not a reduce exclude instruction."""
    if not isinstance(match_result, dict):
        return "analysis_failed"
    status = match_result.get("analysis_status")
    if status and status != "ok":
        return status
    if match_result.get("hard_requirements_met") is False:
        return "hard_requirements"
    return None


def post_interpret_exclude_reason(
    *,
    job_key: str | None,
    already_applied_keys: list[str] | None,
    interpret_result: dict | None,
    tracker=None,
) -> str | None:
    """Readable interpret/tracker annotation. Not a reduce exclude instruction."""
    if isinstance(interpret_result, dict):
        status = interpret_result.get("analysis_status")
        if status and status not in {"ok"}:
            return status if status != "error" else "llm_error"
    return application_exclude_reason(
        job_key=job_key,
        already_applied_keys=already_applied_keys,
        interpret_result=interpret_result,
        tracker=tracker,
    )


def salary_range_upper_yuan(text: Any) -> int | None:
    """Parse the upper bound of a listed salary string. Unparseable → None (do not exclude)."""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    match = _SALARY_RE.search(raw)
    if not match:
        return None
    first = float(match.group(1))
    second = float(match.group(2)) if match.group(2) else first
    unit = (match.group(3) or "").lower()
    high = max(first, second)
    if unit in {"k"}:
        return int(high * 1000)
    if unit in {"w", "万"}:
        return int(high * 10000)
    if high >= 1000:
        return int(high)
    return None
