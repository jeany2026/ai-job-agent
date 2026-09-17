"""Phase 1: Program records Tool results. It does not exclude / recommend / finish."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import Action, tool_action
from agent.loop import reduce, run_loop
from agent.orchestrator import run_agent
from agent.reasoner_context import build_reasoner_payload
from agent.state import JobRecord, new_agent_state
from tests.mock_llm import ScriptedReasonerLLM, goal_response, understanding_response
from tools.registry import build_registry

GOAL_TEXT = "帮我找深圳高级产品经理"

SEARCH = {
    "action_type": "tool",
    "tool_name": "search_jobs",
    "arguments": {"keyword": "高级产品经理", "city": "深圳", "limit": 30},
    "reason": "search",
}
FINISH = {"action_type": "finish", "reason": "reasoner finished"}


def _understand(ctx: dict) -> dict:
    intake = ctx.get("intake") if isinstance(ctx.get("intake"), list) else []
    ids = [str(item.get("id")) for item in intake if isinstance(item, dict) and item.get("id")]
    return {
        "action_type": "tool",
        "tool_name": "understand_user_input",
        "arguments": {"intake_ids": ids},
        "reason": "understand",
    }


def _job_state(*, stage: str = "opened") -> tuple:
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = goal_response()
    listed = {
        "platform": "mock",
        "job_id": "j1",
        "job_url": "https://mock.local/j1",
        "job_title": "高级产品经理",
        "company_name": "支付科技",
        "city": "深圳",
    }
    record = JobRecord(
        job_key="mock:j1",
        stage=stage,
        listed_order=0,
        listed=listed,
        opened=dict(listed),
    )
    state.jobs[record.job_key] = record
    return state, record


def test_interpret_applied_does_not_exclude():
    state, record = _job_state()
    result = {
        "analysis_status": "ok",
        "inferred_context": {"application_evidence": "applied"},
        "job_id": "j1",
    }
    state = reduce(state, tool_action("interpret_job_actions", {"job_id": "j1"}), result)
    assert "mock:j1" in state.jobs
    assert record.stage != "excluded"
    assert record.exclude_reason != "already_applied"
    assert record.interpretations
    assert record.interpretations[0]["kind"] == "application_evidence"
    assert record.interpretations[0]["layer"] == "interpretation"
    assert record.interpretations[0]["payload"]["application_evidence"] == "applied"
    assert record.verifications == []
    assert record.interpret_result["inferred_context"]["application_evidence"] == "applied"
    assert state.last_raw_observation["tool_name"] == "interpret_job_actions"
    assert state.session.status == "RUNNING"


def test_match_hard_requirements_false_does_not_exclude():
    state, record = _job_state(stage="analyzed")
    result = {
        "analysis_status": "ok",
        "hard_requirements_met": False,
        "recommendation": "no",
        "job_id": "j1",
    }
    state = reduce(state, tool_action("match_job", {"job_id": "j1"}), result)
    assert record.stage != "excluded"
    assert record.exclude_reason is None
    assert record.match_result["hard_requirements_met"] is False
    assert state.pending_job_key != record.job_key
    assert state.last_raw_observation["tool_name"] == "match_job"
    assert state.session.status == "RUNNING"


def test_analyze_llm_error_does_not_exclude():
    state, record = _job_state()
    result = {"analysis_status": "llm_error", "error": "timeout", "job_id": "j1"}
    state = reduce(state, tool_action("analyze_job", {"job": record.opened}), result)
    assert record.stage != "excluded"
    assert record.job_profile["analysis_status"] == "llm_error"
    assert state.last_raw_observation["tool_name"] == "analyze_job"
    assert state.session.status == "RUNNING"


def test_search_does_not_auto_open():
    registry = build_registry(data_source="mock")
    llm = ScriptedReasonerLLM([_understand, SEARCH, FINISH])
    state = run_agent(
        goal=GOAL_TEXT,
        resume=None,
        llm_provider=llm,
        registry=registry,
        data_source="mock",
    )
    assert "search_jobs" in registry.invocations
    assert "open_job" not in registry.invocations
    assert any(record.stage == "listed" for record in state.jobs.values())
    assert state.session.status == "DONE"


def test_user_blacklist_is_marked_and_visible_to_reasoner():
    registry = build_registry(data_source="mock")
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = goal_response()
    state.constraints.blacklist = ["黑名单科技"]
    result = registry.invoke("search_jobs", dict(SEARCH["arguments"]))
    state = reduce(
        state,
        tool_action("search_jobs", dict(SEARCH["arguments"])),
        result,
    )
    blocked = state.jobs["mock:mock-blocked"]
    assert blocked.stage == "listed"
    assert blocked.stage != "excluded"
    assert "blacklist" in blocked.constraint_flags
    assert blocked.exclude_reason == "blacklist"
    assert blocked.opened is None
    kept = [record for record in state.jobs.values() if record.stage == "listed"]
    assert kept
    payload = build_reasoner_payload(state, registry)
    summaries = payload["jobs"]
    assert any("blacklist" in (item.get("constraint_flags") or []) for item in summaries)
    assert any(item.get("stage") == "listed" for item in summaries)


def test_already_applied_keys_are_flags_not_excludes():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    state.constraints.already_applied = ["mock:j1"]
    job = {
        "platform": "mock",
        "job_id": "j1",
        "job_url": "https://mock.local/j1",
        "job_title": "高级产品经理",
        "company_name": "支付科技",
        "city": "深圳",
    }
    state = reduce(state, tool_action("search_jobs", {"keyword": "产品经理"}), {"jobs": [job]})
    record = state.jobs["mock:j1"]
    assert record.stage == "listed"
    assert record.exclude_reason != "already_applied"
    assert "already_applied" in record.constraint_flags
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    flags = next(item["constraint_flags"] for item in payload["jobs"] if item["job_key"] == "mock:j1")
    assert "already_applied" in flags


def test_insufficient_match_does_not_exclude_or_reject_task():
    state, record = _job_state(stage="analyzed")
    state = reduce(state, tool_action("record_insufficient_match", {"job_id": "j1"}), {})
    assert record.stage != "excluded"
    assert record.match_result["recommendation"] == "insufficient_evidence"
    assert state.session.status == "RUNNING"


def test_rank_does_not_write_recommended_or_excluded():
    state, yes = _job_state(stage="matched")
    yes.match_result = {"recommendation": "yes", "overall_fit": "strong", "hard_requirements_met": True}
    no = JobRecord(
        job_key="mock:j2",
        stage="matched",
        listed_order=1,
        listed={"platform": "mock", "job_id": "j2", "job_title": "B"},
        match_result={"recommendation": "no", "overall_fit": "none", "hard_requirements_met": False},
    )
    state.jobs[no.job_key] = no
    state = reduce(state, tool_action("rank_jobs", {}), {})
    assert yes.stage == "matched"
    assert no.stage == "matched"
    assert yes.exclude_reason is None
    assert no.exclude_reason is None
    ranked = state.last_raw_observation["result"]["ranked"]
    assert {item["job_key"] for item in ranked} == {"mock:j1", "mock:j2"}
    assert ranked[0]["recommendation"] == "yes"


def test_execute_does_not_exclude():
    state, record = _job_state()
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
    assert record.stage != "excluded"
    assert record.interpret_result["inferred_context"]["application_evidence"] == "applied"
    assert record.verifications
    assert record.verifications[0]["layer"] == "verification"
    assert record.verifications[0]["basis"] == "tool_mechanical_result"
    assert state.last_raw_observation["tool_name"] == "execute_job_action"
    assert state.session.status == "RUNNING"


def test_plan_failure_does_not_fail_or_finish_when_jobs_exist():
    state, _record = _job_state(stage="listed")
    state.search.plans = []
    state = reduce(
        state,
        tool_action("plan_search", {}),
        {"analysis_status": "llm_error", "error": "timeout"},
    )
    assert state.session.status == "RUNNING"
    assert state.search.stop_reason == "plan_search_failed"
    assert "mock:j1" in state.jobs


def test_goal_cities_salary_are_not_copied_into_constraints():
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    result = understanding_response(
        user_goal={
            "target_roles": ["高级产品经理"],
            "cities": ["深圳"],
            "salary_min": 35000,
            "exclude_companies": ["黑名单科技"],
        }
    )
    result["analysis_status"] = "ok"
    state = reduce(state, tool_action("understand_user_input", {"message": GOAL_TEXT}), result)
    assert (state.goal or {}).get("exclude_companies") == ["黑名单科技"]
    assert "黑名单科技" not in (state.constraints.blacklist or [])
    assert state.constraints.cities == []
    assert state.constraints.salary_min is None


def test_loop_does_not_call_stop_rules_to_end_task():
    source = (ROOT_DIR / "agent" / "loop.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "should_continue_search" not in names
    assert "should_stop_enriching" not in names


def test_loop_and_payload_do_not_pick_job_via_in_flight():
    loop_names = {
        node.id
        for node in ast.walk(ast.parse((ROOT_DIR / "agent" / "loop.py").read_text(encoding="utf-8")))
        if isinstance(node, ast.Name)
    }
    payload_names = {
        node.id
        for node in ast.walk(ast.parse((ROOT_DIR / "agent" / "reasoner_context.py").read_text(encoding="utf-8")))
        if isinstance(node, ast.Name)
    }
    assert "in_flight_job" not in loop_names
    assert "next_job_to_open" not in loop_names
    assert "in_flight_job" not in payload_names
    assert "next_job_to_open" not in payload_names
    payload = ast.parse((ROOT_DIR / "agent" / "reasoner_context.py").read_text(encoding="utf-8"))
    assigned = {
        target.value
        for node in ast.walk(payload)
        if isinstance(node, ast.Dict)
        for key, _value in zip(node.keys, node.values)
        if isinstance(key, ast.Constant)
        for target in [key]
    }
    assert "current_job" not in assigned


def test_same_search_observation_reasoner_can_finish_without_open():
    registry = build_registry(data_source="mock")
    state = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = goal_response()
    state.last_raw_observation = {"kind": "user_turn_understood", "has_user_goal": True}
    result = registry.invoke("search_jobs", dict(SEARCH["arguments"]))
    state = reduce(state, tool_action("search_jobs", dict(SEARCH["arguments"])), result)
    llm = ScriptedReasonerLLM([FINISH])
    state = run_loop(state, build_registry(data_source="mock"), llm_provider=llm)
    assert state.session.status == "DONE"
    assert all(record.opened is None for record in state.jobs.values())


ASK = {"action_type": "ask_user", "intent": "clarification", "question": "你更倾向哪个方向？", "reason": "reasoner chose ask_user"}


def _matched_state():
    state, record = _job_state(stage="matched")
    record.match_result = {
        "analysis_status": "ok",
        "hard_requirements_met": True,
        "recommendation": "yes",
    }
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": "match_job",
        "result": {
            "analysis_status": "ok",
            "hard_requirements_met": True,
            "recommendation": "yes",
            "job_id": "j1",
        },
    }
    return state, record


def test_after_match_reasoner_may_ask_user_search_or_finish():
    ask_state, _ = _matched_state()
    search_state, _ = _matched_state()
    finish_state, _ = _matched_state()

    ask_registry = build_registry(data_source="mock")
    ask_state = run_loop(ask_state, ask_registry, llm_provider=ScriptedReasonerLLM([ASK]))
    assert ask_state.session.status == "WAITING_USER"
    assert "decide_next_action" not in ask_registry.invocations

    search_registry = build_registry(data_source="mock")
    search_state = run_loop(search_state, search_registry, llm_provider=ScriptedReasonerLLM([SEARCH, FINISH]))
    assert "search_jobs" in search_registry.invocations
    assert search_state.session.status == "DONE"
    assert search_state.session.status != "WAITING_USER"
    assert "decide_next_action" not in search_registry.invocations

    finish_registry = build_registry(data_source="mock")
    finish_state = run_loop(finish_state, finish_registry, llm_provider=ScriptedReasonerLLM([FINISH]))
    assert finish_state.session.status == "DONE"
    assert finish_registry.invocations == []
    assert (finish_state.output or {}).get("waiting_job") is None


def test_program_does_not_ask_user_after_match():
    state, record = _matched_state()
    registry = build_registry(data_source="mock")
    state = run_loop(state, registry, llm_provider=ScriptedReasonerLLM([FINISH]))
    assert state.session.status == "DONE"
    assert record.stage == "matched"
    assert record.stage != "recommended"
    assert "decide_next_action" not in registry.invocations


def test_not_understood_reasoner_may_search_with_own_keyword():
    raw = "这是我的简历。我想找点事做。"
    registry = build_registry(data_source="mock")
    state = new_agent_state(goal_input=raw, resume=None, data_source="mock")
    assert state.understanding_status != "ok"
    llm = ScriptedReasonerLLM(
        [
            {
                "action_type": "tool",
                "tool_name": "search_jobs",
                "arguments": {"keyword": "高级产品经理", "city": "深圳", "limit": 30},
                "reason": "reasoner chose its own keyword",
            },
            FINISH,
        ]
    )
    state = run_loop(state, registry, llm_provider=llm)
    assert "search_jobs" in registry.invocations
    assert state.session.status == "DONE"
    assert (state.last_raw_observation or {}).get("kind") != "action_rejected"


def test_ununderstood_raw_goal_as_search_keyword_is_rejected():
    raw = "这是我的简历。"
    registry = build_registry(data_source="mock")
    state = new_agent_state(goal_input=raw, resume=None, data_source="mock")
    llm = ScriptedReasonerLLM(
        [
            {
                "action_type": "tool",
                "tool_name": "search_jobs",
                "arguments": {"keyword": raw, "city": "深圳", "limit": 30},
                "reason": "illegal: copy pending_goal",
            },
            {
                "action_type": "tool",
                "tool_name": "search_jobs",
                "arguments": {"keyword": "产品经理", "city": "深圳", "limit": 30},
                "reason": "reasoner chose a different keyword",
            },
            FINISH,
        ]
    )
    state = run_loop(state, registry, llm_provider=llm)
    assert registry.invocations == ["search_jobs"]
    assert state.session.status == "DONE"


def test_ask_user_goal_incomplete_waits_not_done():
    state = new_agent_state(goal_input="这是我的简历。", resume=None, data_source="mock")
    state.status = "RUNNING"
    state.last_raw_observation = {"kind": "user_turn_understood", "has_user_goal": False}
    llm = ScriptedReasonerLLM(
        [
            {
                "action_type": "ask_user",
                "intent": "clarification",
                "question": "你想找什么方向的工作？",
                "error_code": "GOAL_INCOMPLETE",
                "reason": "need a job-search goal",
            }
        ]
    )
    state = run_loop(state, build_registry(data_source="mock"), llm_provider=llm)
    assert state.session.status == "WAITING_USER"
    assert state.session.status != "DONE"
    assert (state.output or {}).get("error_code") == "GOAL_INCOMPLETE"
    assert (state.output or {}).get("waiting_job") is None
    assert (state.output or {}).get("recommended") == []
    assert state.decisions[-1]["action"] == "ask_user"


def test_ask_user_without_job_key_does_not_auto_recommend():
    state, record = _matched_state()
    llm = ScriptedReasonerLLM(
        [
            {
                "action_type": "ask_user",
                "intent": "clarification",
                "question": "还需要补充什么约束？",
                "reason": "clarify without picking a job",
            }
        ]
    )
    state = run_loop(state, build_registry(data_source="mock"), llm_provider=llm)
    assert state.session.status == "WAITING_USER"
    assert record.stage == "matched"
    assert record.stage != "recommended"
    assert (state.output or {}).get("waiting_job") is None
    assert (state.output or {}).get("recommended") == []


def test_ask_user_with_explicit_job_key_writes_that_job():
    state, record = _matched_state()
    llm = ScriptedReasonerLLM(
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
    )
    state = run_loop(state, build_registry(data_source="mock"), llm_provider=llm)
    assert state.session.status == "WAITING_USER"
    assert (state.output or {}).get("waiting_job", {}).get("job_key") == record.job_key
    assert record.stage == "matched"
    assert record.stage != "recommended"
    assert any(item.get("kind") == "surfaced" for item in record.decisions)


def test_misinvoked_decide_next_action_is_observation_only():
    state, record = _matched_state()
    state = reduce(
        state,
        tool_action("decide_next_action", {"job_id": "j1"}),
        {"next_action": "REJECT_JOB", "rationale": "should not exclude"},
    )
    assert record.stage != "excluded"
    assert record.exclude_reason is None
    assert state.pending_job_key != record.job_key
    assert state.search.stop_reason is None
    assert state.last_raw_observation["tool_name"] == "decide_next_action"
    assert state.session.status == "RUNNING"
