"""BOSS write adapter: open JobContext.job_url and execute a live semantic action.

Read path stays in open_boss_job. This module is the only BOSS click path.
Does not search. Does not interpret by button copy.
"""

from __future__ import annotations

from typing import Any

from agent.human_gate import reraise_as_human_gate
from apply.live_execute import execute_authorized_page_action, execution_result, job_identity_from_payload
from browser.edge_session import check_human_verification, connect_existing_edge, disconnect_edge
from tools.boss_job_search import BOSS_LOGGED_IN_JS, parse_job_id, resolve_boss_content_target


def execute_boss_job_action(
    arguments: dict | None = None,
    *,
    page=None,
    llm_provider=None,
    click_log: list | None = None,
    **_: Any,
) -> dict:
    payload = arguments if isinstance(arguments, dict) else {}
    identity = job_identity_from_payload(payload)
    task_id = payload.get("task_id")
    job_context_id = payload.get("job_context_id") or identity.get("job_key")
    user_authorization = payload.get("user_authorization")

    if (identity.get("platform") or "").strip().lower() not in {"", "boss"}:
        return execution_result(
            ok=False,
            result="failed",
            execution_status="failed",
            verification="failed",
            error="execute_boss_job_action requires platform=boss",
            error_code="unsupported_platform",
            identity=identity,
            task_id=task_id,
            job_context_id=job_context_id,
        )

    job_url = identity.get("job_url")
    job_id = identity.get("job_id")
    if not job_url and job_id:
        from tools.boss_job_search import job_url_from_id

        job_url = job_url_from_id(job_id)
        identity["job_url"] = job_url
    if not job_url or "zhipin.com" not in str(job_url):
        return execution_result(
            ok=False,
            result="failed",
            execution_status="failed",
            verification="failed",
            error="JobContext.job_url is required for BOSS execution",
            error_code="missing_job_url",
            identity=identity,
            task_id=task_id,
            job_context_id=job_context_id,
        )

    owns_session = page is None
    playwright = None
    browser = None
    try:
        if owns_session:
            playwright, browser, page = connect_existing_edge()
        check_human_verification(page, "execute_boss_job_action connect")
        _goto_job(page, job_url, job_id)
        check_human_verification(page, "execute_boss_job_action after goto")
        _ensure_boss_login(page)
        return execute_authorized_page_action(
            page,
            identity,
            user_authorization=user_authorization,
            llm_provider=llm_provider,
            task_id=task_id,
            job_context_id=job_context_id,
            click_log=click_log,
        )
    except Exception as exc:
        reraise_as_human_gate(exc, source="execute_job_action")
        raise
    finally:
        if owns_session:
            disconnect_edge(playwright, browser)


def _goto_job(page, job_url: str, job_id: str | None) -> None:
    current = getattr(page, "url", "") or ""
    current_id = parse_job_id(current)
    target_id = job_id or parse_job_id(job_url)
    if current_id and target_id and current_id == target_id:
        return
    page.goto(job_url, wait_until="domcontentloaded", timeout=60_000)


def _ensure_boss_login(page) -> None:
    url = getattr(page, "url", "") or ""
    if "zhipin.com" not in url:
        return
    target = resolve_boss_content_target(page)
    try:
        logged_in = bool(target.evaluate(BOSS_LOGGED_IN_JS))
    except Exception:
        logged_in = False
    if logged_in:
        return
    raise RuntimeError("未检测到 BOSS 登录态。请先在 Edge 中手动登录 BOSS。")
