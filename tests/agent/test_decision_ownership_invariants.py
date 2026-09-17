"""Program must not take the 4 remaining Reasoner decisions.

bind_task create, follow-up JobProfile, task_kind, and search keyword
are Reasoner / Understanding owned. Program records or rejects.
"""

from __future__ import annotations

import ast
import copy
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import Action, tool_action
from agent.loop import LoopContext, reduce, run_loop
from agent.state import new_agent_state
from task.schema import new_job_search_task
from task.store import JobSearchTaskStore
from tests.mock_llm import MockLLMProvider, ScriptedReasonerLLM, understanding_response
from tools.registry import build_registry
from understanding.understand_user_input import understand_user_input

GOAL_TEXT = "帮我找金融科技方向高级产品经理"
ROLE = "高级产品经理"

SEARCH = {
    "action_type": "tool",
    "tool_name": "search_jobs",
    "arguments": {"keyword": "金融科技产品经理", "city": "深圳", "limit": 30},
    "reason": "search",
}
ASK = {"action_type": "ask_user", "intent": "clarification", "question": "你希望下一步怎么做？", "reason": "ask"}
FINISH = {"action_type": "finish", "reason": "done"}
BIND_CREATE = {
    "action_type": "tool",
    "tool_name": "bind_task",
    "arguments": {"create": True},
    "reason": "create after clarification",
}
ANALYZE = {
    "action_type": "tool",
    "tool_name": "analyze_job",
    "arguments": {"job_key": "mock:j-follow"},
    "reason": "analyze missing profile",
}


def _running_state(**kwargs):
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock", **kwargs)
    state.status = "RUNNING"
    state.goal = {"target_roles": [ROLE], "cities": ["深圳"]}
    return state


def _reasoner_owns_next(state, scripts: list[list[dict]]) -> list[tuple[str, list[str]]]:
    outcomes = []
    for script in scripts:
        clone = copy.deepcopy(state)
        registry = build_registry(data_source="mock")
        result = run_loop(clone, registry, llm_provider=ScriptedReasonerLLM(script))
        outcomes.append((result.session.status, list(registry.invocations)))
    return outcomes


def test_bind_task_without_create_does_not_create_and_returns_to_reasoner():
    store = JobSearchTaskStore()
    ctx = LoopContext(task_store=store, conversation_id="conv-bind")
    state = _running_state(conversation_id="conv-bind")
    state = reduce(state, tool_action("bind_task", {}), None, ctx)

    assert state.session.status == "RUNNING"
    assert state.session.status != "FAILED"
    assert state.job_search_task_id is None
    assert store.list_all() == []
    assert state.last_raw_observation["kind"] == "task_clarification"
    assert state.last_raw_observation["reason"] == "create_decision_missing"

    null_state = reduce(
        _running_state(conversation_id="conv-bind-null"),
        tool_action("bind_task", {"create": None}),
        None,
        LoopContext(task_store=JobSearchTaskStore(), conversation_id="conv-bind-null"),
    )
    assert null_state.job_search_task_id is None
    assert null_state.last_raw_observation["reason"] == "create_decision_missing"
    assert null_state.session.status == "RUNNING"

    outcomes = _reasoner_owns_next(state, [[ASK], [FINISH], [BIND_CREATE, FINISH]])
    assert outcomes[0][0] == "WAITING_USER"
    assert outcomes[1][0] == "DONE"
    assert outcomes[2][0] == "DONE"
    assert "search_jobs" not in outcomes[0][1]
    assert "search_jobs" not in outcomes[1][1]


def test_bind_task_create_true_creates_task():
    store = JobSearchTaskStore()
    ctx = LoopContext(task_store=store, conversation_id="conv-create")
    state = _running_state(conversation_id="conv-create")
    state = reduce(
        state,
        tool_action("bind_task", {"create": True}),
        None,
        ctx,
    )
    assert state.session.status == "RUNNING"
    assert state.job_search_task_id
    assert len(store.list_all()) == 1
    assert store.list_all()[0].task_id == state.job_search_task_id
    assert state.last_raw_observation["kind"] == "task_bound"
    assert state.last_raw_observation["created"] is True


