"""CommonJob list/detail fields. No semantic guessing; missing values stay null."""

from __future__ import annotations

from typing import Any

COMMON_JOB_FIELDS = (
    "platform",
    "job_id",
    "job_url",
    "job_title",
    "company_name",
    "city",
    "salary",
    "experience",
    "education",
    "company_industry",
    "company_size",
    "job_description",
    "requirements",
    "benefits",
)


def empty_job(*, platform: str | None = None) -> dict:
    job = {field: None for field in COMMON_JOB_FIELDS}
    job["platform"] = platform
    return job


def job_key(job: dict | None) -> str | None:
    """Stable map key: platform:job_id, else job_url. Null if neither exists."""
    if not isinstance(job, dict):
        return None
    platform = _optional_text(job.get("platform")) or "unknown"
    job_id = _optional_text(job.get("job_id"))
    if job_id:
        return f"{platform}:{job_id}"
    url = _optional_text(job.get("job_url"))
    if url:
        return f"{platform}:{url}"
    return None


def normalize_common_job(job: dict | None, *, platform: str | None = None) -> dict:
    """Keep known CommonJob fields. Do not invent values."""
    out = empty_job(platform=platform)
    if not isinstance(job, dict):
        return out
    for field in COMMON_JOB_FIELDS:
        if field == "platform":
            out[field] = _optional_text(job.get("platform")) or platform
            continue
        value = job.get(field)
        if value is None:
            out[field] = None
        elif isinstance(value, str):
            out[field] = _optional_text(value)
        else:
            text = str(value).strip()
            out[field] = text or None
    raw_actions = job.get("raw_actions")
    if isinstance(raw_actions, list):
        out["raw_actions"] = raw_actions
    return out


def merge_opened(listed: dict, opened: dict) -> dict:
    """Fill list record with open_* detail. Non-null opened fields win."""
    merged = normalize_common_job(listed, platform=listed.get("platform") if listed else None)
    detail = normalize_common_job(opened, platform=opened.get("platform") if opened else merged.get("platform"))
    for field in COMMON_JOB_FIELDS:
        if detail.get(field) not in (None, ""):
            merged[field] = detail[field]
    if "raw_actions" in detail:
        merged["raw_actions"] = detail["raw_actions"]
    elif "raw_actions" in (opened or {}):
        merged["raw_actions"] = opened["raw_actions"]
    return merged


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "n/a"}:
        return None
    return text
