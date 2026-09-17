"""Context epistemic honesty: World projection, not invented Fact/Evidence."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.bind_arguments import bind_tool_arguments
from agent.decide import tool_action
from agent.loop import reduce
from agent.reasoner_context import build_reasoner_payload
from agent.state import JobRecord, new_agent_state
from tools.registry import build_registry

GOAL_TEXT = "帮我找深圳高级产品经理"
JD_TEXT = "负责支付产品规划，要求本科，5年经验。"


def _opened_state():
    state = new_agent_state(goal_input=GOAL_TEXT, resume="简历：做过支付。", data_source="mock")
    listed = {
        "platform": "mock",
        "job_id": "j1",
        "job_url": "https://mock.local/j1",
        "job_title": "高级产品经理",
        "company_name": "支付科技",
        "city": "深圳",
    }
    opened = {**listed, "job_description": JD_TEXT, "raw_actions": [{"text": "立即沟通"}]}
    record = JobRecord(
        job_key="mock:j1",
        stage="opened",
        listed_order=0,
        listed=listed,
        opened=opened,
        job_profile={"analysis_status": "ok", "job_summary": "支付产品经理"},
        match_result={"recommendation": "yes", "hard_requirements_met": True},
    )
    state.jobs[record.job_key] = record
    return state, record


def test_context_does_not_synthesize_jd_evidence():
    state, _ = _opened_state()
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    job = next(item for item in payload["jobs"] if item["job_key"] == "mock:j1")
    assert "jd_evidence" not in job
    assert not any(
        str(item.get("id") or "").startswith("jd:") for item in payload["evidence"]
    )
    assert JD_TEXT not in str(payload)


def test_context_job_fact_is_not_interpretation_and_has_no_body():
    state, _ = _opened_state()
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    job = next(item for item in payload["jobs"] if item["job_key"] == "mock:j1")
    assert job["fact"]["layer"] == "fact"
    assert job["fact"]["has_job_description"] is True
    assert job["fact"]["jd_char_count"] == len(JD_TEXT)
    assert "job_description" not in job["fact"]
    assert "content" not in job["fact"]
    assert job["listed_card_facts"]["layer"] == "fact"
    assert job["listed_card_facts"]["job_title"] == "高级产品经理"
    assert job["job_profile"]["layer"] == "interpretation"
    assert job["job_profile"]["kind"] == "interpretation_projection"
    assert job["match_result"]["layer"] == "interpretation"
    assert job["match_result"]["kind"] == "interpretation"


def test_context_does_not_invent_derived_from_for_profiles():
    state, record = _opened_state()
    # World profile has no derived_from — Context must not invent jd: links.
    assert "derived_from" not in (record.job_profile or {})
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    job = next(item for item in payload["jobs"] if item["job_key"] == "mock:j1")
    assert job["job_profile"]["derived_from"] == []
    assert "jd:mock:j1" not in job["job_profile"]["derived_from"]
    profile = payload["candidate_profile"]
    assert profile["kind"] == "interpretation_projection"
    assert isinstance(profile["derived_from"], list)


def test_missing_jd_stays_missing_in_context_binding_does_not_invent():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    listed = {
        "platform": "mock",
        "job_id": "empty",
        "job_url": "https://mock.local/empty",
        "job_title": "产品经理",
        "company_name": "空JD公司",
        "city": "深圳",
    }
    state.jobs["mock:empty"] = JobRecord(
        job_key="mock:empty",
        stage="listed",
        listed_order=0,
        listed=listed,
        opened=None,
    )
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    job = next(item for item in payload["jobs"] if item["job_key"] == "mock:empty")
    assert job["fact"]["has_job_description"] is False
    assert job["fact"]["jd_char_count"] == 0
    assert "job_description" not in job["fact"]
    # Binding reads World as-is — may use title, but never invents a JD body in Context.
    bound = bind_tool_arguments(state, tool_action("analyze_job", {"job_key": "mock:empty"}))
    assert bound["ok"] is True
    assert not (bound["arguments"]["job"].get("job_description") or "").strip()

    barren = {
        "platform": "mock",
        "job_id": "barren",
        "job_url": "https://mock.local/barren",
    }
    state.jobs["mock:barren"] = JobRecord(
        job_key="mock:barren",
        stage="listed",
        listed_order=1,
        listed=barren,
        opened=None,
    )
    missing = bind_tool_arguments(state, tool_action("analyze_job", {"job_key": "mock:barren"}))
    assert missing["ok"] is False
    assert missing["observation"]["kind"] == "missing_job_material"


def test_binding_still_loads_world_jd_when_reasoner_selects_job():
    state, _ = _opened_state()
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert JD_TEXT not in str(payload["jobs"])
    bound = bind_tool_arguments(state, tool_action("analyze_job", {"job_key": "mock:j1"}))
    assert bound["ok"] is True
    assert bound["arguments"]["job"]["job_description"] == JD_TEXT


def test_goal_and_claims_are_labeled_interpretation_or_claim():
    state, _ = _opened_state()
    state.goal = {
        "analysis_status": "ok",
        "target_roles": ["高级产品经理"],
        "cities": ["深圳"],
    }
    state.claims = [
        {
            "id": "cl-1",
            "kind": "skill",
            "statement": "做过支付",
            "derived_from": [],
            "evidence_status": "unverified",
        }
    ]
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert payload["goal"]["layer"] == "interpretation"
    assert payload["goal"]["kind"] == "interpretation"
    assert payload["claims"][0]["layer"] == "claim"
    assert payload["context"]["understanding"]["layer"] == "interpretation"


def test_user_turn_observation_does_not_dump_full_message_as_fact():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    observation = payload["observation"]
    assert observation["layer"] == "observation"
    assert observation.get("message") != GOAL_TEXT
    assert observation.get("message_excerpt")
    assert len(observation["message_excerpt"]) <= 200
    assert payload["context"]["raw_user_message_excerpt"]
    assert "raw_user_message" not in payload["context"] or payload["context"].get("raw_user_message") is None
