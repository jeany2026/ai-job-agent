"""Epistemic honesty: Fact / Evidence / Interpretation / Observation / Program Hint."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.bind_arguments import bind_tool_arguments
from agent.decide import tool_action
from agent.epistemic import is_restored, stamp_restored
from agent.human_gate_continuity import restore_world_from_human_gate_snapshot, snapshot_world_for_human_gate
from agent.reasoner_context import build_reasoner_payload
from agent.state import JobRecord, new_agent_state
from agent.world_restore import restore_job_record
from conversation.job_context import JobContext, hydrate_job_record
from tools.registry import build_registry

GOAL = "找深圳产品经理"
JD = "负责支付产品规划。"


def _registry():
    return build_registry(data_source="mock")


def test_application_evidence_is_interpretation_not_bare_fact():
    state = new_agent_state(goal_input=GOAL, data_source="mock")
    state.jobs["mock:j1"] = JobRecord(
        job_key="mock:j1",
        stage="opened",
        listed_order=0,
        listed={"platform": "mock", "job_id": "j1", "job_title": "PM", "city": "深圳"},
        opened={
            "platform": "mock",
            "job_id": "j1",
            "job_title": "PM",
            "job_description": JD,
        },
        interpret_result={
            "analysis_status": "ok",
            "inferred_context": {"application_evidence": "applied", "evidence_source": "page_action_semantics"},
        },
    )
    payload = build_reasoner_payload(state, _registry())
    job = payload["jobs"][0]
    assert "application_evidence" not in job or not isinstance(job.get("application_evidence"), str)
    app = job["interpret_result"]["application_evidence"]
    assert app["layer"] == "interpretation"
    assert app["value"] == "applied"
    assert app["source"] == "interpret_job_actions"
    assert app["provenance"] in {"llm_interpretation", "restored"}
    assert job["listed_card_facts"]["layer"] == "fact"
    assert job["listed_card_facts"]["job_title"] == "PM"
    assert job["fact"]["layer"] == "fact"
    assert job["job_profile"] is None


def test_listed_card_facts_separated_from_interpretations():
    state = new_agent_state(goal_input=GOAL, data_source="mock")
    state.jobs["mock:j1"] = JobRecord(
        job_key="mock:j1",
        stage="opened",
        listed_order=0,
        listed={
            "platform": "mock",
            "job_id": "j1",
            "job_title": "高级产品经理",
            "company_name": "支付科技",
            "city": "深圳",
            "salary": "25-40K",
        },
        opened={
            "platform": "mock",
            "job_id": "j1",
            "job_title": "高级产品经理",
            "company_name": "支付科技",
            "city": "深圳",
            "salary": "25-40K",
            "job_description": JD,
        },
        job_profile={"analysis_status": "ok", "job_summary": "支付"},
        match_result={"recommendation": "yes", "hard_requirements_met": True},
    )
    payload = build_reasoner_payload(state, _registry())
    job = payload["jobs"][0]
    assert job.get("salary") is None or "salary" not in job
    assert job["listed_card_facts"]["salary"] == "25-40K"
    assert job["job_profile"]["layer"] == "interpretation"
    assert job["match_result"]["layer"] == "interpretation"
    assert JD not in str(payload["jobs"])


def test_observation_excludes_coach_and_recovery():
    state = new_agent_state(goal_input=GOAL, data_source="mock")
    state.last_raw_observation = {
        "kind": "action_rejected",
        "error": "quota exceeded: open",
        "attempted": {"tool_name": "open_job"},
        "coach_zh": "should be stripped",
        "recovery": {"focus": ["x"]},
    }
    payload = build_reasoner_payload(state, _registry())
    assert "coach_zh" not in payload["observation"]
    assert "recovery" not in payload["observation"]
    hints = payload["program_hints"]
    assert hints["layer"] == "program_hint"
    assert hints["not_fact"] is True
    assert hints["not_instruction"] is True
    assert hints["not_next_action"] is True
    assert "next_action" not in hints
    assert "tool_name" not in hints or hints.get("not_next_action") is True


def test_restore_does_not_upgrade_stage_to_matched():
    state = new_agent_state(goal_input=GOAL, data_source="mock")
    ctx = JobContext(
        context_id="mock:j1",
        job_key="mock:j1",
        platform="mock",
        job_id="j1",
        job_url="https://mock.local/j1",
        title="PM",
        company="Co",
        job_listing={
            "platform": "mock",
            "job_id": "j1",
            "job_title": "PM",
            "job_description": JD,
        },
        job_profile={"analysis_status": "ok", "job_summary": "x"},
        match_result={"recommendation": "yes", "hard_requirements_met": True},
        stage="matched",
    )
    record = restore_job_record(state, ctx)
    assert record.stage == "opened"
    assert record.stage != "matched"
    assert is_restored(record.match_result)
    assert is_restored(record.job_profile)
    payload = build_reasoner_payload(state, _registry())
    job = next(item for item in payload["jobs"] if item["job_key"] == "mock:j1")
    assert job["stage"] == "opened"
    assert job["match_result"]["provenance"] == "restored"
    assert job["match_result"]["scope"] == "restored_historical"
    assert job["job_profile"]["provenance"] == "restored"


def test_hydrate_without_jd_stays_listed_even_with_profile():
    state = new_agent_state(goal_input=GOAL, data_source="mock")
    ctx = JobContext(
        context_id="mock:j2",
        job_key="mock:j2",
        platform="mock",
        job_id="j2",
        job_url="https://mock.local/j2",
        title="PM",
        company="Co",
        job_listing={"platform": "mock", "job_id": "j2", "job_title": "PM"},
        job_profile={"analysis_status": "ok"},
        stage="listed",  # never opened — profile alone must not open/analyze
    )
    record = hydrate_job_record(state, ctx)
    assert record.stage == "listed"
    assert is_restored(record.job_profile)


def test_hydrate_prior_opened_without_jd_is_opened_not_analyzed():
    state = new_agent_state(goal_input=GOAL, data_source="mock")
    ctx = JobContext(
        context_id="mock:j3",
        job_key="mock:j3",
        platform="mock",
        job_id="j3",
        job_url="https://mock.local/j3",
        title="PM",
        company="Co",
        job_listing={"platform": "mock", "job_id": "j3", "job_title": "PM"},
        job_profile={"analysis_status": "ok"},
        stage="analyzed",
    )
    record = hydrate_job_record(state, ctx)
    assert record.stage == "opened"
    assert record.stage != "analyzed"
    assert is_restored(record.job_profile)


def test_human_gate_restore_stamps_provenance_and_observed_stage():
    state = new_agent_state(goal_input=GOAL, data_source="mock")
    state.jobs["mock:j1"] = JobRecord(
        job_key="mock:j1",
        stage="matched",
        listed_order=0,
        listed={"platform": "mock", "job_id": "j1", "job_title": "PM"},
        opened={"platform": "mock", "job_id": "j1", "job_title": "PM", "job_description": JD},
        job_profile={"analysis_status": "ok", "job_summary": "x"},
        match_result={"recommendation": "yes"},
    )
    snap = snapshot_world_for_human_gate(state)
    fresh = new_agent_state(goal_input=GOAL, data_source="mock")
    restore_world_from_human_gate_snapshot(fresh, snap)
    record = fresh.jobs["mock:j1"]
    assert record.stage == "opened"
    assert is_restored(record.match_result)
    assert is_restored(record.job_profile)
    payload = build_reasoner_payload(fresh, _registry())
    job = payload["jobs"][0]
    assert job["match_result"]["evidence_status"] == "restored_unverified"


def test_restored_match_does_not_block_rematch_gate():
    from agent.validate_action import _job_already_matched, _job_already_analyzed

    record = JobRecord(
        job_key="mock:j1",
        stage="opened",
        listed_order=0,
        listed={"platform": "mock", "job_id": "j1"},
        opened={"platform": "mock", "job_id": "j1", "job_description": JD},
        job_profile=stamp_restored({"analysis_status": "ok"}, source="persistence"),
        match_result=stamp_restored({"recommendation": "yes"}, source="persistence"),
    )
    assert _job_already_matched(record) is False
    assert _job_already_analyzed(record) is False


def test_binding_still_does_not_invent_jd():
    state = new_agent_state(goal_input=GOAL, data_source="mock")
    state.jobs["mock:empty"] = JobRecord(
        job_key="mock:empty",
        stage="listed",
        listed_order=0,
        listed={"platform": "mock", "job_id": "empty"},
    )
    missing = bind_tool_arguments(state, tool_action("analyze_job", {"job_key": "mock:empty"}))
    assert missing["ok"] is False
    assert missing["observation"]["kind"] == "missing_job_material"


def test_persistence_view_marks_historical_fields():
    state = new_agent_state(
        goal_input=GOAL,
        data_source="mock",
        session_context={
            "previous_goal": {"target_roles": ["PM"]},
            "last_recommended": [{"job_key": "mock:x"}],
            "previous_matching_context": {"note": "old"},
        },
    )
    payload = build_reasoner_payload(state, _registry())
    persistence = payload["context"]["persistence"]
    assert persistence["previous_goal"]["provenance"] == "restored"
    assert persistence["last_recommended"]["provenance"] == "restored"
    assert persistence["previous_matching_context"]["evidence_status"] == "restored_unverified"
