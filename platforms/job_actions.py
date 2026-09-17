"""Platform-agnostic Job Actions. Dispatch only. Not an Agent.

Reasoner sees search_jobs / open_job / inspect_job / inspect_application_state /
execute_action. Selector / DOM / URL differences stay in platform adapters.
Does not interpret intents, analyze JD, match, or apply without authorization.
"""

from __future__ import annotations

from typing import Any

from job.common_schema import job_key as make_job_key
from tools.execute_job_action import EXECUTE_JOB_ACTION_SPEC, execute_job_action

SUPPORTED_PLATFORMS = ("mock", "boss", "liepin", "job51")

# Small batch for Agent explore-one-by-one; not a bulk crawl dump.
DEFAULT_SEARCH_BATCH = 8
MAX_SEARCH_BATCH = 30


def clamp_search_limit(limit: Any = None) -> int:
    try:
        value = int(limit) if limit is not None else DEFAULT_SEARCH_BATCH
    except (TypeError, ValueError):
        value = DEFAULT_SEARCH_BATCH
    return max(1, min(value, MAX_SEARCH_BATCH))


SEARCH_JOBS_SPEC = {
    "name": "search_jobs",
    "description": (
        "Search jobs on the active platform and return CommonJob list records. "
        "Inputs: keyword (required), optional city, limit, mode, platform. "
        "mode=fresh starts a new SearchSession; mode=continue returns jobs from the "
        "active SearchSession that have not yet been exposed to the Agent. "
        "limit caps how many newly exposed jobs this invocation returns. "
        "Outputs: jobs list plus search-session / fetch fields from the platform adapter. "
        "Does not open JD text, interpret page buttons, analyze, match, apply, or "
        "choose the next Agent Action."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "keyword": {"type": "string"},
            "city": {"type": "string"},
            "limit": {"type": "integer", "default": DEFAULT_SEARCH_BATCH},
            "mode": {
                "type": "string",
                "enum": ["fresh", "continue"],
                "default": "fresh",
                "description": (
                    "fresh: new business result set. "
                    "continue: more unexposed jobs from the active SearchSession."
                ),
            },
            "platform": {"type": "string", "description": "Optional platform override."},
        },
        "required": ["keyword"],
    },
}

OPEN_JOB_SPEC = {
    "name": "open_job",
    "description": (
        "Navigate to a job URL on the active platform and read JD plus raw_actions. "
        "Does not interpret intents, analyze JD, match, or apply."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_url": {"type": "string"},
            "job_id": {"type": "string"},
            "job_key": {"type": "string"},
            "platform": {"type": "string"},
        },
    },
}

INSPECT_JOB_SPEC = {
    "name": "inspect_job",
    "description": (
        "Re-observe an opened job: CommonJob fields and raw_actions only. "
        "Does not interpret intents, analyze JD, match, or click."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_url": {"type": "string"},
            "job_id": {"type": "string"},
            "job_key": {"type": "string"},
            "job": {"type": "object"},
            "platform": {"type": "string"},
        },
    },
}

INSPECT_APPLICATION_STATE_SPEC = {
    "name": "inspect_application_state",
    "description": (
        "Read mechanical application records and raw page action texts for a job. "
        "Does not classify intents, guess applied status from copy, or apply."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job": {"type": "object"},
            "job_key": {"type": "string"},
            "job_id": {"type": "string"},
            "job_url": {"type": "string"},
            "platform": {"type": "string"},
            "already_applied": {"type": "array"},
            "application_history": {"type": "array"},
            "raw_actions": {"type": "array"},
        },
    },
}

EXECUTE_ACTION_SPEC = {
    **EXECUTE_JOB_ACTION_SPEC,
    "name": "execute_action",
    "description": (
        "Execute a user-authorized apply/contact action against the current JobContext. "
        "Requires user_authorization=apply_job. Does not search or choose a different job."
    ),
}


def resolve_platform(arguments: dict | None = None, extra: dict | None = None) -> str:
    payload = arguments if isinstance(arguments, dict) else {}
    extras = extra if isinstance(extra, dict) else {}
    for source in (
        payload.get("platform"),
        extras.get("data_source"),
        extras.get("platform"),
    ):
        name = _platform_name(source)
        if name:
            return name
    job = payload.get("job") if isinstance(payload.get("job"), dict) else {}
    name = _platform_name(job.get("platform"))
    if name:
        return name
    return "mock"


