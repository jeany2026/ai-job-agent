"""ConversationReference helper JSON for tests. Not a keyword classifier."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import Action, tool_action
from agent.loop import reduce, run_loop
from agent.state import new_agent_state
from tests.mock_llm import (
    MockLLMProvider,
    ScriptedReasonerLLM,
    conversation_reference,
    understanding_response,
)
from tools.registry import build_registry
from understanding.schema import has_conversation_reference, validate_llm_payload
from understanding.understand_user_input import understand_user_input


def test_understanding_emits_semantic_reference_not_job_id():
    message = "刚才那个职位怎么样？"
    result, llm = _run(
        message,
        understanding_response(
            task_kind="follow_up_job",
            conversation_reference=conversation_reference(
                reference_text="刚才那个职位",
                resolution_hint={"recency": "last"},
            ),
        ),
    )
    assert result["analysis_status"] == "ok"
    assert result["task_kind"] == "follow_up_job"
    ref = result["conversation_reference"]
    assert ref["type"] == "conversation_reference"
    assert ref["target_kind"] == "job"
    assert "job_id" not in ref
    assert "job_key" not in ref
    assert "context_id" not in ref
    assert ref["resolution_hint"]["recency"] == "last"
    assert "禁止输出 job_id" in llm.calls[0]["system"]


def test_understanding_strips_guessed_job_id():
    raw = understanding_response(
        task_kind="follow_up_job",
        conversation_reference={
            "type": "conversation_reference",
            "target_kind": "job",
            "job_id": "should-not-survive",
            "job_key": "mock:should-not-survive",
            "reference_text": "刚才那个职位",
            "resolution_hint": {"recency": "last"},
        },
    )
    fields = validate_llm_payload(raw)
    assert "job_id" not in fields["conversation_reference"]
    assert "job_key" not in fields["conversation_reference"]
    assert has_conversation_reference(fields)


def test_follow_up_without_new_goal_is_valid():
    result, _ = _run(
        "刚才那个职位怎么样？",
        understanding_response(
            task_kind="follow_up_job",
            conversation_reference=conversation_reference(
                reference_text="刚才那个职位",
                resolution_hint={"recency": "last"},
            ),
        ),
    )
    assert result["analysis_status"] == "ok"
    assert result["user_goal"]["target_roles"] == []
    assert has_conversation_reference(result)


def test_follow_up_does_not_inherit_session_goal():
    result, _ = _run(
        "刚才那个职位怎么样？",
        understanding_response(
            task_kind="follow_up_job",
            conversation_reference=conversation_reference(
                reference_text="刚才那个职位",
                resolution_hint={"recency": "last"},
            ),
        ),
        session_context={"previous_goal": {"target_roles": ["高级产品经理"], "cities": ["深圳"]}},
    )
    assert result["user_goal"]["target_roles"] == []
    assert result["task_kind"] == "follow_up_job"


def test_unresolved_reference_is_observation_reasoner_asks():
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
    state = new_agent_state(
        goal_input="刚才那个职位怎么样？",
        resume=None,
        data_source="mock",
        session_context={"job_contexts": jobs},
    )
    state.status = "RUNNING"
    result = understanding_response(
        task_kind="follow_up_job",
        conversation_reference=conversation_reference(
            reference_text="刚才那个职位",
            resolution_hint={"recency": "last"},
        ),
    )
    result["analysis_status"] = "ok"
    state = reduce(
        state,
        tool_action("understand_user_input", {"message": "刚才那个职位怎么样？"}),
        result,
    )
    assert state.session.status == "RUNNING"
    assert state.session.status != "DONE"
    assert state.follow_up_of is None
    assert state.last_raw_observation["kind"] == "reference_unresolved"

    llm = ScriptedReasonerLLM(
        [
            {
                "action_type": "ask_user",
                "intent": "clarification",
                "question": state.last_raw_observation.get("message") or "请再说明一下。",
                "error_code": "reference_unresolved",
                "reason": "reasoner chose ask_user after unresolved reference",
            }
        ]
    )
    state = run_loop(state, build_registry(data_source="mock"), llm_provider=llm)
    assert state.session.status == "WAITING_USER"
    assert (state.output or {}).get("error_code") == "reference_unresolved"
    assert state.follow_up_of is None


def _run(message, response, *, session_context=None):
    llm = MockLLMProvider(response)
    result = understand_user_input(
        message,
        session_context=session_context,
        llm_provider=llm,
    )
    return result, llm
