"""Browser Search Executor helpers. Page strategy stays here — not Reasoner Tools."""

from __future__ import annotations

from typing import Any, Callable

from job.common_schema import job_key as make_job_key
from platforms.search_contract import (
    DEFAULT_EXECUTION_BUDGET,
    SearchSession,
    contract_error_result,
    filter_unexposed,
    make_fetch_result,
    new_search_session,
    normalize_city,
    normalize_mode,
    query_fingerprint,
    record_exposed,
    session_from_dict,
)

ExploreFn = Callable[[], list[dict] | None]
ReadFn = Callable[[], list[dict]]


def resolve_session_for_mode(
    *,
    mode: str,
    platform: str,
    keyword: str,
    city: str | None,
    existing: SearchSession | dict | None,
    limit: int = 0,
) -> tuple[SearchSession | None, dict | None]:
    """Apply fresh|continue Session rules. Returns (session, early_error_result)."""
    mode_norm = normalize_mode(mode)
    city_norm = normalize_city(city)
    current = existing if isinstance(existing, SearchSession) else session_from_dict(existing)

    if mode_norm == "fresh":
        return new_search_session(platform=platform, keyword=keyword, city=city_norm), None

    if current is None:
        return None, contract_error_result(
            platform=platform,
            keyword=keyword,
            city=city_norm,
            mode=mode_norm,
            limit=limit,
            fetch_status="no_active_session",
            error="mode=continue requires an active SearchSession",
        )

    expected = query_fingerprint(platform, keyword, city_norm)
    if current.query_fingerprint != expected:
        return current, contract_error_result(
            platform=platform,
            keyword=keyword,
            city=city_norm,
            mode=mode_norm,
            limit=limit,
            fetch_status="query_mismatch",
            error="continue query does not match active SearchSession",
            session=current,
        )

    if current.status == "exhausted":
        return current, make_fetch_result(
            platform=platform,
            keyword=keyword,
            city=city_norm,
            mode=mode_norm,
            limit=limit,
            jobs=[],
            newly_exposed=0,
            fetch_status="exhausted",
            session=current,
        )

    return current, None


def collect_unexposed_batch(
    *,
    available_jobs: list[dict],
    session: SearchSession,
    limit: int,
    already_picked: set[str] | None = None,
) -> list[dict]:
    """Take up to limit jobs not yet exposed to Agent (not merely loaded in DOM)."""
    blocked = set(session.exposed_set())
    if already_picked:
        blocked |= already_picked
    unexposed = filter_unexposed(available_jobs, blocked)
    if limit <= 0:
        return []
    return unexposed[:limit]


def fetch_with_explore(
    *,
    platform: str,
    keyword: str,
    city: str | None,
    mode: str,
    limit: int,
    session: SearchSession,
    read_available: ReadFn,
    explore_once: ExploreFn | None = None,
    execution_budget: int = DEFAULT_EXECUTION_BUDGET,
    ensure_result_page: Callable[[], None] | None = None,
) -> dict:
    """Shared Executor loop: read → take unexposed → explore only if needed.

    Does not open / analyze / match. Marks exposed_job_keys only for returned jobs.
    """
    mode_norm = normalize_mode(mode)
    city_norm = normalize_city(city)
    budget = max(0, int(execution_budget))
    limit_n = max(0, int(limit))
    diagnostic: dict[str, Any] = {"explore_rounds": 0, "budget": budget}

    if ensure_result_page is not None:
        ensure_result_page()

    selected: list[dict] = []
    picked: set[str] = set()
    rounds = 0
    stagnant_explore = 0

    while len(selected) < limit_n:
        available = list(read_available() or [])
        batch = collect_unexposed_batch(
            available_jobs=available,
            session=session,
            limit=limit_n - len(selected),
            already_picked=picked,
        )
        for job in batch:
            key = make_job_key(job)
            if not key or key in picked:
                continue
            selected.append(job)
            picked.add(key)
            if len(selected) >= limit_n:
                break
        if len(selected) >= limit_n:
            break

        if explore_once is None or rounds >= budget:
            break

        before_keys = {make_job_key(job) for job in available if make_job_key(job)}
        explored = explore_once()
        rounds += 1
        diagnostic["explore_rounds"] = rounds
        after = list(explored if explored is not None else (read_available() or []))
        after_keys = {make_job_key(job) for job in after if make_job_key(job)}
        grew = bool(after_keys - before_keys)
        still = collect_unexposed_batch(
            available_jobs=after,
            session=session,
            limit=limit_n,
            already_picked=picked,
        )
        if not grew and not still:
            stagnant_explore += 1
            break
        if not grew:
            stagnant_explore += 1
            if stagnant_explore >= 2:
                break
        else:
            stagnant_explore = 0

    record_exposed(session, selected)
    final_jobs = selected[:limit_n]
    remaining = collect_unexposed_batch(
        available_jobs=list(read_available() or []),
        session=session,
        limit=1,
    )

    if len(final_jobs) >= limit_n:
        session.status = "active"
        fetch_status = "ok"
    elif remaining:
        session.status = "active"
        fetch_status = "ok"
    elif explore_once is not None and rounds >= budget and not remaining:
        # Budget used up; may still get more with another continue invocation.
        session.status = "resource_limited"
        fetch_status = "resource_limited"
    else:
        session.status = "exhausted"
        fetch_status = "exhausted"

    return make_fetch_result(
        platform=platform,
        keyword=keyword,
        city=city_norm,
        mode=mode_norm,
        limit=limit_n,
        jobs=final_jobs,
        newly_exposed=len(final_jobs),
        fetch_status=fetch_status,
        session=session,
        diagnostic=diagnostic,
    )
