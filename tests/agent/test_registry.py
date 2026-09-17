"""Tool registry for the Agent Loop. Does not register browse_boss_jobs."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.mock_llm import MockLLMProvider, candidate_profile_response
from tools.registry import (
    FORBIDDEN_LOOP_TOOLS,
    REASONER_HIDDEN_TOOLS,
    SUPPORTED_DATA_SOURCES,
    available_tool_contracts,
    build_registry,
)

UNIFIED_JOB_ACTIONS = {
    "search_jobs",
    "open_job",
    "inspect_job",
    "inspect_application_state",
    "execute_action",
}


def test_registry_has_semantic_and_unified_job_actions():
    registry = build_registry(data_source="mock")
    names = {item["name"] for item in registry.specs()}
    assert names == {
        "parse_user_goal",
        "reason_next_action",
        "understand_user_input",
        "analyze_candidate",
        "analyze_job",
        "interpret_job_actions",
        "match_job",
        "plan_search",
        "search_jobs",
        "open_job",
        "inspect_job",
        "inspect_application_state",
        "execute_action",
        "execute_job_action",
        "bind_task",
        "skip_job",
        "stop_task",
        "hydrate_job_reference",
    }
    assert UNIFIED_JOB_ACTIONS.issubset(names)
    assert "browse_boss_jobs" not in names
    assert "decide_next_action" not in names
    assert "mock_search_jobs" not in names
    assert "search_boss_jobs" not in names
    assert not registry.has("decide_next_action")
    assert "browse_boss_jobs" in FORBIDDEN_LOOP_TOOLS
    assert "decide_next_action" in REASONER_HIDDEN_TOOLS
    assert "execute_job_action" in REASONER_HIDDEN_TOOLS


def test_registry_boss_source_uses_same_job_actions():
    registry = build_registry(data_source="boss")
    names = {item["name"] for item in registry.specs()}
    assert UNIFIED_JOB_ACTIONS.issubset(names)
    assert "plan_search" in names
    assert "search_boss_jobs" not in names
    assert "open_boss_job" not in names
    assert "mock_search_jobs" not in names
    assert "browse_boss_jobs" not in names
    assert registry.has("interpret_job_actions")
    assert registry.has("analyze_job")
    assert registry.has("match_job")


def test_registry_invokes_analyze_candidate():
    registry = build_registry()
    resume = (ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt").read_text(encoding="utf-8")
    result = registry.invoke(
        "analyze_candidate",
        {"resume": resume},
        llm_provider=MockLLMProvider(candidate_profile_response()),
    )
    assert result["analysis_status"] == "ok"
    assert registry.invocations == ["analyze_candidate"]


def test_registry_unknown_tool():
    registry = build_registry()
    try:
        registry.invoke("browse_boss_jobs", {})
    except ValueError as exc:
        assert "未知 Tool" in str(exc)
        return
    raise AssertionError("browse_boss_jobs must not be registered on the Agent registry")


def test_registry_liepin_source_uses_same_job_actions():
    registry = build_registry(data_source="liepin")
    names = {item["name"] for item in registry.specs()}
    assert UNIFIED_JOB_ACTIONS.issubset(names)
    assert "plan_search" in names
    assert "search_liepin_jobs" not in names
    assert "search_boss_jobs" not in names
    assert "mock_search_jobs" not in names
    assert "browse_boss_jobs" not in names
    assert registry.has("interpret_job_actions")
    assert registry.has("analyze_job")
    assert registry.has("match_job")


def test_registry_job51_source_uses_same_job_actions():
    registry = build_registry(data_source="job51")
    names = {item["name"] for item in registry.specs()}
    assert UNIFIED_JOB_ACTIONS.issubset(names)
    assert "plan_search" in names
    assert "search_51job_jobs" not in names
    assert "search_boss_jobs" not in names
    assert "search_liepin_jobs" not in names
    assert "mock_search_jobs" not in names
    assert "browse_boss_jobs" not in names
    assert registry.has("interpret_job_actions")
    assert registry.has("analyze_job")
    assert registry.has("match_job")


def test_registry_rejects_unknown_data_source():
    try:
        build_registry(data_source="unknown_site")
    except ValueError as exc:
        assert "unsupported data_source" in str(exc)
        assert "unknown_site" not in SUPPORTED_DATA_SOURCES
        assert SUPPORTED_DATA_SOURCES == ("mock", "boss", "liepin", "job51")
        return
    raise AssertionError("unknown_site must not be a data_source")


def test_mock_search_and_open():
    registry = build_registry()
    search = registry.invoke("search_jobs", {"keyword": "高级产品经理", "city": "深圳", "limit": 30})
    ids = [job["job_id"] for job in search["jobs"]]
    assert ids[0] == "mock-direct"
    assert "mock-blocked" in ids
    opened = registry.invoke("open_job", {"job_id": "mock-direct"})
    assert opened["job_description"]
    assert opened["raw_actions"]
    assert opened["job_id"] == "mock-direct"


def test_available_tool_contracts_hide_reasoner_and_expose_unified_actions():
    registry = build_registry(data_source="mock")
    contracts = available_tool_contracts(registry)
    names = {item["name"] for item in contracts}
    assert "reason_next_action" not in names
    assert "parse_user_goal" not in names
    assert "decide_next_action" not in names
    assert "browse_boss_jobs" not in names
    assert "execute_job_action" not in names
    assert "search_boss_jobs" not in names
    assert "mock_search_jobs" not in names
    assert "decide_next_action" in REASONER_HIDDEN_TOOLS
    assert UNIFIED_JOB_ACTIONS.issubset(names)
    assert {
        "understand_user_input",
        "analyze_candidate",
        "analyze_job",
        "interpret_job_actions",
        "match_job",
        "plan_search",
        "bind_task",
        "skip_job",
        "stop_task",
        "hydrate_job_reference",
    }.issubset(names)
    search = next(item for item in contracts if item["name"] == "search_jobs")
    assert search["parameters"]["required"] == ["keyword"]
    assert search["allowed_in_loop"] is True
