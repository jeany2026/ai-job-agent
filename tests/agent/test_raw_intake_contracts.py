"""Intake facts are Context — not a forced analyze_candidate → ask_user pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.bind_arguments import bind_tool_arguments
from agent.decide import tool_action
from agent.intake import CARRIER_TEXT, CARRIER_UPLOAD, make_intake_item, raw_candidate_intake_present
from agent.reasoner_context import build_reasoner_payload
from agent.state import JobRecord, new_agent_state
from agent.validate_action import validate_reasoner_action
from tools.registry import build_registry


def _state_with_upload(*, text: str = "三年产品经验，做过 B 端 CRM") -> object:
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.understanding_status = "ok"
    goal_item = make_intake_item(text="找深圳产品经理", carrier=CARRIER_TEXT, existing=[])
    upload_item = make_intake_item(
        text=text,
        carrier=CARRIER_UPLOAD,
        filename="resume.txt",
        existing=[goal_item],
    )
    state.intake = [goal_item, upload_item]
    return state


def test_raw_candidate_intake_present_upload_only():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    state.intake = [make_intake_item(text="找深圳产品经理", carrier=CARRIER_TEXT)]
    assert raw_candidate_intake_present(state) is False

    state.intake.append(
        make_intake_item(text="产品经理三年", carrier=CARRIER_UPLOAD, filename="cv.txt", existing=state.intake)
    )
    assert raw_candidate_intake_present(state) is True


def test_context_exposes_raw_intake_separately_from_material():
    state = _state_with_upload()
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert payload["context"]["raw_candidate_intake_present"] is True
    assert payload["context"]["candidate_material_present"] is False


def test_ask_user_clarification_allowed_while_upload_unread():
    state = _state_with_upload()
    registry = build_registry(data_source="mock")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "ask_user",
            "intent": "clarification",
            "question": "你更看重深圳还是远程？",
            "reason": "clarify city preference",
        },
        registry,
    )
    assert checked["ok"] is True


def test_match_without_material_is_binding_observation_not_forced_analyze():
    state = _state_with_upload()
    state.jobs["mock:j1"] = JobRecord(
        job_key="mock:j1",
        stage="analyzed",
        listed_order=0,
        listed={"platform": "mock", "job_id": "j1"},
        opened={"platform": "mock", "job_id": "j1", "job_description": "JD"},
        job_profile={"analysis_status": "ok", "job_summary": "x"},
    )
    registry = build_registry(data_source="mock")
    # Program validate must not prescribe analyze_candidate.
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "tool",
            "tool_name": "match_job",
            "arguments": {"job_key": "mock:j1"},
            "reason": "match",
        },
        registry,
    )
    assert checked["ok"] is True
    assert "analyze_candidate" not in str(checked.get("error") or "")

    bound = bind_tool_arguments(state, tool_action("match_job", {"job_key": "mock:j1"}))
    assert bound["ok"] is False
    obs = bound["observation"]
    assert obs["kind"] == "insufficient_candidate_material"
    assert "analyze_candidate" not in str(obs.get("error") or "")
    assert obs.get("raw_candidate_intake_present") is True


def test_ask_user_clarification_allowed_without_upload():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.understanding_status = "ok"
    state.intake = [make_intake_item(text="找深圳产品经理", carrier=CARRIER_TEXT)]
    registry = build_registry(data_source="mock")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "ask_user",
            "intent": "clarification",
            "question": "请补充一份简历或简要经历",
            "reason": "no materials",
        },
        registry,
    )
    assert checked["ok"] is True


def test_text_supplement_does_not_force_analyze_before_ask():
    from tests.mock_llm import understanding_response

    state = new_agent_state(goal_input="我做过支付，帮我找产品经理", data_source="mock")
    state.understanding_status = "ok"
    state.understanding = understanding_response(
        user_goal={"target_roles": ["产品经理"], "cities": ["深圳"]},
        candidate_supplement={
            "claimed_capabilities": [
                {"name": "支付", "quote": "支付", "source_kind": "user_statement"}
            ]
        },
    )
    state.candidate.supplement = state.understanding["candidate_supplement"]
    registry = build_registry(data_source="mock")
    ask_checked = validate_reasoner_action(
        state,
        {
            "action_type": "ask_user",
            "intent": "clarification",
            "question": "期望薪资范围是多少？",
            "reason": "ask salary",
        },
        registry,
    )
    assert ask_checked["ok"] is True
    payload = build_reasoner_payload(state, registry)
    assert payload["context"]["has_candidate_supplement"] is True
