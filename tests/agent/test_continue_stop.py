"""Phase 6: continue search when recommendations are short; stop at quota. No heuristic keywords."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.orchestrator import run_agent
from agent.state import JobRecord, new_agent_state
from rules.stop import STOP_MIN_RECOMMEND, STOP_OPEN_QUOTA, should_continue_search
from tests.mock_llm import AgentRoutingLLM, default_reasoner_decision
from tools.registry import build_registry

RESUME_PATH = ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")

TWO_ROLE_GOAL = {
    "target_roles": ["高级产品经理", "产品总监"],
    "cities": ["深圳"],
    "salary_min": None,
    "focus_areas": ["金融科技", "支付", "复杂业务系统"],
    "exclude_companies": [],
    "platforms": [],
}


class AlwaysRejectLLM(AgentRoutingLLM):
    """Test double: never surface. One legal exploration strategy, not production."""

    def complete_json(self, *, system: str, user: str) -> dict:
        if "你是全局 Agent Reasoner" in system:
            self.calls.append({"system": system, "user": user})
            return default_reasoner_decision(user, after_match="continue")
        return super().complete_json(system=system, user=user)

RESUME_PATH = ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")

TWO_ROLE_GOAL = {
    "target_roles": ["高级产品经理", "产品总监"],
    "cities": ["深圳"],
    "salary_min": None,
    "focus_areas": ["金融科技", "支付", "复杂业务系统"],
    "exclude_companies": [],
    "platforms": [],
}


def _run(*, goal, constraints, llm=None, registry=None):
    return run_agent(
        resume=SAMPLE_RESUME,
        goal=goal,
        constraints={"blacklist": ["黑名单科技"], **constraints},
        llm_provider=llm or AlwaysRejectLLM(),
        registry=registry or build_registry(data_source="mock"),
        data_source="mock",
    )


def test_insufficient_recommendations_starts_second_plan():
    registry = build_registry(data_source="mock")
    state = _run(goal=TWO_ROLE_GOAL, constraints={"min_recommend": 3}, registry=registry)
    assert state.session.status == "DONE"
    assert [plan.keyword for plan in state.search.plans] == ["高级产品经理", "产品总监"]
    # SearchPlan.exhausted is result-set exhaustion, not "search was called once".
    assert "mock:mock-director" in state.jobs
    assert state.search.stats["searches"] == 2
    assert registry.invocations.count("search_jobs") == 2


def test_quota_ceiling_stops_without_second_plan():
    registry = build_registry(data_source="mock")
    state = _run(
        goal=TWO_ROLE_GOAL,
        constraints={"min_recommend": 5, "max_open_jd": 2},
        registry=registry,
    )
    assert state.session.status == "DONE"
    assert len(state.search.plans) == 1
    assert state.search.stats["searches"] == 1
    assert state.search.stats["opened"] == 2
    assert state.search.stats["recommended"] < 5
    assert registry.invocations.count("search_jobs") == 1
    assert state.search.stop_reason == STOP_OPEN_QUOTA
    # Unified mock catalog may list director on first search; second plan must not run.
    assert all(plan.keyword != "产品总监" for plan in state.search.plans[1:])


def test_llm_plan_search_generates_next_keyword():
    registry = build_registry(data_source="mock")
    llm = AlwaysRejectLLM()
    state = _run(
        goal="帮我找深圳高级产品经理，重点金融科技/支付/复杂业务系统，薪资符合目标",
        constraints={"min_recommend": 3},
        llm=llm,
        registry=registry,
    )
    assert state.session.status == "DONE"
    assert [plan.keyword for plan in state.search.plans] == ["高级产品经理", "产品总监"]
    assert "plan_search" in registry.invocations
    assert any("SearchPlan" in call["system"] for call in llm.calls)
    assert "mock:mock-director" in state.jobs


def test_illegal_plan_search_json_does_not_guess_keyword():
    class BadPlanLLM(AlwaysRejectLLM):
        def complete_json(self, *, system: str, user: str) -> dict:
            if "SearchPlan" in system:
                self.calls.append({"system": system, "user": user})
                return {"oops": True}
            return super().complete_json(system=system, user=user)

    registry = build_registry(data_source="mock")
    state = _run(
        goal="帮我找深圳高级产品经理，重点金融科技/支付/复杂业务系统，薪资符合目标",
        constraints={"min_recommend": 3},
        llm=BadPlanLLM(),
        registry=registry,
    )
    assert state.session.status == "DONE"
    assert [plan.keyword for plan in state.search.plans] == ["高级产品经理"]
    assert state.search.stop_reason == "plan_search_failed"
    assert any(item.get("kind") == "plan_search" for item in state.errors)
    assert "plan_search" in registry.invocations
    # No second keyword invented by Program; director may already be listed from first search.

def test_should_continue_search_is_isolated_quota_math_not_a_program_gate():
    """The helper may still exist. Loop / payload must not use it to decide next."""
    import ast

    source = (ROOT_DIR / "agent" / "loop.py").read_text(encoding="utf-8")
    names = {node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)}
    assert "should_continue_search" not in names
    assert "should_stop_enriching" not in names

    state = new_agent_state(resume="resume", goal_input="goal", data_source="mock")
    state.constraints.min_recommend = 2
    state.search.stats.update({"opened": 0, "llm_calls": 0, "listed": 0, "recommended": 0})
    state.jobs["mock:a"] = JobRecord(
        job_key="mock:a",
        stage="recommended",
        listed_order=0,
        listed={"job_id": "a"},
    )
    assert should_continue_search(state) is True
    state.jobs["mock:b"] = JobRecord(
        job_key="mock:b",
        stage="recommended",
        listed_order=1,
        listed={"job_id": "b"},
    )
    assert should_continue_search(state) is False
    state.constraints.min_recommend = 5
    state.search.stats["opened"] = state.constraints.max_open_jd
    assert should_continue_search(state) is False
