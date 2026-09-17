"""Unified Job Actions dispatch to adapters. Not an Agent. No CSS in Reasoner output."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from platforms.job_actions import (
    inspect_application_state,
    inspect_job,
    open_job,
    search_jobs,
)
from tools.registry import available_tool_contracts, build_registry


ACTIONS_SRC = ROOT_DIR / "platforms" / "job_actions.py"


def test_reasoner_contracts_are_platform_agnostic():
    for source in ("mock", "boss", "liepin", "job51"):
        names = {item["name"] for item in available_tool_contracts(build_registry(data_source=source))}
        assert {
            "search_jobs",
            "open_job",
            "inspect_job",
            "inspect_application_state",
            "execute_action",
        }.issubset(names)
        assert "search_boss_jobs" not in names
        assert "search_liepin_jobs" not in names
        assert "search_51job_jobs" not in names
        assert "mock_search_jobs" not in names
        assert "execute_job_action" not in names


def test_search_jobs_dispatches_by_data_source():
    mock = search_jobs({"keyword": "高级产品经理", "city": "深圳", "limit": 5}, data_source="mock")
    assert mock["platform"] == "mock"
    assert mock["jobs"]
    assert all("job_description" not in item or item.get("job_description") is None for item in mock["jobs"][:1])


def test_inspect_job_returns_page_facts_without_interpret():
    opened = inspect_job({"job_id": "mock-direct"}, data_source="mock")
    assert opened["job_id"] == "mock-direct"
    assert opened["job_description"]
    assert opened["raw_actions"]
    assert "action_analysis" not in opened
    assert "semantic_intent" not in opened


def test_inspect_application_state_is_mechanical_only():
    result = inspect_application_state(
        {
            "job": {
                "platform": "mock",
                "job_id": "mock-applied",
                "job_url": "https://mock.local/job/mock-applied",
                "raw_actions": [
                    {"text": "Continue the existing recruiter conversation", "dom_context": "button#x"},
                ],
            },
            "already_applied": ["mock:mock-applied", "other"],
            "application_history": [
                {"job_key": "mock:mock-applied", "result": "applied", "evidence": "tracker"},
            ],
        },
        data_source="mock",
    )
    assert result["job_key"] == "mock:mock-applied"
    assert result["already_applied_matches"] == ["mock:mock-applied"]
    assert result["history_records"][0]["result"] == "applied"
    assert result["page_action_texts"] == ["Continue the existing recruiter conversation"]
    assert "application_evidence" not in result
    assert "recommendation" not in result
    joined = " ".join(str(value) for value in result.values())
    assert "button#x" not in joined
    assert "dom_context" not in joined


def test_open_job_and_inspect_share_adapter_extract():
    opened = open_job({"job_id": "mock-direct"}, data_source="mock")
    inspected = inspect_job({"job_id": "mock-direct"}, data_source="mock")
    assert opened["job_id"] == inspected["job_id"]
    assert opened["job_title"] == inspected["job_title"]


def test_job_actions_module_is_not_an_agent():
    tree = ast.parse(ACTIONS_SRC.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name)
    assert "interpret_job_actions" not in names
    assert "analyze_job" not in names
    assert "match_job" not in names
    assert "reason_next_action" not in names
    assert "Heuristic" not in names
    source = ACTIONS_SRC.read_text(encoding="utf-8")
    assert "querySelector" not in source
    assert "page.click" not in source