def search_jobs(arguments: dict | None = None, **extra: Any) -> dict:
    from platforms.search_contract import normalize_mode

    payload = arguments if isinstance(arguments, dict) else {}
    platform = resolve_platform(payload, extra)
    kwargs = _pick(payload, "keyword", "city", "limit", "mode")
    kwargs["limit"] = clamp_search_limit(kwargs.get("limit"))
    kwargs["mode"] = normalize_mode(kwargs.get("mode") or extra.get("mode"))
    kwargs["search_session"] = extra.get("search_session")
    if extra.get("page") is not None:
        kwargs["page"] = extra["page"]
    if extra.get("execution_budget") is not None:
        kwargs["execution_budget"] = extra["execution_budget"]
    if platform == "boss":
        from platforms.boss.jobs import search_boss_jobs

        return search_boss_jobs(**kwargs)
    if platform == "liepin":
        from platforms.liepin.jobs import search_liepin_jobs

        return search_liepin_jobs(**kwargs)
    if platform == "job51":
        from platforms.job51.jobs import search_51job_jobs

        return search_51job_jobs(**kwargs)
    from platforms.mock.jobs import mock_search_jobs

    return mock_search_jobs(**kwargs)


def open_job(arguments: dict | None = None, **extra: Any) -> dict:
    payload = arguments if isinstance(arguments, dict) else {}
    return _open_or_inspect(payload, extra, navigate=True)


def inspect_job(arguments: dict | None = None, **extra: Any) -> dict:
    """Re-observe page facts only when already on the target page. Never navigates."""
    payload = arguments if isinstance(arguments, dict) else {}
    job = payload.get("job") if isinstance(payload.get("job"), dict) else {}
    merged = dict(payload)
    if job:
        for key in ("job_url", "job_id", "job_key", "platform"):
            if not merged.get(key) and job.get(key):
                merged[key] = job[key]
    return _open_or_inspect(merged, extra, navigate=False)


def _open_or_inspect(payload: dict, extra: dict, *, navigate: bool = True) -> dict:
    platform = resolve_platform(payload, extra)
    kwargs = _pick(payload, "job_url", "job_id")
    if extra.get("page") is not None:
        kwargs["page"] = extra["page"]
    kwargs["navigate"] = navigate
    if platform == "boss":
        from platforms.boss.jobs import open_boss_job

        return open_boss_job(**kwargs)
    if platform == "liepin":
        from platforms.liepin.jobs import open_liepin_job

        return open_liepin_job(**kwargs)
    if platform == "job51":
        from platforms.job51.jobs import open_51job_job

        return open_51job_job(**kwargs)
    from platforms.mock.jobs import mock_open_job

    # Mock has no browser navigation; navigate flag is ignored.
    kwargs.pop("navigate", None)
    return mock_open_job(**kwargs)


def inspect_application_state(arguments: dict | None = None, **extra: Any) -> dict:
    payload = arguments if isinstance(arguments, dict) else {}
    extras = extra if extra else {}
    job = payload.get("job") if isinstance(payload.get("job"), dict) else {}
    platform = resolve_platform(payload, extras)
    identity = {
        "platform": platform,
        "job_id": payload.get("job_id") or job.get("job_id"),
        "job_url": payload.get("job_url") or job.get("job_url"),
        "job_key": payload.get("job_key") or job.get("job_key"),
    }
    if not identity["job_key"]:
        identity["job_key"] = make_job_key(
            {
                "platform": platform,
                "job_id": identity["job_id"],
                "job_url": identity["job_url"],
            }
        )
    keys = {value for value in identity.values() if isinstance(value, str) and value.strip()}
    already_applied = _string_list(payload.get("already_applied")) or _string_list(extras.get("already_applied"))
    already_applied_matches = [item for item in already_applied if item in keys]
    history_records = []
    for item in _history_items(payload.get("application_history") or extras.get("application_history")):
        item_keys = {
            item.get("job_key"),
            item.get("job_id"),
            item.get("job_url"),
            item.get("job_context_id"),
        }
        if keys & {value for value in item_keys if isinstance(value, str) and value.strip()}:
            history_records.append(
                {
                    "job_key": item.get("job_key") or item.get("job_context_id"),
                    "result": item.get("result"),
                    "evidence": item.get("evidence") or item.get("application_evidence"),
                }
            )
    return {
        "platform": identity["platform"],
        "job_id": identity["job_id"],
        "job_url": identity["job_url"],
        "job_key": identity["job_key"],
        "already_applied_matches": already_applied_matches,
        "history_records": history_records,
        "page_action_texts": _page_action_texts(payload, job),
    }


def execute_action(arguments: dict | None = None, **extra: Any) -> dict:
    return execute_job_action(arguments, **extra)


def _platform_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip().lower()
    if name in SUPPORTED_PLATFORMS:
        return name
    return None


def _pick(payload: dict, *keys: str) -> dict:
    return {key: payload[key] for key in keys if key in payload and payload[key] not in (None, "")}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        result.append(text)
    return result


def _history_items(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _page_action_texts(payload: dict, job: dict) -> list[str]:
    raw = payload.get("raw_actions")
    if not isinstance(raw, list):
        raw = job.get("raw_actions")
    if not isinstance(raw, list):
        return []
    texts: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            texts.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return texts
