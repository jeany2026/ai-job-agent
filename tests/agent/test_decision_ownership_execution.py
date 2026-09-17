"""Decision ownership: Program executes Reasoner's choice; it does not invent the choice.

These tests do NOT assert that the Agent must explore, open A, or surface after match.
They assert that when Reasoner already chose an Action, Program respects it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import tool_action
from agent.loop import reduce, run_loop
from agent.state import JobRecord, new_agent_state
from tests.mock_llm import ScriptedReasonerLLM
from tools.registry import build_registry

GOAL = "帮我找深圳的产品经理工作"
ROLE = "产品经理"
FINISH = {"action_type": "finish", "reason": "reasoner chose finish"}


def _state():
    state = new_agent_state(goal_input=GOAL, resume=None, data_source="mock")
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = {"target_roles": [ROLE], "cities": ["深圳"]}
    return state


def _seed_listed(state, *keys: str):
    for index, key in enumerate(keys):
        job_id = key.split(":")[-1]
        state.jobs[key] = JobRecord(
            job_key=key,
            stage="listed",
            listed_order=index,
            listed={
                "platform": "mock",
                "job_id": job_id,
                "job_title": ROLE,
                "company_name": f"Co-{job_id}",
                "city": "深圳",
                "job_url": f"https://mock.local/{job_id}",
            },
        )
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": "search_jobs",
        "result": {"count": len(keys), "jobs": [{"job_id": k.split(":")[-1]} for k in keys]},
    }
    return state


def test_program_opens_job_b_when_reasoner_names_b():
    """Test 1: Reasoner picks B → Program opens B, not A."""
    state = _seed_listed(_state(), "mock:A", "mock:B", "mock:C")
    registry = build_registry(data_source="mock")
    open_b = {
        "action_type": "tool",
        "tool_name": "open_job",
        "arguments": {"job_key": "mock:B"},
        "reason": "reasoner chose B",
    }
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([open_b, FINISH]))
    assert "open_job" in registry.invocations
    assert state.jobs["mock:B"].opened is not None or state.jobs["mock:B"].stage == "opened"
    assert state.jobs["mock:A"].opened is None
    assert state.jobs["mock:C"].opened is None


def test_program_opens_job_c_when_reasoner_names_c():
    """Test 2: Reasoner picks C → Program opens C (proves choice is not hard-coded to B)."""
    state = _seed_listed(_state(), "mock:A", "mock:B", "mock:C")
    registry = build_registry(data_source="mock")
    open_c = {
        "action_type": "tool",
        "tool_name": "open_job",
        "arguments": {"job_key": "mock:C"},
        "reason": "reasoner chose C",
    }
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([open_c, FINISH]))
    assert state.jobs["mock:C"].opened is not None or state.jobs["mock:C"].stage == "opened"
    assert state.jobs["mock:A"].opened is None
    assert state.jobs["mock:B"].opened is None


def test_program_surfaces_job_b_only_when_reasoner_asks():
    """Test 3: Reasoner surface(B) → WAITING_USER bound to B."""
    state = _seed_listed(_state(), "mock:A", "mock:B")
    state.jobs["mock:B"].stage = "matched"
    state.jobs["mock:B"].match_result = {
        "analysis_status": "ok",
        "recommendation": "yes",
        "hard_requirements_met": True,
    }
    # A is also matched — Program must not prefer A over Reasoner's B.
    state.jobs["mock:A"].stage = "matched"
    state.jobs["mock:A"].match_result = {
        "analysis_status": "ok",
        "recommendation": "yes",
        "hard_requirements_met": True,
    }
    surface_b = {
        "action_type": "ask_user",
        "intent": "surface",
        "job_key": "mock:B",
        "question": "要不要考虑 B？",
        "reason": "reasoner surfaced B",
    }
    state = run_loop(
        state,
        build_registry(data_source="mock"),
        llm_provider=ScriptedReasonerLLM([surface_b]),
    )
    assert state.session.status == "WAITING_USER"
    assert (state.output or {}).get("waiting_job", {}).get("job_key") == "mock:B"
    assert any(item.get("kind") == "surfaced" for item in state.jobs["mock:B"].decisions)
    assert not any(item.get("kind") == "surfaced" for item in state.jobs["mock:A"].decisions)


def test_program_does_not_force_waiting_user_when_reasoner_opens_another_job():
    """Test 4: matched A exists; Reasoner opens C → no forced WAITING_USER."""
    state = _seed_listed(_state(), "mock:A", "mock:C")
    state.jobs["mock:A"].stage = "matched"
    state.jobs["mock:A"].match_result = {
        "analysis_status": "ok",
        "recommendation": "yes",
        "hard_requirements_met": True,
    }
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": "match_job",
        "result": {"analysis_status": "ok", "recommendation": "yes", "job_id": "A"},
    }
    open_c = {
        "action_type": "tool",
        "tool_name": "open_job",
        "arguments": {"job_key": "mock:C"},
        "reason": "continue exploring C",
    }
    state = run_loop(
        state,
        build_registry(data_source="mock"),
        llm_provider=ScriptedReasonerLLM([open_c, FINISH]),
    )
    assert state.session.status == "DONE"
    assert (state.output or {}).get("waiting_job") is None
    assert not any(item.get("kind") == "surfaced" for item in state.jobs["mock:A"].decisions)


def test_program_does_not_force_explore_when_reasoner_finishes_with_listed_jobs():
    """Test 5: listed jobs remain; Reasoner finish → DONE, no auto open/analyze/match."""
    state = _seed_listed(_state(), "mock:A", "mock:B", "mock:C")
    registry = build_registry(data_source="mock")
    # Also prove reduce(search) alone leaves RUNNING without choosing next tool.
    search_result = {
        "jobs": [
            {"platform": "mock", "job_id": "X", "job_title": ROLE, "city": "深圳"},
        ],
        "platform": "mock",
    }
    state = reduce(
        state,
        tool_action("search_jobs", {"keyword": ROLE, "city": "深圳"}),
        search_result,
    )
    assert state.session.status == "RUNNING"

    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([FINISH]))
    assert state.session.status == "DONE"
    assert registry.invocations == []
    assert all(record.opened is None for record in state.jobs.values())
    assert all(record.match_result is None for record in state.jobs.values())
    assert (state.output or {}).get("waiting_job") is None
