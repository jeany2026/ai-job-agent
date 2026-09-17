"""Search Observation world facts + Reasoner authority (no forced search→open)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import tool_action
from agent.loop import reduce, run_loop
from agent.reasoner_context import build_reasoner_payload
from agent.state import new_agent_state
from agent.validate_action import validate_reasoner_action
from platforms.mock.jobs import mock_search_jobs
from platforms.mock.search_executor import reset_mock_search_buffers
from tests.mock_llm import ScriptedReasonerLLM
from tools.registry import build_registry
from tools.reason_next_action import REASON_NEXT_ACTION_SYSTEM_PROMPT


def setup_function(_fn=None):
    reset_mock_search_buffers()


def _searched_pm_state(*, limit: int = 8, pad_extra: int = 0):
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = {
        "analysis_status": "ok",
        "target_roles": ["产品经理"],
        "cities": ["深圳"],
    }
    result = mock_search_jobs(
        keyword="产品经理",
        city="深圳",
        limit=limit,
        mode="fresh",
        pad_extra=pad_extra,
    )
    action = tool_action(
        "search_jobs",
        {"keyword": "产品经理", "city": "深圳", "limit": limit, "mode": "fresh"},
        reason="search",
    )
    return reduce(state, action, result), action, result


def test_exhausted_search_observation_exposes_world_facts_to_reasoner():
    state, _action, result = _searched_pm_state(limit=30, pad_extra=0)
    # Drain until exhausted so Observation reflects result-set end.
    while result.get("can_continue"):
        result = mock_search_jobs(
            keyword="产品经理",
            city="深圳",
            limit=8,
            mode="continue",
            search_session=result["search_session"],
            pad_extra=0,
        )
        state = reduce(
            state,
            tool_action(
                "search_jobs",
                {
                    "keyword": "产品经理",
                    "city": "深圳",
                    "limit": 8,
                    "mode": "continue",
                },
            ),
            result,
        )
    # Force an identical zero-progress fresh observation for fact projection.
    dup = mock_search_jobs(
        keyword="产品经理",
        city="深圳",
        limit=8,
        mode="fresh",
        pad_extra=0,
    )
    state = reduce(
        state,
        tool_action(
            "search_jobs",
            {"keyword": "产品经理", "city": "深圳", "limit": 8, "mode": "fresh"},
        ),
        dup,
    )
    registry = build_registry(data_source="mock")
    payload = build_reasoner_payload(state, registry)
    obs = payload["observation"]
    assert obs.get("search_executed") is True
    assert "world_search_facts" in obs
    facts = obs["world_search_facts"]
    assert facts.get("query_executed_this_turn") is True
    assert "unexplored_listed_count" in obs
    assert "unexplored_listed_job_keys" in obs
    assert obs["unexplored_listed_count"] == payload["context"]["unexplored_listed_count"]
    assert isinstance(obs["unexplored_listed_job_keys"], list)
    notes = (payload.get("program_hints") or {}).get("notes_zh") or ""
    # Facts OK; must not prescribe a mandatory next Tool.
    for banned in ("必须 open", "必须换", "禁止再次 search", "必须 ask_user"):
        assert banned not in notes
    assert "open_job" not in notes or "必须" not in notes


def test_reasoner_may_choose_open_or_ask_or_finish_after_search():
    base, _action, _result = _searched_pm_state(limit=4, pad_extra=0)
    job_key = next(iter(base.jobs))
    open_act = {
        "action_type": "tool",
        "tool_name": "open_job",
        "arguments": {"job_key": job_key},
        "reason": "explore listed",
    }
    ask_act = {
        "action_type": "ask_user",
        "intent": "clarification",
        "question": "你更看重薪资还是行业？",
        "reason": "clarify",
    }
    finish_act = {"action_type": "finish", "reason": "本轮先停下"}
    alt_search = {
        "action_type": "tool",
        "tool_name": "search_jobs",
        "arguments": {"keyword": "产品主管", "city": "深圳", "limit": 8},
        "reason": "change query",
    }

    for script, expect_status, expect_tool in (
        ([open_act, finish_act], "DONE", "open_job"),
        ([ask_act], "WAITING_USER", None),
        ([finish_act], "DONE", None),
        ([alt_search, finish_act], "DONE", "search_jobs"),
    ):
        state = run_loop(
            __import__("copy").deepcopy(base),
            build_registry(data_source="mock"),
            llm_provider=ScriptedReasonerLLM(script),
            max_steps=10,
        )
        assert state.session.status == expect_status
        if expect_tool == "open_job":
            assert state.jobs[job_key].opened is not None
        if expect_tool == "search_jobs":
            assert state.search.stats.get("search_executed_count", 0) >= 2


def test_program_does_not_force_search_then_open():
    state, _action, _result = _searched_pm_state(limit=4, pad_extra=0)
    registry = build_registry(data_source="mock")
    # Same-query search still structurally allowed by validate (Reasoner authority).
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
    # Finish without open is allowed.
    checked_finish = validate_reasoner_action(
        state,
        {"action_type": "finish", "reason": "本轮无需打开"},
        registry,
    )
    assert checked_finish["ok"] is True
    prompt = REASON_NEXT_ACTION_SYSTEM_PROMPT
    assert "必须 open_job" not in prompt
    assert "禁止再次 search" not in prompt
    assert "不是固定 Workflow" in prompt or "不是固定顺序" in prompt


def test_mock_search_does_not_auto_open_analyze_match():
    result = mock_search_jobs(keyword="产品经理", city="深圳", limit=8, mode="fresh")
    assert len(result["jobs"]) >= 2
    for job in result["jobs"]:
        assert job.get("job_description") in (None, "")
        assert "job_profile" not in job
        assert "match_result" not in job
        assert "action_analysis" not in job


def test_program_hints_reach_reasoner_llm_user_payload():
    """program_hints must be wired into reason_next_action's LLM user JSON."""
    state, _action, _result = _searched_pm_state(limit=4, pad_extra=0)
    # Force a no-progress observation so program_hints are non-null.
    state.last_raw_observation = {
        **(state.last_raw_observation or {}),
        "progress": False,
        "new_jobs": 0,
        "newly_ingested": 0,
        "duplicate_jobs": 4,
        "fetch_status": "exhausted",
        "can_continue": False,
    }
    registry = build_registry(data_source="mock")
    payload = build_reasoner_payload(state, registry)
    assert payload.get("program_hints") is not None

    captured: dict = {}

    class CaptureLLM:
        def complete_json(self, *, system: str, user: str):
            captured["user"] = user
            return {
                "action_type": "finish",
                "reason": "本轮先结束以便断言 program_hints 已送达",
            }

    from tools.reason_next_action import reason_next_action

    out = reason_next_action(payload, llm_provider=CaptureLLM())
    assert out.get("action_type") == "finish"
    user_obj = __import__("json").loads(captured["user"])
    assert "program_hints" in user_obj
    assert user_obj["program_hints"] is not None
    assert user_obj["program_hints"].get("trigger") == "search_observation"
    assert "observation" in user_obj
    assert "world_search_facts" in (user_obj.get("observation") or {})
