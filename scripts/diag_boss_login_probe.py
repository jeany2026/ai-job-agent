"""One-shot: probe Agent Edge BOSS page against current login detectors."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from browser.edge_session import connect_existing_edge, disconnect_edge
from tools.boss_job_search import (
    BOSS_LOGGED_IN_JS,
    READ_LIST_JS,
    resolve_boss_content_target,
    snapshot_indicates_boss_login,
)

PROBE_JS = """() => {
  const hrefHas = (part) => !!document.querySelector('a[href*="' + part + '"]');
  const loginCtas = Array.from(document.querySelectorAll('a, button')).filter((el) => {
    const t = ((el.innerText || el.textContent || '') + '').replace(/\\s+/g, '');
    return t === '登录/注册' || t === '立即登录' || t === '短信登录';
  }).map((el) => ((el.innerText || '') + '').trim()).slice(0, 5);
  const geekLinks = Array.from(document.querySelectorAll('a[href*="/web/geek/"]'))
    .map((a) => a.getAttribute('href'))
    .filter(Boolean)
    .slice(0, 25);
  const headerHits = {
    ka_username: !!document.querySelector('[ka="header-username"]'),
    personal_center: !!document.querySelector('a.personal-center'),
    nav_figure: !!document.querySelector('.nav-figure'),
    header_figure: !!document.querySelector('.header-figure, .user-nav, .label-text'),
  };
  const nameHit = Array.from(document.querySelectorAll('a, span, div'))
    .some((el) => ((el.innerText || '').trim()) === '鲍玲俐');
  const jobCardCandidates = {
    job_name: document.querySelectorAll('a.job-name').length,
    job_card_box: document.querySelectorAll('.job-card-box').length,
    job_card_wrapper: document.querySelectorAll('.job-card-wrapper').length,
    links_job_detail: document.querySelectorAll('a[href*="job_detail"]').length,
    page_jobs: !!document.querySelector('.page-jobs-main, .page-jobs'),
  };
  const headerRight = Array.from(
    document.querySelectorAll('header a, .header a, .nav a, .top-nav a, [class*="header"] a')
  ).slice(0, 40).map((a) => ({
    text: ((a.innerText || '').replace(/\\s+/g, ' ').trim()).slice(0, 40),
    href: (a.getAttribute('href') || '').slice(0, 100),
    className: (a.className || '').toString().slice(0, 80),
    ka: a.getAttribute('ka') || null,
  }));
  return {
    href: location.href,
    path: location.pathname,
    loginCtas,
    geekLinks,
    headerHits,
    nameHit,
    jobCardCandidates,
    headerRight,
    href_recommend: hrefHas('/web/geek/recommend'),
    href_chat: hrefHas('/web/geek/chat'),
    href_resume: hrefHas('/web/geek/resume'),
    href_notify: hrefHas('/web/geek/notify'),
  };
}"""


def main() -> int:
    pw, browser, page = connect_existing_edge()
    try:
        print("PAGE_URL", page.url)
        content = resolve_boss_content_target(page)
        print("CONTENT_IS_PAGE", content is page)
        print("CONTENT_URL", getattr(content, "url", None) or page.url)
        probe = content.evaluate(PROBE_JS)
        print("PROBE", json.dumps(probe, ensure_ascii=False, indent=2))
        logged = content.evaluate(BOSS_LOGGED_IN_JS)
        print("BOSS_LOGGED_IN_JS", logged)
        snap = content.evaluate(READ_LIST_JS)
        slim = {
            "url": snap.get("url"),
            "loggedIn": snap.get("loggedIn"),
            "vueCount": snap.get("vueCount"),
            "domCount": snap.get("domCount"),
            "jobs_len": len(snap.get("jobs") or []),
            "first_job": (snap.get("jobs") or [None])[0],
        }
        print("SNAPSHOT", json.dumps(slim, ensure_ascii=False, indent=2))
        print("snapshot_indicates_boss_login", snapshot_indicates_boss_login(snap, page.url))
    finally:
        disconnect_edge(pw, browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
