"""List Agent Edge pages/frames and score login signals on each."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from browser.edge_session import connect_existing_edge, disconnect_edge
from tools.boss_job_search import BOSS_LOGGED_IN_JS, READ_LIST_JS

SCORE_JS = """() => {
  let n = 0;
  if (document.querySelector('a[href*="/web/geek/chat"], a[href*="/web/geek/resume"], a[href*="/web/geek/recommend"]')) n += 3;
  n += Math.min(document.querySelectorAll('a.job-name').length, 5);
  if (document.querySelector('.page-jobs-main, .page-jobs')) n += 2;
  return n;
}"""

SIGNAL_JS = """() => {
  const text = (document.body && (document.body.innerText || '')) || '';
  return {
    href: location.href,
    title: document.title,
    job_name: document.querySelectorAll('a.job-name').length,
    job_detail_links: document.querySelectorAll('a[href*="job_detail"]').length,
    geek_chat: !!document.querySelector('a[href*="/web/geek/chat"]'),
    geek_resume: !!document.querySelector('a[href*="/web/geek/resume"]'),
    geek_recommend: !!document.querySelector('a[href*="/web/geek/recommend"]'),
    ka_username: !!document.querySelector('[ka="header-username"]'),
    nav_figure: !!document.querySelector('.nav-figure'),
    personal_center: !!document.querySelector('a.personal-center'),
    has_name_baolingli: text.includes('鲍玲俐'),
    has_login_cta: /登录\\/注册|立即登录|短信登录/.test(text.replace(/\\s+/g, '')),
    page_jobs: !!document.querySelector('.page-jobs-main, .page-jobs'),
  };
}"""


def main() -> int:
    pw, browser, page = connect_existing_edge()
    try:
        pages = []
        for ctx in browser.contexts:
            pages.extend(ctx.pages)
        print(f"TAB_COUNT {len(pages)}")
        for i, p in enumerate(pages):
            try:
                print(f"TAB[{i}] {p.url}")
            except Exception as exc:
                print(f"TAB[{i}] <err {exc}>")

        print("--- frames on reusable page ---")
        print("REUSE_URL", page.url)
        frames = list(page.frames)
        print(f"FRAME_COUNT {len(frames)}")
        for i, frame in enumerate(frames):
            try:
                score = frame.evaluate(SCORE_JS)
            except Exception as exc:
                print(f"FRAME[{i}] score_err={exc}")
                continue
            try:
                signals = frame.evaluate(SIGNAL_JS)
            except Exception as exc:
                signals = {"error": str(exc)}
            try:
                logged = frame.evaluate(BOSS_LOGGED_IN_JS)
            except Exception as exc:
                logged = f"err:{exc}"
            try:
                snap = frame.evaluate(READ_LIST_JS)
                snap_slim = {
                    "loggedIn": snap.get("loggedIn"),
                    "vueCount": snap.get("vueCount"),
                    "domCount": snap.get("domCount"),
                    "jobs_len": len(snap.get("jobs") or []),
                }
            except Exception as exc:
                snap_slim = {"error": str(exc)}
            print(
                f"FRAME[{i}] score={score} loggedInJS={logged} "
                f"name={getattr(frame, 'name', '')!r} url={getattr(frame, 'url', '')}"
            )
            print("  signals", json.dumps(signals, ensure_ascii=False))
            print("  snapshot", json.dumps(snap_slim, ensure_ascii=False))

        # Also probe every zhipin tab's main frame
        print("--- zhipin tabs main document ---")
        for i, p in enumerate(pages):
            url = ""
            try:
                url = p.url or ""
            except Exception:
                continue
            if "zhipin.com" not in url:
                continue
            try:
                signals = p.evaluate(SIGNAL_JS)
                logged = p.evaluate(BOSS_LOGGED_IN_JS)
                snap = p.evaluate(READ_LIST_JS)
                slim = {
                    "loggedIn": snap.get("loggedIn"),
                    "vueCount": snap.get("vueCount"),
                    "domCount": snap.get("domCount"),
                    "jobs_len": len(snap.get("jobs") or []),
                }
            except Exception as exc:
                print(f"TAB[{i}] evaluate_err={exc} url={url}")
                continue
            print(f"TAB[{i}] loggedInJS={logged} url={url}")
            print("  signals", json.dumps(signals, ensure_ascii=False))
            print("  snapshot", json.dumps(slim, ensure_ascii=False))
    finally:
        disconnect_edge(pw, browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
