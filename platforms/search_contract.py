"""Search business contract + execution SearchSession.

Reasoner sees mode=fresh|continue and Observation fields.
Browser URL / scroll / DOM stay inside Executors.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from job.common_schema import job_key as make_job_key

SEARCH_MODES = ("fresh", "continue")
DEFAULT_SEARCH_MODE = "fresh"

FETCH_STATUSES = (
    "ok",
    "exhausted",
    "resource_limited",
    "needs_human",
    "failed",
    "no_active_session",
    "query_mismatch",
)

SESSION_STATUSES = (
    "active",
    "exhausted",
    "resource_limited",
    "needs_human",
    "failed",
)

# Max explore rounds (scroll / load-more / page) per search_jobs invocation.
DEFAULT_EXECUTION_BUDGET = 6


@dataclass
class SearchSession:
    """Execution continuity for one business result set. Not a Reasoner Action."""

    session_id: str
    platform: str
    query_fingerprint: str
    keyword: str
    city: str | None = None
    exposed_job_keys: list[str] = field(default_factory=list)
    status: str = "active"

    def exposed_set(self) -> set[str]:
        return {key for key in self.exposed_job_keys if isinstance(key, str) and key.strip()}


def normalize_mode(value: Any) -> str:
    if value is None or value == "":
        return DEFAULT_SEARCH_MODE
    text = str(value).strip().casefold()
    if text in SEARCH_MODES:
        return text
    return DEFAULT_SEARCH_MODE


def normalize_city(city: Any) -> str | None:
    if city is None:
        return None
    text = str(city).strip()
    return text or None


def query_fingerprint(platform: str, keyword: str, city: str | None = None) -> str:
    plat = (platform or "unknown").strip().casefold()
    key = (keyword or "").strip().casefold()
    city_part = (city or "").strip().casefold()
    return f"{plat}|{key}|{city_part}"


def new_search_session(
    *,
    platform: str,
    keyword: str,
    city: str | None = None,
) -> SearchSession:
    city_norm = normalize_city(city)
    return SearchSession(
        session_id=f"ss-{uuid.uuid4().hex[:12]}",
        platform=(platform or "mock").strip().casefold() or "mock",
        query_fingerprint=query_fingerprint(platform, keyword, city_norm),
        keyword=str(keyword).strip(),
        city=city_norm,
        exposed_job_keys=[],
        status="active",
    )


def session_to_dict(session: SearchSession | None) -> dict | None:
    if session is None:
        return None
    return {
        "session_id": session.session_id,
        "platform": session.platform,
        "query_fingerprint": session.query_fingerprint,
        "keyword": session.keyword,
        "city": session.city,
        "exposed_job_keys": list(session.exposed_job_keys),
        "status": session.status,
    }


def session_from_dict(raw: Any) -> SearchSession | None:
    if not isinstance(raw, dict):
        return None
    session_id = str(raw.get("session_id") or "").strip()
    platform = str(raw.get("platform") or "").strip()
    keyword = str(raw.get("keyword") or "").strip()
    if not session_id or not platform or not keyword:
        return None
    city = normalize_city(raw.get("city"))
    fingerprint = str(raw.get("query_fingerprint") or "").strip() or query_fingerprint(
        platform, keyword, city
    )
    exposed = []
    seen: set[str] = set()
    for item in raw.get("exposed_job_keys") or []:
        key = str(item).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        exposed.append(key)
    status = str(raw.get("status") or "active").strip()
    if status not in SESSION_STATUSES:
        status = "active"
    return SearchSession(
        session_id=session_id,
        platform=platform.casefold(),
        query_fingerprint=fingerprint,
        keyword=keyword,
        city=city,
        exposed_job_keys=exposed,
        status=status,
    )


def can_continue(status: str | None) -> bool:
    """Whether Reasoner may legally choose mode=continue on this session."""
    return status in {"active", "resource_limited"}


def job_keys_for_jobs(jobs: list[dict]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        key = make_job_key(job)
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def filter_unexposed(jobs: list[dict], exposed: set[str]) -> list[dict]:
    """Deterministic Executor dedupe by job_key. Does not judge fit/recommend."""
    out: list[dict] = []
    seen: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        key = make_job_key(job)
        if not key or key in exposed or key in seen:
            continue
        seen.add(key)
        out.append(job)
    return out


def record_exposed(session: SearchSession, jobs: list[dict]) -> None:
    """Mark only jobs returned in this Observation as exposed."""
    known = session.exposed_set()
    for key in job_keys_for_jobs(jobs):
        if key not in known:
            session.exposed_job_keys.append(key)
            known.add(key)


def make_fetch_result(
    *,
    platform: str,
    keyword: str,
    city: str | None,
    mode: str,
    limit: int,
    jobs: list[dict] | None = None,
    newly_exposed: int | None = None,
    duplicates: int = 0,
    fetch_status: str = "ok",
    session: SearchSession | None = None,
    error: str | None = None,
    diagnostic: dict | None = None,
) -> dict:
    """Unified search_jobs Tool result (business Observation fields)."""
    listed = [job for job in (jobs or []) if isinstance(job, dict)]
    if newly_exposed is None:
        newly_exposed = len(listed)
    status = fetch_status if fetch_status in FETCH_STATUSES else "failed"

    if session is not None:
        if status == "exhausted":
            session.status = "exhausted"
        elif status == "resource_limited":
            session.status = "resource_limited"
        elif status == "needs_human":
            session.status = "needs_human"
        elif status == "failed":
            session.status = "failed"
        elif status == "ok":
            session.status = "active"

    continue_ok = False
    if status == "resource_limited":
        continue_ok = True
    elif status == "ok" and session is not None:
        continue_ok = can_continue(session.status)
    elif status == "exhausted":
        continue_ok = False

    result: dict[str, Any] = {
        "platform": platform,
        "query": {
            "keyword": keyword,
            "city": city,
            "mode": mode,
            "limit": limit,
        },
        "mode": mode,
        "jobs": listed,
        "newly_ingested": int(newly_exposed),
        "newly_exposed": int(newly_exposed),
        "fetch_status": status,
        "can_continue": bool(continue_ok),
        "search_session": session_to_dict(session),
        # Tool-level duplicates among returned rows (usually 0); World may add more.
        "duplicates": int(duplicates),
    }
    if error:
        result["error"] = error
    if diagnostic:
        result["diagnostic"] = diagnostic
    return result


def contract_error_result(
    *,
    platform: str,
    keyword: str,
    city: str | None,
    mode: str,
    limit: int,
    fetch_status: str,
    error: str,
    session: SearchSession | None = None,
) -> dict:
    return make_fetch_result(
        platform=platform,
        keyword=keyword,
        city=city,
        mode=mode,
        limit=limit,
        jobs=[],
        newly_exposed=0,
        duplicates=0,
        fetch_status=fetch_status,
        session=session,
        error=error,
    )
