"""Action contract: only tool / ask_user / finish. No compatibility mapping."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import decide, tool_action
from agent.loop import LoopContext, reduce, run_loop
from agent.state import JobRecord, new_agent_state
from agent.validate_action import ALLOWED_ACTION_TYPES as VALIDATE_TYPES
from task.schema import TaskJobProgress, new_job_search_task, set_task_status
from task.store import JobSearchTaskStore
from tests.mock_llm import ScriptedReasonerLLM
from tools.reason_next_action import ALLOWED_ACTION_TYPES, reason_next_action
from tools.registry import available_tool_contracts, build_registry

GOAL = "帮我找深圳的产品经理工作"
ROLE = "高级产品经理"
SEARCH = {
    "action_type": "tool",
    "tool_name": "search_jobs",
    "arguments": {"keyword": "产品经理", "city": "深圳", "limit": 30},
    "reason": "search",
}
ASK = {"action_type": "ask_user", "intent": "clarification", "question": "下一步怎么做？", "reason": "ask"}
FINISH = {"action_type": "finish", "reason": "done"}


def _state(**kwargs):
    state = new_agent_state(goal_input=GOAL, resume=None, data_source="mock", **kwargs)
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = {"target_roles": [ROLE], "cities": ["深圳"]}
    return state


def _illegal(action_type: str, **extra):
    class _LLM:
        def complete_json(self, *, system: str, user: str) -> dict:
            payload = {"action_type": action_type, "reason": "illegal"}
            payload.update(extra)
            return payload

    return _LLM()


def test_reasoner_action_types_are_only_three():
    assert ALLOWED_ACTION_TYPES == {"tool", "ask_user", "finish"}
    assert VALIDATE_TYPES == {"tool", "ask_user", "finish"}


def _raw_action_type(raw) -> str | None:
    if not isinstance(raw, dict):
        return None
    if "first" in raw and isinstance(raw["first"], dict):
        return raw["first"].get("action_type")
    return raw.get("action_type")


def test_search_as_action_type_is_contract_failure_not_mapped():
    registry = build_registry(data_source="mock")
    state = _state()
    action = decide(state, registry=registry, llm_provider=_illegal("search"))
    assert action.action_type == "fail"
    assert _raw_action_type(action.raw_reasoner_output) == "search"
    assert action.tool_name is None
    state = reduce(state, action, {"ok": False}, LoopContext())
    assert state.session.status == "FAILED"
    assert registry.invocations == []
    assert any(_raw_action_type(item.get("raw_reasoner_output")) == "search" for item in state.errors)


def test_legacy_task_verbs_as_action_type_are_contract_failures():
    registry = build_registry(data_source="mock")
    for verb in ("bind_task", "skip_job", "stop_task", "apply_job", "resolve_follow_up", "continue_task"):
        state = _state()
        action = decide(state, registry=registry, llm_provider=_illegal(verb))
        assert action.action_type == "fail", verb
        assert _raw_action_type(action.raw_reasoner_output) == verb
        state = reduce(state, action, {"ok": False})
        assert state.session.status == "FAILED", verb


def test_legal_tool_form_for_task_verbs():
    registry = build_registry(data_source="mock")
    names = {item["name"] for item in available_tool_contracts(registry)}
    for name in ("bind_task", "skip_job", "stop_task", "hydrate_job_reference"):
        assert registry.has(name)
        assert name in names
    assert "apply_job" not in names
    assert "resolve_follow_up" not in names
    assert "continue_task" not in names
    assert not registry.has("apply_job")
    assert not registry.has("continue_task")
    assert not registry.has("resolve_follow_up")


def test_bind_task_as_tool_without_create_does_not_create():
    store = JobSearchTaskStore()
    state = _state(conversation_id="conv-bind")
    state = reduce(
        state,
        tool_action("bind_task", {}),
        {"ok": True},
        LoopContext(task_store=store, conversation_id="conv-bind"),
    )
    assert state.session.status == "RUNNING"
    assert state.job_search_task_id is None
    assert store.list_all() == []
    assert state.last_raw_observation["reason"] == "create_decision_missing"


def test_skip_job_does_not_auto_search_open_or_finish():
    store = JobSearchTaskStore()
    task = new_job_search_task(conversation_id="conv-skip", user_goal={"target_roles": [ROLE]})
    set_task_status(task, "WAITING_USER")
    task.current_job_context_id = "mock:j1"
    task.explored_jobs = [
        TaskJobProgress(job_context_id="mock:j1", job_key="mock:j1", status="WAITING_USER")
    ]
    store.save(task)
    state = _state(conversation_id="conv-skip")
    state.job_search_task_id = task.task_id
    state.jobs["mock:j1"] = JobRecord(
        job_key="mock:j1",
        stage="matched",
        listed_order=0,
        listed={"platform": "mock", "job_id": "j1", "job_title": ROLE},
    )
    state = reduce(
        state,
        tool_action("skip_job", {"task_id": task.task_id}),
        {"ok": True},
        LoopContext(task_store=store, conversation_id="conv-skip"),
    )
    assert state.session.status == "RUNNING"
    assert state.last_raw_observation["kind"] == "job_skipped"
    registry = build_registry(data_source="mock")
    ask = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([ASK]))
    assert ask.session.status == "WAITING_USER"
    assert "search_jobs" not in registry.invocations
    assert "open_job" not in registry.invocations


def test_stop_task_does_not_auto_finish():
    store = JobSearchTaskStore()
    task = new_job_search_task(conversation_id="conv-stop", user_goal={"target_roles": [ROLE]})
    store.save(task)
    state = _state(conversation_id="conv-stop")
    state.job_search_task_id = task.task_id
    state = reduce(
        state,
        tool_action("stop_task", {"task_id": task.task_id}),
        {"ok": True},
        LoopContext(task_store=store, conversation_id="conv-stop"),
    )
    assert state.session.status == "RUNNING"
    assert state.last_raw_observation["kind"] == "task_stopped"
    assert store.load(task.task_id).task_status == "STOPPED"
    ask = run_loop(state, build_registry(data_source="mock"), llm_provider=ScriptedReasonerLLM([ASK]))
    assert ask.session.status == "WAITING_USER"
    finish = run_loop(
        reduce(
            _state(conversation_id="conv-stop-2"),
            tool_action("stop_task", {"task_id": task.task_id}),
            {"ok": True},
            LoopContext(task_store=store, conversation_id="conv-stop-2"),
        ),
        build_registry(data_source="mock"),
        llm_provider=ScriptedReasonerLLM([FINISH]),
    )
    assert finish.session.status == "DONE"


def test_hydrate_job_reference_does_not_auto_analyze():
    state = _state()
    state.reference_resolution = {
        "status": "resolved",
        "job_key": "mock:j-follow",
        "job_context": {
            "job_key": "mock:j-follow",
            "job_id": "j-follow",
            "title": ROLE,
            "job_profile": None,
            "job_listing": {"platform": "mock", "job_id": "j-follow", "job_title": ROLE},
        },
    }
    state = reduce(state, tool_action("hydrate_job_reference", {"job_key": "mock:j-follow"}), {"ok": True})
    assert state.session.status == "RUNNING"
    assert state.last_raw_observation["missing_context"] == ["job_profile"]
    registry = build_registry(data_source="mock")
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([FINISH]))
    assert state.session.status == "DONE"
    assert "analyze_job" not in registry.invocations
    assert "match_job" not in registry.invocations
    assert "open_job" not in registry.invocations


def test_search_jobs_tool_result_does_not_choose_next_tool():
    state = _state()
    state = reduce(
        state,
        tool_action("search_jobs", {"keyword": "产品经理", "city": "深圳"}),
        {"jobs": []},
    )
    assert state.session.status == "RUNNING"
    registry = build_registry(data_source="mock")
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([ASK]))
    assert state.session.status == "WAITING_USER"
    assert registry.invocations == []


def test_legal_search_jobs_walks_reasoner_to_observation():
    state = new_agent_state(goal_input=GOAL, resume=None, data_source="mock")
    registry = build_registry(data_source="mock")
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([SEARCH, FINISH]))
    assert "search_jobs" in registry.invocations
    assert state.session.status == "DONE"
    assert state.last_raw_observation["kind"] in {"user_turn", "tool_result"} or state.jobs


def test_reasoner_parser_keeps_raw_on_failure():
    result = reason_next_action(
        {"available_tools": [{"name": "search_jobs"}]},
        llm_provider=_illegal("search", tool_name="search_jobs"),
    )
    assert result["analysis_status"] == "analysis_failed"
    raw = result["raw_reasoner_output"]
    # Contract retry stores both attempts; never remaps the illegal action_type.
    if isinstance(raw, dict) and "first" in raw:
        assert raw["first"]["action_type"] == "search"
        assert raw["retry"]["action_type"] == "search"
    else:
        assert raw["action_type"] == "search"
        assert raw["tool_name"] == "search_jobs"
    assert result["llm_calls"] == 2


def test_reasoner_contract_retry_recovers_without_mapping():
    class _RetryLLM:
        def __init__(self) -> None:
            self.n = 0

        def complete_json(self, *, system: str, user: str) -> dict:
            self.n += 1
            if self.n == 1:
                return {
                    "action_type": "search_jobs",
                    "tool_name": "search_jobs",
                    "arguments": {"keyword": "产品经理", "city": "深圳"},
                    "reason": "illegal first attempt",
                }
            return {
                "action_type": "tool",
                "tool_name": "search_jobs",
                "arguments": {"keyword": "产品经理", "city": "深圳"},
                "reason": "rewritten",
            }

    result = reason_next_action(
        {"available_tools": [{"name": "search_jobs"}]},
        llm_provider=_RetryLLM(),
    )
    assert result["analysis_status"] == "ok"
    assert result["action_type"] == "tool"
    assert result["tool_name"] == "search_jobs"
    assert result["llm_calls"] == 2
    assert result["raw_reasoner_output"]["first"]["action_type"] == "search_jobs"
    assert result["raw_reasoner_output"]["retry"]["action_type"] == "tool"


def test_prompt_and_decide_have_no_old_action_types():
    prompt = (ROOT_DIR / "tools" / "reason_next_action.py").read_text(encoding="utf-8")
    decide_src = (ROOT_DIR / "agent" / "decide.py").read_text(encoding="utf-8")
    assert 'action_type="search"' not in prompt
    assert "action_type=search" not in prompt
    assert "choose search" not in prompt
    assert "PROGRAM_ACTIONS" not in decide_src
    assert "kind=\"task_control\"" not in decide_src
    assert 'action_type="tool"' in prompt
    assert "即使要搜索，也必须 action_type=\"tool\"" in prompt
    assert '{"action_type":"search_jobs"' in prompt  # negative example only
    assert "CONTRACT_RETRY_USER_PREFIX" in prompt

def test_action_name_is_gone():
    decide_src = ast.parse((ROOT_DIR / "agent" / "decide.py").read_text(encoding="utf-8"))
    loop_src = ast.parse((ROOT_DIR / "agent" / "loop.py").read_text(encoding="utf-8"))
    for tree in (decide_src, loop_src):
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "name":
                if isinstance(node.value, ast.Name) and node.value.id == "action":
                    raise AssertionError("Action.name must not be used")
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "name":
                if isinstance(getattr(node, "annotation", None), ast.Name):
                    continue
