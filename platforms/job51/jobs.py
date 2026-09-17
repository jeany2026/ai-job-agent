"""51Job (前程无忧) platform Tools for the Agent Loop.

search/open return CommonJob. interpret / analyze_job / match_job stay in the Loop.
Does not ask the user to open a JD. Does not apply or chat.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, urlparse

from agent.human_gate import reraise_as_human_gate
from job.common_schema import normalize_common_job

JOB51_JOB_HOST = "jobs.51job.com"
JOB51_SEARCH_PATH = "https://we.51job.com/pc/search"

# City codes used by 51Job search URLs (jobArea). Unknown cities pass through as text.
JOB51_CITY_IDS = {
    "北京": "010000",
    "上海": "020000",
    "广州": "030200",
    "深圳": "040000",
    "杭州": "080200",
    "成都": "090200",
    "南京": "070200",
    "武汉": "180200",
    "西安": "200200",
    "苏州": "070300",
    "长沙": "190200",
    "重庆": "060000",
}

SEARCH_51JOB_JOBS_SPEC = {
    "name": "search_51job_jobs",
    "description": (
        "Search 51Job jobs in the user's already-logged-in Edge. "
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

OPEN_51JOB_JOB_SPEC = {
    "name": "open_51job_job",
    "description": (
        "Navigate to a 51Job job URL in the current Edge tab, read JD, "
        "and optionally return raw_actions. Does not interpret intents, analyze JD, or match."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_url": {"type": "string"},
            "job_id": {"type": "string"},
        },
    },
}

EXTRACT_LIST_JS = r"""() => {
  /* extract_51job_job_list */
  const textOf = (el) => ((el && el.innerText) || '').replace(/\s+/g, ' ').trim();
  const absUrl = (href) => {
    if (!href) return '';
    try { return new URL(href, location.href).href; } catch (e) { return href; }
  };
  const jobIdFromUrl = (href) => {
    try {
      const u = new URL(href, location.href);
      const jobid = u.searchParams.get('jobid') || u.searchParams.get('jobId') || u.searchParams.get('job_id');
      if (jobid) return String(jobid);
      const htmlId = (u.pathname || '').match(/\/(\d+)\.html$/i);
      if (htmlId) return htmlId[1];
      const jobPath = (u.pathname || '').match(/\/job\/([^./?#]+)/i);
      return jobPath ? jobPath[1] : '';
    } catch (e) {
      return '';
    }
  };
  const isJobHref = (href) => {
    if (!/51job\.com/i.test(href)) return false;
    if (/\/pc\/search/i.test(href) || /search\.51job\.com\/list/i.test(href)) return false;
    return /jobs\.51job\.com\/.+\.html/i.test(href) || /[?&]jobid=/i.test(href) || /\/job\//i.test(href);
  };
  const pick = (root, selectors) => {
    for (const sel of selectors) {
      const el = root.querySelector(sel);
      if (el && textOf(el)) return textOf(el);
    }
    return '';
  };
  const seen = new Set();
  const jobs = [];
  const anchors = Array.from(document.querySelectorAll(
    'a[href*="jobs.51job.com"], a[href*="jobid="], a[href*="/job/"]'
  ));
  for (const a of anchors) {
    const href = absUrl(a.getAttribute('href') || a.href || '');
    if (!isJobHref(href)) continue;
    const job_id = jobIdFromUrl(href);
    if (!job_id || seen.has(job_id)) continue;
    const card = a.closest(
      '[class*="joblist"], [class*="job-item"], [class*="job-card"], [class*="e "], li, article, [data-jobid], [data-job-id]'
    ) || a.parentElement || a;
    const job_title = textOf(a).slice(0, 80) || pick(card, [
      '[class*="jname"]', '[class*="job-title"]', '[class*="job-name"]', 'p.t', 'h3', 'h2'
    ]);
    if (!job_title) continue;
    seen.add(job_id);
    jobs.push({
      job_id,
      job_url: href.split('?')[0],
      job_title,
      company_name: pick(card, ['[class*="cname"]', '[class*="company-name"]', '[class*="comp-name"]', '[class*="company"] a']),
      city: pick(card, ['[class*="job-area"]', '[class*="area"]', '[class*="city"]', '[class*="location"]']),
      salary: pick(card, ['[class*="sal"]', '[class*="salary"]', '[class*="job-salary"]']),
      experience: pick(card, ['[class*="experience"]', '[class*="work-year"]']),
      education: pick(card, ['[class*="edu"]']),
      company_industry: pick(card, ['[class*="industry"]']),
      company_size: pick(card, ['[class*="scale"]', '[class*="company-size"]']),
    });
    if (jobs.length >= 40) break;
  }
  const url = location.href || '';
  const loggedIn = !/login\.51job\.com/i.test(url) && !/passport\.51job\.com/i.test(url) && !/\/login/i.test(url);
  return { jobs, loggedIn, url };
}"""

EXTRACT_DETAIL_JS = r"""() => {
  /* extract_51job_job_detail */
  const textOf = (el) => ((el && el.innerText) || '').replace(/\s+/g, ' ').trim();
  const h1 = document.querySelector('h1, [class*="job-title"], [class*="jname"], [class*="cn"] h1');
  const company = document.querySelector(
    '[class*="cname"], [class*="company-name"], [class*="comp-name"], a[href*="/company/"], a[href*="jobs.51job.com/all/"]'
  );
  const bodyEl = document.querySelector(
    '[class*="job-detail"], [class*="job_msg"], [class*="bmsg"], [class*="job-description"], article, main'
  ) || document.body;
  return {
    url: location.href,
    title: document.title || '',
    h1: h1 ? textOf(h1) : '',
    company_name: company ? textOf(company).slice(0, 80) : '',
    body: textOf(bodyEl).slice(0, 8000),
  };
}"""

EXTRACT_ACTIONS_JS = r"""() => {
  /* extract_51job_page_actions */
  const visible = (el) => {
    if (!el || el.nodeType !== 1) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 2 && rect.height > 2;
  };
  const textOf = (el) => ((el && (el.innerText || el.value || '')) + '').replace(/\s+/g, ' ').trim();
  const skipChrome = (el) => !!(el.closest && el.closest(
    'nav, header, footer, [role="navigation"], [class*="similar"], [class*="recommend"]'
  ));
  const firstMatch = (selectors, root) => {
    const base = root || document;
    for (const sel of selectors) {
      const el = base.querySelector(sel);
      if (el) return el;
    }
    return null;
  };
  const detailRoot = firstMatch([
    '[class*="job-detail"]',
    '[class*="job-apply"]',
    '[class*="apply"]',
    '[class*="op"]',
    'article',
    'main',
  ]) || document.body;
  const opRoot = firstMatch([
    '[class*="apply"]',
    '[class*="op-btn"]',
    '[class*="job-buttons"]',
    '[class*="btn-container"]',
    '[class*="delivery"]',
  ], detailRoot);
  const scope = opRoot || detailRoot;
  const nodes = Array.from(scope.querySelectorAll(
    'button, [role="button"], a[href], input[type="button"], input[type="submit"]'
  ));
  const seen = new Set();
  const actions = [];
  for (const el of nodes) {
    if (!visible(el) || skipChrome(el)) continue;
    const text = textOf(el).slice(0, 80);
    const aria = ((el.getAttribute && el.getAttribute('aria-label')) || '').trim();
    const title = ((el.getAttribute && el.getAttribute('title')) || '').trim();
    const hrefRaw = el.tagName === 'A' ? (el.getAttribute('href') || el.href || '') : '';
    const href = (hrefRaw || '').trim();
    const role = ((el.getAttribute && (el.getAttribute('role') || el.getAttribute('type'))) || '').trim();
    const hrefNorm = href.toLowerCase();
    if (!text && !aria && !title && (!href || hrefNorm.startsWith('javascript:') || hrefNorm === '#' || hrefNorm.endsWith('#'))) {
      continue;
    }
    const key = [el.tagName, text, aria, href].join('|');
    if (seen.has(key)) continue;
    seen.add(key);
    const parentText = el.parentElement ? textOf(el.parentElement).slice(0, 80) : '';
    const id = el.id ? ('#' + el.id) : '';
    actions.push({
      text: text || null,
      aria_label: aria || null,
      title: title || null,
      role: role || el.tagName.toLowerCase(),
      href: href || null,
      tag: el.tagName.toLowerCase(),
      surrounding_text: parentText || null,
      dom_context: (el.tagName.toLowerCase() + id + (role ? ('[role=' + role + ']') : '')).slice(0, 120),
    });
    if (actions.length >= 12) break;
  }
  return actions;
}"""


def _null_if_blank(value) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def parse_job_id(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("jobid", "jobId", "job_id"):
        values = query.get(key)
        if values and _null_if_blank(values[0]):
            return _null_if_blank(values[0])
    match = re.search(r"/(\d+)\.html", parsed.path)
    if match:
        return match.group(1)
    match = re.search(r"/job/([^./?#]+)", parsed.path)
    return match.group(1) if match else None


def job_url_from_id(job_id: str | None) -> str | None:
    job_id = _null_if_blank(job_id)
    if not job_id:
        return None
    return f"https://{JOB51_JOB_HOST}/all/{job_id}.html"


def search_url(keyword: str, city: str) -> str:
    url = f"{JOB51_SEARCH_PATH}?keyword={quote(keyword)}&searchType=2"
    city_id = JOB51_CITY_IDS.get(city) or city
    if city_id:
        url += f"&jobArea={quote(str(city_id))}"
    return url


def _is_51job_job_url(url: str | None) -> bool:
    text = (url or "").lower()
    if "51job.com" not in text:
        return False
    return parse_job_id(text) is not None


def _split_detail_text(body: str) -> tuple[str | None, str | None]:
    """Split visible detail text into description / requirements. No guessing."""
    if not body:
        return None, None
    desc_labels = ("岗位职责", "职位描述", "工作内容", "职责描述")
    req_labels = ("任职要求", "岗位要求", "任职资格")

    def slice_after(label: str) -> str | None:
        idx = body.find(label)
        if idx < 0:
            return None
        rest = body[idx + len(label) :].lstrip(" ：:：")
        cut_at = len(rest)
        for other in desc_labels + req_labels + ("工作地址", "公司介绍"):
            if other == label:
                continue
            found = rest.find(other)
            if 4 <= found < cut_at:
                cut_at = found
        chunk = rest[:cut_at].strip()
        return chunk if len(chunk) >= 4 else None

    desc = None
    req = None
    for label in desc_labels:
        desc = slice_after(label)
        if desc:
            break
    for label in req_labels:
        req = slice_after(label)
        if req:
            break
    if desc is None and req is None:
        text = body.strip()
        return (text or None), None
    return desc, req


def _raw_to_listed(item: dict, *, fallback_city: str | None = None) -> dict:
    job_id = _null_if_blank(item.get("job_id")) or parse_job_id(item.get("job_url"))
    job_url = _null_if_blank(item.get("job_url")) or job_url_from_id(job_id)
    listed = {
        "platform": "job51",
        "job_id": job_id,
        "job_url": job_url,
        "job_title": _null_if_blank(item.get("job_title")),
        "company_name": _null_if_blank(item.get("company_name")),
        "city": _null_if_blank(item.get("city")) or _null_if_blank(fallback_city),
        "salary": _null_if_blank(item.get("salary")),
        "experience": _null_if_blank(item.get("experience")),
        "education": _null_if_blank(item.get("education")),
        "company_industry": _null_if_blank(item.get("company_industry")),
        "company_size": _null_if_blank(item.get("company_size")),
        "job_description": None,
        "requirements": None,
        "benefits": None,
    }
    return normalize_common_job(listed, platform="job51")


def _raw_to_opened(raw: dict, *, job_url: str) -> dict:
    title = _null_if_blank(raw.get("h1")) or _null_if_blank(raw.get("job_title"))
    body = raw.get("body") or ""
    desc, req = _split_detail_text(body)
    opened = {
        "platform": "job51",
        "job_id": parse_job_id(raw.get("url") or job_url) or parse_job_id(job_url),
        "job_url": _null_if_blank(raw.get("url")) or job_url,
        "job_title": title,
        "company_name": _null_if_blank(raw.get("company_name")),
        "job_description": desc,
        "requirements": req,
    }
    return normalize_common_job(opened, platform="job51")


def extract_51job_job_list(page) -> dict:
    raw = page.evaluate(EXTRACT_LIST_JS)
    return raw if isinstance(raw, dict) else {}


def extract_51job_job_detail(page) -> dict:
    raw = page.evaluate(EXTRACT_DETAIL_JS)
    return raw if isinstance(raw, dict) else {}


def extract_51job_page_actions(page) -> list[dict]:
    raw = page.evaluate(EXTRACT_ACTIONS_JS)
    if not isinstance(raw, list):
        return []
    actions = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        actions.append(
            {
                "text": _null_if_blank(item.get("text")),
                "aria_label": _null_if_blank(item.get("aria_label")),
                "title": _null_if_blank(item.get("title")),
                "role": _null_if_blank(item.get("role")),
                "href": _null_if_blank(item.get("href")),
                "tag": _null_if_blank(item.get("tag")),
                "surrounding_text": _null_if_blank(item.get("surrounding_text")),
                "dom_context": _null_if_blank(item.get("dom_context")),
            }
        )
    return actions


def _ensure_logged_in(page, snapshot: dict) -> None:
    url = (page.url or "") + " " + str(snapshot.get("url") or "")
    lowered = url.lower()
    if "login.51job.com" in lowered or "passport.51job.com" in lowered or snapshot.get("loggedIn") is False:
        raise RuntimeError("请先在 Edge 中手动登录前程无忧。")


def _search_on_page(page, keyword: str, city: str, limit: int) -> list[dict]:
    from browser.edge_session import check_human_verification

    target = search_url(keyword, city)
    page.goto(target, wait_until="domcontentloaded", timeout=60_000)
    check_human_verification(page, "打开前程无忧搜索页")
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('a[href*=\"jobs.51job.com\"], a[href*=\"jobid=\"]').length > 0",
            timeout=15_000,
        )
    except Exception:
        pass
    snapshot = extract_51job_job_list(page)
    _ensure_logged_in(page, snapshot)
    raw_jobs = snapshot.get("jobs") or []
    jobs = []
    for item in raw_jobs:
        if not isinstance(item, dict):
            continue
        jobs.append(_raw_to_listed(item, fallback_city=city))
        if len(jobs) >= limit:
            break
    return jobs


def _open_on_page(page, url: str, *, navigate: bool = True) -> dict:
    from browser.edge_session import check_human_verification

    current = page.url or ""
    already_open = parse_job_id(current) is not None and parse_job_id(current) == parse_job_id(url)
    if not already_open:
        if not navigate:
            raise RuntimeError(
                "inspect_job 禁止导航重开页面。当前浏览器不在目标职位详情上；"
                f"当前 URL：{current or '(空白)'}"
            )
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    check_human_verification(page, "打开前程无忧职位详情")
    current_url = page.url or ""
    _ensure_logged_in(
        page,
        {
            "url": current_url,
            "loggedIn": "login.51job.com" not in current_url.lower()
            and "passport.51job.com" not in current_url.lower(),
        },
    )
    job = _raw_to_opened(extract_51job_job_detail(page), job_url=url)
    job["raw_actions"] = extract_51job_page_actions(page)
    job.pop("action_analysis", None)
    return job


def search_51job_jobs(
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
    from platforms.job51.search_executor import fetch_51job_jobs
    from platforms.search_contract import DEFAULT_EXECUTION_BUDGET

    return fetch_51job_jobs(
        keyword=keyword,
        city=city,
        limit=limit,
        mode=mode,
        search_session=search_session,
        page=page,
        execution_budget=DEFAULT_EXECUTION_BUDGET if execution_budget is None else execution_budget,
    )


def open_51job_job(
    job_url: str | None = None,
    job_id: str | None = None,
    page=None,
    *,
    navigate: bool = True,
) -> dict:
    """goto(job_url) + JD + raw_actions. Loop calls interpret/analyze/match. Never clicks apply."""
    from browser.edge_session import connect_existing_edge, disconnect_edge

    url = _null_if_blank(job_url) or job_url_from_id(job_id)
    if not url or not _is_51job_job_url(url):
        raise ValueError("需要有效的前程无忧 job_url 或 job_id")

    owns_session = page is None
    playwright = None
    browser = None
    try:
        if owns_session:
            playwright, browser, page = connect_existing_edge()
        return _open_on_page(page, url, navigate=navigate)
    except Exception as exc:
        reraise_as_human_gate(exc, source="open_51job_job")
        raise
    finally:
        if owns_session:
            disconnect_edge(playwright, browser)
