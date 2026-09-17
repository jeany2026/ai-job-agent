from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from browser.edge_launcher import cdp_url, ensure_agent_edge
from playwright.sync_api import sync_playwright
from tools.boss_job_search import BOSS_LOGGED_IN_JS, READ_LIST_JS, snapshot_indicates_boss_login

DEBUG_JS = r"""() => {
  const hrefHas = (part) => !!document.querySelector(`a[href*="${part}"]`);
  const loginCta = Array.from(document.querySelectorAll('a, button')).some((el) => {
    const t = ((el.innerText || el.textContent || '') + '').replace(/\s+/g, '');
    return t === '登录/注册' || t === '立即登录' || t === '短信登录';
  });
  const geekNav =
    hrefHas('/web/geek/recommend') ||
    hrefHas('/web/geek/chat') ||
    hrefHas('/web/geek/resume') ||
    hrefHas('/web/geek/notify') ||
    !!document.querySelector('[ka="header-username"], a.personal-center, .nav-figure');
  const geekJobs =
    /\/web\/geek\//.test(location.pathname || '') &&
    document.querySelectorAll('a.job-name').length > 0;
  return {
    path: location.pathname,
    loginCta,
    geekNav,
    geekJobs,
    href_chat: hrefHas('/web/geek/chat'),
    href_resume: hrefHas('/web/geek/resume'),
    header_username: !!document.querySelector('[ka="header-username"]'),
    job_name: document.querySelectorAll('a.job-name').length,
    result: (loginCta && !geekNav && !geekJobs) ? false : !!(geekNav || geekJobs),
  };
}"""


def eval_retry(page, js, tries=5):
    last = None
    for _ in range(tries):
        try:
            return page.evaluate(js)
        except Exception as exc:
            last = exc
            time.sleep(0.4)
    raise last


def main() -> int:
    info = ensure_agent_edge()
    endpoint = str(info.get("cdp_url") or cdp_url())
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(endpoint)
    try:
        pages = [p for ctx in browser.contexts for p in ctx.pages]
        print("tabs:")
        for i, p in enumerate(pages):
            print(f"  [{i}] {p.url}")
        geek = next(p for p in pages if "/web/geek/jobs" in (p.url or ""))
        print("using", geek.url)
        print("BOSS_LOGGED_IN_JS:", eval_retry(geek, BOSS_LOGGED_IN_JS))
        print("DEBUG:", json.dumps(eval_retry(geek, DEBUG_JS), ensure_ascii=False, indent=2))
        snap = eval_retry(geek, READ_LIST_JS)
        print(
            "READ_LIST:",
            {
                "loggedIn": snap.get("loggedIn"),
                "vueCount": snap.get("vueCount"),
                "domCount": snap.get("domCount"),
                "jobs": len(snap.get("jobs") or []),
            },
        )
        print("indicates:", snapshot_indicates_boss_login(snap, geek.url))
    finally:
        pw.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
