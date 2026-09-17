"""Attach to the dedicated Agent Edge used by recruitment Tools.

Tool execution infrastructure: ensure Agent Edge CDP is up, then connect.
Does not launch or touch the user's daily Edge profile. Not a Reasoner Action.
"""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from browser.edge_launcher import cdp_url, ensure_agent_edge

DEFAULT_CDP_PORT = 9222
DEFAULT_CDP_ENDPOINT = f"http://127.0.0.1:{DEFAULT_CDP_PORT}"
HUMAN_VERIFY_MESSAGE = "检测到网站要求人工验证，POC 已停止。"

BLOCKER_KEYWORDS = (
    "请完成安全验证",
    "拖动滑块",
    "滑动验证",
    "人机验证",
    "访问异常",
    "网络环境异常",
    "当前访问行为异常",
    "检测到异常访问",
    "访问存在风险",
    "请进行验证",
    "verify you are human",
    "access denied",
)


class HumanVerificationStopped(RuntimeError):
    """Site asked for captcha / slider / risk control. Do not bypass."""


class EdgeNotConnected(RuntimeError):
    """Agent Edge is up (or expected), but CDP attach / page reuse failed."""


def visible_text(page) -> str:
    try:
        return page.inner_text("body") or ""
    except Exception:
        return ""


def find_human_verification(text: str, url: str) -> str | None:
    haystack = f"{url}\n{text}".lower()
    for keyword in BLOCKER_KEYWORDS:
        if keyword.lower() in haystack:
            return keyword
    return None


def check_human_verification(page, step: str) -> None:
    url = page.url or ""
    text = visible_text(page)
    hit = find_human_verification(text, url)
    if hit:
        print(HUMAN_VERIFY_MESSAGE)
        print(f"发生在哪一步：{step}")
        print(f"当前 URL：{url}")
        print(f"命中信号：{hit}")
        raise HumanVerificationStopped(hit)


# Minimum gap between navigations / list clicks — reduces BOSS risk-control triggers.
_MIN_BROWSER_ACTION_GAP_MS = 900
_last_browser_action_at = 0.0


def pace_browser_action(page, min_gap_ms: int = _MIN_BROWSER_ACTION_GAP_MS) -> None:
    """Wait out a short gap before the next goto/click. Safe no-op if page has no wait."""
    global _last_browser_action_at
    import time

    now = time.monotonic()
    elapsed_ms = (now - _last_browser_action_at) * 1000.0
    wait_ms = max(0, int(min_gap_ms - elapsed_ms))
    if wait_ms > 0:
        waiter = getattr(page, "wait_for_timeout", None)
        if callable(waiter):
            waiter(wait_ms)
        else:
            time.sleep(wait_ms / 1000.0)
    _last_browser_action_at = time.monotonic()


def connect_existing_edge(cdp_endpoint: str | None = None):
    """Ensure dedicated Agent Edge, then CDP-attach. Reuse if already ready.

    Returns (playwright, browser, page). Never opens the daily Edge profile.
    """
    endpoint = (cdp_endpoint or "").strip() or None
    if endpoint is None:
        info = ensure_agent_edge()
        endpoint = str(info.get("cdp_url") or cdp_url())

    try:
        playwright = sync_playwright().start()
    except Exception as exc:
        raise EdgeNotConnected(f"Playwright 启动失败，无法建立招聘浏览调试连接：{exc}") from exc

    try:
        browser = playwright.chromium.connect_over_cdp(endpoint)
    except Exception as exc:
        playwright.stop()
        raise EdgeNotConnected(
            "Agent 招聘浏览器已准备，但无法建立 CDP 调试连接。"
            "请重试；无需手动开日常 Edge 或勾选 inspect。"
            f" 原始错误：{exc}"
        ) from exc

    try:
        page = get_reusable_page(browser)
    except EdgeNotConnected:
        playwright.stop()
        raise
    return playwright, browser, page


def disconnect_edge(playwright=None, browser=None) -> None:
    """Drop the CDP connection. Do not close Agent Edge."""
    del browser
    if playwright is not None:
        try:
            playwright.stop()
        except Exception:
            pass


def get_reusable_page(browser, *, prefer_host: str | None = None):
    """Reuse a job-site or blank tab. Never keep the Agent on unrelated pages (e.g. Baidu).

    Prefer: matching host → known job hosts → about:blank → new tab.
    On BOSS, prefer an existing /web/geek/jobs list tab over a leftover job_detail tab
    so search/open does not keep reloading the last detail page.
    Opening the job URL is still the Tool's job after this returns.
    """
    pages = []
    for context in browser.contexts:
        pages.extend(context.pages)

    def url_of(page) -> str:
        try:
            return page.url or ""
        except Exception:
            return ""

    prefer = (prefer_host or "").strip().lower()
    if prefer:
        preferred = [p for p in pages if prefer in url_of(p).lower()]
        if preferred:
            page = _prefer_list_tab(preferred, url_of)
            _show(page)
            return page

    for marker in ("zhipin.com", "liepin.com", "51job.com", "www.51job"):
        matched = [p for p in pages if marker in url_of(p).lower()]
        if matched:
            page = _prefer_list_tab(matched, url_of) if "zhipin.com" in marker else matched[-1]
            _show(page)
            return page

    blanks = []
    for page in pages:
        url = url_of(page)
        if url.startswith("edge://") or url.startswith("devtools://"):
            continue
        if not url or url == "about:blank":
            blanks.append(page)
    if blanks:
        page = blanks[-1]
        _show(page)
        return page

    contexts = list(browser.contexts or [])
    if contexts:
        page = contexts[0].new_page()
        _show(page)
        return page

    raise EdgeNotConnected(
        "Agent 招聘浏览器已连接，但没有可用标签页。"
        "请重试；Agent 会自行打开招聘站点。"
    )


def _prefer_list_tab(matched: list, url_of) -> object:
    """Among zhipin tabs, reuse geek jobs list before a sticky job_detail."""
    lists = [p for p in matched if "/web/geek/jobs" in url_of(p).lower()]
    if lists:
        return lists[-1]
    return matched[-1]


def _show(page):
    try:
        page.bring_to_front()
    except Exception:
        pass
    return page
