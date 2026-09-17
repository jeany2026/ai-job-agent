"""51Job platform Tool adapter. Fake page only. No live Edge. No interpret inside open."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.human_gate import HumanGateError
from agent.orchestrator import run_agent
from browser.edge_session import EdgeNotConnected, HumanVerificationStopped
from platforms.job51.jobs import (
    OPEN_51JOB_JOB_SPEC,
    SEARCH_51JOB_JOBS_SPEC,
    job_url_from_id,
    open_51job_job,
    parse_job_id,
    search_51job_jobs,
    search_url,
)
from platforms.mock.jobs import mock_open_job, mock_search_jobs
from tests.mock_llm import AgentRoutingLLM
from tools.registry import FORBIDDEN_LOOP_TOOLS, build_registry

JOB51_SRC = ROOT_DIR / "platforms" / "job51" / "jobs.py"
RESUME_PATH = ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")
GOAL_TEXT = "帮我找深圳高级产品经理，重点金融科技/支付/复杂业务系统，薪资符合目标"

DETAIL_URL = "https://jobs.51job.com/shenzhen/88001122.html"
DETAIL_FIXTURE = {
    "url": DETAIL_URL,
    "title": "高级产品经理-测试公司招聘-前程无忧",
    "h1": "高级产品经理",
    "company_name": "测试公司",
    "body": "岗位职责：负责平台产品规划。任职要求：本科及以上，三年以上。",
}

LIST_FIXTURE = [
    {
        "job_id": "88001122",
        "job_url": "https://jobs.51job.com/shenzhen/88001122.html",
        "job_title": "Role A",
        "company_name": "Acme",
        "city": "深圳",
    }
]


class FakeJobPage:
    def __init__(self, extracted_actions: list[dict] | None = None, list_jobs: list[dict] | None = None):
        self.extracted_actions = extracted_actions or []
        self.list_jobs = list_jobs if list_jobs is not None else list(LIST_FIXTURE)
        self.url = DETAIL_URL
        self.goto_urls: list[str] = []
        self.went_back = False
        self.logged_in = True

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)
        self.url = url

    def go_back(self, **kwargs):
        self.went_back = True

    def wait_for_function(self, *args, **kwargs):
        return True

    def wait_for_timeout(self, *args, **kwargs):
        return None

    def wait_for_url(self, *args, **kwargs):
        return True

    def inner_text(self, selector):
        return DETAIL_FIXTURE["body"]

    def evaluate(self, expression, *args, **kwargs):
        source = expression if isinstance(expression, str) else str(expression)
        if "extract_51job_job_list" in source:
            return {"jobs": list(self.list_jobs), "loggedIn": self.logged_in, "url": self.url}
        if "extract_51job_job_detail" in source:
            return dict(DETAIL_FIXTURE)
        if "extract_51job_page_actions" in source:
            return list(self.extracted_actions)
        return {}

    def click(self, *args, **kwargs):
        raise AssertionError("open_51job_job must not click job actions")


def _button(text: str) -> dict:
    return {
        "text": text,
        "aria_label": None,
        "title": None,
        "role": "button",
        "href": None,
        "tag": "button",
        "surrounding_text": None,
        "dom_context": "button",
    }


def _job51ify(job: dict) -> dict:
    out = dict(job)
    out["platform"] = "job51"
    job_id = out.get("job_id")
    if job_id:
        out["job_url"] = f"https://jobs.51job.com/all/{job_id}.html"
    return out


def _fake_search(arguments, **_extra):
    listed = mock_search_jobs(
        keyword=arguments.get("keyword") or "高级产品经理",
        city=arguments.get("city") or "深圳",
        limit=int(arguments.get("limit") or 30),
    )
    jobs = [_job51ify(item) for item in listed["jobs"]]
    return {"jobs": jobs, "platform": "job51"}


def _fake_open(arguments, **_extra):
    opened = mock_open_job(job_url=arguments.get("job_url"), job_id=arguments.get("job_id"))
    job = _job51ify(opened)
    assert "action_analysis" not in job
    assert isinstance(job.get("raw_actions"), list)
    return job


def test_specs_are_search_and_open_only():
    assert SEARCH_51JOB_JOBS_SPEC["name"] == "search_51job_jobs"
    assert OPEN_51JOB_JOB_SPEC["name"] == "open_51job_job"
    assert "browse_51job_jobs" not in {SEARCH_51JOB_JOBS_SPEC["name"], OPEN_51JOB_JOB_SPEC["name"]}


def test_search_url_and_job_id():
    url = search_url("高级产品经理", "深圳")
    assert "we.51job.com/pc/search" in url
    assert "keyword=" in url
    assert "jobArea=040000" in url
    assert parse_job_id(DETAIL_URL) == "88001122"
    assert parse_job_id("https://we.51job.com/pc/job?jobid=88001122") == "88001122"
    assert job_url_from_id("88001122") == "https://jobs.51job.com/all/88001122.html"


def test_open_navigates_and_returns_raw_actions_without_interpret():
    page = FakeJobPage([_button("Submit resume for this role")])
    page.url = "https://we.51job.com/pc/search"
    job = open_51job_job(job_url=DETAIL_URL, page=page)
    assert job["platform"] == "job51"
    assert job["job_title"] == "高级产品经理"
    assert job["job_description"]
    assert job["raw_actions"][0]["text"] == "Submit resume for this role"
    assert "action_analysis" not in job
    assert page.goto_urls == [DETAIL_URL]
    assert not page.went_back


def test_search_wraps_list_as_common_jobs():
    page = FakeJobPage(list_jobs=LIST_FIXTURE)
    result = search_51job_jobs(keyword="高级产品经理", city="深圳", limit=10, page=page)
    assert result["platform"] == "job51"
    assert result["jobs"][0]["job_id"] == "88001122"
    assert result["jobs"][0]["platform"] == "job51"
    assert result["jobs"][0]["job_description"] is None
    assert page.goto_urls and "we.51job.com/pc/search" in page.goto_urls[0]


def test_search_maps_edge_not_connected():
    with patch(
        "browser.edge_session.connect_existing_edge",
        side_effect=EdgeNotConnected("no edge"),
    ):
        try:
            search_51job_jobs(keyword="pm")
        except HumanGateError as exc:
            assert exc.reason == "cdp_unavailable"
            assert exc.source == "search_51job_jobs"
            return
    raise AssertionError("EdgeNotConnected must become HumanGateError")


def test_open_maps_captcha():
    page = FakeJobPage()
    with patch(
        "browser.edge_session.check_human_verification",
        side_effect=HumanVerificationStopped("请完成安全验证"),
    ):
        try:
            open_51job_job(job_url=DETAIL_URL, page=page)
        except HumanGateError as exc:
            assert exc.reason == "captcha"
            assert exc.source == "open_51job_job"
            return
    raise AssertionError("HumanVerificationStopped must become HumanGateError")


def test_search_maps_login_required():
    page = FakeJobPage()
    page.logged_in = False
    try:
        search_51job_jobs(keyword="pm", page=page)
    except HumanGateError as exc:
        assert exc.reason == "login_required"
        assert exc.source == "search_51job_jobs"
        return
    raise AssertionError("missing 51Job login must become HumanGateError")


def test_adapter_source_does_not_call_semantic_tools():
    tree = ast.parse(JOB51_SRC.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name)
    assert "interpret_job_actions" not in names
    assert "analyze_job" not in names
    assert "match_job" not in names
    assert "browse_51job_jobs" not in names
    assert "Heuristic" not in names
    source = JOB51_SRC.read_text(encoding="utf-8")
    assert "page.click" not in source
    assert "apply_resume" not in source


def test_registry_invoke_open_uses_adapter(monkeypatch):
    captured = {}

    def fake_open(**kwargs):
        captured.update(kwargs)
        return {
            "platform": "job51",
            "job_id": "88001122",
            "job_url": kwargs.get("job_url"),
            "job_title": "Role A",
            "raw_actions": [{"text": "Submit resume for this role"}],
        }

    monkeypatch.setattr("platforms.job51.jobs.open_51job_job", fake_open)
    registry = build_registry(data_source="job51")
    result = registry.invoke("open_job", {"job_url": DETAIL_URL})
    assert result["raw_actions"]
    assert "action_analysis" not in result
    assert captured["job_url"] == DETAIL_URL
    assert registry.has("search_jobs")
    assert registry.has("open_job")
    assert not registry.has("search_51job_jobs")
    assert not registry.has("search_boss_jobs")
    assert not registry.has("search_liepin_jobs")
    assert "browse_boss_jobs" not in {item["name"] for item in registry.specs()}


def test_job51_source_reaches_done_without_live_site():
    registry = build_registry(data_source="job51")
    registry._handlers["search_jobs"] = _fake_search
    registry._handlers["open_job"] = _fake_open
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal=GOAL_TEXT,
        constraints={"blacklist": ["黑名单科技"]},
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="job51",
    )
    assert state.session.status == "WAITING_USER"
    assert state.constraints.data_source == "job51"
    assert "search_jobs" in registry.invocations
    assert "open_job" in registry.invocations
    assert "search_51job_jobs" not in registry.invocations
    assert "mock_search_jobs" not in registry.invocations
    assert "search_boss_jobs" not in registry.invocations
    assert "search_liepin_jobs" not in registry.invocations
    assert not (FORBIDDEN_LOOP_TOOLS & set(registry.invocations))
    interpret_at = registry.invocations.index("interpret_job_actions")
    open_at = registry.invocations.index("open_job")
    assert open_at < interpret_at
    assert "analyze_job" in registry.invocations
    assert "match_job" in registry.invocations
    recommended_ids = {item["job_id"] for item in state.output["recommended"]}
    assert "mock-direct" in recommended_ids