def test_bind_task_existing_task_id_does_not_need_create():
    store = JobSearchTaskStore()
    task = new_job_search_task(
        conversation_id="conv-existing",
        user_goal={"target_roles": [ROLE]},
    )
    store.save(task)
    state = _running_state(conversation_id="conv-existing")
    state = reduce(
        state,
        tool_action("bind_task", {"task_id": task.task_id}),
        None,
        LoopContext(task_store=store, conversation_id="conv-existing"),
    )
    assert state.session.status == "RUNNING"
    assert state.job_search_task_id == task.task_id
    assert state.last_raw_observation["kind"] == "task_bound"
    assert state.last_raw_observation["created"] is False
    assert len(store.list_all()) == 1


def test_follow_up_missing_job_profile_is_running_observation():
    job_key = "mock:j-follow"
    state = _running_state()
    state.reference_resolution = {
        "status": "resolved",
        "job_key": job_key,
        "job_context": {
            "job_key": job_key,
            "platform": "mock",
            "job_id": "j-follow",
            "title": ROLE,
            "company": "支付科技",
            "job_listing": {
                "platform": "mock",
                "job_id": "j-follow",
                "job_url": "https://mock.local/j-follow",
                "job_title": ROLE,
                "company_name": "支付科技",
            },
            "job_profile": None,
        },
    }
    state = reduce(
        state,
        tool_action("hydrate_job_reference", {"job_key": job_key}),
        None,
    )
    assert state.session.status == "RUNNING"
    assert state.session.status != "FAILED"
    assert state.last_raw_observation["kind"] == "follow_up_resolved"
    assert state.last_raw_observation["job_key"] == job_key
    assert state.last_raw_observation["has_job_profile"] is False
    assert state.last_raw_observation["missing_context"] == ["job_profile"]
    assert state.follow_up_of == job_key
    assert not any(item.get("kind") == "follow_up" for item in state.errors)

    outcomes = _reasoner_owns_next(state, [[ASK], [FINISH], [ANALYZE, FINISH]])
    assert outcomes[0][0] == "WAITING_USER"
    assert outcomes[1][0] == "DONE"
    assert "analyze_job" not in outcomes[0][1]
    assert "analyze_job" not in outcomes[1][1]
    assert "analyze_job" in outcomes[2][1]
    assert "search_jobs" not in outcomes[0][1]
    assert "search_jobs" not in outcomes[1][1]
    assert "search_jobs" not in outcomes[2][1]


def test_task_kind_null_is_not_inferred_from_goal():
    payload = understanding_response(
        task_kind=None,
        user_goal={"target_roles": [ROLE], "cities": ["深圳"]},
    )
    result = understand_user_input(
        GOAL_TEXT,
        llm_provider=MockLLMProvider(payload),
    )
    assert result["analysis_status"] == "ok"
    assert result["task_kind"] is None
    assert result["user_goal"]["target_roles"] == [ROLE]

    state = _running_state()
    understood = dict(result)
    state = reduce(
        state,
        tool_action("understand_user_input", {"message": GOAL_TEXT}),
        understood,
    )
    assert state.understanding["task_kind"] is None
    assert state.last_raw_observation["task_kind"] is None
    assert state.last_raw_observation["missing"] == ["task_kind"]
    assert state.last_raw_observation["uncertainty"] is True
    assert state.session.status == "RUNNING"

    outcomes = _reasoner_owns_next(state, [[SEARCH, FINISH], [ASK], [FINISH]])
    assert "search_jobs" in outcomes[0][1]
    assert outcomes[1][0] == "WAITING_USER"
    assert outcomes[2][0] == "DONE"
    assert outcomes[2][1] == []


