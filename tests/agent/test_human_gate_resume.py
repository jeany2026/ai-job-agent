"""Human Gate continuation: User feedback → Observation → World → Reasoner.

Not a fixed Human Gate → search_jobs workflow.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.human_gate import HumanGateError, apply_human_gate
from agent.human_gate_continuity import (
    OBS_HUMAN_GATE,
    OBS_HUMAN_GATE_RESOLVED,
    RESOLUTION_USER_CONFIRMED,
    snapshot_world_for_human_gate,
)
from agent.orchestrator import run_agent
from agent.reasoner_context import build_reasoner_payload
from agent.state import JobRecord, SearchPlan, new_agent_state
from api.runs import ConversationStore
from task.store import JobSearchTaskStore
from tests.mock_llm import AgentRoutingLLM, ScriptedReasonerLLM
from tools.registry import build_registry

RESUME_PATH = ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")
GOAL_TEXT = "帮我找深圳高级产品经理，重点金融科技/支付/复杂业务系统，薪资符合目标"


def test_apply_human_gate_writes_observation():
    state = new_agent_state(resume="resume", goal_input="goal", data_source="boss")
    apply_human_gate(
        state,
        {"reason": "login_required", "message": "未检测到登录", "source": "search_jobs"},
    )
    assert state.session.status == "NEEDS_HUMAN"
    assert state.last_raw_observation["kind"] == OBS_HUMAN_GATE
    assert state.last_raw_observation["awaiting_human"] is True
    assert state.last_raw_observation["reason"] == "login_required"
    assert state.last_raw_observation["source"] == "search_jobs"


def test_conversation_remembers_human_gate_world_snapshot():
    store = ConversationStore()
    conversation = store.ensure(None)
    cid = conversation.conversation_id
    state = new_agent_state(
        resume=SAMPLE_RESUME,
        goal_input=GOAL_TEXT,
        data_source="boss",
        conversation_id=cid,
    )
    state.goal = {"analysis_status": "ok", "target_roles": ["高级产品经理"], "cities": ["深圳"]}
    state.understanding_status = "ok"
    state.search.plans.append(SearchPlan(plan_id="plan-1", keyword="高级产品经理", city="深圳", platform="boss"))
    state.search.active_plan_id = "plan-1"
    state.jobs["boss:job-1"] = JobRecord(
        job_key="boss:job-1",
        stage="listed",
        listed_order=0,
        listed={"platform": "boss", "job_id": "job-1", "job_title": "高级产品经理"},
    )
    state.job_search_task_id = "task-gate-1"
    apply_human_gate(
        state,
        {"reason": "login_required", "message": "请先登录", "source": "search_jobs"},
    )
    store.remember(cid, state, run_id="run-1")
    session = store.session_context(cid)
    assert session["pending_human_gate"]["reason"] == "login_required"
    assert session["human_gate_world_snapshot"]["jobs"]["boss:job-1"]["stage"] == "listed"
    assert session["human_gate_world_snapshot"]["search"]["active_plan_id"] == "plan-1"
    assert "task-gate-1" in session["task_ids"]


def test_resume_after_human_gate_same_task_and_reasoner_sees_user_confirmed():
    """A–B, E–F: login gate → user feedback → same Task World → Reasoner Observation."""
    conversations = ConversationStore()
    tasks = JobSearchTaskStore()
    cid = conversations.ensure(None).conversation_id

    registry = build_registry(data_source="boss")

    def gated_search(arguments, **_extra):
        raise HumanGateError("login_required", "未检测到 BOSS 登录态", source="search_jobs")

    registry._handlers["search_jobs"] = gated_search
    first = run_agent(
        resume=SAMPLE_RESUME,
        goal=GOAL_TEXT,
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="boss",
        conversation_id=cid,
        task_store=tasks,
    )
    assert first.session.status == "NEEDS_HUMAN"
    assert first.last_raw_observation["kind"] == OBS_HUMAN_GATE
    conversations.remember(cid, first, run_id="run-gate")
    task_ids_before = list(conversations.session_context(cid)["task_ids"])
    assert task_ids_before, "gate turn should bind/create a JobSearchTask"

    seen = {"payloads": []}

    def choose_ask(payload):
        seen["payloads"].append(payload)
        observation = payload.get("observation") or {}
        assert observation.get("kind") == OBS_HUMAN_GATE_RESOLVED
        assert observation.get("resolution") == RESOLUTION_USER_CONFIRMED
        assert observation.get("browser_login_verified") is False
        assert observation.get("prior_gate", {}).get("reason") == "login_required"
        history = payload.get("history") or {}
        assert history.get("search_plans") or payload.get("goal")
        return {
            "action_type": "ask_user",
            "intent": "clarification",
            "question": "登录后想先继续搜，还是先看已打开的职位？",
        }

    # D: Reasoner need not call search_jobs — ask_user is a valid next Action.
    second = run_agent(
        message="我已经登录了",
        resume=None,
        llm_provider=ScriptedReasonerLLM([choose_ask]),
        registry=build_registry(data_source="boss"),
        data_source="boss",
        session_context=conversations.session_context(cid),
        conversation_id=cid,
        task_store=tasks,
    )
    assert seen["payloads"], "Reasoner must run after human_gate_resolved"
    assert second.session.status == "WAITING_USER"
    conversations.remember(cid, second, run_id="run-resume")
    session_after = conversations.session_context(cid)
    assert session_after.get("pending_human_gate") is None
    assert set(task_ids_before).issubset(set(session_after["task_ids"]))
    # E: same Task continuity — no second independent task forced by resume protocol.
    assert len(session_after["task_ids"]) == len(task_ids_before)


def test_user_confirmed_but_still_logged_out_raises_gate_again():
    """C: user claim does not assert browser login; Tool may gate again."""
    conversations = ConversationStore()
    cid = conversations.ensure(None).conversation_id
    state = new_agent_state(resume=SAMPLE_RESUME, goal_input=GOAL_TEXT, data_source="boss", conversation_id=cid)
    state.goal = {"analysis_status": "ok", "target_roles": ["高级产品经理"], "cities": ["深圳"]}
    state.understanding_status = "ok"
    state.understanding = {"task_kind": "new_job_search"}
    state.candidate.profile_status = "ok"
    state.candidate.profile = {"analysis_status": "ok", "summary": "PM"}
    state.search.plans.append(SearchPlan(plan_id="plan-1", keyword="高级产品经理", city="深圳", platform="boss"))
    state.search.active_plan_id = "plan-1"
    apply_human_gate(state, {"reason": "login_required", "message": "未登录", "source": "search_jobs"})
    conversations.remember(cid, state, run_id="r1")

    registry = build_registry(data_source="boss")
    calls = {"search": 0}

    def still_gated(arguments, **_extra):
        calls["search"] += 1
        raise HumanGateError("login_required", "未检测到 BOSS 登录态", source="search_jobs")

    registry._handlers["search_jobs"] = still_gated

    # Script enough Reasoner steps: may still ask understand if intake changes; prefer explicit search.
    resumed = run_agent(
        message="我已登录",
        llm_provider=ScriptedReasonerLLM(
            [
                {"action_type": "tool", "tool_name": "search_jobs", "arguments": {"keyword": "高级产品经理", "city": "深圳"}},
            ]
        ),
        registry=registry,
        data_source="boss",
        session_context=conversations.session_context(cid),
        conversation_id=cid,
    )
    assert calls["search"] == 1
    assert resumed.session.status == "NEEDS_HUMAN"
    assert resumed.human_gate["reason"] == "login_required"
    assert resumed.last_raw_observation["kind"] == OBS_HUMAN_GATE


def test_continue_shortcut_text_is_plain_user_feedback_observation():
    """G: shortcut text enters the same human_gate_resolved Observation path."""
    from agent.human_gate_continuity import build_initial_turn_observation

    state = new_agent_state(
        goal_input="我已完成，继续",
        data_source="boss",
        session_context={
            "pending_human_gate": {
                "reason": "captcha",
                "message": "拖动滑块",
                "source": "open_job",
            },
            "human_gate_world_snapshot": snapshot_world_for_human_gate(
                new_agent_state(goal_input=GOAL_TEXT, data_source="boss")
            ),
        },
    )
    observation = build_initial_turn_observation(state, world_restore={"reason": "human_gate_continuity"})
    assert observation["kind"] == OBS_HUMAN_GATE_RESOLVED
    assert observation["message"] == "我已完成，继续"
    assert observation["resolution"] == RESOLUTION_USER_CONFIRMED
    assert observation["browser_login_verified"] is False


def test_reasoner_context_projects_gate_resume_without_forcing_search():
    state = new_agent_state(
        goal_input="好了",
        data_source="boss",
        session_context={
            "previous_goal": {"target_roles": ["高级产品经理"]},
            "pending_human_gate": {"reason": "login_required", "source": "search_jobs", "message": "login"},
        },
    )
    state.last_raw_observation = {
        "kind": OBS_HUMAN_GATE_RESOLVED,
        "resolution": RESOLUTION_USER_CONFIRMED,
        "browser_login_verified": False,
        "prior_gate": {"reason": "login_required", "source": "search_jobs"},
    }
    payload = build_reasoner_payload(state, build_registry(data_source="boss"))
    assert payload["observation"]["kind"] == OBS_HUMAN_GATE_RESOLVED
    assert payload["context"]["persistence"]["pending_human_gate"]["reason"] == "login_required"
    tools = {item["name"] for item in payload["available_tools"]}
    assert "search_jobs" in tools
    assert "launch_edge" not in tools
    assert "continue_search" not in tools


def test_loop_human_gate_dict_still_needs_human_with_observation():
    registry = build_registry(data_source="boss")

    def gated(arguments, **_extra):
        return {
            "ok": False,
            "human_gate": {
                "reason": "browser_unavailable",
                "message": "Edge not connected",
            },
        }

    registry._handlers["search_jobs"] = gated
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal=GOAL_TEXT,
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="boss",
    )
    assert state.session.status == "NEEDS_HUMAN"
    assert state.last_raw_observation["kind"] == OBS_HUMAN_GATE
    assert state.human_gate["source"] == "search_jobs"


def test_captcha_resume_can_choose_open_job():
    """D/H: after captcha gate, Reasoner may open_job — no browser business Action added."""
    conversations = ConversationStore()
    cid = conversations.ensure(None).conversation_id
    paused = new_agent_state(resume=SAMPLE_RESUME, goal_input=GOAL_TEXT, data_source="mock", conversation_id=cid)
    paused.goal = {"analysis_status": "ok", "target_roles": ["高级产品经理"], "cities": ["深圳"]}
    paused.understanding_status = "ok"
    paused.understanding = {"task_kind": "new_job_search"}
    paused.candidate.profile_status = "ok"
    paused.candidate.profile = {"analysis_status": "ok"}
    paused.search.plans.append(SearchPlan(plan_id="plan-1", keyword="高级产品经理", city="深圳"))
    paused.search.active_plan_id = "plan-1"
    paused.jobs["mock:j1"] = JobRecord(
        job_key="mock:j1",
        stage="listed",
        listed_order=0,
        listed={"platform": "mock", "job_id": "j1", "job_title": "高级产品经理", "job_url": "https://mock/j1"},
    )
    apply_human_gate(paused, {"reason": "captcha", "message": "拖动滑块", "source": "open_job"})
    conversations.remember(cid, paused, run_id="c1")

    opened = {"count": 0}

    def open_ok(arguments, **_extra):
        opened["count"] += 1
        return {
            "ok": True,
            "job": {
                "platform": "mock",
                "job_id": "j1",
                "job_url": "https://mock/j1",
                "job_title": "高级产品经理",
                "company_name": "支付科技",
                "job_description": "负责支付产品。",
            },
        }

    registry = build_registry(data_source="mock")
    registry._handlers["open_job"] = open_ok

    state = run_agent(
        message="验证码弄好了",
        llm_provider=ScriptedReasonerLLM(
            [
                {
                    "action_type": "tool",
                    "tool_name": "open_job",
                    "arguments": {"job_key": "mock:j1"},
                },
                {"action_type": "finish", "reason": "opened after gate"},
            ]
        ),
        registry=registry,
        data_source="mock",
        session_context=conversations.session_context(cid),
        conversation_id=cid,
    )
    assert opened["count"] == 1
    assert "mock:j1" in state.jobs
    assert state.jobs["mock:j1"].stage in {"opened", "listed", "analyzed", "matched"}
