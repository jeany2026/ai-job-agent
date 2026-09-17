"""Search Observation progress semantics + generic repeated_no_progress loop safety."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import tool_action
from agent.loop import reduce, run_loop
from agent.loop_safety import (
    REPEATED_NO_PROGRESS_LIMIT,
    action_fingerprint,
    should_block_repeated_no_progress,
)
from agent.reasoner_context import build_reasoner_payload
from agent.state import new_agent_state
from rules.quota import listed_count, note_search, remaining_list_capacity
from tests.mock_llm import ScriptedReasonerLLM
from tools.registry import build_registry


def _job(job_id: str, title: str = "产品经理") -> dict:
    return {
        "platform": "mock",
        "job_id": job_id,
        "job_title": title,
        "company_name": f"公司{job_id}",
        "city": "深圳",
        "job_url": f"https://mock.example/{job_id}",
    }


def test_note_search_distinguishes_executed_listed_and_zero_increment():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    note_search(state, newly_ingested=3, duplicates=5)
    assert state.search.stats["search_executed_count"] == 1
    assert state.search.stats["searches"] == 1
    assert listed_count(state) == 3
    assert state.search.stats["newly_ingested_total"] == 3
    assert state.search.stats["duplicate_total"] == 5
    assert state.search.stats["zero_increment_count"] == 0
    assert state.search.stats["last_search_progress"] is True

    note_search(state, newly_ingested=0, duplicates=8)
    assert state.search.stats["search_executed_count"] == 2
    assert listed_count(state) == 3
    assert state.search.stats["zero_increment_count"] == 1
    assert state.search.stats["last_search_progress"] is False
    assert state.search.stats["duplicate_total"] == 13
    assert remaining_list_capacity(state) == state.constraints.max_search_results - 3


def test_reduce_search_observation_exposes_progress_fields():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.status = "RUNNING"
    action = tool_action(
        "search_jobs",
        {"keyword": "产品经理", "city": "深圳", "limit": 8},
        reason="first search",
    )
    state = reduce(state, action, {"jobs": [_job("a"), _job("b")], "platform": "mock"})
    obs = state.last_raw_observation
    assert obs["kind"] == "tool_result"
    assert obs["search_executed"] is True
    assert obs["new_jobs"] == 2
    assert obs["duplicate_jobs"] == 0
    assert obs["progress"] is True
    assert obs["query"]["keyword"] == "产品经理"
    assert listed_count(state) == 2

    state = reduce(state, action, {"jobs": [_job("a"), _job("b")], "platform": "mock"})
    obs = state.last_raw_observation
    assert obs["search_executed"] is True
    assert obs["new_jobs"] == 0
    assert obs["duplicate_jobs"] == 2
    assert obs["progress"] is False
    assert state.search.stats["search_executed_count"] == 2
    assert state.search.stats["zero_increment_count"] == 1
    assert listed_count(state) == 2


def test_reasoner_payload_includes_search_stats_and_list_capacity():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    note_search(state, newly_ingested=2, duplicates=0)
    note_search(state, newly_ingested=0, duplicates=4)
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert "remaining_list_capacity" in payload["constraints"]
    assert payload["constraints"]["remaining_list_capacity"] == payload["constraints"]["remaining_search_slots"]
    stats = payload["history"]["search_stats"]
    assert stats["search_executed_count"] == 2
    assert stats["listed_count"] == 2
    assert stats["zero_increment_count"] == 1
    assert stats["last_search_progress"] is False


def test_identical_zero_progress_search_emits_repeated_no_progress():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = {
        "analysis_status": "ok",
        "target_roles": ["产品经理"],
        "cities": ["深圳"],
    }

    search = {
        "action_type": "tool",
        "tool_name": "search_jobs",
        "arguments": {"keyword": "产品经理", "city": "深圳", "limit": 8},
        "reason": "search again",
    }
    finish = {"action_type": "finish", "reason": "本轮先停下，换条件再找"}
    jobs_batch = [_job("j1"), _job("j2")]
    base = build_registry(data_source="mock")
    search_calls = {"n": 0}

    class CountingRegistry:
        def has(self, name: str) -> bool:
            return base.has(name)

        def specs(self):
            return base.specs()

        def invoke(self, name: str, arguments: dict | None = None, **extra):
            if name == "search_jobs":
                search_calls["n"] += 1
                return {"jobs": jobs_batch, "platform": "mock"}
            return base.invoke(name, arguments, **extra)

    out = run_loop(
        state,
        CountingRegistry(),
        llm_provider=ScriptedReasonerLLM([search, search, search, finish]),
        max_steps=20,
    )

    assert search_calls["n"] == 2
    assert out.search.stats["search_executed_count"] == 2
    assert out.search.stats["zero_increment_count"] == 1
    assert out.session.status == "DONE"


def test_repeated_no_progress_fail_closed_when_reasoner_keeps_retrying():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = {
        "analysis_status": "ok",
        "target_roles": ["产品经理"],
        "cities": ["深圳"],
    }
    search = {
        "action_type": "tool",
        "tool_name": "search_jobs",
        "arguments": {"keyword": "产品经理", "city": "深圳", "limit": 8},
        "reason": "keep searching",
    }
    jobs_batch = [_job("j1")]
    base = build_registry(data_source="mock")
    search_calls = {"n": 0}

    class CountingRegistry:
        def has(self, name: str) -> bool:
            return base.has(name)

        def specs(self):
            return base.specs()

        def invoke(self, name: str, arguments: dict | None = None, **extra):
            if name == "search_jobs":
                search_calls["n"] += 1
                return {"jobs": jobs_batch, "platform": "mock"}
            return base.invoke(name, arguments, **extra)

    script = [search] * (REPEATED_NO_PROGRESS_LIMIT + 4)
    out = run_loop(state, CountingRegistry(), llm_provider=ScriptedReasonerLLM(script), max_steps=40)
    assert search_calls["n"] == 2
    assert out.session.status == "FAILED"
    assert out.output.get("error_code") == "repeated_no_progress" or any(
        e.get("kind") == "repeated_no_progress" for e in (out.errors or [])
    )


def test_different_keyword_search_is_not_blocked_as_no_progress():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    action_a = tool_action("search_jobs", {"keyword": "产品经理", "city": "深圳"}, reason="a")
    state.search.stats["last_action_fingerprint"] = action_fingerprint(action_a)
    state.search.stats["last_action_progress"] = False
    state.search.stats["consecutive_identical_no_progress"] = 1

    action_b = tool_action("search_jobs", {"keyword": "高级产品经理", "city": "深圳"}, reason="b")
    assert should_block_repeated_no_progress(state, action_b) is False
    assert should_block_repeated_no_progress(state, action_a) is True


def test_search_mode_omitted_and_fresh_share_fingerprint():
    without = tool_action(
        "search_jobs",
        {"keyword": "产品经理", "city": "深圳", "limit": 8},
        reason="a",
    )
    with_fresh = tool_action(
        "search_jobs",
        {"keyword": "产品经理", "city": "深圳", "limit": 8, "mode": "fresh"},
        reason="b",
    )
    with_continue = tool_action(
        "search_jobs",
        {"keyword": "产品经理", "city": "深圳", "limit": 8, "mode": "continue"},
        reason="c",
    )
    assert action_fingerprint(without) == action_fingerprint(with_fresh)
    assert action_fingerprint(without) != action_fingerprint(with_continue)

    state = new_agent_state(goal_input="找工作", data_source="mock")
    state.search.stats["last_action_fingerprint"] = action_fingerprint(without)
    state.search.stats["last_action_progress"] = False
    state.search.stats["consecutive_identical_no_progress"] = 1
    assert should_block_repeated_no_progress(state, with_fresh) is True
    assert should_block_repeated_no_progress(state, with_continue) is False
