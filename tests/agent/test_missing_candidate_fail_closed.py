"""Fail closed when match is attempted without candidate material."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.bind_arguments import bind_tool_arguments
from agent.decide import tool_action
from agent.loop import run_loop
from agent.state import JobRecord, new_agent_state, record_error
from api.serialize import serialize_agent_state
from tests.mock_llm import ScriptedReasonerLLM
from tools.registry import build_registry


def test_binding_rejects_match_without_candidate_material():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.understanding_status = "ok"
    state.jobs["mock:j1"] = JobRecord(
        job_key="mock:j1",
        stage="analyzed",
        listed_order=0,
        listed={"platform": "mock", "job_id": "j1"},
        opened={"platform": "mock", "job_id": "j1", "job_description": "JD"},
        job_profile={"analysis_status": "ok", "job_summary": "x"},
    )
    bound = bind_tool_arguments(state, tool_action("match_job", {"job_key": "mock:j1"}))
    assert bound["ok"] is False
    assert bound["observation"]["kind"] == "insufficient_candidate_material"
    assert "analyze_candidate" not in str(bound["observation"].get("error") or "")


def test_two_match_rejects_fail_with_clear_message():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.search.stats["opened"] = 2
    state.search.stats["listed"] = 8
    state.jobs["mock:j1"] = JobRecord(
        job_key="mock:j1",
        stage="opened",
        listed_order=0,
        listed={"platform": "mock", "job_id": "j1"},
        opened={"platform": "mock", "job_id": "j1", "job_description": "JD 文本"},
        job_profile={"analysis_status": "ok", "job_summary": "产品经理"},
    )
    match = {
        "action_type": "tool",
        "tool_name": "match_job",
        "arguments": {"job_key": "mock:j1"},
        "reason": "match",
    }
    # First reject is tolerated; second fail-closes.
    llm = ScriptedReasonerLLM([match, match, match])
    state = run_loop(state, build_registry(data_source="mock"), llm_provider=llm, max_steps=10)
    assert state.session.status == "FAILED"
    payload = serialize_agent_state(state)
    assert payload["error_code"] == "insufficient_candidate_material"
    assert "材料" in payload["message"] or "经历" in payload["message"]


def test_serialize_max_steps_without_candidate_points_to_resume():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    state.status = "FAILED"
    state.search.stats["opened"] = 2
    state.search.stats["listed"] = 8
    record_error(state, "loop exceeded max_steps=120")
    payload = serialize_agent_state(state)
    assert payload["error_code"] == "missing_candidate_material"
    assert "简历" in payload["message"] or "经历" in payload["message"]
