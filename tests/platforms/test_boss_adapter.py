"""BOSS platform Tool adapter. Fake page only. No live Edge. No interpret inside open."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.human_gate import HumanGateError
from browser.edge_session import EdgeNotConnected, HumanVerificationStopped
from platforms.boss.jobs import OPEN_BOSS_JOB_SPEC, SEARCH_BOSS_JOBS_SPEC, open_boss_job, search_boss_jobs
from tools.registry import build_registry

BOSS_SRC = ROOT_DIR / "platforms" / "boss" / "jobs.py"

DETAIL_FIXTURE = {
    "url": "https://www.zhipin.com/job_detail/mockjob.html",
    "title": "「高级产品经理招聘」_测试公司招聘",
    "h1": "高级产品经理",
    "body": "岗位职责：负责平台产品规划。任职要求：本科及以上，三年以上。",
    "benefits": [],
    "industry": "",
    "company_size": "",
}


class FakeJobPage:
    def __init__(self, extracted_actions: list[dict] | None = None):
        self.extracted_actions = extracted_actions or []
        self.url = DETAIL_FIXTURE["url"]
        self.goto_urls: list[str] = []
        self.went_back = False

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)
        self.url = url

    def go_back(self, **kwargs):
        self.went_back = True

    def wait_for_function(self, *args, **kwargs):
        return True

    def wait_for_timeout(self, *args, **kwargs):
        return None

    def inner_text(self, selector):
        return DETAIL_FIXTURE["body"]

    def evaluate(self, expression, *args, **kwargs):
        source = expression if isinstance(expression, str) else str(expression)
        if "extract_job_page_actions" in source:
            return list(self.extracted_actions)
        if "knownBenefits" in source:
            return dict(DETAIL_FIXTURE)
        return {}

    def click(self, *args, **kwargs):
        raise AssertionError("open_boss_job must not click job actions")


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


def test_specs_are_search_and_open_only():
    assert SEARCH_BOSS_JOBS_SPEC["name"] == "search_boss_jobs"
    assert OPEN_BOSS_JOB_SPEC["name"] == "open_boss_job"
    assert "browse_boss_jobs" not in {SEARCH_BOSS_JOBS_SPEC["name"], OPEN_BOSS_JOB_SPEC["name"]}


def test_open_navigates_and_returns_raw_actions_without_interpret():
    page = FakeJobPage([_button("Submit resume for this role")])
    page.url = "https://www.zhipin.com/web/geek/jobs"
    job = open_boss_job(job_url=DETAIL_FIXTURE["url"], page=page)
    assert job["platform"] == "boss"
    assert job["job_title"] == "高级产品经理"
    assert job["job_description"]
    assert job["raw_actions"][0]["text"] == "Submit resume for this role"
    assert "action_analysis" not in job
    assert page.goto_urls == [DETAIL_FIXTURE["url"]]
    assert not page.went_back


def test_search_wraps_list_as_common_jobs():
    with patch(
        "platforms.boss.search_executor.fetch_boss_jobs",
        return_value={
            "platform": "boss",
            "jobs": [
                {
                    "platform": "boss",
                    "job_id": "joba",
                    "job_url": "https://www.zhipin.com/job_detail/joba.html",
                    "job_title": "Role A",
                    "company_name": "Acme",
                    "city": "深圳",
                    "job_description": None,
                }
            ],
            "mode": "fresh",
            "newly_ingested": 1,
            "newly_exposed": 1,
            "duplicates": 0,
            "fetch_status": "ok",
            "can_continue": True,
            "query": {"keyword": "高级产品经理", "city": "深圳", "mode": "fresh", "limit": 10},
            "search_session": {
                "session_id": "ss-test",
                "status": "active",
                "exposed_job_keys": ["boss:joba"],
            },
        },
    ):
        result = search_boss_jobs(keyword="高级产品经理", city="深圳", limit=10, page=FakeJobPage())
    assert result["platform"] == "boss"
    assert result["jobs"][0]["job_id"] == "joba"
    assert result["jobs"][0].get("job_description") is None
    assert result["fetch_status"] == "ok"


def test_search_maps_edge_not_connected():
    with patch(
        "platforms.boss.search_executor.fetch_boss_jobs",
        side_effect=EdgeNotConnected("no edge"),
    ):
        try:
            search_boss_jobs(keyword="pm", page=FakeJobPage())
        except HumanGateError as exc:
            assert exc.reason == "cdp_unavailable"
            assert exc.source == "search_boss_jobs"
            return
    raise AssertionError("EdgeNotConnected must become HumanGateError")


def test_open_maps_captcha():
    page = FakeJobPage()
    with patch(
        "tools.boss_job_search.open_listed_job",
        side_effect=HumanVerificationStopped("请完成安全验证"),
    ):
        try:
            open_boss_job(job_url=DETAIL_FIXTURE["url"], page=page)
        except HumanGateError as exc:
            assert exc.reason == "captcha"
            assert exc.source == "open_boss_job"
            return
    raise AssertionError("HumanVerificationStopped must become HumanGateError")


def test_adapter_source_does_not_call_semantic_tools():
    tree = ast.parse(BOSS_SRC.read_text(encoding="utf-8"))
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
    assert "browse_boss_jobs" not in names
    assert "Heuristic" not in names


def test_registry_invoke_open_uses_adapter(monkeypatch):
    captured = {}

    def fake_open(**kwargs):
        captured.update(kwargs)
        return {
            "platform": "boss",
            "job_id": "joba",
            "job_url": kwargs.get("job_url"),
            "job_title": "Role A",
            "raw_actions": [{"text": "Submit resume for this role"}],
        }

    monkeypatch.setattr("platforms.boss.jobs.open_boss_job", fake_open)
    registry = build_registry(data_source="boss")
    result = registry.invoke("open_job", {"job_url": DETAIL_FIXTURE["url"]})
    assert result["raw_actions"]
    assert "action_analysis" not in result
    assert captured["job_url"] == DETAIL_FIXTURE["url"]
