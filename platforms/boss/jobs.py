"""BOSS platform Tools for the Agent Loop.

search/open return CommonJob. interpret / analyze_job / match_job stay in the Loop.
Does not ask the user to open a JD. Does not apply or chat.
"""

from __future__ import annotations

from agent.human_gate import reraise_as_human_gate
from job.common_schema import normalize_common_job

SEARCH_BOSS_JOBS_SPEC = {
    "name": "search_boss_jobs",
    "description": (
        "Search BOSS jobs via the Agent browser executor. "
        "Returns CommonJob list records. Does not open JD, match, or apply."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "keyword": {"type": "string"},
            "city": {"type": "string", "default": "深圳"},
            "limit": {"type": "integer", "default": 8},
        },
        "required": ["keyword"],
    },
}

OPEN_BOSS_JOB_SPEC = {
    "name": "open_boss_job",
    "description": (
        "Open a BOSS job from the geek jobs list (prefer card click / right pane) "
        "and read JD. Falls back to job_detail navigation only when needed. "
        "Does not interpret intents, analyze JD, or match."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_url": {"type": "string"},
            "job_id": {"type": "string"},
        },
    },
}


def search_boss_jobs(
    keyword: str,
    city: str | None = None,
    limit: int = 8,
    page=None,
    mode: str = "fresh",
    search_session=None,
    execution_budget: int | None = None,
    **_unused,
) -> dict:
    """List-level CommonJob records via Browser Search Executor. No JD open/match."""
    from platforms.boss.search_executor import fetch_boss_jobs
    from platforms.search_contract import DEFAULT_EXECUTION_BUDGET

    try:
        return fetch_boss_jobs(
            keyword=keyword,
            city=city,
            limit=limit,
            mode=mode,
            search_session=search_session,
            page=page,
            execution_budget=DEFAULT_EXECUTION_BUDGET if execution_budget is None else execution_budget,
        )
    except Exception as exc:
        reraise_as_human_gate(exc, source="search_boss_jobs")
        raise



def open_boss_job(
    job_url: str | None = None,
    job_id: str | None = None,
    page=None,
    *,
    navigate: bool = True,
) -> dict:
    """Open JD via list-card click when possible; fallback goto. Loop analyzes/matches.

    navigate=False (inspect_job): read current page only; never goto/go_back/click.
    """
    from browser.edge_session import check_human_verification, connect_existing_edge, disconnect_edge
    from tools.boss_job_search import (
        extract_job_page_actions,
        job_url_from_id,
        open_listed_job,
        resolve_boss_content_target,
        _null_if_blank,
    )

    url = _null_if_blank(job_url) or job_url_from_id(job_id)
    if not url or "zhipin.com" not in url:
        raise ValueError("需要有效的 BOSS job_url 或 job_id")

    owns_session = page is None
    playwright = None
    browser = None
    try:
        if owns_session:
            playwright, browser, page = connect_existing_edge()
        check_human_verification(page, "连接已有 Edge 之后")
        job = open_listed_job(page, url=url, job_id=job_id, navigate=navigate)
        content = resolve_boss_content_target(page)
        job["raw_actions"] = extract_job_page_actions(content)
        job.pop("action_analysis", None)
        return normalize_common_job(job, platform="boss")
    except Exception as exc:
        reraise_as_human_gate(exc, source="open_boss_job")
        raise
    finally:
        if owns_session:
            disconnect_edge(playwright, browser)
