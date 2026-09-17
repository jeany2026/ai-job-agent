"""Deterministic job-key and cross-platform identity dedupe.

Identity is exact normalized company + title + city, or an exact job_url.
No title/keyword similarity, synonym lists, or LLM.
"""

from __future__ import annotations

import re

from job.common_schema import job_key
from rules.blacklist import normalize_company

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_SEP = "\x1f"


def normalize_title(title: str | None) -> str | None:
    """Collapse whitespace and casefold. Does not map synonyms or word families."""
    if title is None:
        return None
    text = _SPACE_RE.sub("", str(title)).strip().casefold()
    return text or None


def normalize_city(city: str | None) -> str | None:
    """Collapse whitespace and casefold. Strip a trailing 市 only; no district aliases."""
    if city is None:
        return None
    text = _SPACE_RE.sub("", str(city)).strip().casefold()
    if text.endswith("市") and len(text) > 1:
        text = text[:-1]
    return text or None


def url_identity(job: dict | None) -> str | None:
    """Platform-independent exact URL key. Not a canonicalization of query params."""
    if not isinstance(job, dict):
        return None
    url = job.get("job_url")
    if url is None:
        return None
    text = str(url).strip().casefold().rstrip("/")
    if not text or text.lower() in {"none", "null", "n/a"}:
        return None
    return f"url:{text}"


def cross_platform_identity(job: dict | None) -> str | None:
    """Exact company|title|city identity. None if any field is missing (fail closed)."""
    if not isinstance(job, dict):
        return None
    company = normalize_company(job.get("company_name"))
    title = normalize_title(job.get("job_title"))
    city = normalize_city(job.get("city"))
    if not company or not title or not city:
        return None
    return "xplat:" + _IDENTITY_SEP.join((company, title, city))


def remember_job(existing: set[str], job: dict | None) -> None:
    """Add platform job_key, exact URL, and cross-platform identity into the seen set."""
    if not isinstance(job, dict):
        return
    key = job_key(job)
    if key:
        existing.add(key)
    url = url_identity(job)
    if url:
        existing.add(url)
    ident = cross_platform_identity(job)
    if ident:
        existing.add(ident)


def seen_keys(existing_jobs: dict) -> set[str]:
    seen: set[str] = set()
    if not isinstance(existing_jobs, dict):
        return seen
    for key, record in existing_jobs.items():
        if key:
            seen.add(key)
        remember_job(seen, _job_from_record(record))
    return seen


def is_duplicate(job: dict, existing: set[str]) -> bool:
    if not existing:
        return False
    key = job_key(job)
    if key and key in existing:
        return True
    url = url_identity(job)
    if url and url in existing:
        return True
    ident = cross_platform_identity(job)
    if ident and ident in existing:
        return True
    return False


def _job_from_record(record) -> dict | None:
    if isinstance(record, dict):
        return record
    opened = getattr(record, "opened", None)
    if isinstance(opened, dict):
        return opened
    listed = getattr(record, "listed", None)
    if isinstance(listed, dict):
        return listed
    return None
