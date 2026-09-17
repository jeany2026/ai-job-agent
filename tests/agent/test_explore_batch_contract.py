"""Small-batch search + explore-before-re-search + open not gated by LLM quota."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.reasoner_context import build_reasoner_payload
from agent.state import JobRecord, SearchPlan, new_agent_state
from agent.validate_action import validate_reasoner_action
from platforms.job_actions import DEFAULT_SEARCH_BATCH, clamp_search_limit
from rules.quota import can_open
from tools.registry import build_registry


def test_clamp_search_limit_defaults_small():
    assert clamp_search_limit(None) == DEFAULT_SEARCH_BATCH
    assert clamp_search_limit(8) == 8
    assert clamp_search_limit(1) == 1
    assert clamp_search_limit(100) == 30
    assert clamp_search_limit("nope") == DEFAULT_SEARCH_BATCH


def test_can_open_ignores_exhausted_llm_quota():
    state = new_agent_state(
        goal_input="找工作",
        data_source="boss",
        constraints={"max_open_jd": 10, "max_llm_calls": 1},
    )
    state.search.stats["llm_calls"] = 50
    state.search.stats["opened"] = 0
    assert can_open(state) is True
    state.search.stats["opened"] = 10
    assert can_open(state) is False


def test_same_query_search_allowed_while_listed_unexplored():
    """Program does not forbid same-query search; Reasoner + Observation + loop safety do."""
    state = new_agent_state(goal_input="找深圳产品经理", data_source="boss")
    state.understanding_status = "ok"
    state.search.plans.append(
        SearchPlan(plan_id="plan-1", keyword="产品经理", city="深圳", platform="boss", exhausted=True)
    )
    state.search.active_plan_id = "plan-1"
    state.jobs["boss:j1"] = JobRecord(
        job_key="boss:j1",
        stage="listed",
        listed_order=0,
        listed={"platform": "boss", "job_id": "j1", "job_title": "产品经理", "salary": "25-40K"},
    )
    state.search.stats["listed"] = 1
    state.search.stats["listed_count"] = 1
    state.search.stats["searches"] = 1
    state.search.stats["search_executed_count"] = 1
    registry = build_registry(data_source="boss")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "tool",
            "tool_name": "search_jobs",
            "arguments": {"keyword": "产品经理", "city": "深圳", "limit": 8},
        },
        registry,
    )
    assert checked["ok"] is True


def test_allow_search_after_listed_are_opened():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="boss")
    state.understanding_status = "ok"
    state.search.plans.append(
        SearchPlan(plan_id="plan-1", keyword="产品经理", city="深圳", platform="boss", exhausted=True)
    )
    state.search.active_plan_id = "plan-1"
    state.jobs["boss:j1"] = JobRecord(
        job_key="boss:j1",
        stage="opened",
        listed_order=0,
        listed={"platform": "boss", "job_id": "j1"},
        opened={"platform": "boss", "job_id": "j1", "job_description": "JD"},
    )
    state.search.stats["listed"] = 1
    state.search.stats["searches"] = 1
    state.search.stats["opened"] = 1
    registry = build_registry(data_source="boss")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "tool",
            "tool_name": "search_jobs",
            "arguments": {"keyword": "产品经理", "city": "深圳", "limit": 8},
        },
        registry,
    )
    assert checked["ok"] is True


def test_job_view_includes_list_salary_fact():
    state = new_agent_state(goal_input="找工作", data_source="boss")
    state.jobs["boss:j1"] = JobRecord(
        job_key="boss:j1",
        stage="listed",
        listed_order=0,
        listed={
            "platform": "boss",
            "job_id": "j1",
            "job_title": "产品经理",
            "company_name": "示例",
            "city": "深圳",
            "salary": "25-40K",
            "experience": "3-5年",
            "education": "本科",
        },
    )
    registry = build_registry(data_source="boss")
    payload = build_reasoner_payload(state, registry)
    jobs = payload.get("jobs") or []
    assert jobs and jobs[0].get("listed_card_facts", {}).get("salary") == "25-40K"
    assert jobs[0].get("listed_card_facts", {}).get("experience") == "3-5年"
