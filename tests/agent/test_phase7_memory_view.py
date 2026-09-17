"""Phase 7: payload is a Memory view. Tests are Agent principles, not a fixed pipeline."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import Action, tool_action
from agent.loop import reduce, run_loop
from agent.reasoner_context import PROGRAM_CONCLUSION_FIELDS, build_reasoner_payload
from agent.state import JobRecord, new_agent_state
from storage.candidate_profile import CandidateProfileStore
from tests.mock_llm import ScriptedReasonerLLM, candidate_profile_response, goal_response
from tools.registry import REASONER_HIDDEN_TOOLS, available_tool_contracts, build_registry

GOAL_TEXT = "帮我找深圳高级产品经理"
SEARCH = {
    "action_type": "tool",
    "tool_name": "search_jobs",
    "arguments": {"keyword": "高级产品经理", "city": "深圳", "limit": 30},
    "reason": "search",
}
OPEN = {
    "action_type": "tool",
    "tool_name": "open_job",
    "arguments": {"job_id": "mock-direct"},
    "reason": "open",
}
ASK = {"action_type": "ask_user", "intent": "clarification", "question": "要不要继续？", "reason": "ask"}
FINISH = {"action_type": "finish", "reason": "reasoner finished"}


def _listed_state():
    registry = build_registry(data_source="mock")
    state = new_agent_state(goal_input=GOAL_TEXT, resume="简历：做过支付产品。", data_source="mock")
    state.status = "RUNNING"
    state.understanding_status = "ok"
    state.goal = goal_response()
    result = registry.invoke("search_jobs", dict(SEARCH["arguments"]))
    state = reduce(state, tool_action("search_jobs", dict(SEARCH["arguments"])), result)
    return state, registry


def test_same_observation_can_search_open_ask_or_finish():
    state, _ = _listed_state()
    observation = dict(state.last_raw_observation)
    clones = []
    for _ in range(4):
        clone = new_agent_state(goal_input=GOAL_TEXT, resume=None, data_source="mock")
        clone.status = "RUNNING"
        clone.understanding_status = "ok"
        clone.goal = goal_response()
        clone.jobs = {key: JobRecord(**{**record.__dict__}) for key, record in state.jobs.items()}
        clone.last_raw_observation = dict(observation)
        # Keep search Memory so a different keyword is not treated as "same query".
        clone.search.plans = list(state.search.plans)
        clone.search.active_plan_id = state.search.active_plan_id
        clone.search.exhausted = list(state.search.exhausted)
        clone.search.stats = dict(state.search.stats)
        clones.append(clone)

    search_registry = build_registry(data_source="mock")
    search_state = run_loop(
        clones[0],
        search_registry,
        llm_provider=ScriptedReasonerLLM(
            [
                {
                    "action_type": "tool",
                    "tool_name": "search_jobs",
                    "arguments": {"keyword": "产品总监", "city": "深圳", "limit": 30},
                    "reason": "search again",
                },
                FINISH,
            ]
        ),
    )
    open_registry = build_registry(data_source="mock")
    open_state = run_loop(clones[1], open_registry, llm_provider=ScriptedReasonerLLM([OPEN, FINISH]))
    ask_state = run_loop(clones[2], build_registry(data_source="mock"), llm_provider=ScriptedReasonerLLM([ASK]))
    finish_state = run_loop(clones[3], build_registry(data_source="mock"), llm_provider=ScriptedReasonerLLM([FINISH]))

    assert search_state.session.status == "DONE"
    assert "search_jobs" in search_registry.invocations
    assert open_state.jobs["mock:mock-direct"].opened is not None
    assert ask_state.session.status == "WAITING_USER"
    assert finish_state.session.status == "DONE"
    assert finish_state.last_raw_observation == observation


def test_program_does_not_auto_exclude_recommend_apply_or_finish():
    state, _ = _listed_state()
    record = state.jobs["mock:mock-direct"]
    assert record.stage == "listed"
    assert record.stage != "excluded"
    assert record.stage != "recommended"
    assert state.session.status == "RUNNING"
    assert (state.output or {}).get("waiting_job") is None
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert all(field not in payload for field in PROGRAM_CONCLUSION_FIELDS)
    assert all(field not in payload["constraints"] for field in PROGRAM_CONCLUSION_FIELDS)


def test_search_does_not_auto_open():
    state, registry = _listed_state()
    assert "open_job" not in registry.invocations
    assert all(item.opened is None for item in state.jobs.values())


def test_payload_is_memory_view_with_projections_and_raw_evidence():
    state, _ = _listed_state()
    opened = build_registry(data_source="mock").invoke("open_job", {"job_id": "mock-direct"})
    state = reduce(state, tool_action("open_job", {"job_id": "mock-direct"}), opened)
    state.jobs["mock:mock-direct"].job_profile = {
        "analysis_status": "ok",
        "job_summary": "支付产品经理",
        "job_id": "mock-direct",
    }
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert payload["goal"]
    assert "evidence" in payload
    assert "claims" in payload
    assert "interpretations" in payload
    assert "verifications" in payload
    assert "available_tools" in payload
    assert "history" in payload
    assert "uncertainty" in payload
    assert isinstance(payload["jobs"], list)
    assert set(payload["constraints"]) <= {
        "data_source",
        "remaining_list_capacity",
        "remaining_search_slots",
        "remaining_opens",
        "remaining_llm_calls",
        "blacklist",
        "already_applied",
        "cities",
        "salary_min",
        "platforms",
        "min_recommend",
    }
    assert payload["constraints"]["remaining_search_slots"] >= 0
    assert "recommended_count" not in payload["constraints"]
    assert "stop_reason" not in payload["constraints"]
    assert "should_continue_search" not in payload["constraints"]
    resume_ids = [
        item["id"]
        for item in payload["intake"]
        if "支付" in str(item.get("short_excerpt") or "")
    ]
    assert resume_ids
    assert not payload["evidence"]
    assert all("content" not in item for item in payload["intake"])
    assert "resume_text" not in payload["memory"]
    profile = payload["candidate_profile"]
    assert profile["kind"] == "interpretation_projection"
    assert profile["layer"] == "interpretation"
    assert isinstance(profile.get("derived_from"), list)
    job = next(item for item in payload["jobs"] if item["job_key"] == "mock:mock-direct")
    assert "jd_evidence" not in job
    assert "job" not in job or "job_description" not in (job.get("job") or {})
    assert job["fact"]["layer"] == "fact"
    assert job["fact"]["has_job_description"] is True
    assert job["job_profile"]["kind"] == "interpretation_projection"
    assert job["job_profile"]["layer"] == "interpretation"
    # Must not invent synthetic jd:{job_key} provenance.
    assert "jd:mock:mock-direct" not in (job["job_profile"].get("derived_from") or [])


def test_persist_keeps_layers_and_does_not_reload_profile_as_sole_truth(tmp_path):
    store = CandidateProfileStore(tmp_path)
    evidence = [
        {
            "id": "ev-1",
            "content_type": "resume",
            "content": "做过支付和对账",
            "origin": "resume",
            "source": "resume",
        }
    ]
    claims = [
        {
            "id": "cl-1",
            "kind": "skill",
            "statement": "做过支付",
            "derived_from": ["ev-1"],
            "layer": "claim",
            "evidence_status": "unverified",
        }
    ]
    store.create(
        candidate_profile_response(),
        candidate_id="cand-1",
        source_kinds=["resume"],
        evidence=evidence,
        claims=claims,
        interpretations=[{"id": "int-1", "kind": "analyze_candidate", "derived_from": ["ev-1"], "layer": "interpretation"}],
        verifications=[],
    )
    record = store.load("cand-1")
    assert record["evidence"][0]["id"] == "ev-1"
    assert record["claims"][0]["id"] == "cl-1"
    assert record["profile"]["kind"] == "interpretation_projection"
    state = new_agent_state(
        goal_input=GOAL_TEXT,
        resume=None,
        data_source="mock",
        candidate_id="cand-1",
        profile_record=record,
        profile_dir=str(tmp_path),
    )
    assert any(item.get("id") == "ev-1" for item in state.evidence)
    assert any(item.get("id") == "cl-1" for item in state.claims)
    assert state.candidate.profile["kind"] == "interpretation_projection"
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert any(item.get("id") == "ev-1" for item in payload["evidence"])
    assert payload["candidate_profile"]["kind"] == "interpretation_projection"
    assert payload["candidate_profile"] is not payload["evidence"]


def test_production_path_has_one_reasoner_no_stop_rule_codecision():
    names = {item["name"] for item in available_tool_contracts(build_registry(data_source="mock"))}
    assert "reason_next_action" not in names
    assert "decide_next_action" not in names
    assert "rank_jobs" not in names
    assert "build_report" not in names
    assert "decide_next_action" in REASONER_HIDDEN_TOOLS
    assert "rank_jobs" in REASONER_HIDDEN_TOOLS

    loop_tree = ast.parse((ROOT_DIR / "agent" / "loop.py").read_text(encoding="utf-8"))
    payload_tree = ast.parse((ROOT_DIR / "agent" / "reasoner_context.py").read_text(encoding="utf-8"))
    loop_names = {node.id for node in ast.walk(loop_tree) if isinstance(node, ast.Name)}
    payload_names = {node.id for node in ast.walk(payload_tree) if isinstance(node, ast.Name)}
    assert "should_continue_search" not in loop_names
    assert "should_stop_enriching" not in loop_names
    assert "next_explore_block_reason" not in loop_names
    assert "can_search" not in loop_names
    assert "can_match" not in loop_names
    assert "should_continue_search" not in payload_names
    assert "decide_next_action" not in payload_names

    understand = None
    for node in ast.walk(loop_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_reduce_understanding":
            understand = node
            break
    assert understand is not None
    understand_names = {node.id for node in ast.walk(understand) if isinstance(node, ast.Name)}
    assert "_reduce_follow_up" not in understand_names
    assert "create_bound_task" not in understand_names
    assert "_reduce_task_control" not in understand_names
    assert "resolve_task_intent" not in understand_names
