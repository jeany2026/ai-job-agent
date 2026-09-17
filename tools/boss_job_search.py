"""Agent-callable BOSS job browsing tools.

Uses the flow already verified with Playwright MCP + CDP:

1. Attach to the user's already-logged-in Edge (do not launch Edge).
2. Search on /web/geek/jobs with query + city.
3. Read batch 1 from the page's own Vue jobList (backed by joblist.json).
4. Load batch 2 by scrolling document.documentElement (the real scroller).
5. Open one job via its existing job_detail URL in the same tab (no stable click).
6. Read JD from the detail page, then goBack to the list.

Missing fields stay null. No guessed values. No captcha bypass.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, urlparse

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from browser.edge_session import (  # noqa: E402
    EdgeNotConnected,
    HumanVerificationStopped,
    check_human_verification,
    connect_existing_edge,
    disconnect_edge,
    pace_browser_action,
)
from tools.interpret_job_actions import interpret_job_actions  # noqa: E402

BOSS_JOBS_PATH = "https://www.zhipin.com/web/geek/jobs"

# DOM login signals only (no BOSS API). Prefer geek nav links that stay in the header;
# /web/geek/recommend alone is brittle when the username menu markup changes.
BOSS_LOGGED_IN_JS = r"""() => {
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
  if (loginCta && !geekNav && !geekJobs) return false;
  return !!(geekNav || geekJobs);
}"""

# City IDs used by BOSS itself in /web/geek/jobs?city=...
BOSS_CITY_IDS = {
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    "杭州": "101210100",
    "成都": "101270100",
    "南京": "101190100",
    "武汉": "101200100",
    "西安": "101110100",
    "苏州": "101190400",
    "长沙": "101250100",
    "重庆": "101040100",
}

EMPTY_JOB = {
    "platform": "boss",
    "job_id": None,
    "job_url": None,
    "job_title": None,
    "company_name": None,
    "city": None,
    "salary": None,
    "experience": None,
    "education": None,
    "company_industry": None,
    "company_size": None,
    "job_description": None,
    "requirements": None,
    "benefits": None,
}

READ_LIST_JS = r"""() => {
  const findVm = () => {
    const main = document.querySelector('.page-jobs-main');
    if (main && main.__vue__ && Array.isArray(main.__vue__.jobList)) return main.__vue__;
    const root = document.querySelector('.page-jobs');
    if (root && root.__vue__ && Array.isArray(root.__vue__.$children)) {
      const hit = root.__vue__.$children.find((c) => c && Array.isArray(c.jobList));
      if (hit) return hit;
    }
    return main && main.__vue__ ? main.__vue__ : null;
  };
  const vm = findVm();
  const cards = Array.from(document.querySelectorAll('a.job-name'));
  const vueJobs = (vm && Array.isArray(vm.jobList)) ? vm.jobList.map((j) => ({
    job_id: j.encryptJobId || null,
    job_title: j.jobName || null,
    company_name: j.brandName || null,
    city: j.cityName || null,
    salary: j.salaryDesc || null,
    experience: j.jobExperience || null,
    education: j.jobDegree || null,
    company_industry: j.brandIndustry || null,
    company_size: j.brandScaleName || null,
    benefits: Array.isArray(j.welfareList) && j.welfareList.length ? j.welfareList.join('、') : null,
  })) : [];
  const domJobs = cards.map((a) => {
    const box = a.closest('.job-card-box') || a.closest('li') || a.parentElement;
    const company = box ? box.querySelector('a[href*="/gongsi/"]') : null;
    const cityEl = box ? box.querySelector('[class*="company-location"], [class*="job-area"], .job-card-footer') : null;
    const tags = box ? Array.from(box.querySelectorAll('li, span')).map((el) => (el.innerText || '').trim()) : [];
    const exp = tags.find((t) => /年|经验不限|应届/.test(t)) || null;
    const edu = tags.find((t) => /学历不限|大专|本科|硕士|博士/.test(t)) || null;
    const href = a.getAttribute('href') || '';
    const idMatch = href.match(/job_detail\/([^./]+)/);
    return {
      job_id: idMatch ? idMatch[1] : null,
      job_title: (a.innerText || '').trim() || null,
      company_name: company ? (company.innerText || '').trim() : null,
      city: cityEl ? (cityEl.innerText || '').trim().split(/[·\s]/)[0] : null,
      salary: null,
      experience: exp,
      education: edu,
      company_industry: null,
      company_size: null,
      benefits: null,
    };
  });
  const jobs = vueJobs.length ? vueJobs : domJobs;
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
    /\/web\/geek\//.test(location.pathname || '') && cards.length > 0;
  const loggedIn = (loginCta && !geekNav && !geekJobs) ? false : !!(geekNav || geekJobs);
  return {
    url: location.href,
    title: document.title,
    loggedIn: loggedIn,
    hasMore: !!(vm && vm.hasMore),
    moreLoading: !!(vm && vm.moreLoading),
    pageVo: vm && vm.pageVo ? { page: vm.pageVo.page, pageSize: vm.pageVo.pageSize } : null,
    vueCount: vueJobs.length,
    domCount: cards.length,
    jobs: jobs,
  };
}"""

LOAD_MORE_JS = r"""() => {
  const html = document.documentElement;
  html.scrollTop = html.scrollHeight;
  window.dispatchEvent(new Event('scroll'));
  const vm = document.querySelector('.page-jobs-main') && document.querySelector('.page-jobs-main').__vue__;
  return {
    scrollTop: html.scrollTop,
    vueCount: vm && Array.isArray(vm.jobList) ? vm.jobList.length : 0,
    domCount: document.querySelectorAll('a.job-name').length,
    moreLoading: !!(vm && vm.moreLoading),
    hasMore: !!(vm && vm.hasMore),
  };
}"""

DETAIL_JS = r"""() => {
  const textOf = (el) => ((el && el.innerText) || '').replace(/\s+/g, ' ').trim();
  const h1 = document.querySelector('h1');
  const body = (document.body && document.body.innerText) || '';
  const benefitNodes = Array.from(document.querySelectorAll('div, span, a, li'))
    .map((el) => (el.innerText || '').trim())
    .filter((t) => t && t.length <= 12);
  const knownBenefits = ['五险一金','带薪年假','年终奖','餐补','加班补助','补充医疗保险',
    '定期体检','节日福利','生日福利','团建聚餐','零食下午茶','股票期权','免费班车',
    '宿舍有空调','员工旅游','全勤奖','工龄奖','交通补贴','住房补贴'];
  const benefits = knownBenefits.filter((name) => benefitNodes.includes(name));
  const industryLink = Array.from(document.querySelectorAll('a[href*="/i"]'))
    .map((a) => textOf(a))
    .find((t) => t && t.length <= 20);
  const sizeNode = Array.from(document.querySelectorAll('p, span, div'))
    .map((el) => (el.innerText || '').trim())
    .find((t) => /^\d+\s*[-–]\s*\d+人$|^10000人以上$|^\d+人以上$/.test(t));
  return {
    url: location.href,
    title: document.title,
    h1: h1 ? textOf(h1) : '',
    body: body.slice(0, 8000),
    benefits: benefits,
    industry: industryLink || '',
    company_size: sizeNode || '',
  };
}"""

EXTRACT_JOB_ACTIONS_JS = r"""() => {
  /* extract_job_page_actions: job-detail / operate region only, not whole-page chrome */
  const visible = (el) => {
    if (!el || el.nodeType !== 1) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 2 && rect.height > 2;
  };
  const textOf = (el) => ((el && (el.innerText || el.value || '')) + '').replace(/\s+/g, ' ').trim();
  const skipChrome = (el) => !!(el.closest && el.closest(
    'nav, header, footer, [role="navigation"], [class*="similar"], [class*="recommend-job"], [class*="job-list"]'
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
    '[class*="job-box"]',
    '[class*="detail-content"]',
    'article',
    'main',
  ]) || document.body;
  const opRoot = firstMatch([
    '[class*="job-op"]',
    '[class*="op-area"]',
    '[class*="op-btn"]',
    '[class*="sider-op"]',
    '[class*="btn-container"]',
    '[class*="job-buttons"]',
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
    if isinstance(value, (list, tuple)):
        joined = "、".join(str(x).strip() for x in value if str(x).strip())
        return joined or None
    stripped = str(value).strip()
    return stripped or None


def empty_job() -> dict:
    return dict(EMPTY_JOB)


def parse_job_id(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    match = re.search(r"/job_detail/([^./?]+)", parsed.path)
    return match.group(1) if match else None


def job_url_from_id(job_id: str | None) -> str | None:
    job_id = _null_if_blank(job_id)
    if not job_id:
        return None
    return f"https://www.zhipin.com/job_detail/{job_id}.html"


def search_url(keyword: str, city: str) -> str:
    url = f"{BOSS_JOBS_PATH}?query={quote(keyword)}"
    city_id = BOSS_CITY_IDS.get(city)
    if city_id:
        url += f"&city={city_id}"
    return url


def vue_item_to_job(item: dict, fallback_city: str | None = None) -> dict:
    job = empty_job()
    job_id = _null_if_blank(item.get("job_id"))
    job["job_id"] = job_id
    job["job_url"] = job_url_from_id(job_id)
    job["job_title"] = _null_if_blank(item.get("job_title"))
    job["company_name"] = _null_if_blank(item.get("company_name"))
    job["city"] = _null_if_blank(item.get("city")) or _null_if_blank(fallback_city)
    job["salary"] = _null_if_blank(item.get("salary"))
    job["experience"] = _null_if_blank(item.get("experience"))
    job["education"] = _null_if_blank(item.get("education"))
    job["company_industry"] = _null_if_blank(item.get("company_industry"))
    job["company_size"] = _null_if_blank(item.get("company_size"))
    job["benefits"] = _null_if_blank(item.get("benefits"))
    return job


def split_detail_text(body: str) -> tuple[str | None, str | None]:
    """Split visible detail text into description / requirements. No guessing."""
    if not body:
        return None, None

    desc_labels = ("岗位职责", "工作要求", "职位描述", "工作内容")
    req_labels = ("任职要求", "岗位要求", "任职资格", "工作职责", "【任职要求】")

    def slice_after(label: str) -> str | None:
        idx = body.find(label)
        if idx < 0:
            return None
        rest = body[idx + len(label) :].lstrip(" ：:：")
        cut_at = len(rest)
        for other in desc_labels + req_labels + ("工作地址", "公司介绍", "竞争力分析", "工商信息"):
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
    return _null_if_blank(desc), _null_if_blank(req)


def evaluate_retry(page, expression, retries: int = 6):
    last_error = None
    for _ in range(retries):
        try:
            return page.evaluate(expression)
        except Exception as exc:
            last_error = exc
            message = str(exc)
            if "destroyed" not in message and "navigation" not in message.lower():
                raise
            page.wait_for_timeout(500)
    raise last_error


def resolve_boss_content_target(page):
    """Prefer the frame that actually hosts geek UI (nav / job cards). Falls back to page."""
    frames_attr = getattr(page, "frames", None)
    if not isinstance(frames_attr, (list, tuple)):
        return page
    frames = list(frames_attr)
    if not frames:
        return page

    scored: list[tuple[int, object]] = []
    for frame in frames:
        try:
            score = frame.evaluate(
                """() => {
                  let n = 0;
                  if (document.querySelector('a[href*="/web/geek/chat"], a[href*="/web/geek/resume"], a[href*="/web/geek/recommend"]')) n += 3;
                  n += Math.min(document.querySelectorAll('a.job-name').length, 5);
                  if (document.querySelector('.page-jobs-main, .page-jobs')) n += 2;
                  return n;
                }"""
            )
        except Exception:
            continue
        if isinstance(score, (int, float)) and score > 0:
            scored.append((int(score), frame))
    if not scored:
        return page
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def wait_for_list(page, timeout_ms: int = 20_000) -> dict:
    deadline = timeout_ms
    elapsed = 0
    last = None
    while elapsed <= deadline:
        last = evaluate_retry(page, READ_LIST_JS)
        if last.get("jobs"):
            return last
        page.wait_for_timeout(500)
        elapsed += 500
    url = (last or {}).get("url") or page.url
    raise RuntimeError(
        "已打开 BOSS 搜索页，但没有读到职位卡片。"
        f" url={url} loggedIn={last.get('loggedIn') if last else None}"
        f" vueCount={last.get('vueCount') if last else None}"
        f" domCount={last.get('domCount') if last else None}"
    )


def navigate_to_boss_search(page, keyword: str, city: str) -> None:
    """Open the BOSS geek jobs search URL. Fail closed if still not on zhipin.com."""
    target = search_url(keyword, city)
    current = page.url or ""
    already = (
        "zhipin.com" in current
        and "/web/geek/jobs" in current
        and (quote(keyword) in current or keyword in current)
    )
    if not already:
        pace_browser_action(page)
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            page.evaluate("(url) => { location.assign(url); }", target)
            page.wait_for_timeout(1500)
        try:
            page.wait_for_url(re.compile(r"zhipin\.com"), timeout=30_000)
        except Exception:
            pass
    current = page.url or ""
    if "zhipin.com" not in current:
        raise RuntimeError(
            "未能打开 BOSS 直聘网站，Agent 浏览器仍停留在其他页面："
            f"{current or '(空白)'}。请确认网络后点「继续」重试；"
            "登录检测只会在 zhipin.com 上进行。"
        )


def open_search(page, keyword: str, city: str) -> dict:
    """Navigate to BOSS search, then read the list snapshot (may be empty / logged out)."""
    navigate_to_boss_search(page, keyword, city)
    check_human_verification(page, "打开 BOSS 搜索页")
    content = resolve_boss_content_target(page)
    return evaluate_retry(content, READ_LIST_JS)


def snapshot_indicates_boss_login(snapshot: dict, page_url: str = "") -> bool:
    """True when page browse evidence shows an authenticated geek session."""
    if snapshot.get("loggedIn"):
        return True
    url = (snapshot.get("url") or page_url or "").lower()
    if "zhipin.com" not in url or "/web/geek/" not in url:
        return False
    return _list_count(snapshot) > 0


def ensure_logged_in(snapshot: dict, page, *, content=None) -> None:
    url = getattr(page, "url", "") or ""
    if "zhipin.com" not in url:
        raise RuntimeError(
            "登录检测前尚未打开 BOSS 网站。"
            f" 当前 URL：{url or '(空白)'}"
        )
    if not isinstance(snapshot, dict):
        snapshot = {}
    if snapshot_indicates_boss_login(snapshot, url):
        return

    target = content or resolve_boss_content_target(page)
    try:
        wait = getattr(page, "wait_for_timeout", None)
        if callable(wait):
            wait(600)
        refreshed = evaluate_retry(target, READ_LIST_JS)
    except Exception:
        refreshed = None
    if isinstance(refreshed, dict) and snapshot_indicates_boss_login(refreshed, url):
        snapshot.clear()
        snapshot.update(refreshed)
        return
    try:
        raw = target.evaluate(BOSS_LOGGED_IN_JS)
    except Exception:
        raw = False
    if raw is True:
        return

    raise RuntimeError(
        "当前 Edge 未检测到 BOSS 登录态。请先在 Agent 打开的浏览器窗口中登录 BOSS，然后再继续。"
        f" 当前 URL：{url}"
    )


def _list_count(snapshot: dict) -> int:
    return max(snapshot.get("vueCount") or 0, snapshot.get("domCount") or 0, len(snapshot.get("jobs") or []))


def load_more_batches(page, target_count: int, max_rounds: int = 6) -> dict:
    """Scroll the real document scroller until the job list grows or rounds run out."""
    snapshot = evaluate_retry(page, READ_LIST_JS)
    best = snapshot
    before = _list_count(snapshot)
    if target_count <= before:
        return snapshot
    if snapshot.get("hasMore") is False:
        return snapshot

    for _ in range(max_rounds):
        check_human_verification(page, "加载下一批职位")
        evaluate_retry(page, LOAD_MORE_JS)
        page.wait_for_timeout(1200)
        snapshot = evaluate_retry(page, READ_LIST_JS)
        after = _list_count(snapshot)
        if after > _list_count(best):
            best = snapshot
        if after > before:
            before = after
            if after >= target_count or snapshot.get("hasMore") is False:
                break
            continue
        if not snapshot.get("moreLoading"):
            page.wait_for_timeout(800)
            snapshot = evaluate_retry(page, READ_LIST_JS)
            if _list_count(snapshot) > _list_count(best):
                best = snapshot
            if _list_count(snapshot) == before:
                break
    return best


def read_detail_on_page(page) -> dict:
    check_human_verification(page, "打开职位详情")
    try:
        page.wait_for_function(
            "() => !!document.querySelector('h1')",
            timeout=15_000,
        )
    except Exception:
        pass
    page.wait_for_timeout(500)
    raw = evaluate_retry(page, DETAIL_JS)
    desc, req = split_detail_text(raw.get("body") or "")
    job = empty_job()
    job["job_url"] = _null_if_blank(raw.get("url"))
    job["job_id"] = parse_job_id(job["job_url"])
    job["job_title"] = _null_if_blank(raw.get("h1"))
    job["job_description"] = desc
    job["requirements"] = req
    job["benefits"] = _null_if_blank(raw.get("benefits"))
    job["company_industry"] = _null_if_blank(raw.get("industry"))
    job["company_size"] = _null_if_blank(raw.get("company_size"))
    city_hit = re.search(
        r"(北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|苏州|长沙|重庆)",
        raw.get("body") or "",
    )
    if city_hit:
        job["city"] = city_hit.group(1)
    salary_hit = re.search(
        r"(\d+(?:\.\d+)?\s*[-–~]\s*\d+(?:\.\d+)?\s*[Kk千万](?:·\d+薪)?)",
        (raw.get("h1") or "") + " " + (raw.get("body") or "")[:400],
    )
    if salary_hit:
        job["salary"] = salary_hit.group(1).replace(" ", "")
    exp_hit = re.search(r"(经验不限|应届生?|\d+\s*[-–]\s*\d+年|\d+年以上)", raw.get("body") or "")
    edu_hit = re.search(r"(学历不限|大专|本科|硕士|博士)", raw.get("body") or "")
    if exp_hit:
        job["experience"] = exp_hit.group(1).replace(" ", "")
    if edu_hit:
        job["education"] = edu_hit.group(1)
    company_hit = re.search(r"「[^」]+招聘」_([^招聘]+)招聘", raw.get("title") or "")
    if company_hit:
        job["company_name"] = _null_if_blank(company_hit.group(1))
    return job


def merge_detail(base: dict, detail: dict) -> dict:
    merged = dict(base)
    for key, value in detail.items():
        if value and not merged.get(key):
            merged[key] = value
        elif key in {"job_description", "requirements", "benefits"} and value:
            merged[key] = value
        elif key in {"job_title", "job_url", "job_id"} and value:
            merged[key] = value
    return merged


def extract_job_page_actions(page) -> list[dict]:
    """Collect visible interactive elements on the current JD page. Read-only; never clicks."""
    raw = evaluate_retry(page, EXTRACT_JOB_ACTIONS_JS)
    if not isinstance(raw, list):
        return []
    actions = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        actions.append({
            "text": _null_if_blank(item.get("text")),
            "aria_label": _null_if_blank(item.get("aria_label")),
            "title": _null_if_blank(item.get("title")),
            "role": _null_if_blank(item.get("role")),
            "href": _null_if_blank(item.get("href")),
            "tag": _null_if_blank(item.get("tag")),
            "surrounding_text": _null_if_blank(item.get("surrounding_text")),
            "dom_context": _null_if_blank(item.get("dom_context")),
        })
    return actions


def analyze_extracted_job_actions(job: dict, actions: list[dict], llm_provider=None) -> dict:
    """Hand observed actions to interpret_job_actions. No local semantic rules here."""
    page_context = {
        "platform": job.get("platform") or "boss",
        "job_id": job.get("job_id"),
        "job_url": job.get("job_url"),
        "actions": actions,
    }
    return interpret_job_actions(page_context, llm_provider=llm_provider)


CLICK_JOB_CARD_JS = r"""(jobId) => {
  const id = String(jobId || '');
  if (!id) return { ok: false, reason: 'no_id' };
  const links = Array.from(document.querySelectorAll('a.job-name, a[href*="job_detail/"]'));
  const hit = links.find((a) => {
    const href = a.getAttribute('href') || '';
    return href.includes(id);
  });
  if (!hit) return { ok: false, reason: 'card_not_found', count: links.length };
  const card = hit.closest('.job-card-box')
    || hit.closest('li')
    || hit.closest('[class*="job-card"]')
    || hit;
  try { card.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) {}
  hit.click();
  return { ok: true, href: hit.getAttribute('href') || null };
}"""


def _page_url(page) -> str:
    return getattr(page, "url", "") or ""


def _is_geek_jobs_list(url: str) -> bool:
    lowered = (url or "").lower()
    return "zhipin.com" in lowered and "/web/geek/jobs" in lowered


def click_job_card(page, job_id: str) -> bool:
    """Select a card on the geek jobs list (right-pane detail). No full-page job_detail hop."""
    job_id = _null_if_blank(job_id)
    if not job_id:
        return False
    try:
        result = page.evaluate(CLICK_JOB_CARD_JS, job_id)
    except Exception:
        return False
    return isinstance(result, dict) and result.get("ok") is True


def _finalize_opened_job(job: dict, url: str | None, job_id: str | None) -> dict:
    job = dict(job or {})
    if job_id and not job.get("job_id"):
        job["job_id"] = job_id
    if url and not job.get("job_url"):
        job["job_url"] = url
    elif job.get("job_id") and not job.get("job_url"):
        job["job_url"] = job_url_from_id(job.get("job_id"))
    return job


def open_listed_job(
    page,
    url: str | None = None,
    job_id: str | None = None,
    *,
    navigate: bool = True,
) -> dict:
    """Prefer list-card click on /web/geek/jobs; fall back to paced goto(job_detail).

    navigate=False: never goto/go_back/click — only read if already on the target job.
    """
    job_id = _null_if_blank(job_id) or parse_job_id(url)
    url = _null_if_blank(url) or job_url_from_id(job_id)
    if not url or "zhipin.com" not in url:
        raise ValueError("需要有效的 BOSS job_url 或 job_id")

    content = resolve_boss_content_target(page)
    current = _page_url(content) or _page_url(page)

    if job_id and parse_job_id(current) == job_id:
        check_human_verification(content, "已在目标职位详情")
        return _finalize_opened_job(read_detail_on_page(content), url, job_id)

    if not navigate:
        raise RuntimeError(
            "inspect_job 禁止导航重开页面。当前浏览器不在目标职位详情上；"
            "请基于 World 已 open 的 JD 做 analyze_job / match_job，"
            "或 open_job 其他未探索 listed（不要为重读而 inspect）。"
            f" 当前 URL：{current or '(空白)'}"
        )

    if job_id and not _is_geek_jobs_list(current) and "job_detail" in current:
        pace_browser_action(page)
        try:
            page.go_back(wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            pass
        content = resolve_boss_content_target(page)
        current = _page_url(content) or _page_url(page)

    if job_id and _is_geek_jobs_list(current):
        pace_browser_action(page)
        if click_job_card(content, job_id):
            try:
                content.wait_for_timeout(700)
            except Exception:
                pass
            check_human_verification(content, "列表点选职位后")
            job = read_detail_on_page(content)
            return _finalize_opened_job(job, url, job_id)

    pace_browser_action(page)
    return open_detail_and_return(page, url, analyze_actions=False, return_to_list=False)


def open_detail_and_return(
    page,
    url: str,
    analyze_actions: bool = False,
    llm_provider=None,
    return_to_list: bool = True,
) -> dict:
    current = page.url or ""
    already_open = parse_job_id(current) is not None and parse_job_id(current) == parse_job_id(url)
    if not already_open:
        pace_browser_action(page)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    job = read_detail_on_page(page)
    if analyze_actions:
        try:
            actions = extract_job_page_actions(page)
            job["action_analysis"] = analyze_extracted_job_actions(job, actions, llm_provider=llm_provider)
        except Exception as exc:
            job["action_analysis"] = analyze_extracted_job_actions(job, [], llm_provider=llm_provider)
            if isinstance(job.get("action_analysis"), dict):
                job["action_analysis"]["error"] = str(exc)
    if return_to_list and not already_open:
        pace_browser_action(page)
        page.go_back(wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_function(
                "() => document.querySelectorAll('a.job-name').length > 0",
                timeout=15_000,
            )
        except Exception:
            pass
    return job


def search_boss_jobs(
    keyword: str,
    city: str = "深圳",
    limit: int = 8,
    detail_limit: int = 0,
    page=None,
    mode: str = "fresh",
    search_session=None,
    execution_budget: int | None = None,
) -> list[dict] | dict:
    """Deprecated direct entry. Prefer platforms.boss.jobs.search_boss_jobs.

    detail_limit is ignored (always 0). Production search never opens JD inline.
    Returns FetchResult dict when called with mode/session; legacy list when detail_limit path...
    Actually always routes to Executor FetchResult for Session continuity.
    """
    if int(detail_limit or 0) > 0:
        raise ValueError(
            "search_boss_jobs no longer opens JD inline (detail_limit removed). "
            "Use open_job / analyze_job / match_job via the Agent Loop."
        )
    from platforms.boss.search_executor import fetch_boss_jobs
    from platforms.search_contract import DEFAULT_EXECUTION_BUDGET

    return fetch_boss_jobs(
        keyword=keyword,
        city=city,
        limit=limit,
        mode=mode,
        search_session=search_session,
        page=page,
        execution_budget=DEFAULT_EXECUTION_BUDGET if execution_budget is None else execution_budget,
    )


def open_boss_job(
    job_url: str | None = None,
    job_id: str | None = None,
    page=None,
    llm_provider=None,
    *,
    navigate: bool = True,
) -> dict:
    """Open one job via list click when possible. Never interprets or matches.

    llm_provider is accepted for call-site compatibility but ignored — use
    interpret_job_actions as a separate Tool.
    """
    del llm_provider  # not used; semantic interpret is a separate Tool
    url = _null_if_blank(job_url) or job_url_from_id(job_id)
    if not url or "zhipin.com" not in url:
        raise ValueError("需要有效的 BOSS job_url 或 job_id")

    owns_session = page is None
    playwright = None
    browser = None
    if owns_session:
        playwright, browser, page = connect_existing_edge()

    try:
        check_human_verification(page, "连接已有 Edge 之后")
        job = open_listed_job(page, url=url, job_id=job_id, navigate=navigate)
        job.pop("action_analysis", None)
        return job
    finally:
        if owns_session:
            disconnect_edge(playwright, browser)


def jobs_to_json(jobs: list[dict]) -> str:
    return json.dumps(jobs, ensure_ascii=False, indent=2)
