"""parse_user_goal: LLM + schema, or structured object validation. No keyword intent."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.mock_llm import MockLLMProvider, goal_response
from tools.parse_user_goal import parse_user_goal
from tools.registry import build_registry


def test_llm_parses_free_text_goal():
    llm = MockLLMProvider(goal_response())
    result = parse_user_goal(
        "帮我找深圳高级产品经理，重点金融科技/支付/复杂业务系统，薪资符合目标",
        llm_provider=llm,
    )
    assert result["analysis_status"] == "ok"
    assert result["target_roles"] == ["高级产品经理"]
    assert result["cities"] == ["深圳"]
    assert result["salary_min"] is None
    assert "支付" in result["focus_areas"]
    assert len(llm.calls) == 1
    assert "UserGoal" in llm.calls[0]["system"]


def test_structured_goal_skips_llm():
    llm = MockLLMProvider(goal_response())
    result = parse_user_goal(
        {
            "raw_text": "already structured",
            "target_roles": ["高级产品经理"],
            "cities": ["深圳"],
            "salary_min": None,
            "focus_areas": ["支付"],
            "exclude_companies": ["黑名单科技"],
            "platforms": ["mock"],
        },
        llm_provider=llm,
    )
    assert result["analysis_status"] == "ok"
    assert result["exclude_companies"] == ["黑名单科技"]
    assert llm.calls == []


def test_empty_goal_insufficient():
    llm = MockLLMProvider(goal_response())
    result = parse_user_goal("  ", llm_provider=llm)
    assert result["analysis_status"] == "insufficient_data"
    assert llm.calls == []


def test_llm_unavailable():
    import importlib

    mod = importlib.import_module("tools.parse_user_goal")
    original = mod.get_llm_provider
    mod.get_llm_provider = lambda: None
    try:
        result = parse_user_goal("找深圳产品经理", llm_provider=None)
    finally:
        mod.get_llm_provider = original
    assert result["analysis_status"] == "llm_unavailable"


def test_illegal_json_is_llm_error():
    llm = MockLLMProvider(error=RuntimeError("LLM response is not valid JSON"))
    result = parse_user_goal("找深圳产品经理", llm_provider=llm)
    assert result["analysis_status"] == "llm_error"


def test_missing_target_roles_insufficient():
    llm = MockLLMProvider(goal_response(target_roles=[]))
    result = parse_user_goal("随便看看", llm_provider=llm)
    assert result["analysis_status"] == "insufficient_data"


def test_registry_dispatches_parse_user_goal():
    registry = build_registry()
    result = registry.invoke(
        "parse_user_goal",
        {"goal": "帮我找深圳高级产品经理"},
        llm_provider=MockLLMProvider(goal_response()),
    )
    assert result["analysis_status"] == "ok"
    assert result["target_roles"] == ["高级产品经理"]
