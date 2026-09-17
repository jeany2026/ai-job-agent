"""Agent-architecture tests for candidate working memory. Not field-coercion unit tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.orchestrator import run_agent
from agent.proceed import can_match
from candidate.errors import ATTACHMENT_PROCESSING_FAILED, NO_EVIDENCE, SCHEMA_ERROR
from candidate.memory import MEMORY_USABLE, empty_memory, memory_is_usable, project_profile_view
from candidate.normalize import normalize_candidate_fact
from tests.mock_llm import (
    AgentRoutingLLM,
    MockLLMProvider,
    candidate_profile_response,
    understanding_response,
)
from tools.registry import build_registry

RESUME_PATH = ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")


class _Routing(AgentRoutingLLM):
    def __init__(self, understanding: dict | None = None, *, profile=None):
        super().__init__()
        self.understanding = understanding
        self.profile = profile
        self.reasoner_decisions: list[dict] = []

    def complete_json(self, *, system: str, user: str) -> dict:
        if self.understanding is not None and "UserInputUnderstanding" in system:
            self.calls.append({"system": system, "user": user})
            return self.understanding
        if self.profile is not None and (
            "候选人事实抽取器" in system or "候选人事实增量更新器" in system or "CandidateProfile" in system
        ):
            self.calls.append({"system": system, "user": user})
            return self.profile
        result = super().complete_json(system=system, user=user)
        if "你是全局 Agent Reasoner" in system:
            self.reasoner_decisions.append(result)
        return result


def test_case1_goal_and_resume_survive_typed_field_anomaly():
    payload = candidate_profile_response()
    payload["years_experience"] = 16
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal="我需要在深圳找产品经理，附件是我的简历",
        resume=SAMPLE_RESUME,
        llm_provider=_Routing(
            understanding_response(
                user_goal={
                    "target_roles": ["产品经理"],
                    "cities": ["深圳", "福田区", "南山区"],
                }
            ),
            profile=payload,
        ),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status != "FAILED"
    assert state.goal["target_roles"] == ["产品经理"]
    assert state.candidate.memory["status"] in {MEMORY_USABLE, "partial"}
    assert "search_jobs" in registry.invocations
    assert not any("请补充经历、简历或项目资料" in str(item.get("message")) for item in state.errors)


def test_case2_goal_only_can_search_matching_not_required():
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal="帮我找深圳产品经理",
        resume=None,
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="mock",
        constraints={"max_open_jd": 0, "max_search_results": 5},
    )
    assert state.session.status != "FAILED"
    assert "search_jobs" in registry.invocations
    assert "match_job" not in registry.invocations
    assert not can_match(state)
    assert state.candidate.memory is None or state.candidate.memory.get("status") in {
        None,
        "empty",
        "insufficient",
        "partial",
        "usable",
    }


def test_case3_resume_without_goal_asks_for_goal():
    registry = build_registry(data_source="mock")
    llm = _Routing(
        understanding_response(
            candidate_supplement={
                "claimed_capabilities": [
                    {"name": "支付", "quote": "支付", "source_kind": "resume"}
                ]
            }
        )
    )
    state = run_agent(
        goal="这是我的简历。",
        resume=SAMPLE_RESUME,
        llm_provider=llm,
        registry=registry,
        data_source="mock",
    )
    last = llm.reasoner_decisions[-1]
    assert last["action_type"] == "finish"
    assert last.get("error_code") == "GOAL_INCOMPLETE"
    assert state.decisions[-1]["action"] == "finish"
    assert state.output["error_code"] == "GOAL_INCOMPLETE"
    assert state.session.status == "DONE"
    assert "search_jobs" not in registry.invocations
    assert memory_is_usable(state.candidate.memory)


def test_case4_user_message_enters_goal_and_memory():
    message = "我有16年产品经验，主要做金融科技，现在想找深圳产品经理"
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal=message,
        resume=None,
        llm_provider=_Routing(
            understanding_response(
                user_goal={"target_roles": ["产品经理"], "cities": ["深圳"]},
                candidate_supplement={
                    "statements": ["我有16年产品经验，主要做金融科技"],
                    "claimed_capabilities": [
                        {"name": "金融科技", "quote": "金融科技", "source_kind": "user_statement"}
                    ],
                },
            )
        ),
        registry=registry,
        data_source="mock",
        constraints={"max_open_jd": 0},
    )
    assert state.goal["target_roles"] == ["产品经理"]
    names = [item.get("name") for item in (state.candidate.memory or {}).get("facts") or []]
    assert "金融科技" in names
    assert any("16年产品经验" in str(item.get("value") or item.get("display") or "") for item in state.candidate.memory["facts"])


def test_case5_partial_ingest_keeps_good_facts_and_continues():
    payload = candidate_profile_response()
    payload["education"] = {"degree": "本科"}
    payload["years_experience"] = 8
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal="帮我找深圳高级产品经理",
        resume=SAMPLE_RESUME,
        llm_provider=_Routing(
            understanding_response(user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]}),
            profile=payload,
        ),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status != "FAILED"
    assert "search_jobs" in registry.invocations
    years = [item for item in state.candidate.memory["facts"] if item.get("name") == "years_experience"]
    assert years
    assert years[0]["normalization_status"] == "coerced"


def test_case6_unreadable_empty_resume_is_attachment_issue_not_no_candidate():
    result = __import__("candidate.analyze_candidate", fromlist=["analyze_candidate"]).analyze_candidate(
        "",
        llm_provider=MockLLMProvider(candidate_profile_response()),
    )
    assert result["analysis_status"] == "insufficient_data"
    assert result["error_code"] == NO_EVIDENCE
    assert result["memory"]["unresolved"]


def test_schema_error_is_not_missing_materials():
    from agent.state import new_agent_state
    from api.serialize import serialize_agent_state

    state = new_agent_state(resume=SAMPLE_RESUME, goal_input="帮我找深圳产品经理", data_source="mock")
    state.status = "FAILED"
    state.errors.append({"kind": "candidate", "message": "CandidateProfile schema validation error: string field must be a string"})
    payload = serialize_agent_state(state)
    assert payload["error_code"] in {SCHEMA_ERROR, "TOOL_ERROR"}
    assert "请补充经历、简历或项目资料" not in (payload.get("message") or "")
    assert "候选人判断" not in (payload.get("message") or "")


def test_string_field_type_error_does_not_fail_the_run():
    from api.serialize import serialize_agent_state

    payload = candidate_profile_response()
    payload["education"] = ["本科"]
    payload["years_experience"] = 16
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal="帮我找深圳高级产品经理",
        resume=SAMPLE_RESUME,
        llm_provider=_Routing(
            understanding_response(user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]}),
            profile=payload,
        ),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status != "FAILED"
    assert "analyze_candidate" in registry.invocations
    view = serialize_agent_state(state)
    assert view.get("error_code") != "candidate_failed"
    assert "请补充经历、简历或项目资料" not in (view.get("message") or "")
    assert "候选人判断" not in (view.get("message") or "")


def test_resume_and_user_message_enter_same_evidence_system():
    registry = build_registry(data_source="mock")
    message = "我做过支付，现在想找深圳产品经理"
    state = run_agent(
        goal=message,
        resume=SAMPLE_RESUME,
        llm_provider=_Routing(
            understanding_response(
                user_goal={"target_roles": ["产品经理"], "cities": ["深圳"]},
                candidate_supplement={
                    "claimed_capabilities": [
                        {"name": "支付", "quote": "我做过支付", "source_kind": "user_statement"}
                    ]
                },
            )
        ),
        registry=registry,
        data_source="mock",
        constraints={"max_open_jd": 0},
    )
    assert state.intake
    assert {item.get("carrier") for item in state.intake} >= {"text", "compat_parameter"}
    types = {item.get("content_type") for item in state.evidence}
    assert "intake_text" in types
    assert "resume" not in types
    memory_types = {item.get("content_type") for item in state.candidate.memory["evidence"]}
    assert types == memory_types
    assert not any(item.get("kind") == "resume" for item in state.attachments or [])
    claims = state.candidate.memory["claims"]
    payment = [item for item in claims if item.get("name") == "支付"]
    assert payment
    assert payment[0]["evidence_status"] == "unverified"
    assert payment[0]["derived_from"]
    assert not any(item.get("layer") == "verification" for item in state.verifications)
    assert all(item.get("evidence_status") == "unverified" for item in claims)


def test_no_resume_reasoner_can_still_search():
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal="帮我找深圳产品经理",
        resume=None,
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="mock",
        constraints={"max_open_jd": 0, "max_search_results": 5},
    )
    assert state.session.status != "FAILED"
    assert "search_jobs" in registry.invocations
    types = {item.get("content_type") for item in (state.candidate.memory or {}).get("evidence") or []}
    assert "intake_text" in types or "user_message" in types
    assert "resume" not in types
    assert not any(item.get("error") == "missing_resume" for item in state.errors)


def test_projection_omits_sourceless_skills():
    memory = empty_memory()
    memory["facts"] = [
        normalize_candidate_fact({"kind": "skill", "name": "无来源技能", "value": "无来源技能"}),
        normalize_candidate_fact(
            {
                "kind": "skill",
                "name": "支付",
                "value": "支付",
                "source_kind": "user_statement",
                "derived_from": ["ev-1"],
            }
        ),
    ]
    view = project_profile_view(memory)
    names = []
    for field in (
        "product_capabilities",
        "business_capabilities",
        "technical_capabilities",
        "management_experience",
        "direct_capabilities",
    ):
        names.extend(item.get("name") for item in view[field])
    assert "无来源技能" not in names
    assert "支付" in names
    payment = next(item for item in view["direct_capabilities"] if item["name"] == "支付")
    assert payment["derived_from"] == ["ev-1"]
    assert payment["evidence_status"] == "unverified"
    assert view["kind"] == "interpretation_projection"


def test_user_claim_and_resume_gap_are_both_kept():
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal="我做过支付，帮我找深圳产品经理",
        resume="姓名：张三\n经历：做过电商后台，没有提到支付。",
        llm_provider=_Routing(
            understanding_response(
                user_goal={"target_roles": ["产品经理"], "cities": ["深圳"]},
                candidate_supplement={
                    "claimed_capabilities": [
                        {"name": "支付", "quote": "我做过支付", "source_kind": "user_statement"}
                    ]
                },
            ),
            profile={
                "facts": [
                    {
                        "kind": "skill",
                        "name": "电商后台",
                        "value": "电商后台",
                        "source_kind": "resume",
                    }
                ]
            },
        ),
        registry=registry,
        data_source="mock",
        constraints={"max_open_jd": 0},
    )
    claim_names = {item.get("name") for item in state.candidate.memory["claims"]}
    assert "支付" in claim_names
    assert "电商后台" in claim_names
    payment = next(item for item in state.candidate.memory["claims"] if item.get("name") == "支付")
    commerce = next(item for item in state.candidate.memory["claims"] if item.get("name") == "电商后台")
    assert payment["derived_from"]
    assert commerce["derived_from"]
    assert payment["evidence_status"] == "unverified"
    assert commerce["evidence_status"] == "unverified"
    types = {item.get("content_type") for item in state.evidence}
    assert "intake_text" in types
    assert "resume" not in types
    assert "user_message" not in types


def test_unreadable_attachment_code_is_not_missing_materials():
    from agent.state import new_agent_state
    from api.serialize import serialize_agent_state

    state = new_agent_state(goal_input="帮我找深圳产品经理", data_source="mock")
    state.status = "FAILED"
    state.errors.append({"kind": "attachment", "message": ATTACHMENT_PROCESSING_FAILED})
    payload = serialize_agent_state(state)
    assert payload["error_code"] == "PARSER_ERROR"
    assert "请补充经历、简历或项目资料" not in (payload.get("message") or "")
