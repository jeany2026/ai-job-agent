from __future__ import annotations

import difflib
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from browser.edge_launcher import cdp_url, ensure_agent_edge
from playwright.sync_api import sync_playwright
from tools.boss_job_search import BOSS_LOGGED_IN_JS, READ_LIST_JS

# Extract login logic from READ_LIST_JS for comparison
m = re.search(
    r"const hrefHas = .*?return \{\n    url:",
    READ_LIST_JS,
    flags=re.S,
)
print("READ_LIST login fragment found", bool(m))
if m:
    frag = m.group(0)
    print("--- READ_LIST fragment ---")
    print(frag[:500])

print("--- BOSS_LOGGED_IN_JS ---")
print(BOSS_LOGGED_IN_JS)

# Normalize both to compare core
a = BOSS_LOGGED_IN_JS.strip()
# Build equivalent from READ_LIST pieces


def eval_retry(page, js):
    last = None
    for _ in range(6):
        try:
            return page.evaluate(js)
        except Exception as exc:
            last = exc
            time.sleep(0.35)
    raise last


def main() -> int:
    info = ensure_agent_edge()
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(str(info.get("cdp_url") or cdp_url()))
    try:
        pages = [p for c in browser.contexts for p in c.pages]
        geek = next(p for p in pages if "/web/geek/jobs" in (p.url or ""))
        print("url", geek.url)
        print("logged_in_js", eval_retry(geek, BOSS_LOGGED_IN_JS), type(eval_retry(geek, BOSS_LOGGED_IN_JS)))
        snap = eval_retry(geek, READ_LIST_JS)
        print("read_list.loggedIn", snap.get("loggedIn"))
        # Exact True check as ensure_logged_in does
        raw = eval_retry(geek, BOSS_LOGGED_IN_JS)
        print("raw is True?", raw is True, "raw == True?", raw == True, "bool(raw)?", bool(raw), "repr", repr(raw))
        # Try evaluate with function reference style using JS handle
        alt = eval_retry(
            geek,
            "async () => { const fn = " + BOSS_LOGGED_IN_JS + "; return await fn(); }",
        )
        print("alt async invoke", alt)
        alt2 = eval_retry(geek, "(" + BOSS_LOGGED_IN_JS + ")()")
        print("alt2 IIFE", alt2)
    finally:
        pw.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
