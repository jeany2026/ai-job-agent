"""Cross-platform ingest, unified rank, and report platform fields."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import Action, tool_action
from agent.loop import reduce
from agent.orchestrator import run_agent
from agent.report import build_report
from agent.state import DECISION_SURFACED, JobRecord, new_agent_state, note_job_decision
from tests.mock_llm import AgentRoutingLLM
from tools.registry import build_registry

FIXTURE_PATH = ROOT_DIR / "tests" / "fixtures" / "jobs" / "cross_platform_dupes.json"
RESUME_PATH = ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")
GOAL_TEXT = "帮我找深圳高级产品经理，重点金融科技/支付/复杂业务系统，薪资符合目标"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_reduce_search_drops_cross_platform_duplicate():
    data = _fixture()
    boss, liepin = data["duplicate_pair"]
    other = data["unique_other"]
    state = new_agent_state(resume="x", goal_input="y", data_source="boss")
    state = reduce(
        state,
        tool_action("search_jobs", {}),
        {"jobs": [boss, liepin, other], "platform": "boss"},
    )
    keys = sorted(state.jobs)
    assert keys == ["boss:boss-pay-1", "job51:51-pharmacist"]
    assert state.jobs["boss:boss-pay-1"].listed["platform"] == "boss"
    assert state.jobs["job51:51-pharmacist"].listed["platform"] == "job51"
    assert all(record.listed.get("platform") != "liepin" for record in state.jobs.values())


def test_report_includes_platform_and_rank_order():
    state = new_agent_state(resume="x", goal_input="y", data_source="boss")
    state.constraints.platforms = ["boss", "liepin"]
    weak = JobRecord(
        job_key="liepin:weak",
        stage="matched",
        listed_order=0,
        listed={
            "platform": "liepin",
            "job_id": "weak",
            "job_title": "保险产品经理",
            "company_name": "某保险公司",
        },
        match_result={
            "recommendation": "weak",
            "overall_fit": "moderate",
            "hard_requirements_met": True,
            "rationale": "可迁移",
        },
    )
    yes = JobRecord(
        job_key="boss:yes",
        stage="matched",
        listed_order=1,
        listed={
            "platform": "boss",
            "job_id": "yes",
            "job_title": "高级产品经理",
            "company_name": "支付科技有限公司",
        },
        match_result={
            "recommendation": "yes",
            "overall_fit": "strong",
            "hard_requirements_met": True,
            "rationale": "直接匹配",
        },
    )
    blocked = JobRecord(
        job_key="job51:blocked",
        stage="listed",
        listed_order=2,
        listed={
            "platform": "job51",
            "job_id": "blocked",
            "job_title": "高级产品经理",
            "company_name": "黑名单科技",
        },
        exclude_reason="blacklist",
        constraint_flags=["blacklist"],
    )
    state.jobs = {"liepin:weak": weak, "boss:yes": yes, "job51:blocked": blocked}
    empty = build_report(state)
    assert empty["recommended"] == []
    assert [item["job_id"] for item in empty["jobs"]] == ["weak", "yes", "blocked"]
    assert empty["excluded"][0]["platform"] == "job51"
    assert "job51" in empty["platforms"]

    note_job_decision(yes, kind=DECISION_SURFACED, source="reasoner")
    note_job_decision(weak, kind=DECISION_SURFACED, source="reasoner")
    report = build_report(state)
    assert [item["job_id"] for item in report["recommended"]] == ["yes", "weak"]
    assert report["recommended"][0]["platform"] == "boss"
    assert report["recommended"][1]["platform"] == "liepin"
    assert report["excluded"][0]["platform"] == "job51"
    assert report["platforms"] == ["boss", "liepin", "job51"]


def test_mock_loop_report_includes_platform():
    registry = build_registry(data_source="mock")
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal=GOAL_TEXT,
        constraints={"blacklist": ["黑名单科技"]},
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status == "WAITING_USER"
    assert state.output is not None
    assert "mock" in state.output["platforms"]
    for item in state.output["recommended"]:
        assert item["platform"] == "mock"
        assert item["job_key"].startswith("mock:")
    for item in state.output["excluded"]:
        assert item["platform"] == "mock"
