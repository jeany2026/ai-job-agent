"""Soft-pipeline removal: material preconditions only, no forced Tool order."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.bind_arguments import bind_tool_arguments
from agent.decide import tool_action
from agent.loop import run_loop
from agent.state import JobRecord, new_agent_state
from agent.validate_action import validate_reasoner_action
from tests.mock_llm import ScriptedReasonerLLM
from tools.reason_next_action import REASON_NEXT_ACTION_SYSTEM_PROMPT
from tools.registry import build_registry

FORBIDDEN_PIPELINE_PHRASES = (
    "匹配前必须 analyze_candidate",
    "优先 open_job",
    "尚未 search_jobs、World 中也没有职位时，不要 finish",
    "求职目标未搜索前不要 finish",
    "use analyze_candidate with intake_ids before ask_user",
    "open_job an unexplored listed job first",
)


def _world_with_unexplored():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.understanding_status = "ok"
    state.candidate.memory = {"status": "usable", "facts": [{"kind": "skill", "name": "产品"}]}
    state.candidate.profile = {
        "analysis_status": "ok",
        "summary": "产品经理",
        "direct_capabilities": [{"name": "产品"}],
    }
    state.jobs["mock:a"] = JobRecord(
        job_key="mock:a",
        stage="analyzed",
        listed_order=0,
        listed={"platform": "mock", "job_id": "a"},
        opened={"platform": "mock", "job_id": "a", "job_description": "JD A"},
        job_profile={"analysis_status": "ok", "job_summary": "A"},
    )
    state.jobs["mock:b"] = JobRecord(
        job_key="mock:b",
        stage="listed",
        listed_order=1,
        listed={"platform": "mock", "job_id": "b", "job_title": "产品经理"},
    )
    state.search.stats.update({"listed": 2, "opened": 1, "searches": 1})
    return state


def test_finish_without_search_allowed():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.goal = {"target_roles": ["产品经理"], "cities": ["深圳"]}
    state.understanding = {"task_kind": "new_job_search"}
    state.understanding_status = "ok"
    state.status = "RUNNING"
    result = run_loop(
        state,
        build_registry(data_source="mock"),
        llm_provider=ScriptedReasonerLLM(
            [{"action_type": "finish", "reason": "本轮只记下目标，先不搜索"}]
        ),
    )
    assert result.session.status == "DONE"


def test_unexplored_listed_allows_non_open_actions():
    state = _world_with_unexplored()
    registry = build_registry(data_source="mock")
    # Surface already-analyzed job while another listed remains unexplored.
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "ask_user",
            "intent": "surface",
            "job_key": "mock:a",
            "question": "这个岗位你可以看看。",
            "reason": "surface without opening b",
        },
        registry,
    )
    assert checked["ok"] is True

    finish = validate_reasoner_action(
        state,
        {"action_type": "finish", "reason": "先结束，稍后再看 listed"},
        registry,
    )
    assert finish["ok"] is True
    assert "open_job" not in str(finish.get("error") or "")


def test_match_with_candidate_material_does_not_require_analyze_history():
    """Usable World candidate material is enough — no analyze_candidate step stamp."""
    state = _world_with_unexplored()
    # Material present via profile/memory; no requirement that analyze_candidate ran this turn.
    registry = build_registry(data_source="mock")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "tool",
            "tool_name": "match_job",
            "arguments": {"job_key": "mock:a"},
            "reason": "match with existing material",
        },
        registry,
    )
    assert checked["ok"] is True
    bound = bind_tool_arguments(state, tool_action("match_job", {"job_key": "mock:a"}))
    assert bound["ok"] is True
    assert "candidate_profile" in bound["arguments"] or "candidate_context" in bound["arguments"]


def test_match_insufficient_material_returns_observation_not_auto_analyze():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    state.understanding_status = "ok"
    state.jobs["mock:a"] = JobRecord(
        job_key="mock:a",
        stage="analyzed",
        listed_order=0,
        listed={"platform": "mock", "job_id": "a"},
        opened={"platform": "mock", "job_id": "a", "job_description": "JD"},
        job_profile={"analysis_status": "ok", "job_summary": "A"},
    )
    state.status = "RUNNING"
    registry = build_registry(data_source="mock")
    seen = []

    def script(ctx: dict) -> dict:
        obs = ctx.get("observation") or {}
        seen.append(obs.get("kind"))
        if obs.get("kind") == "insufficient_candidate_material":
            return {"action_type": "finish", "reason": "缺材料，本轮先结束"}
        return {
            "action_type": "tool",
            "tool_name": "match_job",
            "arguments": {"job_key": "mock:a"},
            "reason": "try match",
        }

    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([script, script]))
    assert "insufficient_candidate_material" in seen
    assert "analyze_candidate" not in registry.invocations
    assert state.session.status == "DONE"


def test_analyze_listed_rejects_as_insufficient_material_without_forcing_open():
    state = _world_with_unexplored()
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "tool",
            "tool_name": "analyze_job",
            "arguments": {"job_key": "mock:b"},
            "reason": "skip open",
        },
        build_registry(data_source="mock"),
    )
    assert checked["ok"] is False
    assert "insufficient_job_material" in checked["error"]
    assert "open_job before" not in checked["error"]
    assert "open_job an unexplored" not in checked["error"]


def test_prompt_has_no_fixed_pipeline_orders():
    prompt = REASON_NEXT_ACTION_SYSTEM_PROMPT
    for phrase in FORBIDDEN_PIPELINE_PHRASES:
        assert phrase not in prompt, f"pipeline phrase still in prompt: {phrase}"
    assert "analyze_candidate 只是形成候选人 Interpretation 的一种方式" in prompt
