"""Finish contract: placeholder reasons only — search is not a Program duty."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.state import new_agent_state
from agent.validate_action import validate_reasoner_action
from tools.registry import build_registry


def test_finish_rejects_placeholder_reason():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    checked = validate_reasoner_action(
        state,
        {"action_type": "finish", "reason": "..."},
        build_registry(data_source="mock"),
    )
    assert checked["ok"] is False
    assert "placeholder" in checked["error"]


def test_finish_allows_before_any_search_when_job_goal_active():
    """Reasoner may finish without searching — not a Program obligation to search."""
    state = new_agent_state(goal_input="找深圳产品经理", data_source="boss")
    state.goal = {"analysis_status": "ok", "target_roles": ["产品经理"], "cities": ["深圳"]}
    state.understanding = {"task_kind": "new_job_search"}
    state.understanding_status = "ok"
    checked = validate_reasoner_action(
        state,
        {"action_type": "finish", "reason": "本轮先记录目标，暂不搜索"},
        build_registry(data_source="boss"),
    )
    assert checked["ok"] is True


def test_finish_allows_goal_incomplete_without_search():
    state = new_agent_state(goal_input="你好", data_source="mock")
    checked = validate_reasoner_action(
        state,
        {
            "action_type": "finish",
            "reason": "还需要求职目标",
            "error_code": "GOAL_INCOMPLETE",
        },
        build_registry(data_source="mock"),
    )
    assert checked["ok"] is True


def test_finish_allows_after_search_even_if_empty_recommend():
    state = new_agent_state(goal_input="找工作", data_source="mock")
    state.goal = {"analysis_status": "ok", "target_roles": ["产品经理"]}
    state.understanding = {"task_kind": "new_job_search"}
    state.search.stats["searches"] = 1
    checked = validate_reasoner_action(
        state,
        {"action_type": "finish", "reason": "本轮配额用尽，暂无推荐"},
        build_registry(data_source="mock"),
    )
    assert checked["ok"] is True


def test_done_serialize_no_search_is_honest():
    from api.serialize import serialize_agent_state

    state = new_agent_state(goal_input="找工作", data_source="mock")
    state.status = "DONE"
    state.search.stop_reason = "reasoner_finished"
    state.output = {"recommended": [], "excluded": [], "stats": state.search.stats}
    payload = serialize_agent_state(state)
    assert payload["outcome_kind"] == "no_search"
    assert payload["result_title"] == "还没开始搜索"
    assert "没有开始搜索" in payload["message"]
    assert payload["recommended"] == []
