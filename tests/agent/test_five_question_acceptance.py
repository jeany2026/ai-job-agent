"""Five-question Phase gate. Production must stay a real Agent.

1. Record the world, do not choose the next step.
2. Execute a Reasoner decision, do not invent one.
3. Keep Evidence / Claim / Interpretation / Verification distinct.
4. Job.stage is world progress, not a model or Reasoner conclusion.
5. Rules block illegal Actions; they do not teach the correct business move.

Mock LLM is a test double only. Semantic Tools still go through the shared provider.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import Action, tool_action
from agent.loop import reduce, run_loop
from agent.report import build_report
from agent.reasoner_context import build_reasoner_payload
from agent.state import DECISION_SURFACED, JobRecord, new_agent_state
from tests.mock_llm import ScriptedReasonerLLM, goal_response, understanding_response
from tools.registry import build_registry

GOAL_TEXT = "帮我找深圳高级产品经理"


def _loop_tree() -> ast.AST:
    return ast.parse((ROOT_DIR / "agent" / "loop.py").read_text(encoding="utf-8"))


def _fn(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing {name}")


def _assigned_stage_literals(fn: ast.FunctionDef) -> set[str]:
    values: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Attribute) and target.attr == "stage" for target in node.targets
        ):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            values.add(node.value.value)
    return values


def test_q1_q4_production_writes_only_world_stages():
    loop = _loop_tree()
    lifecycle = ast.parse((ROOT_DIR / "task" / "lifecycle.py").read_text(encoding="utf-8"))
    written = set()
    for tree in (loop, lifecycle):
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            written.update(_assigned_stage_literals(node))
    assert written <= {"listed", "opened", "analyzed", "matched"}
    assert "recommended" not in written
    assert "excluded" not in written


def test_q2_understanding_does_not_compute_task_intent():
    understand = _fn(_loop_tree(), "_reduce_understanding")
    names = {node.id for node in ast.walk(understand) if isinstance(node, ast.Name)}
    assert "resolve_task_intent" not in names
    assert "INTENT_CLARIFY" not in names
    assert "_reduce_task_control" not in names
    assert "create_bound_task" not in names

    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    result = understanding_response(
        task_kind="new_job_search",
        user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]},
    )
    result["analysis_status"] = "ok"
    state = reduce(state, tool_action("understand_user_input", {"message": GOAL_TEXT}), result)
    assert state.session.status == "RUNNING"
    assert state.task_intent is None
    assert state.last_raw_observation["kind"] == "user_turn_understood"
    assert state.last_raw_observation["task_kind"] == "new_job_search"
    assert state.last_raw_observation.get("task_intent") is None


def test_q2_q4_ask_user_records_decision_not_recommended_stage():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = goal_response()
    record = JobRecord(
        job_key="mock:j1",
        stage="matched",
        listed_order=0,
        listed={"platform": "mock", "job_id": "j1", "job_title": "高级产品经理"},
        match_result={"analysis_status": "ok", "recommendation": "yes", "hard_requirements_met": True},
    )
    state.jobs[record.job_key] = record
    state = run_loop(
        state,
        build_registry(data_source="mock"),
        llm_provider=ScriptedReasonerLLM(
            [
                {
                    "action_type": "ask_user",
                    "intent": "surface",
                    "job_key": record.job_key,
                    "question": "要投这个职位吗？",
                    "arguments": {"job_key": record.job_key, "intent": "surface"},
                    "reason": "reasoner named the job",
                }
            ]
        ),
    )
    assert state.session.status == "WAITING_USER"
    assert record.stage == "matched"
    assert any(item.get("kind") == DECISION_SURFACED and item.get("source") == "reasoner" for item in record.decisions)


def test_q1_q3_q4_match_and_apply_do_not_become_world_exclude():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    record = JobRecord(
        job_key="mock:j1",
        stage="analyzed",
        listed_order=0,
        listed={"platform": "mock", "job_id": "j1", "job_title": "高级产品经理", "job_url": "https://mock.local/j1"},
        opened={"platform": "mock", "job_id": "j1", "job_title": "高级产品经理", "job_url": "https://mock.local/j1"},
    )
    state.jobs[record.job_key] = record
    state = reduce(
        state,
        tool_action("match_job", {"job_id": "j1"}),
        {"analysis_status": "ok", "recommendation": "no", "hard_requirements_met": False, "job_id": "j1"},
    )
    assert record.stage == "matched"
    assert record.stage != "excluded"
    assert record.match_result["recommendation"] == "no"
    report = build_report(state)
    assert report["recommended"] == []

    state.pending_job_key = record.job_key
    state = reduce(
        state,
        tool_action("execute_job_action", {"job_id": "j1"}),
        {
            "ok": True,
            "result": "applied",
            "verification": "confirmed",
            "execution_status": "success",
            "application_evidence": "applied",
        },
    )
    assert record.stage == "matched"
    assert record.verifications
    assert record.verifications[0]["layer"] == "verification"
    assert record.stage != "excluded"


def test_q5_blacklist_is_a_constraint_flag_not_a_program_exclude():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    state.constraints.blacklist = ["黑名单科技"]
    result = build_registry(data_source="mock").invoke(
        "search_jobs",
        {"keyword": "高级产品经理", "city": "深圳", "limit": 30},
    )
    state = reduce(
        state,
        tool_action("search_jobs", {"keyword": "高级产品经理", "city": "深圳", "limit": 30}),
        result,
    )
    blocked = state.jobs["mock:mock-blocked"]
    assert blocked.stage == "listed"
    assert "blacklist" in blocked.constraint_flags
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    view = next(item for item in payload["jobs"] if item["job_key"] == "mock:mock-blocked")
    assert view["stage"] == "listed"
    assert "blacklist" in view["constraint_flags"]
    assert payload["constraints"]["blacklist"] == ["黑名单科技"]
