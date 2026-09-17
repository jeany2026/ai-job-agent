from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from browser.edge_session import connect_existing_edge, disconnect_edge
from tools.boss_job_search import BOSS_LOGGED_IN_JS

JS = """() => ({
  chat: !!document.querySelector('a[href*="/web/geek/chat"]'),
  resume: !!document.querySelector('a[href*="/web/geek/resume"]'),
  chatFull: !!document.querySelector('a[href*="geek/chat"]'),
  userNav: !!document.querySelector('.user-nav'),
  headerMessage: !!document.querySelector('[ka="header-message"]'),
  headerResume: !!document.querySelector('[ka="header-resume"]'),
  headerUsername: !!document.querySelector('[ka="header-username"]'),
  jobName: document.querySelectorAll('a.job-name').length,
  loginExact: Array.from(document.querySelectorAll('a, button')).some((el) => {
    const t = ((el.innerText || el.textContent || '') + '').replace(/\\s+/g, '');
    return t === '登录/注册' || t === '立即登录' || t === '短信登录';
  }),
})"""


def main() -> int:
    pw, browser, page = connect_existing_edge()
    try:
        pages = [p for ctx in browser.contexts for p in ctx.pages]
        print("reuse_picks", page.url)
        geek = next(p for p in pages if "/web/geek/jobs" in (p.url or ""))
        detail = next((p for p in pages if "job_detail" in (p.url or "")), None)
        for label, p in [("geek", geek), ("detail", detail), ("reuse", page)]:
            if p is None:
                continue
            r = p.evaluate(JS)
            logged = p.evaluate(BOSS_LOGGED_IN_JS)
            print(label, p.url)
            print(" ", json.dumps(r, ensure_ascii=False), "loggedInJS=", logged)
    finally:
        disconnect_edge(pw, browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
