"""Offline checks for BOSS parsers and official tool specs. Not browse_boss_jobs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from candidate.analyze_candidate import ANALYZE_CANDIDATE_SPEC, analyze_candidate
from job.analyze_job import ANALYZE_JOB_SPEC, analyze_job
from platforms.boss.jobs import OPEN_BOSS_JOB_SPEC, SEARCH_BOSS_JOBS_SPEC
from tests.mock_llm import MockLLMProvider, intents_response, job_profile_response
from tools.boss_job_search import parse_job_id, search_url, split_detail_text, vue_item_to_job
from tools.interpret_job_actions import INTERPRET_JOB_ACTIONS_SPEC, interpret_job_actions
from tools.registry import build_registry
from understanding.understand_user_input import UNDERSTAND_USER_INPUT_SPEC


def test_official_tool_specs():
    assert SEARCH_BOSS_JOBS_SPEC["name"] == "search_boss_jobs"
    assert "keyword" in SEARCH_BOSS_JOBS_SPEC["parameters"]["required"]
    assert OPEN_BOSS_JOB_SPEC["name"] == "open_boss_job"
    assert ANALYZE_JOB_SPEC["name"] == "analyze_job"
    assert "job_key" in ANALYZE_JOB_SPEC["parameters"]["required"]
    assert ANALYZE_JOB_SPEC["parameters"]["properties"]["job_key"]["type"] == "string"
    assert INTERPRET_JOB_ACTIONS_SPEC["name"] == "interpret_job_actions"
    assert "job_key" in INTERPRET_JOB_ACTIONS_SPEC["parameters"]["required"]
    assert INTERPRET_JOB_ACTIONS_SPEC["parameters"]["properties"]["job_key"]["type"] == "string"
    assert ANALYZE_CANDIDATE_SPEC["name"] == "analyze_candidate"
    assert "resume" not in (ANALYZE_CANDIDATE_SPEC["parameters"].get("required") or [])
    assert "intake_ids" in ANALYZE_CANDIDATE_SPEC["parameters"]["properties"]
    assert "evidence_ids" in ANALYZE_CANDIDATE_SPEC["parameters"]["properties"]
    assert ANALYZE_CANDIDATE_SPEC["parameters"]["properties"]["resume"]["type"] == "string"
    assert UNDERSTAND_USER_INPUT_SPEC["name"] == "understand_user_input"
    names = {item["name"] for item in build_registry(data_source="boss").specs()}
    assert "search_jobs" in names
    assert "open_job" in names
    assert "inspect_job" in names
    assert "inspect_application_state" in names
    assert "execute_action" in names
    assert "search_boss_jobs" not in names
    assert "open_boss_job" not in names
    assert "analyze_job" in names
    assert "interpret_job_actions" in names
    assert "analyze_candidate" in names
    assert "understand_user_input" in names
    assert "browse_boss_jobs" not in names


def test_search_url_shenzhen():
    url = search_url("高级产品经理", "深圳")
    assert "query=" in url
    assert "city=101280600" in url


def test_parse_job_id():
    url = "https://www.zhipin.com/job_detail/c9e8afdf491d62221HB72tm-ElRU.html"
    assert parse_job_id(url) == "c9e8afdf491d62221HB72tm-ElRU"


def test_vue_item_to_job_nulls():
    job = vue_item_to_job({"job_id": "abc", "job_title": "高级产品经理"}, fallback_city="深圳")
    assert job["platform"] == "boss"
    assert job["job_id"] == "abc"
    assert job["job_url"].endswith("/job_detail/abc.html")
    assert job["job_description"] is None
    assert job["city"] == "深圳"


def test_split_detail_text():
    body = "岗位职责：做产品规划。任职要求：三年以上。"
    desc, req = split_detail_text(body)
    assert desc is not None and "产品规划" in desc
    assert req is not None and "三年" in req


def test_analyze_job_via_function_and_registry():
    job = json.loads((ROOT_DIR / "tests" / "fixtures" / "jobs" / "sample_pm.json").read_text(encoding="utf-8"))
    job["job_id"] = "schema-1"
    direct = analyze_job(job, llm_provider=MockLLMProvider(job_profile_response()))
    via_registry = build_registry().invoke(
        "analyze_job",
        {"job": job},
        llm_provider=MockLLMProvider(job_profile_response()),
    )
    assert direct["analysis_status"] == via_registry["analysis_status"] == "ok"
    assert direct["job_id"] == via_registry["job_id"] == "schema-1"


def test_registry_unknown_tool():
    try:
        build_registry().invoke("not_a_real_tool", {})
    except ValueError as exc:
        assert "未知 Tool" in str(exc)
        return
    raise AssertionError("registry should reject unknown names")


def test_interpret_job_actions_via_function():
    payload = {
        "job_id": "schema-actions",
        "actions": [
            {"text": "Submit your resume for this role"},
            {"text": "Start a new chat with the recruiter"},
        ],
    }
    result = interpret_job_actions(
        payload,
        llm_provider=MockLLMProvider(intents_response("apply_resume", "start_contact")),
    )
    assert result["analysis_status"] == "ok"
    assert result["inferred_context"]["application_evidence"] == "not_applied"


if __name__ == "__main__":
    test_official_tool_specs()
    test_search_url_shenzhen()
    test_parse_job_id()
    test_vue_item_to_job_nulls()
    test_split_detail_text()
    test_analyze_job_via_function_and_registry()
    test_interpret_job_actions_via_function()
    test_registry_unknown_tool()
    print("offline tool tests passed")
