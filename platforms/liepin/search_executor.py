"""Liepin Search Executor: Session continuity; page explore only when needed."""

from __future__ import annotations

from typing import Any

from agent.human_gate import reraise_as_human_gate
from browser.search_executor import fetch_with_explore, resolve_session_for_mode
from platforms.search_contract import (
    DEFAULT_EXECUTION_BUDGET,
    SearchSession,
    normalize_city,
    normalize_mode,
)


def fetch_liepin_jobs(
    *,
    keyword: str,
    city: str | None = None,
    limit: int = 8,
    mode: str = "fresh",
    search_session: SearchSession | dict | None = None,
    page=None,
    execution_budget: int = DEFAULT_EXECUTION_BUDGET,
) -> dict:
    from browser.edge_session import check_human_verification, connect_existing_edge, disconnect_edge
    from platforms.job_actions import clamp_search_limit
    from platforms.liepin.jobs import (
        _ensure_logged_in,
        _raw_to_listed,
        extract_liepin_job_list,
        search_url,
    )

    platform = "liepin"
    mode_norm = normalize_mode(mode)
    city_norm = normalize_city(city) or "深圳"
    limit_n = clamp_search_limit(limit)
    keyword = str(keyword).strip()
    if not keyword:
        raise ValueError("keyword 不能为空")

    session, early = resolve_session_for_mode(
        mode=mode_norm,
        platform=platform,
        keyword=keyword,
        city=city_norm,
        existing=search_session,
        limit=limit_n,
    )
    if early is not None:
        return early
    assert session is not None

    owns_session = page is None
    playwright = None
    browser = None
    try:
        if owns_session:
            playwright, browser, page = connect_existing_edge()

        def ensure_result_page() -> None:
            target = search_url(keyword, city_norm)
            current = page.url or ""
            need = mode_norm == "fresh" or "liepin.com" not in current.lower() or "zhaopin" not in current.lower()
            if need:
                page.goto(target, wait_until="domcontentloaded", timeout=60_000)
            check_human_verification(page, "打开猎聘搜索页")
            try:
                page.wait_for_function(
                    "() => document.querySelectorAll('a[href*=\"/job/\"]').length > 0",
                    timeout=15_000,
                )
            except Exception:
                pass
            snapshot = extract_liepin_job_list(page)
            _ensure_logged_in(page, snapshot)

        def read_available() -> list[dict]:
            snapshot = extract_liepin_job_list(page)
            raw_jobs = snapshot.get("jobs") or []
            jobs = []
            for item in raw_jobs:
                if isinstance(item, dict):
                    jobs.append(_raw_to_listed(item, fallback_city=city_norm))
            return jobs

        def explore_once() -> list[dict] | None:
            # Prefer in-page growth; fall back to next-page control when present.
            before = {job.get("job_id") for job in read_available()}
            try:
                page.evaluate(
                    """() => {
                      const html = document.documentElement;
                      html.scrollTop = html.scrollHeight;
                      window.dispatchEvent(new Event('scroll'));
                    }"""
                )
                page.wait_for_timeout(800)
            except Exception:
                pass
            after = read_available()
            if {job.get("job_id") for job in after} - before:
                return after
            clicked = False
            for sel in (
                "a:has-text('下一页')",
                "button:has-text('下一页')",
                ".pager a.next",
                "a.next",
            ):
                try:
                    loc = page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=3_000)
                        clicked = True
                        page.wait_for_timeout(1000)
                        break
                except Exception:
                    continue
            if not clicked:
                return after
            return read_available()

        return fetch_with_explore(
            platform=platform,
            keyword=keyword,
            city=city_norm,
            mode=mode_norm,
            limit=limit_n,
            session=session,
            read_available=read_available,
            explore_once=explore_once,
            execution_budget=execution_budget,
            ensure_result_page=ensure_result_page,
        )
    except Exception as exc:
        reraise_as_human_gate(exc, source="search_liepin_jobs")
        raise
    finally:
        if owns_session:
            disconnect_edge(playwright, browser)
