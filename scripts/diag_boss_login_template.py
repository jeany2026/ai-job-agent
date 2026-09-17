from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from browser.edge_launcher import cdp_url, ensure_agent_edge
from playwright.sync_api import sync_playwright


def eval_retry(page, js):
    last = None
    for _ in range(8):
        try:
            return page.evaluate(js)
        except Exception as exc:
            last = exc
            time.sleep(0.3)
    raise last


def main() -> int:
    info = ensure_agent_edge()
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(str(info.get("cdp_url") or cdp_url()))
    try:
        pages = [p for c in browser.contexts for p in c.pages]
        geek = next(p for p in pages if "/web/geek/jobs" in (p.url or ""))
        print("url", geek.url)

        tpl = r"""() => {
          const part = '/web/geek/chat';
          const withTpl = !!document.querySelector(`a[href*="${part}"]`);
          const withConcat = !!document.querySelector('a[href*="' + part + '"]');
          return { withTpl, withConcat, hrefSample: (document.querySelector('a[href*="geek/chat"]') || {}).href || null };
        }"""
        print("template test", eval_retry(geek, tpl))

        # Repeat BOSS_LOGGED_IN_JS style many times
        from tools.boss_job_search import BOSS_LOGGED_IN_JS

        results = []
        for _ in range(10):
            results.append(eval_retry(geek, BOSS_LOGGED_IN_JS))
            time.sleep(0.05)
        print("10x BOSS_LOGGED_IN_JS", results)

        concat_js = r"""() => {
          const hrefHas = (part) => !!document.querySelector('a[href*="' + part + '"]');
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
          if (loginCta && !geekNav && !geekJobs) return false;
          return !!(geekNav || geekJobs);
        }"""
        results2 = []
        for _ in range(10):
            results2.append(eval_retry(geek, concat_js))
            time.sleep(0.05)
        print("10x concat version", results2)
    finally:
        pw.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
