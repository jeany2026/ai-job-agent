"""Reasoner Authority Audit: only Reasoner may select business Actions."""

from __future__ import annotations

import ast
import copy
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.human_gate_continuity import (
    restore_world_from_human_gate_snapshot,
    snapshot_world_for_human_gate,
)
from agent.loop import THRASH_RECOVERY_AFTER, _note_thrash_for_reasoner, _thrash_recovery_action, run_loop
from agent.reasoner_context import build_reasoner_payload
from agent.state import JobRecord, new_agent_state
from agent.world_restore import restore_continuity_world, restore_job_record
from conversation.job_context import JobContext
from tests.mock_llm import ScriptedReasonerLLM
from tools.registry import build_registry

BUSINESS_TOOLS = frozenset(
    {
        "search_jobs",
        "open_job",
        "mock_open_job",
        "analyze_candidate",
        "analyze_job",
        "match_job",
        "interpret_job_actions",
        "bind_task",
        "hydrate_job_reference",
        "skip_job",
        "stop_task",
        "execute_action",
        "inspect_job",
        "plan_search",
        "understand_user_input",
    }
)

# Modules that must never construct business tool Actions for recovery/restore.
AUDITED_MODULES = [
    ROOT_DIR / "agent" / "loop.py",
    ROOT_DIR / "agent" / "world_restore.py",
    ROOT_DIR / "agent" / "human_gate_continuity.py",
    ROOT_DIR / "agent" / "human_gate.py",
    ROOT_DIR / "agent" / "bind_arguments.py",
    ROOT_DIR / "agent" / "validate_action.py",
    ROOT_DIR / "agent" / "loop_safety.py",
]

# Functions allowed to mention tool_action for Observation bookkeeping only
# (they do not inject a new Reasoner choice into the loop).
BOOKKEEPING_OK = frozenset(
    {
        "_reduce_goal",
        "_reduce_candidate",
        "_reduce_analyze_candidate",
        "_reduce_parse_user_goal",
        "_reduce_understand",
        "_reduce_understand_user_input",
    }
)


