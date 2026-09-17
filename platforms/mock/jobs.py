"""Fake job source. Returns CommonJob list/detail. No browser. No matching."""

from __future__ import annotations

from typing import Any

from job.common_schema import empty_job, normalize_common_job

MOCK_SEARCH_JOBS_SPEC = {
    "name": "mock_search_jobs",
    "description": (
        "Fake job search for Agent Loop tests. Returns CommonJob list records. "
        "Does not open JD, match, or apply. Supports mode=fresh|continue via SearchSession."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "keyword": {"type": "string"},
            "city": {"type": "string"},
            "limit": {"type": "integer", "default": 8},
            "mode": {"type": "string", "enum": ["fresh", "continue"], "default": "fresh"},
        },
        "required": ["keyword"],
    },
}

MOCK_OPEN_JOB_SPEC = {
    "name": "mock_open_job",
    "description": (
        "Fake open-job for Agent Loop tests. Returns CommonJob with JD and raw_actions. "
        "Does not interpret intents, analyze JD, or match."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_url": {"type": "string"},
            "job_id": {"type": "string"},
        },
    },
}

_LIST_FIELDS = (
    "platform",
    "job_id",
    "job_url",
    "job_title",
    "company_name",
    "city",
    "salary",
    "experience",
    "education",
    "company_industry",
    "company_size",
)


def _catalog() -> list[dict]:
    """Full mock result set for any search keyword (list simulation only)."""
    return [
        _direct_job(),
        _transferable_job(),
        _hard_gap_job(),
        _applied_job(),
        _blacklist_job(),
        _director_job(),
        _direct_job(),  # duplicate of mock-direct for dedupe
    ]


def _items_for_keyword(keyword: str | None) -> list[dict]:
    # Keyword only labels the query; catalog is not gated on exact title strings.
    _ = keyword
    return _catalog()


def _pad_jobs(base: list[dict], *, count: int = 20) -> list[dict]:
    """Deterministic extra listed rows so continue/Session tests have headroom."""
    out = list(base)
    seen = {item.get("job_id") for item in out}
    for index in range(1, count + 1):
        job_id = f"mock-extra-{index:02d}"
        if job_id in seen:
            continue
        out.append(
            _base(
                job_id=job_id,
                job_title=f"产品经理-续{index:02d}",
                company_name=f"续取科技{index:02d}",
                job_description=None,
                requirements=None,
                benefits=None,
                raw_actions=[],
            )
        )
        seen.add(job_id)
    return out


def ordered_search_catalog(
    keyword: str | None,
    city: str | None = None,
    *,
    pad_extra: int = 0,
) -> list[dict]:
    """Full ordered result set for a query. List fields only for search."""
    items = _items_for_keyword(keyword)
    if pad_extra > 0:
        items = _pad_jobs(items, count=int(pad_extra))
    jobs: list[dict] = []
    for item in items:
        listed = {field: item.get(field) for field in _LIST_FIELDS}
        listed["job_description"] = None
        listed["requirements"] = None
        listed["benefits"] = None
        if city and listed.get("city") and listed["city"] != city:
            continue
        jobs.append(normalize_common_job(listed, platform="mock"))
    return jobs


def mock_search_jobs(
    keyword: str,
    city: str | None = None,
    limit: int = 8,
    mode: str = "fresh",
    search_session: Any = None,
    page_window: int = 30,
    execution_budget: int | None = None,
    pad_extra: int = 0,
    **_unused: Any,
) -> dict:
    from platforms.mock.search_executor import fetch_mock_jobs
    from platforms.search_contract import DEFAULT_EXECUTION_BUDGET

    return fetch_mock_jobs(
        keyword=keyword,
        city=city,
        limit=limit,
        mode=mode,
        search_session=search_session,
        page_window=page_window,
        execution_budget=DEFAULT_EXECUTION_BUDGET if execution_budget is None else execution_budget,
        catalog=ordered_search_catalog(keyword, city, pad_extra=pad_extra),
    )


def mock_open_job(job_url: str | None = None, job_id: str | None = None) -> dict:
    target_id = (job_id or "").strip() or None
    target_url = (job_url or "").strip() or None
    for item in _catalog():
        if target_id and item.get("job_id") == target_id:
            return dict(item)
        if target_url and item.get("job_url") == target_url:
            return dict(item)
    blank = empty_job(platform="mock")
    blank["job_id"] = target_id
    blank["job_url"] = target_url
    blank["raw_actions"] = []
    return blank


def catalog_job_ids() -> list[str]:
    seen: list[str] = []
    for item in _catalog():
        key = item["job_id"]
        if key not in seen:
            seen.append(key)
    return seen


def _base(**overrides) -> dict:
    job = empty_job(platform="mock")
    job.update(
        {
            "city": "深圳",
            "salary": "25-40K",
            "experience": "5-10年",
            "education": "本科",
            "raw_actions": [
                {"text": "Submit your resume for this role"},
                {"text": "Start a new chat with the recruiter"},
            ],
        }
    )
    job.update(overrides)
    if not job.get("job_url") and job.get("job_id"):
        job["job_url"] = f"https://mock.local/job/{job['job_id']}"
    return job


def _direct_job() -> dict:
    return _base(
        job_id="mock-direct",
        job_title="高级产品经理",
        company_name="支付科技有限公司",
        company_industry="金融科技",
        job_description="负责平台产品规划，推动支付、清分、结算等业务建设。",
        requirements="本科及以上学历，5年以上产品经验。必须具备产品规划能力。熟悉支付业务。有CRM经验优先。有医疗行业经验优先。",
        benefits=None,
    )


def _transferable_job() -> dict:
    return _base(
        job_id="mock-transfer",
        job_title="保险产品经理",
        company_name="某保险公司",
        company_industry="保险",
        job_description="负责保险产品规划与复杂业务系统设计。",
        requirements="本科及以上学历，5年以上产品经验。必须具备产品规划能力。保险行业产品经验。",
        benefits=None,
    )


def _hard_gap_job() -> dict:
    return _base(
        job_id="mock-hard",
        job_title="执业药师",
        company_name="某大药房",
        company_industry="医药",
        salary="15-25K",
        job_description="负责药品质量管理。",
        requirements="必须持有执业药师资格证。本科及以上学历。",
        benefits=None,
    )


def _applied_job() -> dict:
    return _base(
        job_id="mock-applied",
        job_title="高级产品经理",
        company_name="已沟通科技",
        job_description="负责平台产品规划，推动支付、清分、结算等业务建设。",
        requirements="本科及以上学历，5年以上产品经验。必须具备产品规划能力。熟悉支付业务。有CRM经验优先。有医疗行业经验优先。",
        raw_actions=[
            {"text": "Continue the existing recruiter conversation"},
            {"text": "View application progress"},
        ],
    )


def _blacklist_job() -> dict:
    return _base(
        job_id="mock-blocked",
        job_title="高级产品经理",
        company_name="黑名单科技",
        job_description="负责平台产品规划。",
        requirements="本科及以上学历，5年以上产品经验。必须具备产品规划能力。",
    )


def _director_job() -> dict:
    return _base(
        job_id="mock-director",
        job_title="产品总监",
        company_name="另一家支付科技",
        company_industry="金融科技",
        job_description="负责平台产品规划，推动支付、清分、结算等业务建设。",
        requirements="本科及以上学历，5年以上产品经验。必须具备产品规划能力。熟悉支付业务。有CRM经验优先。有医疗行业经验优先。",
        benefits=None,
    )
