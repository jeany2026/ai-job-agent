"""SearchSession + search_jobs mode=fresh|continue contract tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import tool_action
from agent.loop import reduce
from agent.state import new_agent_state
from browser.search_executor import fetch_with_explore
from job.common_schema import job_key as make_job_key
from platforms.job_actions import SEARCH_JOBS_SPEC, search_jobs
from platforms.mock.jobs import mock_search_jobs, ordered_search_catalog
from platforms.mock.search_executor import reset_mock_search_buffers
from platforms.search_contract import (
    new_search_session,
    record_exposed,
    session_from_dict,
)
from tools.registry import build_registry

PAD = 20


def setup_function(_fn=None):
    reset_mock_search_buffers()


def _search(**kwargs):
    kwargs.setdefault("pad_extra", PAD)
    return mock_search_jobs(**kwargs)


def test_search_jobs_spec_exposes_mode():
    props = SEARCH_JOBS_SPEC["parameters"]["properties"]
    assert "mode" in props
    assert set(props["mode"]["enum"]) == {"fresh", "continue"}


def test_fresh_creates_search_session():
    result = _search(keyword="高级产品经理", city="深圳", limit=8, mode="fresh")
    assert result["fetch_status"] == "ok"
    assert result["mode"] == "fresh"
    assert len(result["jobs"]) == 8
    session = session_from_dict(result["search_session"])
    assert session is not None
    assert session.status == "active"
    assert len(session.exposed_job_keys) == 8
    assert result["can_continue"] is True


def test_continue_returns_next_unexposed_batch():
    first = _search(keyword="高级产品经理", city="深圳", limit=8, mode="fresh")
    keys1 = [make_job_key(job) for job in first["jobs"]]
    second = _search(
        keyword="高级产品经理",
        city="深圳",
        limit=8,
        mode="continue",
        search_session=first["search_session"],
    )
    keys2 = [make_job_key(job) for job in second["jobs"]]
    assert len(keys2) == 8
    assert set(keys1).isdisjoint(set(keys2))
    session = session_from_dict(second["search_session"])
    assert len(session.exposed_job_keys) == 16


def test_preload_does_not_mark_unreturned_as_exposed():
    """Page buffer may hold A-Z; only returned A-H become exposed."""
    catalog = ordered_search_catalog("高级产品经理", "深圳", pad_extra=PAD)
    assert len(catalog) >= 16
    first = _search(
        keyword="高级产品经理",
        city="深圳",
        limit=8,
        mode="fresh",
        page_window=30,
    )
    session = session_from_dict(first["search_session"])
    assert len(session.exposed_job_keys) == 8
    second = _search(
        keyword="高级产品经理",
        city="深圳",
        limit=8,
        mode="continue",
        search_session=session,
        page_window=30,
    )
    assert len(second["jobs"]) == 8
    assert set(session_from_dict(first["search_session"]).exposed_job_keys).isdisjoint(
        {make_job_key(job) for job in second["jobs"]}
    )


def test_continue_never_rereturns_exposed():
    first = _search(keyword="高级产品经理", city="深圳", limit=8, mode="fresh")
    exposed = set(session_from_dict(first["search_session"]).exposed_job_keys)
    nxt = first
    for _ in range(3):
        nxt = _search(
            keyword="高级产品经理",
            city="深圳",
            limit=8,
            mode="continue",
            search_session=nxt["search_session"],
        )
        if not nxt["jobs"]:
            break
        got = {make_job_key(job) for job in nxt["jobs"]}
        assert got.isdisjoint(exposed)
        exposed |= got


def test_unexposed_on_page_does_not_require_explore():
    catalog = ordered_search_catalog("高级产品经理", "深圳", pad_extra=PAD)[:20]
    session = new_search_session(platform="mock", keyword="高级产品经理", city="深圳")
    explore_calls = {"n": 0}

    def read_available():
        return catalog

    def explore_once():
        explore_calls["n"] += 1
        return catalog

    result = fetch_with_explore(
        platform="mock",
        keyword="高级产品经理",
        city="深圳",
        mode="fresh",
        limit=8,
        session=session,
        read_available=read_available,
        explore_once=explore_once,
        execution_budget=6,
    )
    assert len(result["jobs"]) == 8
    assert explore_calls["n"] == 0


def test_explore_runs_when_page_has_no_unexposed():
    page = [{"platform": "mock", "job_id": "only", "job_url": "https://mock/only", "job_title": "A"}]
    more = [
        {"platform": "mock", "job_id": "only", "job_url": "https://mock/only", "job_title": "A"},
        {"platform": "mock", "job_id": "two", "job_url": "https://mock/two", "job_title": "B"},
    ]
    session = new_search_session(platform="mock", keyword="x", city="深圳")
    record_exposed(session, page)
    state = {"buf": page}

    def read_available():
        return list(state["buf"])

    def explore_once():
        state["buf"] = more
        return more

    result = fetch_with_explore(
        platform="mock",
        keyword="x",
        city="深圳",
        mode="continue",
        limit=1,
        session=session,
        read_available=read_available,
        explore_once=explore_once,
        execution_budget=3,
    )
    assert [job["job_id"] for job in result["jobs"]] == ["two"]


def test_continue_without_session_is_no_active_session():
    result = _search(keyword="高级产品经理", city="深圳", limit=8, mode="continue", search_session=None)
    assert result["fetch_status"] == "no_active_session"
    assert result["jobs"] == []
    assert result["can_continue"] is False


def test_continue_query_mismatch():
    first = _search(keyword="高级产品经理", city="深圳", limit=8, mode="fresh")
    result = _search(
        keyword="产品总监",
        city="深圳",
        limit=8,
        mode="continue",
        search_session=first["search_session"],
    )
    assert result["fetch_status"] == "query_mismatch"
    assert result["jobs"] == []


def test_resource_limited_not_exhausted():
    result = _search(
        keyword="高级产品经理",
        city="深圳",
        limit=10,
        mode="fresh",
        page_window=4,
        execution_budget=0,
    )
    assert len(result["jobs"]) == 4
    assert result["fetch_status"] == "resource_limited"
    assert result["can_continue"] is True
    session = session_from_dict(result["search_session"])
    assert session.status == "resource_limited"


def test_exhausted_when_catalog_fully_exposed():
    first = mock_search_jobs("产品总监", "深圳", limit=30, mode="fresh", page_window=50, pad_extra=0)
    assert first["fetch_status"] in {"ok", "exhausted"}
    session = first["search_session"]
    nxt = mock_search_jobs(
        "产品总监",
        "深圳",
        limit=8,
        mode="continue",
        search_session=session,
        page_window=50,
        pad_extra=0,
    )
    assert nxt["jobs"] == []
    assert nxt["fetch_status"] == "exhausted"
    assert nxt["can_continue"] is False


def test_search_plan_not_exhausted_merely_because_search_ran():
    state = new_agent_state(goal_input="找深圳产品经理", data_source="mock")
    state.status = "RUNNING"
    result = _search(keyword="高级产品经理", city="深圳", limit=8, mode="fresh")
    assert result["fetch_status"] == "ok"
    action = tool_action(
        "search_jobs",
        {"keyword": "高级产品经理", "city": "深圳", "limit": 8, "mode": "fresh"},
    )
    state = reduce(state, action, result)
    plan = state.search.plans[0]
    assert plan.exhausted is False
    assert state.search.exhausted == []
    assert state.last_raw_observation["fetch_status"] == "ok"
    assert state.search.session is not None


def test_reduce_marks_plan_exhausted_only_on_fetch_exhausted():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    state.status = "RUNNING"
    result = {
        "platform": "mock",
        "jobs": [],
        "mode": "continue",
        "newly_ingested": 0,
        "newly_exposed": 0,
        "duplicates": 0,
        "fetch_status": "exhausted",
        "can_continue": False,
        "query": {"keyword": "产品总监", "city": "深圳", "mode": "continue", "limit": 8},
        "search_session": {
            "session_id": "ss-x",
            "platform": "mock",
            "query_fingerprint": "mock|产品总监|深圳",
            "keyword": "产品总监",
            "city": "深圳",
            "exposed_job_keys": ["mock:mock-director"],
            "status": "exhausted",
        },
    }
    action = tool_action(
        "search_jobs",
        {"keyword": "产品总监", "city": "深圳", "limit": 8, "mode": "continue"},
    )
    state = reduce(state, action, result)
    assert state.search.plans[0].exhausted is True
    assert state.search.exhausted == ["plan-1"]


def test_unified_contract_via_job_actions_dispatch():
    registry = build_registry(data_source="mock")
    # Direct mock path with pad for continue coverage.
    first = _search(keyword="高级产品经理", city="深圳", limit=8, mode="fresh")
    assert first["fetch_status"] == "ok"
    second = registry.invoke(
        "search_jobs",
        {"keyword": "高级产品经理", "city": "深圳", "limit": 8, "mode": "continue"},
        search_session=first["search_session"],
    )
    # Without pad_extra via registry, continue may exhaust quickly — still must not re-expose.
    if second["jobs"]:
        assert {make_job_key(j) for j in first["jobs"]}.isdisjoint(
            {make_job_key(j) for j in second["jobs"]}
        )
    assert second["fetch_status"] in {"ok", "exhausted", "resource_limited"}


def test_search_does_not_open_analyze_or_match():
    with patch("job.analyze_job.analyze_job") as analyze:
        with patch("matching.match_job.match_job") as match:
            result = search_jobs(
                {"keyword": "高级产品经理", "city": "深圳", "limit": 4, "mode": "fresh"},
                data_source="mock",
            )
            assert result["jobs"]
            analyze.assert_not_called()
            match.assert_not_called()
            for job in result["jobs"]:
                assert job.get("job_description") in (None, "")
                assert "action_analysis" not in job


def test_platforms_share_fetch_status_field():
    result = _search(keyword="高级产品经理", city="深圳", limit=2, mode="fresh")
    for key in ("query", "mode", "jobs", "newly_ingested", "fetch_status", "can_continue"):
        assert key in result


def test_product_manager_keyword_returns_multiple_jobs():
    catalog = ordered_search_catalog("产品经理", "深圳", pad_extra=0)
    ids = [job["job_id"] for job in catalog]
    assert len(set(ids)) >= 5
    fresh = _search(keyword="产品经理", city="深圳", limit=3, mode="fresh")
    assert len(fresh["jobs"]) == 3
    assert fresh["fetch_status"] in {"ok", "resource_limited"}
    assert fresh["can_continue"] is True


def test_product_manager_fresh_continue_exposes_disjoint_then_exhausted():
    first = _search(keyword="产品经理", city="深圳", limit=3, mode="fresh")
    keys1 = {make_job_key(job) for job in first["jobs"]}
    second = _search(
        keyword="产品经理",
        city="深圳",
        limit=3,
        mode="continue",
        search_session=first["search_session"],
    )
    keys2 = {make_job_key(job) for job in second["jobs"]}
    assert keys2
    assert keys1.isdisjoint(keys2)
    nxt = second
    for _ in range(16):
        nxt = _search(
            keyword="产品经理",
            city="深圳",
            limit=8,
            mode="continue",
            search_session=nxt["search_session"],
        )
        if nxt["fetch_status"] == "exhausted" and not nxt["jobs"]:
            break
    assert nxt["fetch_status"] == "exhausted"
    assert nxt["jobs"] == []
    assert nxt["can_continue"] is False
