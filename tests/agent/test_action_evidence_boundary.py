"""Action / Intake / Evidence boundary. Protects Agent invariants, not a fixed pipeline."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.bind_arguments import bind_tool_arguments
from agent.decide import tool_action
from agent.intake import ADMITTED_CONTENT_TYPE
from agent.loop import run_loop
from agent.reasoner_context import build_reasoner_payload
from agent.state import new_agent_state
from tests.mock_llm import ScriptedReasonerLLM, goal_response
from tools.registry import build_registry

GOAL_TEXT = "帮我找深圳高级产品经理"
RESUME_TEXT = "简历：做过支付中台和清结算。"


def _state_with_materials() -> object:
    return new_agent_state(
        goal_input=GOAL_TEXT,
        resume=RESUME_TEXT,
        attachments=[
            {"filename": "project.md", "client_hint": "project_document", "text": "项目说明：对账平台。"}
        ],
        data_source="mock",
    )


def _intake_ids(state) -> list[str]:
    return [item["id"] for item in state.intake if item.get("id")]


def test_seed_creates_intake_not_evidence():
    state = _state_with_materials()
    assert state.intake
    assert len(state.intake) >= 2
    assert not state.evidence
    assert all(item.get("carrier") for item in state.intake)
    assert not any(item.get("content_type") == "resume" for item in state.intake)
    assert not any(item.get("kind") == "resume" for item in state.attachments or [])


def test_reasoner_may_search_without_understand_or_analyze():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=RESUME_TEXT, data_source="mock")
    state.status = "RUNNING"
    registry = build_registry(data_source="mock")
    state = run_loop(
        state,
        registry,
        llm_provider=ScriptedReasonerLLM(
            [
                {
                    "action_type": "tool",
                    "tool_name": "search_jobs",
                    "arguments": {"keyword": "高级产品经理", "city": "深圳", "limit": 30},
                    "reason": "search without understanding",
                },
                {"action_type": "finish", "reason": "done"},
            ]
        ),
    )
    assert registry.invocations[0] == "search_jobs"
    assert "understand_user_input" not in registry.invocations
    assert "analyze_candidate" not in registry.invocations
    assert state.session.status == "DONE"


def test_reasoner_may_analyze_without_understand():
    state = _state_with_materials()
    state.status = "RUNNING"
    intake_id = next(
        item["id"]
        for item in state.intake
        if RESUME_TEXT in str(item.get("text") or "")
    )
    registry = build_registry(data_source="mock")
    state = run_loop(
        state,
        registry,
        llm_provider=ScriptedReasonerLLM(
            [
                {
                    "action_type": "tool",
                    "tool_name": "analyze_candidate",
                    "arguments": {"intake_ids": [intake_id]},
                    "reason": "analyze selected intake",
                },
                {"action_type": "finish", "reason": "done"},
            ]
        ),
    )
    assert registry.invocations == ["analyze_candidate"]
    assert "understand_user_input" not in registry.invocations
    assert state.session.status == "DONE"
    assert any(item.get("content_type") == ADMITTED_CONTENT_TYPE for item in state.evidence)


def test_reasoner_may_ask_or_finish_on_user_turn():
    ask = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    ask.status = "RUNNING"
    asked = run_loop(
        ask,
        build_registry(data_source="mock"),
        llm_provider=ScriptedReasonerLLM(
            [{"action_type": "ask_user", "intent": "clarification", "question": "想看哪个城市？", "reason": "ask"}]
        ),
    )
    assert asked.session.status == "WAITING_USER"

    done_state = new_agent_state(goal_input="谢谢，先这样", resume=None, data_source="mock")
    done_state.status = "RUNNING"
    finished = run_loop(
        done_state,
        build_registry(data_source="mock"),
        llm_provider=ScriptedReasonerLLM([{"action_type": "finish", "reason": "user stopped"}]),
    )
    assert finished.session.status == "DONE"


def test_empty_ids_does_not_auto_pick_intake():
    state = _state_with_materials()
    bound = bind_tool_arguments(state, tool_action("analyze_candidate", {}))
    assert bound["ok"] is False
    observation = bound["observation"]
    assert observation["kind"] == "ambiguous_evidence"
    available = {item["id"] for item in observation["available_intake"]}
    assert len(available) >= 2
    assert available == set(_intake_ids(state))


def test_missing_ids_is_observation_not_new_tool():
    state = new_agent_state(goal_input=None, resume=None, data_source="mock")
    bound = bind_tool_arguments(state, tool_action("understand_user_input", {}))
    assert bound["ok"] is False
    assert bound["observation"]["kind"] == "missing_evidence"


def test_unknown_intake_id_is_missing_observation():
    state = _state_with_materials()
    bound = bind_tool_arguments(
        state,
        tool_action("analyze_candidate", {"intake_ids": ["in-does-not-exist"]}),
    )
    assert bound["ok"] is False
    assert bound["observation"]["kind"] == "missing_evidence"
    assert "in-does-not-exist" in bound["observation"]["missing_intake_ids"]


def test_explicit_ids_bind_memory_and_ignore_action_bodies():
    state = _state_with_materials()
    intake_id = next(
        item["id"]
        for item in state.intake
        if RESUME_TEXT in str(item.get("text") or "")
    )
    bound = bind_tool_arguments(
        state,
        tool_action("analyze_candidate", {"intake_ids": [intake_id], "resume": "这段正文必须被忽略"}),
    )
    assert bound["ok"] is True
    assert "这段正文必须被忽略" not in str(bound["arguments"]["resume"])
    assert RESUME_TEXT in bound["arguments"]["resume"]["text"]


def test_loop_returns_ambiguous_observation_to_reasoner():
    state = _state_with_materials()
    state.status = "RUNNING"
    registry = build_registry(data_source="mock")
    seen = []

    def script(ctx: dict) -> dict:
        observation = ctx.get("observation") or {}
        seen.append(observation.get("kind"))
        if observation.get("kind") == "ambiguous_evidence":
            return {"action_type": "finish", "reason": "reasoner saw ambiguous evidence"}
        return {"action_type": "tool", "tool_name": "analyze_candidate", "arguments": {}, "reason": "no ids"}

    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([script, script]))
    assert "ambiguous_evidence" in seen
    assert "analyze_candidate" not in registry.invocations
    assert "search_jobs" not in registry.invocations
    assert state.session.status == "DONE"


def test_reasoner_payload_lists_intake_without_bodies():
    state = _state_with_materials()
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert payload["intake"]
    assert not payload["evidence"]
    assert all(item.get("id") for item in payload["intake"])
    assert all(item.get("char_count", 0) > 0 for item in payload["intake"])
    assert "content" not in payload["intake"][0]
    assert "text" not in payload["intake"][0]
    assert "resume_text" not in payload["memory"]
    for item in payload["context"]["attachments"]:
        assert "text" not in item
        assert "has_body" in item
        assert "kind" not in item or item.get("kind") is None


def test_resume_is_not_a_tool_precondition():
    spec = __import__("candidate.analyze_candidate", fromlist=["ANALYZE_CANDIDATE_SPEC"]).ANALYZE_CANDIDATE_SPEC
    assert "resume" not in (spec["parameters"].get("required") or [])
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = goal_response()
    registry = build_registry(data_source="mock")
    state = run_loop(
        state,
        registry,
        llm_provider=ScriptedReasonerLLM(
            [
                {
                    "action_type": "tool",
                    "tool_name": "search_jobs",
                    "arguments": {"keyword": "高级产品经理", "city": "深圳"},
                    "reason": "no resume needed",
                },
                {"action_type": "finish", "reason": "done"},
            ]
        ),
    )
    assert "search_jobs" in registry.invocations
    assert "analyze_candidate" not in registry.invocations


def test_profile_is_not_a_tool_precondition():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    assert not state.candidate.profile or state.candidate.profile.get("analysis_status") != "ok"
    registry = build_registry(data_source="mock")
    state = run_loop(
        state,
        registry,
        llm_provider=ScriptedReasonerLLM(
            [
                {
                    "action_type": "tool",
                    "tool_name": "search_jobs",
                    "arguments": {"keyword": "产品经理", "city": "深圳"},
                    "reason": "no profile needed",
                },
                {"action_type": "finish", "reason": "done"},
            ]
        ),
    )
    assert "search_jobs" in registry.invocations


def test_bind_does_not_choose_tools_or_write_decisions():
    source = (ROOT_DIR / "agent" / "bind_arguments.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    forbidden = {
        "resolve_task_intent",
        "parse_user_goal",
        "analyze_candidate",
        "match_job",
        "decide_next_action",
        "understand_user_input",
        "search_jobs",
    }
    assert not (forbidden & calls)
    assert "recommended" not in source
    assert "exclude" not in source
    text = source
    assert "if resume" not in text
    assert "profile_exists" not in text
    assert names  # module is not empty


def test_search_does_not_require_understanding_ok():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    assert state.understanding_status != "ok"
    registry = build_registry(data_source="mock")
    state = run_loop(
        state,
        registry,
        llm_provider=ScriptedReasonerLLM(
            [
                {
                    "action_type": "tool",
                    "tool_name": "search_jobs",
                    "arguments": {"keyword": "高级产品经理", "city": "深圳"},
                    "reason": "understanding is information only",
                },
                {"action_type": "finish", "reason": "done"},
            ]
        ),
    )
    assert registry.invocations == ["search_jobs"]
    assert state.understanding_status != "ok"


def test_carrier_kind_does_not_preset_evidence_type():
    state = new_agent_state(
        goal_input="找工作",
        attachments=[{"filename": "resume.pdf", "kind": "resume", "text": "我做过支付"}],
        data_source="mock",
    )
    assert state.intake
    assert not state.evidence
    uploads = [item for item in state.intake if item.get("carrier") == "upload"]
    assert uploads
    assert all(item.get("filename") == "resume.pdf" for item in uploads)
    assert not any(item.get("content_type") == "resume" for item in state.intake)
    assert not any(item.get("kind") == "resume" for item in state.attachments or [])
