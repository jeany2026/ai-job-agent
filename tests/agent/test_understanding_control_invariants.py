"""Understanding results are Memory/Observation, not Program decisions."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import Action, tool_action, decide
from agent.loop import reduce, run_loop
from agent.state import new_agent_state
from tests.mock_llm import ScriptedReasonerLLM, goal_response, understanding_response
from tools.registry import build_registry

GOAL_TEXT = "帮我找深圳高级产品经理"
SEARCH = {
    "action_type": "tool",
    "tool_name": "search_jobs",
    "arguments": {"keyword": "高级产品经理", "city": "深圳", "limit": 30},
    "reason": "search",
}
ASK = {"action_type": "ask_user", "intent": "clarification", "question": "还要继续吗？", "reason": "ask"}
FINISH = {"action_type": "finish", "reason": "done"}


def _understood(task_kind: str = "skip_job", *, resume: str | None = "简历：做过支付。"):
    state = new_agent_state(goal_input=GOAL_TEXT, resume=resume, data_source="mock")
    state.status = "RUNNING"
    result = understanding_response(
        task_kind=task_kind,
        user_goal={
            "target_roles": ["高级产品经理"],
            "cities": ["深圳"],
            "exclude_companies": ["黑名单科技"],
        },
    )
    result["analysis_status"] = "ok"
    return reduce(state, tool_action("understand_user_input", {"message": GOAL_TEXT}), result)


def test_invariant_a_understanding_failure_is_not_agent_failure():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    state = reduce(
        state,
        tool_action("understand_user_input", {}),
        {"analysis_status": "llm_error", "error": "LLM response is not valid JSON"},
    )
    assert state.session.status == "RUNNING"
    assert state.session.status != "FAILED"
    assert state.last_raw_observation["kind"] == "semantic_understanding"
    assert state.last_raw_observation["analysis_status"] == "llm_error"
    assert state.last_raw_observation["uncertainty"] is True
    assert state.last_raw_observation["tool_name"] == "understand_user_input"

    registry = build_registry(data_source="mock")
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([SEARCH, FINISH]))
    assert "search_jobs" in registry.invocations
    assert "understand_user_input" not in registry.invocations
    assert state.session.status == "DONE"


def test_invariant_a_reasoner_still_controls_after_understand_failure():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    registry = build_registry(data_source="mock")

    class _UnderstandBroken(ScriptedReasonerLLM):
        def complete_json(self, *, system: str, user: str) -> dict:
            if "用户输入理解器" in system or "UserInputUnderstanding" in system:
                self.calls.append({"system": system, "user": user})
                raise RuntimeError("LLM response is not valid JSON")
            return super().complete_json(system=system, user=user)

    def understand(ctx: dict) -> dict:
        ids = [
            str(item.get("id"))
            for item in (ctx.get("intake") or [])
            if isinstance(item, dict) and item.get("id")
        ]
        return {
            "action_type": "tool",
            "tool_name": "understand_user_input",
            "arguments": {"intake_ids": ids},
            "reason": "understand",
        }

    state = run_loop(
        state,
        registry,
        llm_provider=_UnderstandBroken([understand, ASK]),
    )
    assert state.session.status == "WAITING_USER"
    assert state.session.status != "FAILED"
    assert any(item.get("kind") == "understanding" for item in state.errors)


def test_reasoner_failure_is_not_understanding_failure():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    registry = build_registry(data_source="mock")

    class _ReasonerBroken:
        def complete_json(self, *, system: str, user: str) -> dict:
            raise RuntimeError("reasoner json broken")

    action = decide(state, registry=registry, llm_provider=_ReasonerBroken())
    assert action.action_type == "fail"
    assert action.error_code == "llm_error"
    state = reduce(state, action, {"ok": False})
    assert state.session.status == "FAILED"
    assert state.errors[-1]["kind"] == "reasoner"
    assert all(item.get("kind") != "understanding" for item in state.errors)


def test_invariant_b_llm_exclude_companies_is_not_blacklist():
    state = _understood("new_job_search")
    assert (state.goal or {}).get("exclude_companies") == ["黑名单科技"]
    assert "黑名单科技" not in (state.constraints.blacklist or [])
    assert state.constraints.cities == []
    assert state.constraints.salary_min is None


def test_invariant_b_explicit_user_blacklist_stays_a_constraint():
    state = new_agent_state(
        goal_input=GOAL_TEXT,
        resume=None,
        data_source="mock",
        constraints={"blacklist": ["用户指定公司"]},
    )
    assert "用户指定公司" in state.constraints.blacklist
    result = understanding_response(
        user_goal={"target_roles": ["高级产品经理"], "exclude_companies": ["黑名单科技"]},
    )
    result["analysis_status"] = "ok"
    state = reduce(state, tool_action("understand_user_input", {"message": GOAL_TEXT}), result)
    assert "用户指定公司" in state.constraints.blacklist
    assert "黑名单科技" not in state.constraints.blacklist


def test_invariant_c_task_kind_does_not_select_the_next_tool():
    search_registry = build_registry(data_source="mock")
    search_state = run_loop(
        _understood("skip_job"),
        search_registry,
        llm_provider=ScriptedReasonerLLM([SEARCH, FINISH]),
    )
    # Clarification ask requires no unread upload/compat intake (resume= would block it).
    ask_state = run_loop(
        _understood("skip_job", resume=None),
        build_registry(data_source="mock"),
        llm_provider=ScriptedReasonerLLM([ASK]),
    )
    finish_state = run_loop(
        _understood("skip_job"),
        build_registry(data_source="mock"),
        llm_provider=ScriptedReasonerLLM(
            [
                {
                    "action_type": "finish",
                    "reason": "本轮先结束，不搜索",
                    "error_code": "GOAL_INCOMPLETE",
                }
            ]
        ),
    )

    assert search_state.understanding["task_kind"] == "skip_job"
    assert "skip_job" not in search_registry.invocations
    assert "search_jobs" in search_registry.invocations
    assert search_state.session.status == "DONE"
    assert ask_state.session.status == "WAITING_USER"
    assert finish_state.session.status == "DONE"
    assert finish_state.session.status != "WAITING_USER"


def test_invariant_d_resume_does_not_force_analyze_candidate():
    state = new_agent_state(goal_input=GOAL_TEXT, resume="简历：做过支付。", data_source="mock")
    assert state.candidate.resume_ref
    registry = build_registry(data_source="mock")
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([SEARCH, FINISH]))
    assert "analyze_candidate" not in registry.invocations
    assert "search_jobs" in registry.invocations


def test_invariant_e_profile_does_not_force_profile_use_or_rebuild():
    profile = {
        "analysis_status": "ok",
        "summary": "已有画像",
        "direct_capabilities": [{"name": "支付"}],
    }
    state = new_agent_state(
        goal_input=GOAL_TEXT,
        resume=None,
        data_source="mock",
        candidate_profile=profile,
    )
    assert state.candidate.profile
    registry = build_registry(data_source="mock")
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([SEARCH, FINISH]))
    assert "analyze_candidate" not in registry.invocations
    assert "search_jobs" in registry.invocations


def test_program_does_not_copy_exclude_companies_in_reduce():
    source = (ROOT_DIR / "agent" / "loop.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "_apply_goal_constraints" not in names
    assert "_apply_turn_constraints" not in names
    understand = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_reduce_understanding"
    )
    assigned = {
        target.attr
        for node in ast.walk(understand)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute)
    }
    failed_literals = {
        node.value
        for node in ast.walk(understand)
        if isinstance(node, ast.Constant) and node.value == "FAILED"
    }
    assert not failed_literals
    assert "blacklist" not in assigned
