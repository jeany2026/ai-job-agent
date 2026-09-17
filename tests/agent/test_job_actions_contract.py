"""Reasoner / Memory / Match must not see platform CSS or platform-specific Job Tools."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import Action, tool_action
from agent.loop import reduce
from agent.state import new_agent_state
from platforms.job_actions import inspect_application_state, inspect_job
from tests.mock_llm import ScriptedReasonerLLM
from agent.orchestrator import run_agent
from tools.registry import available_tool_contracts, build_registry


def test_available_tools_are_the_same_across_platforms():
    mock = {item["name"] for item in available_tool_contracts(build_registry(data_source="mock"))}
    boss = {item["name"] for item in available_tool_contracts(build_registry(data_source="boss"))}
    assert mock == boss
    assert "search_jobs" in mock
    assert "search_boss_jobs" not in mock


def test_inspect_job_reduce_records_without_deciding():
    state = new_agent_state(resume="x", goal_input="y", data_source="mock")
    state = reduce(
        state,
        tool_action("search_jobs", {"keyword": "高级产品经理"}),
        {
            "jobs": [
                {
                    "platform": "mock",
                    "job_id": "mock-direct",
                    "job_url": "https://mock.local/job/mock-direct",
                    "job_title": "高级产品经理",
                    "company_name": "支付科技有限公司",
                }
            ],
            "platform": "mock",
        },
    )
    opened = inspect_job({"job_id": "mock-direct"}, data_source="mock")
    state = reduce(state, tool_action("inspect_job", {"job_id": "mock-direct"}), opened)
    record = state.jobs["mock:mock-direct"]
    assert record.stage == "opened"
    assert record.opened["job_description"]
    assert state.last_raw_observation["tool_name"] == "inspect_job"
    view = state.last_raw_observation["result"]
    assert "raw_actions" not in view
    assert "dom_context" not in str(view)
    assert view["raw_action_count"] >= 1
    assert record.exclude_reason is None


def test_inspect_application_state_does_not_exclude():
    state = new_agent_state(resume="x", goal_input="y", data_source="mock")
    state = reduce(
        state,
        tool_action("search_jobs", {"keyword": "pm"}),
        {
            "jobs": [
                {
                    "platform": "mock",
                    "job_id": "mock-applied",
                    "job_url": "https://mock.local/job/mock-applied",
                    "job_title": "高级产品经理",
                    "company_name": "已沟通科技",
                }
            ],
            "platform": "mock",
        },
    )
    inspected = inspect_application_state(
        {
            "job_key": "mock:mock-applied",
            "job_id": "mock-applied",
            "already_applied": ["mock:mock-applied"],
        },
        data_source="mock",
    )
    state = reduce(
        state,
        tool_action("inspect_application_state", {"job_key": "mock:mock-applied"}),
        inspected,
    )
    record = state.jobs["mock:mock-applied"]
    assert record.stage == "listed"
    assert record.exclude_reason is None
    assert state.last_raw_observation["tool_name"] == "inspect_application_state"


def test_reasoner_can_choose_inspect_instead_of_open():
    registry = build_registry(data_source="mock")
    llm = ScriptedReasonerLLM(
        [
            {
                "action_type": "tool",
                "tool_name": "understand_user_input",
                "arguments": {"message": "帮我找深圳高级产品经理"},
                "reason": "understand",
            },
            {
                "action_type": "tool",
                "tool_name": "search_jobs",
                "arguments": {"keyword": "高级产品经理", "city": "深圳", "limit": 30},
                "reason": "search",
            },
            {
                "action_type": "tool",
                "tool_name": "inspect_job",
                "arguments": {"job_id": "mock-direct"},
                "reason": "inspect without opening as a pipeline step",
            },
            {"action_type": "finish", "reason": "done"},
        ]
    )
    state = run_agent(
        resume="简历",
        goal="帮我找深圳高级产品经理",
        llm_provider=llm,
        registry=registry,
        data_source="mock",
    )
    assert "inspect_job" in registry.invocations
    assert "open_job" not in registry.invocations
    assert state.jobs["mock:mock-direct"].opened