def _tool_action_names_in_function(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    found: list[str] = []
    for child in ast.walk(fn):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        is_tool_action = (isinstance(func, ast.Name) and func.id == "tool_action") or (
            isinstance(func, ast.Attribute) and func.attr == "tool_action"
        )
        if not is_tool_action or not child.args:
            continue
        first = child.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.append(first.value)
    return found


def test_thrash_helpers_never_hardcode_business_tools():
    tree = ast.parse((ROOT_DIR / "agent" / "loop.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_note_thrash_for_reasoner",
            "_thrash_recovery_action",
        }:
            tools = _tool_action_names_in_function(node)
            assert not tools, f"{node.name} must not call tool_action; found {tools}"


def test_program_modules_do_not_emit_business_tool_actions_via_tool_action():
    violations: list[str] = []
    for path in AUDITED_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in BOOKKEEPING_OK:
                continue
            if node.name in {"_act", "run_loop", "reduce", "decide"}:
                continue
            if node.name.startswith("_apply_"):
                continue
            business = [name for name in _tool_action_names_in_function(node) if name in BUSINESS_TOOLS]
            if business:
                violations.append(f"{path.name}:{node.name}->{business}")
    assert not violations, "Program constructed business Actions:\n" + "\n".join(violations)


def test_thrash_recovery_action_returns_none_and_does_not_open():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.understanding_status = "ok"
    state.jobs["mock:a"] = JobRecord(
        job_key="mock:a",
        stage="analyzed",
        listed_order=0,
        listed={"platform": "mock", "job_id": "a"},
        opened={"platform": "mock", "job_id": "a", "job_description": "JD"},
        job_profile={"analysis_status": "ok"},
    )
    state.jobs["mock:b"] = JobRecord(
        job_key="mock:b",
        stage="listed",
        listed_order=1,
        listed={"platform": "mock", "job_id": "b"},
    )
    state.search.stats["consecutive_action_rejects"] = THRASH_RECOVERY_AFTER
    state.last_raw_observation = {
        "kind": "action_rejected",
        "error": "job already analyzed; do not re-analyze the same job",
        "attempted": {"tool_name": "analyze_job"},
    }
    assert _thrash_recovery_action(state) is None
    assert state.last_raw_observation["kind"] == "thrash_re_reason"
    assert state.last_raw_observation.get("program_must_not_select_tool") is True
    assert "mock:b" in state.last_raw_observation["unexplored_listed_job_keys"]
    assert state.jobs["mock:b"].stage == "listed"
    assert state.jobs["mock:b"].opened is None


def test_thrash_loop_requires_reasoner_to_open():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.understanding_status = "ok"
    state.candidate.memory = {"status": "usable", "facts": [{"kind": "skill", "name": "产品"}]}
    state.jobs["mock:a"] = JobRecord(
        job_key="mock:a",
        stage="analyzed",
        listed_order=0,
        listed={"platform": "mock", "job_id": "a"},
        opened={"platform": "mock", "job_id": "a", "job_description": "JD"},
        job_profile={"analysis_status": "ok"},
    )
    state.jobs["mock:b"] = JobRecord(
        job_key="mock:b",
        stage="listed",
        listed_order=1,
        listed={"platform": "mock", "job_id": "b"},
    )
    state.search.stats.update({"listed": 2, "opened": 1, "searches": 1})
    bad = {
        "action_type": "tool",
        "tool_name": "analyze_job",
        "arguments": {"job_key": "mock:a"},
        "reason": "thrash",
    }
    registry = build_registry(data_source="mock")
    result = run_loop(
        copy.deepcopy(state),
        registry,
        llm_provider=ScriptedReasonerLLM(
            [bad] * THRASH_RECOVERY_AFTER
            + [
                {
                    "action_type": "finish",
                    "reason": "stop without opening",
                    "error_code": "GOAL_INCOMPLETE",
                }
            ]
        ),
        max_steps=20,
    )
    assert "open_job" not in registry.invocations
    assert "analyze_candidate" not in registry.invocations
    assert result.jobs["mock:b"].opened is None
    assert int(result.search.stats.get("thrash_re_reason_notes") or 0) >= 1


def test_thrash_context_exposes_hints_not_next_action():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    state.search.stats["consecutive_action_rejects"] = THRASH_RECOVERY_AFTER
    state.last_raw_observation = {
        "kind": "action_rejected",
        "error": "insufficient_job_material: job is still listed with no opened JD fact for analyze_job",
        "attempted": {"tool_name": "analyze_job"},
    }
    _note_thrash_for_reasoner(state)
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert payload["observation"]["kind"] == "thrash_re_reason"
    hints = payload["program_hints"]
    assert hints["not_next_action"] is True
    assert "next_action" not in hints


def test_human_gate_restore_does_not_invoke_tools():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.jobs["mock:j1"] = JobRecord(
        job_key="mock:j1",
        stage="matched",
        listed_order=0,
        listed={"platform": "mock", "job_id": "j1", "job_title": "PM"},
        opened={"platform": "mock", "job_id": "j1", "job_description": "JD"},
        job_profile={"analysis_status": "ok"},
        match_result={"recommendation": "yes"},
    )
    snap = snapshot_world_for_human_gate(state)
    fresh = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    registry = build_registry(data_source="mock")
    before = list(registry.invocations)
    restore_world_from_human_gate_snapshot(fresh, snap)
    assert registry.invocations == before
    assert fresh.jobs["mock:j1"].stage == "opened"


def test_persistence_restore_does_not_invoke_tools_or_advance_stage():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    ctx = JobContext(
        context_id="mock:j1",
        job_key="mock:j1",
        platform="mock",
        job_id="j1",
        title="PM",
        company="Co",
        job_listing={
            "platform": "mock",
            "job_id": "j1",
            "job_title": "PM",
            "job_description": "JD",
        },
        job_profile={"analysis_status": "ok"},
        match_result={"recommendation": "yes"},
        stage="matched",
    )
    registry = build_registry(data_source="mock")
    before = list(registry.invocations)
    record = restore_job_record(state, ctx)
    assert registry.invocations == before
    assert record.stage == "opened"
    assert record.stage != "matched"
    # Continuity restore entrypoint also must not invoke tools.
    state2 = new_agent_state(
        goal_input="找深圳产品经理",
        data_source="mock",
        session_context={"job_contexts": [ctx.to_dict()]},
    )
    restore_continuity_world(state2)
    assert registry.invocations == before
