"""plan_search: LLM JSON + schema. Fail closed. No synonym/keyword fallback."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.mock_llm import MockLLMProvider, plan_search_response
from tools.plan_search import plan_search
from tools.registry import build_registry


GOAL = {
    "target_roles": ["高级产品经理"],
    "cities": ["深圳"],
    "focus_areas": ["支付"],
}


def test_llm_plan_search_ok():
    llm = MockLLMProvider(plan_search_response())
    result = plan_search(
        {"goal": GOAL, "used_keywords": ["高级产品经理"], "cities": ["深圳"]},
        llm_provider=llm,
    )
    assert result["analysis_status"] == "ok"
    assert result["keyword"] == "产品总监"
    assert result["city"] == "深圳"
    assert len(llm.calls) == 1
    assert "SearchPlan" in llm.calls[0]["system"]


def test_duplicate_keyword_is_analysis_failed():
    llm = MockLLMProvider(plan_search_response(keyword="高级产品经理"))
    result = plan_search(
        {"goal": GOAL, "used_keywords": ["高级产品经理"]},
        llm_provider=llm,
    )
    assert result["analysis_status"] == "analysis_failed"
    assert result["keyword"] is None


def test_illegal_json_is_analysis_failed():
    llm = MockLLMProvider({"oops": True})
    result = plan_search({"goal": GOAL, "used_keywords": []}, llm_provider=llm)
    assert result["analysis_status"] == "analysis_failed"
    assert result["keyword"] is None


def test_llm_error_status():
    llm = MockLLMProvider(error=RuntimeError("LLM response is not valid JSON"))
    result = plan_search({"goal": GOAL}, llm_provider=llm)
    assert result["analysis_status"] == "llm_error"
    assert result["keyword"] is None


def test_llm_unavailable():
    import importlib

    mod = importlib.import_module("tools.plan_search")
    original = mod.get_llm_provider
    mod.get_llm_provider = lambda: None
    try:
        result = plan_search({"goal": GOAL}, llm_provider=None)
    finally:
        mod.get_llm_provider = original
    assert result["analysis_status"] == "llm_unavailable"


def test_registry_invokes_plan_search():
    registry = build_registry()
    result = registry.invoke(
        "plan_search",
        {"goal": GOAL, "used_keywords": ["高级产品经理"]},
        llm_provider=MockLLMProvider(plan_search_response()),
    )
    assert result["analysis_status"] == "ok"
    assert registry.invocations == ["plan_search"]