def test_plan_search_does_not_invent_keyword_from_goal():
    state = _running_state()
    state.understanding_status = "ok"
    state = reduce(state, tool_action("plan_search", {}), {"analysis_status": "ok"})
    assert state.session.status == "RUNNING"
    assert state.session.status != "FAILED"
    assert state.last_raw_observation["kind"] == "search_plan_incomplete"
    assert state.last_raw_observation["missing"] == ["keyword"]
    assert [plan.keyword for plan in state.search.plans] == []
    assert not any((plan.keyword or "") == ROLE for plan in state.search.plans)
    assert state.search.stop_reason != "no_next_plan"

    tool_state = reduce(
        _running_state(),
        tool_action("plan_search", {}),
        {"analysis_status": "ok", "keyword": None, "city": "深圳"},
    )
    assert tool_state.last_raw_observation["kind"] == "search_plan_incomplete"
    assert [plan.keyword for plan in tool_state.search.plans] == []

    outcomes = _reasoner_owns_next(state, [[SEARCH, FINISH], [ASK], [FINISH]])
    assert "search_jobs" in outcomes[0][1]
    assert outcomes[1][0] == "WAITING_USER"
    assert outcomes[2][0] == "DONE"


def test_program_cannot_choose_next_tool_in_these_scenes():
    """Field results are not enough: Program must leave the next Action to Reasoner."""
    scenes = []

    bind_state = reduce(
        _running_state(conversation_id="conv-own-bind"),
        tool_action("bind_task", {}),
        None,
        LoopContext(task_store=JobSearchTaskStore(), conversation_id="conv-own-bind"),
    )
    scenes.append(bind_state)

    follow_state = _running_state()
    follow_state.reference_resolution = {
        "status": "resolved",
        "job_key": "mock:j-follow",
        "job_context": {
            "job_key": "mock:j-follow",
            "job_id": "j-follow",
            "title": ROLE,
            "job_profile": None,
            "job_listing": {
                "platform": "mock",
                "job_id": "j-follow",
                "job_title": ROLE,
            },
        },
    }
    scenes.append(
        reduce(
            follow_state,
            tool_action("hydrate_job_reference", {"job_key": "mock:j-follow"}),
            None,
        )
    )

    understood = understanding_response(
        task_kind=None,
        user_goal={"target_roles": [ROLE], "cities": ["深圳"]},
    )
    understood["analysis_status"] = "ok"
    scenes.append(
        reduce(
            _running_state(),
            tool_action("understand_user_input", {"message": GOAL_TEXT}),
            understood,
        )
    )

    scenes.append(reduce(_running_state(), tool_action("plan_search", {}), {"analysis_status": "ok"}))

    for state in scenes:
        assert state.session.status == "RUNNING"
        ask, finish = _reasoner_owns_next(state, [[ASK], [FINISH]])
        assert ask[0] == "WAITING_USER"
        assert finish[0] == "DONE"
        assert ask[1] != finish[1] or ask[0] != finish[0]


def test_static_program_no_longer_owns_the_four_decisions():
    loop = ast.parse((ROOT_DIR / "agent" / "loop.py").read_text(encoding="utf-8"))
    understand = ast.parse((ROOT_DIR / "understanding" / "understand_user_input.py").read_text(encoding="utf-8"))
    loop_names = {node.id for node in ast.walk(loop) if isinstance(node, ast.Name)}
    assert "next_goal_plan_fields" not in loop_names

    def _fn(tree: ast.AST, name: str) -> ast.FunctionDef:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"missing {name}")

    follow = _fn(loop, "_reduce_follow_up")
    follow_failed = {
        node.value
        for node in ast.walk(follow)
        if isinstance(node, ast.Constant) and node.value == "FAILED"
    }
    assert not follow_failed

    finalize = _fn(understand, "_finalize_task_fields")
    inferred = {
        node.value
        for node in ast.walk(finalize)
        if isinstance(node, ast.Constant) and node.value in {"new_job_search", "follow_up_job"}
    }
    assert not inferred

    bind = _fn(loop, "_reduce_bind_task")
    source = ast.get_source_segment((ROOT_DIR / "agent" / "loop.py").read_text(encoding="utf-8"), bind)
    assert source is not None
    assert "if task is None and not arguments.get(\"create\")" not in source
    assert "create_decision_missing" in source
