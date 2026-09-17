"""Agent Loop Mock E2E: high match, transferable, hard gap, already applied, blacklist."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.orchestrator import run_agent
from tests.mock_llm import AgentRoutingLLM
from tools.registry import FORBIDDEN_LOOP_TOOLS, build_registry

RESUME_PATH = ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")
GOAL_TEXT = "帮我找深圳高级产品经理，重点金融科技/支付/复杂业务系统，薪资符合目标"


def _run():
    registry = build_registry(data_source="mock")
    llm = AgentRoutingLLM()
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal=GOAL_TEXT,
        constraints={"blacklist": ["黑名单科技"]},
        llm_provider=llm,
        registry=registry,
        data_source="mock",
    )
    return state, registry, llm


def test_mock_loop_reaches_done_without_boss():
    state, registry, llm = _run()
    assert state.session.status == "WAITING_USER"
    assert state.output is not None
    assert "browse_boss_jobs" not in registry.invocations
    assert not (FORBIDDEN_LOOP_TOOLS & set(registry.invocations))
    assert "search_jobs" in registry.invocations
    assert "open_job" in registry.invocations
    assert "interpret_job_actions" in registry.invocations
    assert "analyze_job" in registry.invocations
    assert "match_job" in registry.invocations
    assert "decide_next_action" not in registry.invocations
    assert "understand_user_input" in registry.invocations
    assert "parse_user_goal" not in registry.invocations
    assert "analyze_candidate" in registry.invocations
    assert llm.calls
    assert any("UserInputUnderstanding" in call["system"] for call in llm.calls)
    assert any("候选人事实抽取器" in call["system"] or "CandidateProfile" in call["system"] for call in llm.calls)
    assert len(state.search.plans) == 1
    assert state.task_view["task_status"] == "WAITING_USER"


def test_scenario_high_match_recommended_yes():
    state, _, _ = _run()
    waiting = state.output["waiting_job"]
    assert waiting["job_id"] == "mock-direct"
    assert waiting["recommendation"] == "yes"
    assert waiting["hard_requirements_met"] is True
    assert "产品规划" in (waiting["rationale"] or "") or "支付" in (waiting["rationale"] or "")
    outcomes = [item["outcome"] for item in waiting["capability_assessments"]]
    assert "direct" in outcomes
    assert state.output["recommended"][0]["job_id"] == "mock-direct"


def test_scenario_transferable_not_killed_by_industry_word():
    state, _, _ = _run()
    waiting_id = state.output["waiting_job"]["job_id"]
    assert waiting_id == "mock-direct"
    record = state.jobs["mock:mock-transfer"]
    assert record.stage == "listed"
    assert record.exclude_reason != "industry"
    assert record.opened is None


def test_scenario_hard_gap_excluded():
    state, registry, _ = _run()
    waiting_id = state.output["waiting_job"]["job_id"]
    assert waiting_id == "mock-direct"
    record = state.jobs["mock:mock-hard"]
    assert record.stage == "listed"
    assert record.opened is None


def test_scenario_already_applied_not_recommended():
    state, registry, llm = _run()
    waiting_id = state.output["waiting_job"]["job_id"]
    assert waiting_id == "mock-direct"
    record = state.jobs["mock:mock-applied"]
    assert record.stage == "listed"
    assert record.opened is None
    assert record.match_result is None


def test_scenario_blacklist_excluded_without_open():
    state, registry, _ = _run()
    recommended_ids = {item["job_id"] for item in state.output["recommended"]}
    assert "mock-blocked" not in recommended_ids
    record = state.jobs["mock:mock-blocked"]
    assert record.opened is None
    assert record.stage == "listed"
    assert record.stage != "excluded"
    assert "blacklist" in record.constraint_flags
    assert record.exclude_reason == "blacklist"
    opened_ids = [
        rec.listed.get("job_id")
        for rec in sorted(state.jobs.values(), key=lambda item: item.listed_order)
        if rec.opened is not None
    ]
    assert "mock-blocked" not in opened_ids


def test_open_order_is_list_order_not_keyword_enrich():
    state, _, _ = _run()
    opened_ids = [
        rec.listed.get("job_id")
        for rec in sorted(state.jobs.values(), key=lambda item: item.listed_order)
        if rec.opened is not None
    ]
    assert opened_ids == ["mock-direct"]
    listed_ids = [
        rec.listed.get("job_id")
        for rec in sorted(state.jobs.values(), key=lambda item: item.listed_order)
        if rec.stage != "excluded" or rec.exclude_reason != "blacklist"
    ]
    assert listed_ids[0] == "mock-direct"


def test_duplicate_search_hit_is_deduped():
    state, _, _ = _run()
    direct_keys = [key for key in state.jobs if key.endswith("mock-direct")]
    assert direct_keys == ["mock:mock-direct"]
