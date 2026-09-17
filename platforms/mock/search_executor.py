"""Mock Browser Search Executor: catalog + buffer cursor simulates Session continuity."""

from __future__ import annotations

from typing import Any

from browser.search_executor import fetch_with_explore, resolve_session_for_mode
from job.common_schema import normalize_common_job
from platforms.search_contract import (
    DEFAULT_EXECUTION_BUDGET,
    SearchSession,
    normalize_city,
    normalize_mode,
    session_from_dict,
)

# session_id → how many catalog rows are "loaded" on the fake result page
_BUFFERS: dict[str, int] = {}


def reset_mock_search_buffers() -> None:
    _BUFFERS.clear()


def fetch_mock_jobs(
    *,
    keyword: str,
    city: str | None = None,
    limit: int = 8,
    mode: str = "fresh",
    search_session: SearchSession | dict | None = None,
    catalog: list[dict] | None = None,
    page_window: int = 30,
    execution_budget: int = DEFAULT_EXECUTION_BUDGET,
    explore_step: int = 8,
) -> dict:
    """Mock search fetch. explore_once advances the in-memory page buffer only when needed."""
    from platforms.mock.jobs import ordered_search_catalog

    platform = "mock"
    mode_norm = normalize_mode(mode)
    city_norm = normalize_city(city)
    jobs_catalog = catalog if catalog is not None else ordered_search_catalog(keyword, city_norm)
    listed = [normalize_common_job(item, platform="mock") for item in jobs_catalog if isinstance(item, dict)]

    session, early = resolve_session_for_mode(
        mode=mode_norm,
        platform=platform,
        keyword=keyword,
        city=city_norm,
        existing=search_session,
        limit=limit,
    )
    if early is not None:
        return early
    assert session is not None

    if mode_norm == "fresh":
        _BUFFERS[session.session_id] = min(max(1, int(page_window)), len(listed) or 0)
    else:
        _BUFFERS.setdefault(session.session_id, min(max(1, int(page_window)), len(listed) or 0))

    def read_available() -> list[dict]:
        end = _BUFFERS.get(session.session_id, 0)
        return listed[:end]

    def explore_once() -> list[dict] | None:
        end = _BUFFERS.get(session.session_id, 0)
        if end >= len(listed):
            return listed
        _BUFFERS[session.session_id] = min(end + max(1, int(explore_step)), len(listed))
        return listed[: _BUFFERS[session.session_id]]

    return fetch_with_explore(
        platform=platform,
        keyword=keyword,
        city=city_norm,
        mode=mode_norm,
        limit=limit,
        session=session,
        read_available=read_available,
        explore_once=explore_once if listed else None,
        execution_budget=execution_budget,
    )


def active_buffer_end(session: SearchSession | dict | None) -> int | None:
    current = session if isinstance(session, SearchSession) else session_from_dict(session)
    if current is None:
        return None
    return _BUFFERS.get(current.session_id)
