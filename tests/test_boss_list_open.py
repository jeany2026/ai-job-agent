"""Prefer list-card open, no re-open churn, paced browser actions."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.state import JobRecord, new_agent_state
from agent.validate_action import validate_reasoner_action
from tools.boss_job_search import click_job_card, open_listed_job
from tools.registry import build_registry


def test_click_job_card_invokes_evaluate_with_job_id():
    page = MagicMock()
    page.evaluate = MagicMock(return_value={"ok": True, "href": "/job_detail/abc.html"})
    assert click_job_card(page, "abc") is True
    assert page.evaluate.call_args.args[1] == "abc"


def test_open_listed_job_clicks_instead_of_goto_on_geek_list():
    page = MagicMock()
    page.url = "https://www.zhipin.com/web/geek/jobs?query=pm&city=101280600"
    page.frames = []
    page.evaluate = MagicMock(
        side_effect=[
            {"ok": True, "href": "/job_detail/job-a.html"},
            {
                "url": page.url,
                "title": "产品经理",
                "h1": "产品经理",
                "body": "职位描述 工作内容做支付产品 任职要求本科",
                "benefits": None,
                "industry": None,
                "company_size": None,
            },
        ]
    )
    page.wait_for_timeout = MagicMock()
    page.wait_for_function = MagicMock()
    page.goto = MagicMock()

    from unittest.mock import patch

    with patch("tools.boss_job_search.check_human_verification"):
        with patch("tools.boss_job_search.pace_browser_action"):
            with patch(
                "tools.boss_job_search.evaluate_retry",
                return_value={
                    "url": page.url,
                    "title": "产品经理",
                    "h1": "产品经理",
                    "body": "职位描述 工作内容做支付产品 任职要求本科",
                    "benefits": None,
                    "industry": None,
                    "company_size": None,
                },
            ):
                job = open_listed_job(
                    page,
                    url="https://www.zhipin.com/job_detail/job-a.html",
                    job_id="job-a",
                )
    assert page.goto.call_count == 0
    assert job.get("job_id") == "job-a"
    assert job.get("job_title") == "产品经理"


def test_reject_reopen_of_already_opened_job():
    state = new_agent_state(goal_input="找工作", data_source="boss")
    state.understanding_status = "ok"
    state.jobs["boss:job-a"] = JobRecord(
        job_key="boss:job-a",
        stage="opened",
        listed_order=0,
        listed={"platform": "boss", "job_id": "job-a", "job_url": "https://www.zhipin.com/job_detail/job-a.html"},
        opened={"platform": "boss", "job_id": "job-a", "job_description": "JD"},
    )
    state.search.stats["opened"] = 1
    registry = build_registry(data_source="boss")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "tool",
            "tool_name": "open_job",
            "arguments": {"job_key": "boss:job-a"},
        },
        registry,
    )
    assert checked["ok"] is False
    assert "already opened" in checked["error"]


def test_inspect_job_still_allowed_after_open_when_jd_missing():
    state = new_agent_state(goal_input="找工作", data_source="boss")
    state.understanding_status = "ok"
    state.jobs["boss:job-a"] = JobRecord(
        job_key="boss:job-a",
        stage="opened",
        listed_order=0,
        listed={"platform": "boss", "job_id": "job-a"},
        opened={"platform": "boss", "job_id": "job-a", "job_description": None},
    )
    state.search.stats["opened"] = 1
    registry = build_registry(data_source="boss")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "tool",
            "tool_name": "inspect_job",
            "arguments": {"job_key": "boss:job-a"},
        },
        registry,
    )
    assert checked["ok"] is True


def test_inspect_job_blocked_when_jd_already_in_world():
    state = new_agent_state(goal_input="找工作", data_source="boss")
    state.understanding_status = "ok"
    state.jobs["boss:job-a"] = JobRecord(
        job_key="boss:job-a",
        stage="opened",
        listed_order=0,
        listed={"platform": "boss", "job_id": "job-a"},
        opened={"platform": "boss", "job_id": "job-a", "job_description": "完整 JD 文本"},
    )
    state.search.stats["opened"] = 1
    registry = build_registry(data_source="boss")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "tool",
            "tool_name": "inspect_job",
            "arguments": {"job_key": "boss:job-a"},
        },
        registry,
    )
    assert checked["ok"] is False
    assert "inspect_job blocked" in checked["error"]
