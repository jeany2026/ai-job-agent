"""Fail faster on thrash; explore-first contracts; honest progress/fail copy."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.loop import REPEATED_ACTION_REJECT_LIMIT, run_loop
from agent.reasoner_context import build_reasoner_payload
from agent.state import JobRecord, new_agent_state, record_error
from agent.validate_action import validate_reasoner_action
from api.serialize import (
    MAX_STEPS_MESSAGE,
    PROGRESS_MESSAGES,
    progress_message,
    serialize_agent_state,
)
from tests.mock_llm import ScriptedReasonerLLM
from tools.registry import build_registry


def _listed_and_analyzed_state():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.understanding_status = "ok"
    state.candidate.memory = {"status": "usable", "facts": [{"kind": "skill", "name": "产品"}]}
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


def test_progress_messages_distinguish_open_analyze_match():
    assert "打开" in PROGRESS_MESSAGES["ENRICHING_JOBS"]
    assert "分析职位" in PROGRESS_MESSAGES["ANALYZING"]
    assert "匹配" in PROGRESS_MESSAGES["MATCHING"]
    assert progress_message("DECIDING") == "正在判断下一步……"


def test_max_steps_message_does_not_blame_missing_resume_when_material_present():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    state.status = "FAILED"
    state.search.stats["opened"] = 1
    state.candidate.memory = {"status": "usable", "facts": [{"kind": "skill", "name": "x"}]}
    record_error(state, "loop exceeded max_steps=120")
    payload = serialize_agent_state(state)
    assert payload["error_code"] == "max_steps"
    assert payload["message"] == MAX_STEPS_MESSAGE
    assert "补充简历" not in payload["message"]


def test_context_exposes_unexplored_listed():
    state = _listed_and_analyzed_state()
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert payload["context"]["unexplored_listed_count"] == 1
    assert payload["context"]["unexplored_listed_job_keys"] == ["mock:b"]


def test_action_rejected_observation_includes_recovery_card():
    state = _listed_and_analyzed_state()
    state.last_raw_observation = {
        "kind": "action_rejected",
        "error": "job already analyzed; do not re-analyze the same job",
        "attempted": {"tool_name": "analyze_job"},
    }
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    observation = payload["observation"]
    assert observation["kind"] == "action_rejected"
    assert "recovery" not in observation
    assert "coach_zh" not in observation
    hints = payload["program_hints"]
    assert hints["layer"] == "program_hint"
    assert hints["not_observation"] is True
    assert hints["not_next_action"] is True
    recovery = hints["recovery"]
    assert recovery["do_not_retry_same_action"] is True
    assert recovery["unexplored_listed_count"] == 1
    assert recovery["unexplored_listed_job_keys"] == ["mock:b"]
    assert "prefer_open_unexplored_listed_before_reanalyze_or_research" not in recovery["focus"]
    assert "do_not_retry_identical_rejected_action" in recovery["focus"]
    assert recovery["unexplored_listed_count"] == 1


def test_open_quota_reject_coaches_away_from_more_opens():
    state = _listed_and_analyzed_state()
    state.search.stats["opened"] = 10
    state.constraints.max_open_jd = 10
    state.last_raw_observation = {
        "kind": "action_rejected",
        "error": "quota exceeded: open",
        "attempted": {"tool_name": "open_job"},
    }
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert "recovery" not in payload["observation"]
    assert "coach_zh" not in payload["observation"]
    recovery = payload["program_hints"]["recovery"]
    assert recovery["remaining_opens"] == 0
    assert "open_quota_exhausted_do_not_open_job" in recovery["focus"]
    notes = payload["program_hints"].get("notes_zh") or ""
    assert "open_job" in notes
    assert "禁止再" in notes


def test_reject_reanalyze_when_unexplored_listed_remain():
    state = _listed_and_analyzed_state()
    registry = build_registry(data_source="mock")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "tool",
            "tool_name": "analyze_job",
            "arguments": {"job_key": "mock:a"},
            "reason": "again",
        },
        registry,
    )
    assert checked["ok"] is False
    assert "already analyzed" in checked["error"]
    assert "open_job" not in checked["error"]


def test_reject_reanalyze_even_when_no_unexplored_listed():
    """Successful analyze is idempotent — anti-thrash, not an explore-first nudge."""
    state = _listed_and_analyzed_state()
    del state.jobs["mock:b"]
    state.search.stats.update({"listed": 1, "opened": 1})
    registry = build_registry(data_source="mock")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "tool",
            "tool_name": "analyze_job",
            "arguments": {"job_key": "mock:a"},
            "reason": "again after pool empty",
        },
        registry,
    )
    assert checked["ok"] is False
    assert "already analyzed" in checked["error"]
    assert "do not re-analyze" in checked["error"]


def test_reject_inspect_when_world_already_has_jd():
    state = _listed_and_analyzed_state()
    registry = build_registry(data_source="mock")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "tool",
            "tool_name": "inspect_job",
            "arguments": {"job_key": "mock:a"},
            "reason": "re-read",
        },
        registry,
    )
    assert checked["ok"] is False
    assert "inspect_job blocked" in checked["error"]


def test_context_exposes_already_analyzed_keys():
    state = _listed_and_analyzed_state()
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert "mock:a" in payload["context"]["already_analyzed_job_keys"]


def test_reject_analyze_while_still_listed():
    state = _listed_and_analyzed_state()
    registry = build_registry(data_source="mock")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "tool",
            "tool_name": "analyze_job",
            "arguments": {"job_key": "mock:b"},
            "reason": "skip open",
        },
        registry,
    )
    assert checked["ok"] is False
    assert "insufficient_job_material" in checked["error"]
    assert "still listed" in checked["error"] or "listed" in checked["error"]


def test_allow_first_analyze_on_opened_job_with_unexplored_siblings():
    state = _listed_and_analyzed_state()
    state.jobs["mock:a"].stage = "opened"
    state.jobs["mock:a"].job_profile = None
    registry = build_registry(data_source="mock")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "tool",
            "tool_name": "analyze_job",
            "arguments": {"job_key": "mock:a"},
            "reason": "first analyze",
        },
        registry,
    )
    assert checked["ok"] is True


def test_repeated_rejects_fail_closed_fast():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.understanding_status = "ok"
    state.candidate.memory = {"status": "usable", "facts": [{"kind": "skill", "name": "产品"}]}
    state.jobs["mock:a"] = JobRecord(
        job_key="mock:a",
        stage="opened",
        listed_order=0,
        listed={"platform": "mock", "job_id": "a"},
        opened={"platform": "mock", "job_id": "a", "job_description": "JD"},
    )
    # Already-opened re-open is always rejected; no unexplored listed for recovery.
    state.search.stats.update({"listed": 1, "opened": 1, "searches": 1})
    bad = {
        "action_type": "tool",
        "tool_name": "open_job",
        "arguments": {"job_key": "mock:a"},
        "reason": "thrash",
    }
    llm = ScriptedReasonerLLM([bad] * (REPEATED_ACTION_REJECT_LIMIT + 2))
    state = run_loop(state, build_registry(data_source="mock"), llm_provider=llm, max_steps=40)
    assert state.session.status == "FAILED"
    payload = serialize_agent_state(state)
    assert payload["error_code"] == "repeated_action_rejected"
    assert "简历" not in payload["message"]


def test_explore_thrash_emits_observation_not_program_open():
    """Thrash recovery must not inject open_job — Reasoner must choose it."""
    from agent.loop import THRASH_RECOVERY_AFTER

    state = _listed_and_analyzed_state()
    bad = {
        "action_type": "tool",
        "tool_name": "analyze_job",
        "arguments": {"job_key": "mock:a"},
        "reason": "thrash",
    }
    open_b = {
        "action_type": "tool",
        "tool_name": "open_job",
        "arguments": {"job_key": "mock:b"},
        "reason": "reasoner opens after thrash observation",
    }
    script = [bad] * THRASH_RECOVERY_AFTER + [
        open_b,
        {"action_type": "finish", "reason": "done after reasoner open"},
    ]
    registry = build_registry(data_source="mock")
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM(script), max_steps=20)
    assert "open_job" in registry.invocations
    assert registry.invocations.count("open_job") == 1
    assert state.jobs["mock:b"].opened is not None or state.jobs["mock:b"].stage != "listed"
    assert int(state.search.stats.get("thrash_re_reason_notes") or 0) >= 1
    assert int(state.search.stats.get("thrash_recovery_opens") or 0) == 0


def test_llm_quota_rejects_fail_closed_fast():
    state = new_agent_state(
        goal_input="找工作",
        data_source="mock",
        constraints={"max_llm_calls": 0, "max_open_jd": 10},
    )
    state.understanding_status = "ok"
    state.candidate.memory = {"status": "usable", "facts": [{"kind": "skill", "name": "产品"}]}
    state.jobs["mock:a"] = JobRecord(
        job_key="mock:a",
        stage="opened",
        listed_order=0,
        listed={"platform": "mock", "job_id": "a"},
        opened={"platform": "mock", "job_id": "a", "job_description": "JD"},
    )
    state.jobs["mock:b"] = JobRecord(
        job_key="mock:b",
        stage="listed",
        listed_order=1,
        listed={"platform": "mock", "job_id": "b"},
    )
    state.search.stats.update({"listed": 2, "opened": 1, "llm_calls": 99})
    match = {
        "action_type": "tool",
        "tool_name": "match_job",
        "arguments": {"job_key": "mock:a"},
        "reason": "quota thrash",
    }
    # match needs profile — use analyze_job which is LLM-gated
    analyze = {
        "action_type": "tool",
        "tool_name": "analyze_job",
        "arguments": {"job_key": "mock:a"},
        "reason": "quota thrash",
    }
    llm = ScriptedReasonerLLM([analyze, analyze, analyze, analyze])
    state = run_loop(state, build_registry(data_source="mock"), llm_provider=llm, max_steps=20)
    assert state.session.status == "FAILED"
    payload = serialize_agent_state(state)
    assert payload["error_code"] == "llm_quota_exhausted"
