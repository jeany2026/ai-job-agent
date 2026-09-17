"""Action fact surface: tool descriptions, Observation outcomes, action_state_facts.

Facts only — no Program next_action / forced keyword / forced open.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.action_facts import (
    OUTCOME_EXECUTED_NO_PROGRESS,
    OUTCOME_NOT_EXECUTED_KNOWN_NO_PROGRESS,
    OUTCOME_NOT_EXECUTED_REJECTED,
    STATUS_KNOWN_NO_PROGRESS,
    STATUS_REJECTED,
    classify_observation_outcome,
)
from agent.bind_arguments import bind_tool_arguments
from agent.decide import tool_action
from agent.loop import reduce
from agent.loop_safety import (
    action_fingerprint,
    should_block_repeated_no_progress,
)
from agent.reasoner_context import build_reasoner_payload
from agent.state import JobRecord, new_agent_state
from agent.validate_action import validate_reasoner_action
from tools.registry import available_tool_contracts, build_registry


_TIMING_ADVICE = re.compile(
    r"(Prefer\s+open|prefer\s+open|use\s+continue\s+for|use\s+mode=continue\s+for|"
    r"should\s+(first|call|open|search)|先.*再|应该先|must\s+open|must\s+continue)",
    re.IGNORECASE,
)


def test_a_available_tools_have_no_timing_advice():
    registry = build_registry(data_source="mock")
    contracts = available_tool_contracts(registry)
    assert contracts
    for item in contracts:
        blob = " ".join(
            str(item.get(key) or "")
            for key in ("name", "description", "output")
        )
        assert not _TIMING_ADVICE.search(blob), (
            f"timing advice leaked into available_tools for {item.get('name')}: {blob}"
        )
    search = next(c for c in contracts if c["name"] == "search_jobs")
    assert "Prefer open" not in (search.get("description") or "")
    assert "use mode=continue for more" not in (search.get("description") or "")
    assert "Does not" in (search.get("description") or "")


def test_b_search_no_progress_distinct_from_action_reject():
    executed = {
        "kind": "tool_result",
        "search_executed": True,
        "progress": False,
        "new_jobs": 0,
        "duplicate_jobs": 3,
    }
    rejected = {
        "kind": "action_rejected",
        "error": "job already opened with JD text in World",
        "attempted": {"tool_name": "open_job", "arguments": {"job_key": "mock:a"}},
    }
    known = {
        "kind": "repeated_no_progress",
        "search_executed": False,
        "tool_executed": False,
        "progress": False,
        "attempted": {
            "tool_name": "search_jobs",
            "arguments": {"keyword": "产品经理", "city": "深圳", "mode": "fresh"},
        },
    }
    assert classify_observation_outcome(executed) == OUTCOME_EXECUTED_NO_PROGRESS
    assert classify_observation_outcome(rejected) == OUTCOME_NOT_EXECUTED_REJECTED
    assert classify_observation_outcome(known) == OUTCOME_NOT_EXECUTED_KNOWN_NO_PROGRESS
    assert classify_observation_outcome(rejected) != classify_observation_outcome(known)

    state = new_agent_state(goal_input="找工作", data_source="mock")
    state.status = "RUNNING"
    action = tool_action(
        "search_jobs",
        {"keyword": "产品经理", "city": "深圳", "limit": 8},
        reason="search",
    )
    job = {
        "platform": "mock",
        "job_id": "a",
        "job_title": "产品经理",
        "company_name": "X",
        "city": "深圳",
        "job_url": "https://mock.example/a",
    }
    state = reduce(state, action, {"jobs": [job], "platform": "mock"})
    state = reduce(state, action, {"jobs": [job], "platform": "mock"})
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert payload["observation"]["execution_outcome"] == OUTCOME_EXECUTED_NO_PROGRESS

    state.last_raw_observation = rejected
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert payload["observation"]["execution_outcome"] == OUTCOME_NOT_EXECUTED_REJECTED
    assert "action_rejected" in (
        payload["observation"].get("execution_outcome_semantics") or {}
    ).get("note", "")

    state.last_raw_observation = known
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert payload["observation"]["execution_outcome"] == OUTCOME_NOT_EXECUTED_KNOWN_NO_PROGRESS


def test_c_opened_job_fact_in_context_without_next_action():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    state.jobs["mock:a"] = JobRecord(
        job_key="mock:a",
        stage="opened",
        listed_order=0,
        listed={"job_id": "a", "platform": "mock"},
        opened={
            "job_id": "a",
            "platform": "mock",
            "job_description": "负责产品规划",
            "job_url": "https://mock.example/a",
        },
    )
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    facts = payload["action_state_facts"]
    assert facts == payload["context"]["action_state_facts"]
    open_facts = [f for f in facts if f.get("tool_name") == "open_job" and f.get("job_key") == "mock:a"]
    assert open_facts
    assert open_facts[0]["status"] == STATUS_REJECTED
    assert "next_action" not in open_facts[0]
    assert "forbidden_actions" not in payload
    assert "next_action" not in payload
    for fact in facts:
        assert "next_action" not in fact
        assert fact.get("kind") == "action_state_fact"


def test_d_known_no_progress_search_fact_does_not_auto_choose_action():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    action = tool_action(
        "search_jobs",
        {"keyword": "产品经理", "city": "深圳", "limit": 8, "mode": "fresh"},
        reason="again",
    )
    fp = action_fingerprint(action)
    state.search.stats["last_action_fingerprint"] = fp
    state.search.stats["last_action_progress"] = False
    state.search.stats["consecutive_identical_no_progress"] = 2
    state.last_raw_observation = {
        "kind": "repeated_no_progress",
        "search_executed": False,
        "tool_executed": False,
        "progress": False,
        "attempted": {
            "tool_name": "search_jobs",
            "arguments": {"keyword": "产品经理", "city": "深圳", "limit": 8, "mode": "fresh"},
        },
        "message": "identical action repeated without World progress",
    }
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    facts = payload["action_state_facts"]
    search_facts = [
        f
        for f in facts
        if f.get("tool_name") == "search_jobs"
        and f.get("status") in {STATUS_KNOWN_NO_PROGRESS, "no_progress"}
    ]
    assert search_facts
    assert payload["observation"]["execution_outcome"] == OUTCOME_NOT_EXECUTED_KNOWN_NO_PROGRESS
    # Projection must not invent a next Tool / workflow.
    for fact in facts:
        assert "next_action" not in fact
        assert "recommended_tool" not in fact
        assert "suggested_keyword" not in fact
    assert "next_action" not in (payload.get("program_hints") or {})
    blob = str(facts)
    assert "must open" not in blob.lower()
    assert "换关键词" not in blob


def test_e_schema_legal_repeat_still_allowed_loop_safety_fail_closed():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    registry = build_registry(data_source="mock")
    decision = {
        "action_type": "tool",
        "tool_name": "search_jobs",
        "arguments": {"keyword": "产品经理", "city": "深圳", "limit": 8, "mode": "fresh"},
        "reason": "retry same search",
    }
    checked = validate_reasoner_action(state, decision, registry)
    assert checked["ok"] is True

    action = tool_action(
        "search_jobs",
        decision["arguments"],
        reason=decision["reason"],
    )
    state.search.stats["last_action_fingerprint"] = action_fingerprint(action)
    state.search.stats["last_action_progress"] = False
    state.search.stats["consecutive_identical_no_progress"] = 1
    assert should_block_repeated_no_progress(state, action) is True

    # Already-opened job: schema-legal proposal; Program rejects at validate.
    state.jobs["mock:a"] = JobRecord(
        job_key="mock:a",
        stage="opened",
        listed_order=0,
        listed={"job_id": "a", "platform": "mock"},
        opened={
            "job_id": "a",
            "platform": "mock",
            "job_description": "JD text",
            "job_url": "https://mock.example/a",
        },
    )
    open_decision = {
        "action_type": "tool",
        "tool_name": "open_job",
        "arguments": {"job_key": "mock:a"},
        "reason": "open again",
    }
    # Still a schema-shaped Action; validate fails closed (not schema-illegal).
    from tools.reason_next_action import ALLOWED_ACTION_TYPES

    assert open_decision["action_type"] in ALLOWED_ACTION_TYPES
    open_checked = validate_reasoner_action(state, open_decision, registry)
    assert open_checked["ok"] is False
    assert "already opened" in str(open_checked.get("error") or "")


def test_f_reason_not_used_by_binding_or_fingerprint():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    state.jobs["mock:a"] = JobRecord(
        job_key="mock:a",
        stage="opened",
        listed_order=0,
        listed={"job_id": "a", "platform": "mock", "job_description": "x"},
        opened={
            "job_id": "a",
            "platform": "mock",
            "job_description": "负责产品",
            "job_url": "https://mock.example/a",
        },
    )
    args = {"job_key": "mock:a"}
    a = tool_action("analyze_job", args, reason="我想换关键词成高级产品经理")
    b = tool_action("analyze_job", args, reason="完全不同的 reason 文案")
    assert action_fingerprint(a) == action_fingerprint(b)

    bound = bind_tool_arguments(state, a)
    assert bound["ok"] is True
    assert "reason" not in (bound.get("arguments") or {})
    assert (bound.get("arguments") or {}).get("job_key") == "mock:a"

    search_a = tool_action(
        "search_jobs",
        {"keyword": "产品经理", "city": "深圳"},
        reason="我其实想搜高级产品经理并 continue",
    )
    search_b = tool_action(
        "search_jobs",
        {"keyword": "产品经理", "city": "深圳", "mode": "fresh"},
        reason="ignored",
    )
    assert action_fingerprint(search_a) == action_fingerprint(search_b)
