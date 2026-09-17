"""Action / Job Binding boundary. Identity Actions; Binding loads World material."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.bind_arguments import bind_tool_arguments
from agent.decide import tool_action
from agent.loop import reduce, run_loop
from agent.state import JobRecord, new_agent_state
from candidate.context import build_candidate_context
from tests.mock_llm import ScriptedReasonerLLM
from tools.registry import build_registry

ROLE = "高级产品经理"


def _opened_job(job_key: str = "mock:j1", *, with_jd: bool = True, with_actions: bool = True) -> JobRecord:
    listed = {
        "platform": "mock",
        "job_id": "j1",
        "job_url": "https://mock.local/j1",
        "job_title": ROLE,
        "company_name": "支付科技",
    }
    opened = dict(listed)
    if with_jd:
        opened["job_description"] = "负责支付产品规划，要求本科，5年经验。"
    if with_actions:
        opened["raw_actions"] = [{"text": "立即沟通", "tag": "button"}]
    return JobRecord(
        job_key=job_key,
        stage="opened",
        listed_order=0,
        listed=listed,
        opened=opened,
    )


def _state_with_job(**kwargs) -> object:
    state = new_agent_state(goal_input=f"帮我找{ROLE}", resume="简历：做过支付。", data_source="mock")
    state.status = "RUNNING"
    record = _opened_job(**kwargs)
    state.jobs[record.job_key] = record
    state.candidate.profile = {
        "kind": "interpretation_projection",
        "analysis_status": "ok",
        "summary": "支付产品经理",
        "direct_capabilities": [{"name": "支付产品"}],
    }
    state.candidate_context = build_candidate_context(
        persistent_profile=state.candidate.profile,
        candidate_memory=state.candidate.memory,
    )
    return state


def test_analyze_job_action_uses_job_key_binding_loads_jd():
    state = _state_with_job()
    bound = bind_tool_arguments(state, tool_action("analyze_job", {"job_key": "mock:j1"}))
    assert bound["ok"] is True
    assert "job" in bound["arguments"]
    assert "支付产品规划" in (bound["arguments"]["job"].get("job_description") or "")
    assert bound["arguments"]["job_key"] == "mock:j1"
    assert "job_description" not in (tool_action("analyze_job", {"job_key": "mock:j1"}).arguments or {})


def test_analyze_job_rejects_world_material_in_action_and_uses_world():
    state = _state_with_job()
    spoofed = {
        "job_key": "mock:j1",
        "job": {"job_description": "SPOOFED JD THAT MUST NOT WIN"},
    }
    bound = bind_tool_arguments(state, tool_action("analyze_job", spoofed))
    assert bound["ok"] is True
    assert "SPOOFED" not in (bound["arguments"]["job"].get("job_description") or "")
    assert "支付产品规划" in (bound["arguments"]["job"].get("job_description") or "")


def test_missing_job_is_observation_not_other_job():
    state = _state_with_job()
    state.jobs["mock:other"] = _opened_job("mock:other")
    bound = bind_tool_arguments(state, tool_action("analyze_job", {"job_key": "mock:missing"}))
    assert bound["ok"] is False
    assert bound["observation"]["kind"] == "missing_job"
    assert "mock:j1" in bound["observation"]["available_job_keys"]


def test_match_without_job_profile_is_observation_not_auto_analyze():
    state = _state_with_job()
    state.jobs["mock:j1"].job_profile = None
    registry = build_registry(data_source="mock")
    state = run_loop(
        state,
        registry,
        llm_provider=ScriptedReasonerLLM(
            [
                {
                    "action_type": "tool",
                    "tool_name": "match_job",
                    "arguments": {"job_key": "mock:j1"},
                    "reason": "try match",
                },
                {"action_type": "finish", "reason": "stop"},
            ]
        ),
    )
    assert "match_job" not in registry.invocations
    assert "analyze_job" not in registry.invocations
    assert state.session.status == "DONE"
    assert state.jobs["mock:j1"].match_result is None
    assert state.jobs["mock:j1"].job_profile is None


def test_match_job_binding_loads_profile_from_world():
    state = _state_with_job()
    state.jobs["mock:j1"].job_profile = {
        "analysis_status": "ok",
        "job_summary": "支付产品",
        "hard_requirements": [],
    }
    bound = bind_tool_arguments(state, tool_action("match_job", {"job_key": "mock:j1"}))
    assert bound["ok"] is True
    assert bound["arguments"]["job_profile"]["job_summary"] == "支付产品"
    assert bound["arguments"].get("candidate_profile") is not None
    assert "job_profile" not in (tool_action("match_job", {"job_key": "mock:j1"}).arguments or {})


def test_interpret_binding_loads_raw_actions():
    state = _state_with_job()
    bound = bind_tool_arguments(state, tool_action("interpret_job_actions", {"job_key": "mock:j1"}))
    assert bound["ok"] is True
    assert bound["arguments"]["page_context"]["actions"]


def test_analyze_without_jd_is_missing_material():
    state = _state_with_job(with_jd=False)
    # title still counts as analyzable fact — strip title too
    state.jobs["mock:j1"].opened["job_title"] = ""
    state.jobs["mock:j1"].listed["job_title"] = ""
    bound = bind_tool_arguments(state, tool_action("analyze_job", {"job_key": "mock:j1"}))
    assert bound["ok"] is False
    assert bound["observation"]["kind"] == "missing_job_material"
