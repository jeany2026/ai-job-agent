"""Mock the open_boss_job → extract actions → interpret_job_actions chain.

No live browser. No recruiting-site button copy.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.mock_llm import MockLLMProvider, intents_response
from tools.boss_job_search import (
    EXTRACT_JOB_ACTIONS_JS,
    analyze_extracted_job_actions,
    extract_job_page_actions,
    open_boss_job,
    open_detail_and_return,
)


def _open_then_interpret(*, job_url: str, page, llm_provider):
    """Production open never interprets; tests chain the sensor explicitly."""
    job = open_boss_job(job_url=job_url, page=page, llm_provider=llm_provider)
    actions = extract_job_page_actions(page)
    job["action_analysis"] = analyze_extracted_job_actions(job, actions, llm_provider=llm_provider)
    return job

FORBIDDEN_SITE_COPY = (
    "立即联系",
    "继续联系",
    "发送简历",
    "已投递",
    "立即申请",
    "已申请",
)
SOURCE_FILES = (
    ROOT_DIR / "tools" / "boss_job_search.py",
    ROOT_DIR / "tools" / "interpret_job_actions.py",
)

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
    """Read-only stand-in for a Playwright page. Clicks are forbidden."""

    def __init__(self, extracted_actions: list[dict]):
        self.extracted_actions = extracted_actions
        self.url = DETAIL_FIXTURE["url"]
        self.goto_urls: list[str] = []
        self.went_back = False
        self.evaluate_calls = 0

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
        self.evaluate_calls += 1
        source = expression if isinstance(expression, str) else str(expression)
        if "extract_job_page_actions" in source:
            return list(self.extracted_actions)
        if "knownBenefits" in source:
            return dict(DETAIL_FIXTURE)
        return {}

    def click(self, *args, **kwargs):
        raise AssertionError("open_boss_job must not click job actions")

    def locator(self, *args, **kwargs):
        raise AssertionError("open_boss_job must not click job actions")


def _button(text, **fields):
    item = {
        "text": text,
        "aria_label": None,
        "title": None,
        "role": "button",
        "href": None,
        "tag": "button",
        "surrounding_text": None,
        "dom_context": "button",
    }
    item.update(fields)
    return item


def test_chain_apply_and_start_is_not_applied():
    page = FakeJobPage(
        [
            _button("Submit resume for this role"),
            _button("Start a new recruiter conversation"),
        ]
    )
    job = _open_then_interpret(
        job_url=DETAIL_FIXTURE["url"],
        page=page,
        llm_provider=MockLLMProvider(intents_response("apply_resume", "start_contact")),
    )
    assert job["job_title"] == "高级产品经理"
    assert job["job_description"]
    analysis = job["action_analysis"]
    intents = {item["semantic_intent"] for item in analysis["actions"]}
    assert "apply_resume" in intents
    assert "start_contact" in intents
    assert analysis["inferred_context"]["application_evidence"] == "not_applied"
    assert analysis["inferred_context"]["evidence_source"] == "page_action_semantics"
    assert not page.went_back
    assert not page.goto_urls


def test_chain_continue_contact_is_applied():
    page = FakeJobPage([_button("Continue existing recruiter conversation")])
    job = _open_then_interpret(
        job_url=DETAIL_FIXTURE["url"],
        page=page,
        llm_provider=MockLLMProvider(intents_response("continue_contact")),
    )
    analysis = job["action_analysis"]
    assert analysis["actions"][0]["semantic_intent"] == "continue_contact"
    assert analysis["inferred_context"]["application_evidence"] == "applied"


def test_chain_start_contact_only_is_uncertain():
    page = FakeJobPage([_button("Start a new recruiter conversation")])
    job = _open_then_interpret(
        job_url=DETAIL_FIXTURE["url"],
        page=page,
        llm_provider=MockLLMProvider(intents_response("start_contact")),
    )
    analysis = job["action_analysis"]
    assert analysis["actions"][0]["semantic_intent"] == "start_contact"
    assert analysis["inferred_context"]["application_evidence"] == "uncertain"


def test_chain_unrelated_actions_are_uncertain():
    page = FakeJobPage(
        [
            _button("Save this job"),
            _button("Share this posting"),
            _button("Report this listing"),
        ]
    )
    job = _open_then_interpret(
        job_url=DETAIL_FIXTURE["url"],
        page=page,
        llm_provider=MockLLMProvider(intents_response("favorite", "share", "report")),
    )
    intents = {item["semantic_intent"] for item in job["action_analysis"]["actions"]}
    assert intents <= {"favorite", "share", "report", "other", "unknown"}
    assert "apply_resume" not in intents
    assert "continue_contact" not in intents
    assert job["action_analysis"]["inferred_context"]["application_evidence"] == "uncertain"


def test_no_candidate_actions_is_uncertain():
    page = FakeJobPage([])
    job = _open_then_interpret(
        job_url=DETAIL_FIXTURE["url"],
        page=page,
        llm_provider=MockLLMProvider(intents_response("apply_resume")),
    )
    analysis = job["action_analysis"]
    assert analysis["analysis_status"] == "insufficient_data"
    assert analysis["actions"] == []
    assert analysis["inferred_context"]["application_evidence"] == "uncertain"


def test_extract_then_interpret_pipeline():
    page = FakeJobPage(
        [
            _button("Send my CV to the employer"),
            _button("Message HR for the first time"),
        ]
    )
    actions = extract_job_page_actions(page)
    assert actions[0]["text"] == "Send my CV to the employer"
    job = {
        "platform": "boss",
        "job_id": "mockjob",
        "job_url": DETAIL_FIXTURE["url"],
    }
    analysis = analyze_extracted_job_actions(
        job,
        actions,
        llm_provider=MockLLMProvider(intents_response("apply_resume", "start_contact")),
    )
    assert analysis["analysis_status"] == "ok"
    assert analysis["inferred_context"]["application_evidence"] == "not_applied"


def test_search_open_detail_does_not_analyze_actions():
    page = FakeJobPage([_button("Submit resume for this role")])
    job = open_detail_and_return(page, DETAIL_FIXTURE["url"])
    assert "action_analysis" not in job
    assert job["job_title"] == "高级产品经理"
    assert not page.went_back


def test_open_detail_navigates_and_returns_when_not_already_open():
    page = FakeJobPage([_button("Submit resume for this role")])
    page.url = "https://www.zhipin.com/web/geek/jobs"
    job = open_detail_and_return(page, DETAIL_FIXTURE["url"])
    assert job["job_title"] == "高级产品经理"
    assert page.goto_urls == [DETAIL_FIXTURE["url"]]
    assert page.went_back


def test_open_boss_job_chain():
    page = FakeJobPage(
        [
            _button("Submit resume for this role"),
            _button("Start a new recruiter conversation"),
        ]
    )
    job = _open_then_interpret(
        job_url=DETAIL_FIXTURE["url"],
        page=page,
        llm_provider=MockLLMProvider(intents_response("apply_resume", "start_contact")),
    )
    assert job["action_analysis"]["inferred_context"]["application_evidence"] == "not_applied"


def test_no_hardcoded_site_copy_in_chain():
    for path in SOURCE_FILES:
        source = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN_SITE_COPY:
            assert phrase not in source, f"{path.name} contains {phrase}"
        assert "len(buttons)" not in source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            dumped = ast.dump(node)
            for phrase in FORBIDDEN_SITE_COPY:
                assert phrase not in dumped
    assert "extract_job_page_actions" in EXTRACT_JOB_ACTIONS_JS
    assert "detailRoot" in EXTRACT_JOB_ACTIONS_JS
    assert "actions.length >= 12" in EXTRACT_JOB_ACTIONS_JS
    assert "actions.length >= 40" not in EXTRACT_JOB_ACTIONS_JS


def test_llm_unavailable_is_not_faked():
    page = FakeJobPage([_button("Submit resume for this role")])
    job = open_boss_job(job_url=DETAIL_FIXTURE["url"], page=page, llm_provider=None)
    assert "action_analysis" not in job
    analysis = analyze_extracted_job_actions(job, extract_job_page_actions(page), llm_provider=None)
    status = analysis["analysis_status"]
    if status != "ok":
        assert status == "llm_unavailable"
        assert analysis["inferred_context"]["application_evidence"] == "uncertain"


if __name__ == "__main__":
    test_chain_apply_and_start_is_not_applied()
    test_chain_continue_contact_is_applied()
    test_chain_start_contact_only_is_uncertain()
    test_chain_unrelated_actions_are_uncertain()
    test_no_candidate_actions_is_uncertain()
    test_extract_then_interpret_pipeline()
    test_search_open_detail_does_not_analyze_actions()
    test_open_detail_navigates_and_returns_when_not_already_open()
    test_open_boss_job_chain()
    test_no_hardcoded_site_copy_in_chain()
    test_llm_unavailable_is_not_faked()
    print("open_boss_job action chain tests passed")
    demo = _open_then_interpret(
        job_url=DETAIL_FIXTURE["url"],
        page=FakeJobPage(
            [
                _button("Submit resume for this role"),
                _button("Start a new recruiter conversation"),
            ]
        ),
        llm_provider=MockLLMProvider(intents_response("apply_resume", "start_contact")),
    )
    print("mock chain example:")
    for item in demo["action_analysis"]["actions"]:
        print(f"  {item['raw_text']!r} -> {item['semantic_intent']}")
    print("  application_evidence=", demo["action_analysis"]["inferred_context"]["application_evidence"])
