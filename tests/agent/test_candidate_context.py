"""Step C: CandidateContext in the Agent Loop. No keyword fallback, no Web changes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.orchestrator import run_agent
from candidate.context import build_candidate_context
from matching.match_job import match_job
from storage.candidate_profile import CandidateProfileStore, compute_source_hash
from tests.mock_llm import (
    AgentRoutingLLM,
    MockLLMProvider,
    ScriptedReasonerLLM,
    candidate_profile_response,
    goal_response,
    job_profile_response,
    understanding_response,
)
from tools.registry import build_registry

RESUME_PATH = ROOT_DIR / Path("tests/fixtures/resumes/sample_pm.txt")
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")
GOAL_TEXT = "帮我找深圳高级产品经理"


def _ok_profile(**overrides) -> dict:
    profile = candidate_profile_response()
    profile["analysis_status"] = "ok"
    profile["error"] = None
    profile["candidate_id"] = "cand-1"
    profile.update(overrides)
    return profile


def _cap(name: str, quote: str) -> dict:
    return {"name": name, "quote": quote, "source_kind": "user_statement"}


class _Routing(AgentRoutingLLM):
    def __init__(self, understanding: dict | None = None):
        super().__init__()
        self.understanding = understanding

    def complete_json(self, *, system: str, user: str) -> dict:
        if self.understanding is not None and "UserInputUnderstanding" in system:
            self.calls.append({"system": system, "user": user})
            return self.understanding
        return super().complete_json(system=system, user=user)


def test_existing_profile_without_resume_runs_and_skips_analyze():
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal=GOAL_TEXT,
        resume=None,
        candidate_profile=_ok_profile(),
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status == "WAITING_USER"
    assert "understand_user_input" in registry.invocations
    assert "parse_user_goal" not in registry.invocations
    assert "analyze_candidate" not in registry.invocations
    assert state.candidate_context["persistent_profile"]["analysis_status"] == "ok"
    assert state.search.plans[0].keyword == "高级产品经理"


def test_no_profile_but_user_statements_build_context():
    message = "我做过支付、结算、CRM、交易系统，现在帮我找高级产品经理。"
    understanding = understanding_response(
        user_goal={"target_roles": ["高级产品经理"]},
        candidate_supplement={
            "claimed_capabilities": [
                _cap("支付", "支付"),
                _cap("结算", "结算"),
                _cap("CRM", "CRM"),
                _cap("交易系统", "交易系统"),
            ]
        },
    )
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal=message,
        resume=None,
        llm_provider=_Routing(understanding),
        registry=registry,
        data_source="mock",
        constraints={"max_open_jd": 0, "max_search_results": 5},
        profile_dir=None,
    )
    assert state.session.status in {"DONE", "WAITING_USER"}
    assert "understand_user_input" in registry.invocations
    assert "analyze_candidate" in registry.invocations
    names = [item["name"] for item in state.candidate_context["candidate_supplement"]["claimed_capabilities"]]
    assert names == ["支付", "结算", "CRM", "交易系统"]
    assert state.goal["target_roles"] == ["高级产品经理"]
    assert not any("missing_resume" in str(item.get("message")) for item in state.errors)


def test_goal_only_without_candidate_evidence_can_search():
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal="帮我找深圳高级产品经理。",
        resume=None,
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status != "FAILED"
    assert "search_jobs" in registry.invocations
    assert "analyze_candidate" not in registry.invocations
    assert not any(item.get("message") == "insufficient_candidate" for item in state.errors)
    assert not any("missing_resume" in str(item.get("message")) for item in state.errors)


def test_supplement_persists_after_successful_analyze(tmp_path):
    """Job agent: text materials analyzed successfully become portrait (no「记住」required)."""
    store = CandidateProfileStore(tmp_path)
    store.create(_ok_profile(), candidate_id="cand-1", source_kinds=["resume"])
    understanding = understanding_response(
        user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]},
        candidate_supplement={
            "claimed_projects": [
                {
                    "name": "运动小程序",
                    "quote": "运动小程序",
                    "source_kind": "user_statement",
                }
            ]
        },
        persist_requested=False,
    )
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal="这次把运动小程序考虑进去，帮我找高级产品经理。",
        resume=None,
        profile_dir=str(tmp_path),
        candidate_id="cand-1",
        llm_provider=_Routing(understanding),
        registry=registry,
        data_source="mock",
        constraints={"max_open_jd": 0},
    )
    assert state.session.status == "WAITING_USER"
    assert "analyze_candidate" in registry.invocations
    assert state.candidate.profile_persisted_this_turn is True
    loaded = store.load("cand-1")
    assert loaded["profile_version"] == 2
    dumped = json.dumps(loaded["profile"], ensure_ascii=False)
    assert "运动小程序" in dumped


def test_persist_requested_updates_profile_version(tmp_path):
    store = CandidateProfileStore(tmp_path)
    store.create(_ok_profile(), candidate_id="cand-1", source_kinds=["resume"])
    understanding = understanding_response(
        user_goal={"target_roles": ["高级产品经理"]},
        candidate_supplement={
            "claimed_projects": [
                {
                    "name": "运动小程序",
                    "quote": "运动小程序",
                    "source_kind": "user_statement",
                }
            ]
        },
        persist_requested=True,
    )
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal="以后找工作都考虑我这个运动小程序项目。帮我找高级产品经理。",
        resume=None,
        profile_dir=str(tmp_path),
        candidate_id="cand-1",
        llm_provider=_Routing(understanding),
        registry=registry,
        data_source="mock",
        constraints={"max_open_jd": 0},
    )
    assert state.session.status == "WAITING_USER"
    assert "analyze_candidate" in registry.invocations
    loaded = store.load("cand-1")
    assert loaded["profile_version"] == 2
    assert store.list_versions("cand-1") == [1, 2]


def test_match_receives_profile_plus_supplement():
    calls = []
    registry = build_registry(data_source="mock")
    original = registry._handlers["match_job"]

    def wrapped(arguments, **extra):
        calls.append(arguments)
        return original(arguments, **extra)

    registry._handlers["match_job"] = wrapped
    understanding = understanding_response(
        user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]},
        candidate_supplement={"claimed_capabilities": [_cap("支付", "支付")]},
        matching_context="重点判断支付能力向医疗支付的迁移",
    )
    state = run_agent(
        goal="帮我找高级产品经理，支付经验请纳入匹配。",
        resume=None,
        candidate_profile=_ok_profile(),
        llm_provider=_Routing(understanding),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status == "WAITING_USER"
    assert calls
    context = calls[0]["candidate_context"]
    assert context["persistent_profile"]["analysis_status"] == "ok"
    assert context["candidate_supplement"]["claimed_capabilities"][0]["name"] == "支付"
    assert "迁移" in (context["matching_context"] or "")


def test_search_plan_uses_user_goal_not_profile_skills():
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal="帮我找深圳高级产品经理",
        resume=None,
        candidate_profile=_ok_profile(),
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="mock",
        constraints={"max_open_jd": 0, "min_recommend": 1},
    )
    keywords = [plan.keyword for plan in state.search.plans]
    assert keywords == ["高级产品经理"]
    joined = " ".join(keywords)
    assert "支付产品经理" not in joined
    assert "结算产品经理" not in joined
    assert "风控产品经理" not in joined


def test_transferable_match_does_not_reject_on_missing_industry_word():
    profile = _ok_profile()
    context = build_candidate_context(
        persistent_profile=profile,
        candidate_supplement={
            "claimed_capabilities": [
                _cap("支付", "支付"),
                _cap("结算", "结算"),
                _cap("对账", "对账"),
                _cap("风控", "风控"),
                _cap("复杂业务系统", "复杂业务系统"),
            ],
            "acknowledged_gaps": [{"gap": "没有医疗行业经验", "quote": "没有医疗行业经验"}],
        },
        matching_context="不要因为没有医疗行业经验直接排除",
    )
    from tests.mock_llm import match_result_response

    payload = match_result_response(
        recommendation="weak",
        industry_fit="weak",
        overall_fit="moderate",
        rationale="没有医疗行业直接经验，但支付结算复杂业务系统具备较强迁移匹配。",
        capability_assessments=[
            {
                "dimension": "医疗行业经验",
                "outcome": "missing",
                "job_requirement_ref": "有医疗行业经验优先",
                "candidate_capability_ref": None,
                "transfer_rationale": None,
                "evidence": [{"quote": "有医疗行业经验优先", "location_hint": None, "field": None}],
            },
            {
                "dimension": "支付结算",
                "outcome": "transferable",
                "job_requirement_ref": "支付",
                "candidate_capability_ref": "支付",
                "transfer_rationale": "支付结算和对账风控可迁移到医疗支付场景。",
                "evidence": [{"quote": "支付", "location_hint": None, "field": None}],
            },
        ],
        knowledge_gaps=[
            {
                "gap": "医疗行业直接经验不足",
                "severity": "medium",
                "evidence": [{"quote": "有医疗行业经验优先", "location_hint": None, "field": None}],
                "notes": "无行业直接经验不等于无岗位能力",
            }
        ],
        evidence_summary=[
            {"quote": "支付", "location_hint": None, "field": None},
            {"quote": "有医疗行业经验优先", "location_hint": None, "field": None},
        ],
    )
    result = match_job(
        profile,
        job_profile_response(),
        llm_provider=MockLLMProvider(payload),
        candidate_context=context,
    )
    assert result["analysis_status"] == "ok", result.get("error")
    outcomes = {item["dimension"]: item["outcome"] for item in result["capability_assessments"]}
    assert outcomes["医疗行业经验"] == "missing"
    assert outcomes["支付结算"] == "transferable"
    assert result["recommendation"] == "weak"


def test_new_resume_creates_next_profile_version_not_delete(tmp_path):
    store = CandidateProfileStore(tmp_path)
    store.create(
        _ok_profile(summary="旧画像支付结算"),
        candidate_id="cand-1",
        source_resume_hash=compute_source_hash(SAMPLE_RESUME),
        source_kinds=["resume"],
    )
    registry = build_registry(data_source="mock")
    state = run_agent(
        goal=GOAL_TEXT,
        resume=SAMPLE_RESUME + "\n新增项目：清分对账平台。",
        profile_dir=str(tmp_path),
        candidate_id="cand-1",
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="mock",
        constraints={"max_open_jd": 0},
    )
    assert state.session.status == "WAITING_USER"
    assert "analyze_candidate" in registry.invocations
    current = store.load("cand-1")
    archived = store.load_version("cand-1", 1)
    assert current["profile_version"] == 2
    assert archived["profile_version"] == 1
    assert archived["profile"]["summary"] == "旧画像支付结算"


def test_understand_llm_error_does_not_fail_the_agent():
    registry = build_registry(data_source="mock")
    evidence_ids = []

    def _understand(ctx: dict) -> dict:
        ids = [
            str(item.get("id"))
            for item in (ctx.get("intake") or [])
            if isinstance(item, dict) and item.get("id")
        ]
        evidence_ids.extend(ids)
        return {
            "action_type": "tool",
            "tool_name": "understand_user_input",
            "arguments": {"intake_ids": ids},
            "reason": "try to understand",
        }

    class _UnderstandFailsThenSearch(ScriptedReasonerLLM):
        def complete_json(self, *, system: str, user: str) -> dict:
            if "用户输入理解器" in system or "UserInputUnderstanding" in system:
                self.calls.append({"system": system, "user": user})
                raise RuntimeError("LLM response is not valid JSON")
            return super().complete_json(system=system, user=user)

    llm = _UnderstandFailsThenSearch(
        [
            _understand,
            {
                "action_type": "tool",
                "tool_name": "search_jobs",
                "arguments": {"keyword": "高级产品经理", "city": "深圳", "limit": 30},
                "reason": "reasoner still decides after understand failed",
            },
            {"action_type": "finish", "reason": "done"},
        ]
    )
    state = run_agent(
        goal=GOAL_TEXT,
        resume=SAMPLE_RESUME,
        llm_provider=llm,
        registry=registry,
        data_source="mock",
    )
    assert state.session.status != "FAILED"
    assert any(item.get("kind") == "understanding" for item in state.errors)
    assert "search_jobs" in registry.invocations
    assert "parse_user_goal" not in registry.invocations
