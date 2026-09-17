"""D.1 JobContext + Reference Resolver behavior tests T1–T10."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.orchestrator import run_agent
from api.runs import ConversationStore
from conversation.job_context import JobContext, job_context_from_dict
from conversation.reference_resolver import (
    RESOLUTION_RESOLVED,
    RESOLUTION_UNRESOLVED,
    resolve_conversation_reference,
)
from storage.candidate_profile import CandidateProfileStore
from tests.mock_llm import (
    AgentRoutingLLM,
    candidate_profile_response,
    conversation_reference,
    job_profile_response,
    match_result_response,
    understanding_response,
)
from tools.registry import build_registry

RESUME_PATH = ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")
GOAL_TEXT = "帮我找深圳高级产品经理"


def _ok_profile(**overrides) -> dict:
    profile = candidate_profile_response()
    profile["analysis_status"] = "ok"
    profile["error"] = None
    profile["candidate_id"] = "cand-1"
    profile.update(overrides)
    return profile


class _TurnLLM(AgentRoutingLLM):
    def __init__(self, understanding: dict):
        super().__init__()
        self.understanding = understanding

    def complete_json(self, *, system: str, user: str) -> dict:
        if "UserInputUnderstanding" in system:
            self.calls.append({"system": system, "user": user})
            return self.understanding
        return super().complete_json(system=system, user=user)


def _search_state(**kwargs):
    registry = kwargs.pop("registry", None) or build_registry(data_source="mock")
    state = run_agent(
        resume=kwargs.get("resume", SAMPLE_RESUME),
        goal=kwargs.get("goal", GOAL_TEXT),
        candidate_profile=kwargs.get("candidate_profile"),
        profile_dir=kwargs.get("profile_dir"),
        candidate_id=kwargs.get("candidate_id", "cand-1"),
        llm_provider=kwargs.get("llm_provider") or AgentRoutingLLM(),
        registry=registry,
        data_source="mock",
        session_context=kwargs.get("session_context"),
    )
    return state, registry


def _job_context(
    *,
    job_id: str,
    ordinal: int,
    round_no: int = 1,
    title: str = "高级产品经理",
    company: str = "支付科技有限公司",
    recommendation: str = "yes",
    overall_fit: str = "strong",
    gaps: list[str] | None = None,
    job_profile: dict | None = None,
    match_result: dict | None = None,
) -> JobContext:
    profile = job_profile if job_profile is not None else job_profile_response()
    match = match_result if match_result is not None else match_result_response()
    if gaps is not None:
        match = dict(match)
        match["knowledge_gaps"] = [{"gap": gap, "severity": "low", "evidence": [], "notes": None} for gap in gaps]
    return JobContext(
        context_id=f"mock:{job_id}",
        job_key=f"mock:{job_id}",
        platform="mock",
        job_id=job_id,
        job_url=f"https://mock.local/job/{job_id}",
        title=title,
        company=company,
        job_profile=profile,
        job_listing={
            "platform": "mock",
            "job_id": job_id,
            "job_url": f"https://mock.local/job/{job_id}",
            "job_title": title,
            "company_name": company,
            "job_description": "负责支付产品规划。",
        },
        match_result=match,
        application_evidence="not_applied",
        source_run_id="run-1",
        source_round=round_no,
        listed_order=ordinal - 1,
        round_ordinal=ordinal,
        recommendation_context={
            "recommendation": recommendation,
            "overall_fit": overall_fit,
            "rationale": match.get("rationale"),
            "capability_assessments": match.get("capability_assessments") or [],
            "knowledge_gaps": match.get("knowledge_gaps") or [],
            "risks": match.get("risks") or [],
            "hard_requirements_met": match.get("hard_requirements_met"),
            "stage": "recommended",
        },
        stage="recommended",
    )


def _ref(*, reference_text: str | None = None, **hint) -> dict:
    return conversation_reference(reference_text=reference_text, resolution_hint=hint)


def test_t1_new_task_still_searches():
    registry = build_registry(data_source="mock")
    state, registry = _search_state(registry=registry)
    assert state.session.status == "WAITING_USER"
    assert "search_jobs" in registry.invocations
    assert "understand_user_input" in registry.invocations
    assert state.output["recommended"]


def test_t2_search_saves_full_job_context():
    state, _ = _search_state()
    store = ConversationStore()
    store.remember("conv-t2", state, run_id="run-t2")
    contexts = store.get_job_contexts("conv-t2")
    assert contexts
    hit = next(item for item in contexts if item.get("job_id") == "mock-direct")
    assert hit["platform"] == "mock"
    assert hit["job_url"]
    assert hit["title"]
    assert hit["company"]
    assert isinstance(hit["job_profile"], dict)
    assert hit["job_profile"].get("hard_requirements")
    assert isinstance(hit["match_result"], dict)
    assert hit["match_result"].get("recommendation")
    assert hit["source_round"] == 1
    assert hit["source_run_id"] == "run-t2"
    assert hit["recommendation_context"]["recommendation"]
    loaded = store.get_job_context("conv-t2", hit["context_id"])
    assert loaded["job_key"] == hit["job_key"]


def test_t3_last_job_reference_resolves_when_unique():
    ctx = _job_context(job_id="job-a", ordinal=1)
    resolution = resolve_conversation_reference(_ref(recency="last"), [ctx])
    assert resolution.status == RESOLUTION_RESOLVED
    assert resolution.job_context is not None
    assert resolution.job_context.job_id == "job-a"


def test_t3_loop_follow_up_uses_saved_job_context():
    first, _ = _search_state()
    store = ConversationStore()
    store.remember("conv-t3", first, run_id="run-t3")
    only = store.get_job_context("conv-t3", "mock:mock-direct")
    assert only is not None
    session = {
        "previous_goal": first.goal,
        "job_contexts": [only],
    }
    registry = build_registry(data_source="mock")
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal="刚才那个职位怎么样？",
        candidate_profile=_ok_profile(),
        session_context=session,
        llm_provider=_TurnLLM(
            understanding_response(
                task_kind="follow_up_job",
                conversation_reference=_ref(recency="last", reference_text="刚才那个职位"),
            )
        ),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status == "DONE"
    assert state.follow_up_of == "mock:mock-direct"
    assert state.reference_resolution["status"] == RESOLUTION_RESOLVED


def test_t4_deep_follow_up_uses_original_profile_and_match():
    first, _ = _search_state(candidate_profile=_ok_profile())
    store = ConversationStore()
    store.remember("conv-t4", first, run_id="run-t4")
    original = store.get_job_context("conv-t4", "mock:mock-direct")
    message = "这个职位虽然没有医疗行业经验，但我有支付、结算、对账经验，能做吗？"
    registry = build_registry(data_source="mock")
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal=message,
        candidate_profile=_ok_profile(),
        session_context={"job_contexts": [original]},
        llm_provider=_TurnLLM(
            understanding_response(
                task_kind="follow_up_job",
                matching_context="不要因为没有医疗行业经验直接排除，重点判断支付、结算、对账经验。",
                persist_requested=False,
                conversation_reference=_ref(recency="last"),
                candidate_supplement={
                    "claimed_capabilities": [
                        {"name": "支付", "quote": "支付", "source_kind": "user_statement"},
                        {"name": "结算", "quote": "结算", "source_kind": "user_statement"},
                        {"name": "对账", "quote": "对账", "source_kind": "user_statement"},
                    ],
                    "transfer_claims": [
                        {
                            "claim": "支付、结算、对账经验可迁移",
                            "from_domain": "支付结算",
                            "to_domain": "医疗",
                            "quote": "支付、结算、对账经验",
                        }
                    ],
                },
            )
        ),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status == "DONE"
    assert state.output["follow_up"] is True
    assert state.output["original_job_profile"]["hard_requirements"] == original["job_profile"]["hard_requirements"]
    assert state.output["original_match_result"]["recommendation"] == original["match_result"]["recommendation"]
    supplement = state.candidate_context["candidate_supplement"]
    names = [item["name"] for item in supplement["claimed_capabilities"]]
    assert "支付" in names and "结算" in names and "对账" in names
    assert supplement["transfer_claims"]
    assert "match_job" in registry.invocations
    assert state.jobs["mock:mock-direct"].job_profile["job_summary"] == original["job_profile"]["job_summary"]


def test_t5_follow_up_does_not_search():
    first, first_registry = _search_state(candidate_profile=_ok_profile())
    assert "search_jobs" in first_registry.invocations
    store = ConversationStore()
    store.remember("conv-t5", first, run_id="run-t5")
    original = store.get_job_context("conv-t5", "mock:mock-direct")
    message = "这个职位虽然没有医疗行业经验，但我有支付、结算、对账经验，能做吗？"
    registry = build_registry(data_source="mock")
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal=message,
        candidate_profile=_ok_profile(),
        session_context={"job_contexts": [original]},
        llm_provider=_TurnLLM(
            understanding_response(
                task_kind="follow_up_job",
                persist_requested=False,
                conversation_reference=_ref(recency="last"),
                candidate_supplement={
                    "claimed_capabilities": [
                        {"name": "支付", "quote": "支付", "source_kind": "user_statement"},
                    ],
                    "transfer_claims": [
                        {
                            "claim": "支付经验可迁移",
                            "quote": "支付",
                            "from_domain": "支付",
                            "to_domain": "医疗",
                        }
                    ],
                },
            )
        ),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status == "DONE"
    assert "search_jobs" not in registry.invocations
    assert "open_job" not in registry.invocations
    assert "analyze_job" not in registry.invocations
    assert "match_job" in registry.invocations


def test_t6_previous_round_second_job():
    first = _job_context(job_id="job-a", ordinal=1, company="甲公司")
    second = _job_context(job_id="job-b", ordinal=2, company="乙公司", overall_fit="moderate")
    resolution = resolve_conversation_reference(
        _ref(recency="previous_round", ordinal=2),
        [first, second],
    )
    assert resolution.status == RESOLUTION_RESOLVED
    assert resolution.job_context.job_id == "job-b"


def test_t7_semantic_gap_reference():
    medical = _job_context(
        job_id="job-medical",
        ordinal=1,
        company="医疗支付公司",
        gaps=["缺少医疗行业经验"],
        job_profile=job_profile_response(
            industry_requirements=[
                {
                    "requirement": "医疗行业经验",
                    "category": "industry",
                    "importance": "medium",
                    "explicit": True,
                    "evidence_quote": "医疗行业经验",
                }
            ]
        ),
    )
    other = _job_context(
        job_id="job-pay",
        ordinal=2,
        company="支付科技",
        gaps=["CRM经验未出现"],
        job_profile=job_profile_response(bonus_requirements=[], industry_requirements=[]),
    )
    resolution = resolve_conversation_reference(
        _ref(
            recency="last",
            semantic_filters=[{"kind": "knowledge_gap", "value": "医疗行业经验"}],
        ),
        [medical, other],
    )
    assert resolution.status == RESOLUTION_RESOLVED
    assert resolution.job_context.job_id == "job-medical"


def test_t8_ambiguous_reference_does_not_guess():
    jobs = [
        _job_context(job_id="job-a", ordinal=1, company="甲"),
        _job_context(job_id="job-b", ordinal=2, company="乙"),
        _job_context(job_id="job-c", ordinal=3, company="丙"),
    ]
    resolution = resolve_conversation_reference(_ref(recency="last"), jobs)
    assert resolution.status == RESOLUTION_UNRESOLVED
    assert resolution.job_context is None
    assert set(resolution.candidates) == {"mock:job-a", "mock:job-b", "mock:job-c"}

    registry = build_registry(data_source="mock")
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal="刚才那个职位怎么样？",
        candidate_profile=_ok_profile(),
        session_context={"job_contexts": [ctx.to_dict() for ctx in jobs]},
        llm_provider=_TurnLLM(
            understanding_response(
                task_kind="follow_up_job",
                conversation_reference=_ref(recency="last", reference_text="刚才那个职位"),
            )
        ),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status == "WAITING_USER"
    assert state.output["error_code"] == "reference_unresolved"
    assert state.output["clarification_needed"] is True
    assert "search_jobs" not in registry.invocations
    assert state.follow_up_of is None


def test_t8_highest_match_unique_and_tied():
    strong = _job_context(job_id="job-strong", ordinal=1, overall_fit="strong", recommendation="yes")
    weak = _job_context(job_id="job-weak", ordinal=2, overall_fit="weak", recommendation="weak")
    resolved = resolve_conversation_reference(_ref(highest_match=True), [strong, weak])
    assert resolved.status == RESOLUTION_RESOLVED
    assert resolved.job_context.job_id == "job-strong"

    twin = _job_context(job_id="job-twin", ordinal=3, overall_fit="strong", recommendation="yes")
    tied = resolve_conversation_reference(_ref(highest_match=True), [strong, twin])
    assert tied.status == RESOLUTION_UNRESOLVED
    assert tied.job_context is None


def test_t9_follow_up_does_not_mutate_candidate_profile(tmp_path):
    store = CandidateProfileStore(tmp_path)
    store.create(_ok_profile(), candidate_id="cand-1", source_kinds=["resume"])
    first, _ = _search_state(profile_dir=str(tmp_path), candidate_profile=_ok_profile())
    conversations = ConversationStore()
    conversations.remember("conv-t9", first, run_id="run-t9")
    version = store.load("cand-1")["profile_version"]
    original = conversations.get_job_context("conv-t9", "mock:mock-direct")
    registry = build_registry(data_source="mock")
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal="刚才那个职位怎么样？",
        candidate_profile=_ok_profile(),
        profile_dir=str(tmp_path),
        candidate_id="cand-1",
        session_context={"job_contexts": [original]},
        llm_provider=_TurnLLM(
            understanding_response(
                task_kind="follow_up_job",
                persist_requested=False,
                conversation_reference=_ref(recency="last"),
            )
        ),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status == "DONE"
    assert state.understanding["persist_requested"] is False
    assert store.load("cand-1")["profile_version"] == version
    profile = store.load("cand-1")["profile"]
    assert "medical_payment_fit" not in profile
    assert state.follow_up_of == "mock:mock-direct"


def test_t10_new_search_still_searches_with_history():
    first, _ = _search_state()
    store = ConversationStore()
    store.remember("conv-t10", first, run_id="run-t10")
    session = store.session_context("conv-t10")
    assert session["job_contexts"]
    registry = build_registry(data_source="mock")
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal="重新帮我找一批深圳的高级产品经理。",
        candidate_profile=_ok_profile(),
        session_context=session,
        llm_provider=_TurnLLM(
            understanding_response(
                task_kind="new_job_search",
                user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]},
            )
        ),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status == "WAITING_USER"
    assert "search_jobs" in registry.invocations
    assert state.follow_up_of is None


def test_job_context_from_dict_roundtrip():
    ctx = _job_context(job_id="roundtrip", ordinal=1)
    restored = job_context_from_dict(ctx.to_dict())
    assert restored is not None
    assert restored.job_profile["hard_requirements"]
    assert restored.match_result["recommendation"]
    assert restored.application_evidence == "not_applied"
