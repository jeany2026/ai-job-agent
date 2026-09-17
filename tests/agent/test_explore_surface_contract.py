"""Explore → judge → surface contract. Program must not invent the workflow."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import decide, tool_action
from agent.loop import LoopContext, reduce, run_loop
from agent.state import JobRecord, new_agent_state
from agent.validate_action import validate_reasoner_action
from tests.mock_llm import ScriptedReasonerLLM
from tools.reason_next_action import REASON_NEXT_ACTION_SYSTEM_PROMPT
from tools.registry import build_registry

GOAL = "帮我找深圳的产品经理工作"
ROLE = "产品经理"
FINISH = {"action_type": "finish", "reason": "done"}


def _state(**kwargs):
    state = new_agent_state(goal_input=GOAL, resume=None, data_source="mock", **kwargs)
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = {"target_roles": [ROLE], "cities": ["深圳"]}
    return state


def _listed_jobs_state(*keys: str):
    state = _state()
    for index, key in enumerate(keys):
        state.jobs[key] = JobRecord(
            job_key=key,
            stage="listed",
            listed_order=index,
            listed={
                "platform": "mock",
                "job_id": key.split(":")[-1],
                "job_title": ROLE,
                "company_name": f"Co{index}",
                "city": "深圳",
            },
        )
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": "search_jobs",
        "result": {
            "jobs": [
                {
                    "job_id": key.split(":")[-1],
                    "job_title": ROLE,
                    "company_name": f"Co{i}",
                    "city": "深圳",
                }
                for i, key in enumerate(keys)
            ],
            "count": len(keys),
            "platform": "mock",
        },
    }
    return state


def _matched_state(job_key: str = "mock:A"):
    state = _state()
    record = JobRecord(
        job_key=job_key,
        stage="matched",
        listed_order=0,
        listed={"platform": "mock", "job_id": "A", "job_title": ROLE},
        match_result={
            "analysis_status": "ok",
            "hard_requirements_met": True,
            "recommendation": "yes",
        },
    )
    state.jobs[job_key] = record
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": "match_job",
        "result": {
            "analysis_status": "ok",
            "hard_requirements_met": True,
            "recommendation": "yes",
            "job_id": "A",
        },
    }
    return state, record


def test_after_search_reasoner_may_open_named_job_program_does_not_pick():
    """Test 1: Reasoner can open A; Program does not auto-select A."""
    state = _listed_jobs_state("mock:A", "mock:B", "mock:C")
    registry = build_registry(data_source="mock")
    open_a = {
        "action_type": "tool",
        "tool_name": "open_job",
        "arguments": {"job_key": "mock:A"},
        "reason": "explore A",
    }
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([open_a, FINISH]))
    assert "open_job" in registry.invocations
    assert state.jobs["mock:A"].stage in {"opened", "listed"} or state.jobs["mock:A"].opened is not None
    assert state.jobs["mock:B"].opened is None
    assert state.jobs["mock:C"].opened is None
    assert state.session.status != "FAILED"


def test_after_match_reasoner_may_surface_job():
    """Test 2: same match Observation → Reasoner surface → WAITING_USER."""
    state, record = _matched_state("mock:A")
    registry = build_registry(data_source="mock")
    surface = {
        "action_type": "ask_user",
        "intent": "surface",
        "job_key": "mock:A",
        "question": "这个职位与你的经历匹配度较高，要不要考虑申请？",
        "reason": "surface A",
    }
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([surface]))
    assert state.session.status == "WAITING_USER"
    assert (state.output or {}).get("waiting_job", {}).get("job_key") == "mock:A"
    assert any(item.get("kind") == "surfaced" for item in record.decisions)


def test_after_match_reasoner_may_continue_exploring_another_job():
    """Test 3: same match Observation → Reasoner opens B; Program does not auto WAITING_USER."""
    state, record_a = _matched_state("mock:A")
    state.jobs["mock:B"] = JobRecord(
        job_key="mock:B",
        stage="listed",
        listed_order=1,
        listed={"platform": "mock", "job_id": "B", "job_title": ROLE},
    )
    registry = build_registry(data_source="mock")
    open_b = {
        "action_type": "tool",
        "tool_name": "open_job",
        "arguments": {"job_key": "mock:B"},
        "reason": "explore B instead",
    }
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([open_b, FINISH]))
    assert state.session.status == "DONE"
    assert (state.output or {}).get("waiting_job") is None
    assert record_a.stage == "matched"
    assert not any(item.get("kind") == "surfaced" for item in record_a.decisions)
    assert "open_job" in registry.invocations


def test_ask_user_clarification_is_legal_without_job_key():
    """Test 4."""
    state = _state()
    registry = build_registry(data_source="mock")
    clarify = {
        "action_type": "ask_user",
        "intent": "clarification",
        "question": "你更倾向金融科技还是互联网？",
        "reason": "need preference",
    }
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([clarify]))
    assert state.session.status == "WAITING_USER"
    assert (state.output or {}).get("clarification_needed") is True
    assert (state.output or {}).get("waiting_job") is None
    assert (state.output or {}).get("recommended") == []


def test_surface_without_job_key_is_rejected_not_failed_or_auto_fixed():
    """Test 5: Contract reject → Observation → Reasoner decides again. No FAILED, no auto job_key."""
    state, record = _matched_state("mock:A")
    registry = build_registry(data_source="mock")
    illegal = {
        "action_type": "ask_user",
        "intent": "surface",
        "question": "这个职位要不要申请？",
        "reason": "forgot job_key",
    }
    clarify = {
        "action_type": "ask_user",
        "intent": "clarification",
        "question": "你更倾向金融科技还是互联网？",
        "reason": "retry as clarification",
    }
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([illegal, clarify]))
    assert state.session.status == "WAITING_USER"
    assert state.session.status != "FAILED"
    assert (state.output or {}).get("waiting_job") is None
    assert (state.output or {}).get("recommended") == []
    assert record.stage == "matched"
    assert not any(item.get("kind") == "surfaced" for item in record.decisions)
    # First decision was rejected; no invented job_key binding.
    assert any(
        item.get("action") == "rejected" or (item.get("observation") or {}).get("kind") == "action_rejected"
        for item in state.decisions
    ) or any(item.get("kind") == "action_rejected" for item in [state.last_raw_observation] if item)


def test_surface_with_job_key_binds_waiting_job():
    """Test 6."""
    state, record = _matched_state("mock:A")
    action = decide(
        state,
        registry=build_registry(data_source="mock"),
        llm_provider=ScriptedReasonerLLM(
            [
                {
                    "action_type": "ask_user",
                    "intent": "surface",
                    "job_key": "mock:A",
                    "question": "这个职位与你的经历匹配度较高，要不要考虑申请？",
                    "reason": "surface",
                }
            ]
        ),
    )
    assert action.action_type == "ask_user"
    assert action.intent == "surface"
    assert action.job_key == "mock:A"
    assert action.arguments.get("intent") == "surface"
    assert action.arguments.get("job_key") == "mock:A"

    state = run_loop(
        state,
        build_registry(data_source="mock"),
        llm_provider=ScriptedReasonerLLM(
            [
                {
                    "action_type": "ask_user",
                    "intent": "surface",
                    "job_key": "mock:A",
                    "question": "这个职位与你的经历匹配度较高，要不要考虑申请？",
                    "reason": "surface",
                }
            ]
        ),
    )
    assert state.session.status == "WAITING_USER"
    assert (state.output or {}).get("waiting_job", {}).get("job_key") == "mock:A"
    assert any(item.get("kind") == "surfaced" for item in record.decisions)


def test_search_result_does_not_auto_open_analyze_match_or_surface():
    """Test 7: 30 listed jobs → back to Reasoner; Program invents no pipeline."""
    registry = build_registry(data_source="mock")
    state = _state()
    result = registry.invoke(
        "search_jobs",
        {"keyword": ROLE, "city": "深圳", "limit": 30},
    )
    state = reduce(state, tool_action("search_jobs", {"keyword": ROLE, "city": "深圳"}), result)
    assert state.session.status == "RUNNING"
    assert len(state.jobs) >= 1
    listed_before = {key: record.stage for key, record in state.jobs.items()}

    # Reasoner chooses finish — Program must not have opened/analyzed/matched/surfaced already.
    state2 = run_loop(state, build_registry(data_source="mock"), llm_provider=ScriptedReasonerLLM([FINISH]))
    assert state2.session.status == "DONE"
    assert all(record.opened is None for record in state2.jobs.values())
    assert all(record.match_result is None for record in state2.jobs.values())
    assert all(not any(d.get("kind") == "surfaced" for d in record.decisions) for record in state2.jobs.values())
    assert (state2.output or {}).get("waiting_job") is None
    for key, stage in listed_before.items():
        assert state2.jobs[key].stage == stage


def test_prompt_teaches_listed_explored_surfaced_without_fixed_workflow():
    prompt = REASON_NEXT_ACTION_SYSTEM_PROMPT
    assert "listed ≠ explored ≠ surfaced" in prompt
    assert "不是固定 Workflow" in prompt
    assert 'intent":"surface"' in prompt or 'intent": "surface"' in prompt or 'intent":"surface"' in prompt
    assert "不要求每个职位都经过 open/analyze/match" in prompt
    assert "以上职位" in prompt


def test_validate_surface_without_job_key_is_structural_reject():
    state, _ = _matched_state("mock:A")
    registry = build_registry(data_source="mock")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "ask_user",
            "intent": "surface",
            "question": "这个职位要不要申请？",
        },
        registry,
    )
    assert checked["ok"] is False
    assert "job_key" in (checked["error"] or "")
