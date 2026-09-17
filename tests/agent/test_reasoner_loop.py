"""Phase 1: Reasoner, not Program status, chooses the next Tool."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import Action, tool_action
from agent.loop import reduce, run_loop
from agent.orchestrator import run_agent
from agent.state import new_agent_state
from tests.mock_llm import (
    ScriptedReasonerLLM,
    conversation_reference,
    goal_response,
    understanding_response,
)
from tools.registry import build_registry

GOAL_TEXT = "帮我找深圳高级产品经理"


def _understand(ctx: dict) -> dict:
    intake = ctx.get("intake") if isinstance(ctx.get("intake"), list) else []
    memory = ctx.get("memory") if isinstance(ctx.get("memory"), dict) else {}
    nested = memory.get("intake") if isinstance(memory.get("intake"), list) else []
    ids = [
        str(item.get("id"))
        for item in (intake or nested)
        if isinstance(item, dict) and item.get("id")
    ]
    return {
        "action_type": "tool",
        "tool_name": "understand_user_input",
        "arguments": {"intake_ids": ids},
        "reason": "reasoner chose understanding",
    }


SEARCH = {
    "action_type": "tool",
    "tool_name": "search_jobs",
    "arguments": {"keyword": "高级产品经理", "city": "深圳", "limit": 30},
    "reason": "search",
}
SEARCH_AGAIN = {
    "action_type": "tool",
    "tool_name": "search_jobs",
    "arguments": {"keyword": "产品总监", "city": "深圳", "limit": 30},
    "reason": "search again",
}
OPEN = {
    "action_type": "tool",
    "tool_name": "open_job",
    "arguments": {"job_id": "mock-direct"},
    "reason": "open",
}
ASK = {"action_type": "ask_user", "intent": "clarification", "question": "要不要继续看这个方向？", "reason": "ask"}
FINISH = {"action_type": "finish", "reason": "reasoner finished"}


def _ask_from_observation(ctx: dict) -> dict:
    observation = ctx.get("observation") if isinstance(ctx.get("observation"), dict) else {}
    context = ctx.get("context") if isinstance(ctx.get("context"), dict) else {}
    error = observation.get("error_code")
    active = observation.get("active_tasks") or context.get("active_tasks") or []
    if not error and isinstance(active, list) and len(active) > 1:
        error = "clarification_needed"
    return {
        "action_type": "ask_user",
        "intent": "clarification",
        "question": observation.get("message") or "请再说明一下。",
        "error_code": error,
        "reason": "reasoner chose ask_user after observation",
    }


def _job_from_payload(ctx: dict, *, stage: str | None = "opened") -> dict:
    jobs = [item for item in (ctx.get("jobs") or ((ctx.get("memory") or {}).get("jobs") or [])) if isinstance(item, dict)]
    if stage is not None:
        jobs = [item for item in jobs if item.get("stage") == stage]
    jobs.sort(key=lambda item: item.get("listed_order") if isinstance(item.get("listed_order"), int) else 10**9)
    return jobs[0] if jobs else {}


def _identity_args(item: dict) -> dict:
    job = item.get("job") if isinstance(item.get("job"), dict) else {}
    arguments: dict = {}
    for field in ("job_key", "job_id", "job_url"):
        value = item.get(field) or job.get(field)
        if value:
            arguments[field] = value
    return arguments


def _analyze_opened(ctx: dict) -> dict:
    item = _job_from_payload(ctx)
    arguments = _identity_args(item)
    return {
        "action_type": "tool",
        "tool_name": "analyze_job",
        "arguments": arguments,
        "reason": "skip interpret",
    }


def _match_opened(ctx: dict) -> dict:
    item = _job_from_payload(ctx)
    arguments = _identity_args(item)
    return {
        "action_type": "tool",
        "tool_name": "match_job",
        "arguments": arguments,
        "reason": "skip interpret and analyze",
    }


def _run(actions, *, constraints=None):
    registry = build_registry(data_source="mock")
    llm = ScriptedReasonerLLM([_understand, *actions])
    state = run_agent(
        goal=GOAL_TEXT,
        resume=None,
        llm_provider=llm,
        registry=registry,
        data_source="mock",
        constraints=constraints,
    )
    return state, registry, llm


def _reasoner_users(llm: ScriptedReasonerLLM) -> list[dict]:
    payloads = []
    for call in llm.calls:
        if "你是全局 Agent Reasoner" not in (call.get("system") or ""):
            continue
        payloads.append(json.loads(call["user"]))
    return payloads


def _searched_state():
    registry = build_registry(data_source="mock")
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = goal_response()
    state.goal["analysis_status"] = "ok"
    state.last_raw_observation = {"kind": "user_turn_understood", "has_user_goal": True}
    result = registry.invoke("search_jobs", dict(SEARCH["arguments"]))
    state = reduce(
        state,
        tool_action("search_jobs", dict(SEARCH["arguments"])),
        result,
    )
    assert state.session.status == "RUNNING"
    assert state.last_raw_observation["tool_name"] == "search_jobs"
    return state, registry


def test_search_does_not_auto_open():
    state, registry, _ = _run([SEARCH, FINISH])
    assert "search_jobs" in registry.invocations
    assert "open_job" not in registry.invocations
    assert "interpret_job_actions" not in registry.invocations
    assert "analyze_job" not in registry.invocations
    assert "match_job" not in registry.invocations
    assert state.session.status == "DONE"
    assert any(record.stage == "listed" for record in state.jobs.values())
    assert state.last_raw_observation["kind"] == "tool_result"
    assert state.last_raw_observation["tool_name"] == "search_jobs"


def test_search_then_search_again():
    state, registry, _ = _run([SEARCH, SEARCH_AGAIN, FINISH])
    assert registry.invocations.count("search_jobs") == 2
    assert "open_job" not in registry.invocations
    assert state.session.status == "DONE"


def test_search_then_open():
    state, registry, _ = _run([SEARCH, OPEN, FINISH])
    assert "search_jobs" in registry.invocations
    assert "open_job" in registry.invocations
    assert "interpret_job_actions" not in registry.invocations
    assert state.jobs["mock:mock-direct"].stage == "opened"
    assert state.session.status == "DONE"


def test_open_does_not_auto_interpret():
    _, registry, _ = _run([SEARCH, OPEN, FINISH])
    assert "open_job" in registry.invocations
    assert "interpret_job_actions" not in registry.invocations
    assert "analyze_job" not in registry.invocations
    assert "match_job" not in registry.invocations


def test_open_can_skip_interpret_to_analyze():
    """Reasoner may analyze after open without interpret. Not a required pipeline."""
    analyzed, analyze_registry, _ = _run([SEARCH, OPEN, _analyze_opened, FINISH])
    assert "analyze_job" in analyze_registry.invocations
    assert "interpret_job_actions" not in analyze_registry.invocations
    assert analyzed.jobs["mock:mock-direct"].job_profile is not None


def test_match_without_world_job_profile_does_not_invoke_tool():
    """Match needs JobProfile in World; Binding must not invent one from Action bodies."""
    matched, match_registry, _ = _run([SEARCH, OPEN, _match_opened, FINISH])
    assert "match_job" not in match_registry.invocations
    assert "analyze_job" not in match_registry.invocations
    assert matched.jobs["mock:mock-direct"].match_result is None
    assert matched.session.status == "DONE"


def test_reasoner_ask_user_waits():
    state, registry, _ = _run([SEARCH, ASK])
    assert state.session.status == "WAITING_USER"
    assert "open_job" not in registry.invocations
    assert (state.output or {}).get("message") == "要不要继续看这个方向？"


def test_reasoner_finish_ends_loop():
    registry = build_registry(data_source="mock")
    llm = ScriptedReasonerLLM([FINISH])
    state = run_agent(
        goal=GOAL_TEXT,
        resume=None,
        llm_provider=llm,
        registry=registry,
        data_source="mock",
    )
    assert state.session.status == "DONE"
    assert "understand_user_input" not in registry.invocations
    assert "search_jobs" not in registry.invocations
    assert "open_job" not in registry.invocations


def test_list_capacity_full_still_allows_search_with_progress_false():
    """World list capacity limits ingest, not search execution. Reasoner sees progress=false."""
    state, registry, llm = _run(
        [SEARCH, SEARCH_AGAIN, FINISH],
        constraints={"max_search_results": 1},
    )
    assert registry.invocations.count("search_jobs") == 2
    assert "open_job" not in registry.invocations
    assert state.session.status == "DONE"
    assert int(state.search.stats.get("listed_count") or state.search.stats.get("listed") or 0) == 1
    # Last search could not ingest more unique jobs.
    assert state.search.stats.get("last_search_progress") is False
    payloads = _reasoner_users(llm)
    progress_obs = [
        item
        for item in payloads
        if (item.get("observation") or {}).get("search_executed") is True
        and (item.get("observation") or {}).get("progress") is False
    ]
    assert progress_obs
    # No Program "quota exceeded: search" rejection for list capacity.
    rejected = [item for item in payloads if item.get("observation", {}).get("kind") == "action_rejected"]
    assert not any("quota exceeded: search" in str((item.get("observation") or {}).get("error") or "") for item in rejected)


def test_same_search_observation_four_reasoner_paths():
    base, _ = _searched_state()
    observation = copy.deepcopy(base.last_raw_observation)
    memory_goal = copy.deepcopy(base.goal)
    jobs = copy.deepcopy({key: record.listed for key, record in base.jobs.items()})
    quota = copy.deepcopy(base.search.stats)
    clones = [copy.deepcopy(base) for _ in range(4)]
    for clone in clones:
        assert clone.last_raw_observation == observation
        assert clone.goal == memory_goal
        assert {key: record.listed for key, record in clone.jobs.items()} == jobs
        assert clone.search.stats == quota
        assert clone.understanding_status == "ok"
        assert clone.session.status == "RUNNING"

    paths = [
        ([OPEN, FINISH], "open"),
        ([SEARCH_AGAIN, FINISH], "search_again"),
        ([ASK], "ask_user"),
        ([FINISH], "finish"),
    ]
    results = []
    for clone, (actions, label) in zip(clones, paths):
        registry = build_registry(data_source="mock")
        llm = ScriptedReasonerLLM(actions)
        state = run_loop(clone, registry, llm_provider=llm)
        results.append((label, state, registry))

    open_state, open_registry = results[0][1], results[0][2]
    search_state, search_registry = results[1][1], results[1][2]
    ask_state, ask_registry = results[2][1], results[2][2]
    finish_state, finish_registry = results[3][1], results[3][2]

    assert "understand_user_input" not in open_registry.invocations
    assert "open_job" in open_registry.invocations
    assert open_state.jobs["mock:mock-direct"].opened is not None

    assert search_registry.invocations == ["search_jobs"]
    assert "open_job" not in search_registry.invocations

    assert ask_state.session.status == "WAITING_USER"
    assert ask_registry.invocations == []
    assert "open_job" not in ask_registry.invocations

    assert finish_state.session.status == "DONE"
    assert finish_registry.invocations == []
    assert finish_state.last_raw_observation == observation


def test_reasoner_can_skip_understanding_when_context_is_sufficient():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = goal_response()
    state.goal["analysis_status"] = "ok"
    state.last_raw_observation = {"kind": "user_turn_understood", "has_user_goal": True}
    registry = build_registry(data_source="mock")
    llm = ScriptedReasonerLLM([SEARCH, FINISH])
    state = run_loop(state, registry, llm_provider=llm)
    assert registry.invocations[0] == "search_jobs"
    assert "understand_user_input" not in registry.invocations
    payloads = _reasoner_users(llm)
    assert payloads
    assert payloads[0]["memory"]["understanding_status"] == "ok"


def test_reasoner_can_choose_understanding_when_needed():
    registry = build_registry(data_source="mock")
    llm = ScriptedReasonerLLM([_understand, SEARCH, FINISH])
    state = run_agent(
        goal=GOAL_TEXT,
        resume=None,
        llm_provider=llm,
        registry=registry,
        data_source="mock",
    )
    assert registry.invocations[0] == "understand_user_input"
    assert registry.invocations.count("understand_user_input") == 1
    assert "search_jobs" in registry.invocations
    payloads = _reasoner_users(llm)
    assert payloads[0]["memory"]["understanding_status"] == "not_understood"
    assert payloads[0]["observation"]["kind"] == "user_turn"
    assert payloads[1]["observation"]["kind"] == "user_turn_understood"
    assert state.session.status == "DONE"


def test_understanding_does_not_create_task_or_finish():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    result = understanding_response(
        task_kind="new_job_search",
        user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]},
    )
    result["analysis_status"] = "ok"
    state = reduce(state, tool_action("understand_user_input", {"message": GOAL_TEXT}), result)
    assert state.session.status == "RUNNING"
    assert state.job_search_task_id is None
    assert state.last_raw_observation["kind"] == "user_turn_understood"
    assert state.last_raw_observation["task_kind"] == "new_job_search"
    assert state.last_raw_observation["has_user_goal"] is True
    assert state.last_raw_observation.get("task_intent") is None
    assert state.task_intent is None


def test_same_understanding_observation_ask_search_finish():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    result = understanding_response(
        task_kind="new_job_search",
        user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]},
    )
    result["analysis_status"] = "ok"
    state = reduce(state, tool_action("understand_user_input", {"message": GOAL_TEXT}), result)
    observation = copy.deepcopy(state.last_raw_observation)
    assert observation["kind"] == "user_turn_understood"

    clones = [copy.deepcopy(state) for _ in range(3)]
    for clone in clones:
        assert clone.last_raw_observation == observation
        assert clone.session.status == "RUNNING"
        assert clone.job_search_task_id is None

    ask_state = run_loop(clones[0], build_registry(data_source="mock"), llm_provider=ScriptedReasonerLLM([ASK]))
    search_registry = build_registry(data_source="mock")
    search_state = run_loop(clones[1], search_registry, llm_provider=ScriptedReasonerLLM([SEARCH, FINISH]))
    # Premature finish is no longer a Program reject; Reasoner may finish without search.
    finish_state = run_loop(clones[2], build_registry(data_source="mock"), llm_provider=ScriptedReasonerLLM([FINISH]))

    assert ask_state.session.status == "WAITING_USER"
    assert "search_jobs" in search_registry.invocations
    assert search_state.session.status == "DONE"
    # Finish without search is Reasoner-owned — Program no longer rejects it.
    assert finish_state.session.status == "DONE"


def test_clarify_goes_to_reasoner_not_program_done():
    from task.schema import new_job_search_task, set_task_status
    from task.store import JobSearchTaskStore

    store = JobSearchTaskStore()
    first = new_job_search_task(conversation_id="conv-clarify", user_goal={"target_roles": ["产品经理"]})
    second = new_job_search_task(conversation_id="conv-clarify", user_goal={"target_roles": ["设计师"]})
    set_task_status(first, "WAITING_USER")
    set_task_status(second, "WAITING_USER")
    first.current_job_context_id = "job-a"
    second.current_job_context_id = "job-b"
    store.save(first)
    store.save(second)

    class _ClarifyLLM(ScriptedReasonerLLM):
        def complete_json(self, *, system: str, user: str) -> dict:
            if "UserInputUnderstanding" in system:
                self.calls.append({"system": system, "user": user})
                return understanding_response(task_kind="continue_task")
            return super().complete_json(system=system, user=user)

    registry = build_registry(data_source="mock")
    llm = _ClarifyLLM([_understand, _ask_from_observation])
    state = run_agent(
        goal="继续",
        resume=None,
        llm_provider=llm,
        registry=registry,
        data_source="mock",
        task_store=store,
        conversation_id="conv-clarify",
        candidate_profile=None,
    )
    payloads = _reasoner_users(llm)
    understood = next(item["observation"] for item in payloads if item["observation"].get("kind") == "user_turn_understood")
    assert understood["task_kind"] == "continue_task"
    assert len(understood.get("active_tasks") or []) == 2
    assert all(item["observation"].get("kind") != "task_clarification" for item in payloads)
    assert state.session.status == "WAITING_USER"
    assert (state.output or {}).get("error_code") == "clarification_needed"
    assert "search_jobs" not in registry.invocations


def test_unresolved_reference_goes_to_reasoner_not_program_done():
    class _UnresolvedLLM(ScriptedReasonerLLM):
        def complete_json(self, *, system: str, user: str) -> dict:
            if "UserInputUnderstanding" in system:
                self.calls.append({"system": system, "user": user})
                return understanding_response(
                    task_kind="follow_up_job",
                    conversation_reference=conversation_reference(
                        reference_text="刚才那个职位",
                        resolution_hint={"recency": "last"},
                    ),
                )
            return super().complete_json(system=system, user=user)

    jobs = [
        {
            "job_key": "mock:job-a",
            "job_id": "job-a",
            "job_title": "A",
            "company_name": "甲",
            "job_profile": {"job_summary": "a"},
            "match_result": {"recommendation": "yes"},
        },
        {
            "job_key": "mock:job-b",
            "job_id": "job-b",
            "job_title": "B",
            "company_name": "乙",
            "job_profile": {"job_summary": "b"},
            "match_result": {"recommendation": "weak"},
        },
        {
            "job_key": "mock:job-c",
            "job_id": "job-c",
            "job_title": "C",
            "company_name": "丙",
            "job_profile": {"job_summary": "c"},
            "match_result": {"recommendation": "yes"},
        },
    ]
    registry = build_registry(data_source="mock")
    llm = _UnresolvedLLM([_understand, _ask_from_observation])
    state = run_agent(
        goal="刚才那个职位怎么样？",
        resume=None,
        llm_provider=llm,
        registry=registry,
        data_source="mock",
        session_context={"job_contexts": jobs},
    )
    payloads = _reasoner_users(llm)
    assert any(item["observation"].get("kind") == "reference_unresolved" for item in payloads)
    assert state.session.status == "WAITING_USER"
    assert (state.output or {}).get("error_code") == "reference_unresolved"
    assert "search_jobs" not in registry.invocations
    assert state.follow_up_of is None


def test_reasoner_sees_registry_contracts_not_hardcoded_list():
    registry = build_registry(data_source="mock")
    llm = ScriptedReasonerLLM([FINISH])
    run_agent(goal=GOAL_TEXT, resume=None, llm_provider=llm, registry=registry, data_source="mock")
    payloads = _reasoner_users(llm)
    assert payloads
    names = {item["name"] for item in payloads[0]["available_tools"]}
    assert "reason_next_action" not in names
    assert "decide_next_action" not in names
    assert "search_jobs" in names
    assert "open_job" in names
    assert "inspect_job" in names
    assert "inspect_application_state" in names
    assert "execute_action" in names
    assert "search_boss_jobs" not in names
    assert "mock_search_jobs" not in names
    assert "execute_job_action" not in names
    assert "understand_user_input" in names
    search = next(item for item in payloads[0]["available_tools"] if item["name"] == "search_jobs")
    assert "parameters" in search
    assert search["allowed_in_loop"] is True


def test_search_payload_has_no_program_current_job():
    _, _, llm = _run([SEARCH, FINISH])
    payloads = _reasoner_users(llm)
    after_search = [
        item for item in payloads if item.get("observation", {}).get("tool_name") == "search_jobs"
    ]
    assert after_search
    payload = after_search[0]
    assert "current_job" not in payload
    assert "current_job" not in (payload.get("constraints") or {})
    assert "jobs" not in (payload.get("constraints") or {})
    assert isinstance(payload.get("jobs"), list)
    assert any(item.get("stage") == "listed" for item in payload["jobs"])


def test_reasoner_can_open_non_first_listed_job():
    def _open_second(ctx: dict) -> dict:
        jobs = [item for item in (ctx.get("jobs") or []) if isinstance(item, dict)]
        listed = [item for item in jobs if item.get("stage") == "listed"]
        listed.sort(key=lambda item: item.get("listed_order") if isinstance(item.get("listed_order"), int) else 10**9)
        assert len(listed) >= 2
        target = listed[1]
        arguments = _identity_args(target)
        return {
            "action_type": "tool",
            "tool_name": "open_job",
            "arguments": arguments,
            "reason": "open second listed job",
        }

    state, registry, _ = _run([SEARCH, _open_second, FINISH])
    assert "open_job" in registry.invocations
    opened = [record for record in state.jobs.values() if record.opened is not None]
    assert len(opened) == 1
    listed_ordered = sorted(
        [record for record in state.jobs.values() if record.stage in {"listed", "opened"}],
        key=lambda record: record.listed_order,
    )
    assert len(listed_ordered) >= 2
    assert opened[0].job_key == listed_ordered[1].job_key
    assert opened[0].job_key != listed_ordered[0].job_key


def test_interpret_without_job_identity_does_not_bind_a_job():
    state, registry = _searched_state()
    opened = registry.invoke("open_job", {"job_id": "mock-direct"})
    state = reduce(
        state,
        tool_action("open_job", {"job_id": "mock-direct"}),
        opened,
    )
    record = state.jobs["mock:mock-direct"]
    assert record.stage == "opened"
    assert record.interpret_result is None

    state = reduce(
        state,
        tool_action("interpret_job_actions", {}),
        {"analysis_status": "ok", "inferred_context": {"application_evidence": "none"}},
    )
    assert record.interpret_result is None
    assert state.session.status == "RUNNING"
    assert state.last_raw_observation["tool_name"] == "interpret_job_actions"
    assert state.last_raw_observation["result"]["error_code"] == "missing_job_identity"
