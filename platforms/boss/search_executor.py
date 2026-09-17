"""BOSS Search Executor: Session continuity; scroll/load-more only when needed."""

from __future__ import annotations

from typing import Any

from agent.human_gate import reraise_as_human_gate
from browser.search_executor import fetch_with_explore, resolve_session_for_mode
from job.common_schema import normalize_common_job
from platforms.search_contract import (
    DEFAULT_EXECUTION_BUDGET,
    SearchSession,
    normalize_city,
    normalize_mode,
)


def fetch_boss_jobs(
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
    from tools.boss_job_search import (
        ensure_logged_in,
        evaluate_retry,
        load_more_batches,
        navigate_to_boss_search,
        open_search,
        resolve_boss_content_target,
        vue_item_to_job,
        READ_LIST_JS,
        wait_for_list,
    )

    platform = "boss"
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
        check_human_verification(page, "连接已有 Edge 之后")

        def ensure_result_page() -> None:
            # Fresh always enters the result set. Continue re-enters only if needed.
            if mode_norm == "fresh":
                snapshot = open_search(page, keyword, city_norm)
                content = resolve_boss_content_target(page)
                ensure_logged_in(snapshot, page, content=content)
                return
            from tools.boss_job_search import search_url
            from urllib.parse import quote

            current = page.url or ""
            already = (
                "zhipin.com" in current
                and "/web/geek/jobs" in current
                and (quote(keyword) in current or keyword in current)
            )
            if not already:
                navigate_to_boss_search(page, keyword, city_norm)
            content = resolve_boss_content_target(page)
            snapshot = evaluate_retry(content, READ_LIST_JS)
            ensure_logged_in(snapshot or {}, page, content=content)

        content_holder: dict[str, Any] = {"content": None}

        def _content():
            if content_holder["content"] is None:
                content_holder["content"] = resolve_boss_content_target(page)
            return content_holder["content"]

        def read_available() -> list[dict]:
            content = _content()
            snapshot = evaluate_retry(content, READ_LIST_JS) or {}
            raw = snapshot.get("jobs") or []
            if not raw:
                try:
                    content.wait_for_function(
                        "() => document.querySelectorAll('a.job-name').length > 0",
                        timeout=5_000,
                    )
                except Exception:
                    pass
                snapshot = evaluate_retry(content, READ_LIST_JS) or {}
                raw = snapshot.get("jobs") or []
                if not raw:
                    snapshot = wait_for_list(content)
                    raw = (snapshot or {}).get("jobs") or []
            jobs = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                jobs.append(normalize_common_job(vue_item_to_job(item, fallback_city=city_norm), platform="boss"))
            return jobs

        def explore_once() -> list[dict] | None:
            content = _content()
            before = read_available()
            load_more_batches(content, target_count=len(before) + 1, max_rounds=1)
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
        reraise_as_human_gate(exc, source="search_boss_jobs")
        raise
    finally:
        if owns_session:
            disconnect_edge(playwright, browser)
