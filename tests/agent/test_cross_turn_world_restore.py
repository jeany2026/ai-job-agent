"""Cross-turn: Persistence → Live World restore → Context projection.

Restore is continuity only — never a business pipeline step.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.orchestrator import run_agent
from agent.reasoner_context import build_reasoner_payload
from agent.state import new_agent_state
from agent.world_restore import restore_continuity_world
from conversation.job_context import JobContext
from task.schema import TaskJobProgress, new_job_search_task, set_task_status
from task.store import JobSearchTaskStore
from tests.mock_llm import ScriptedReasonerLLM
from tools.registry import build_registry

ROLE = "高级产品经理"
JOB_KEY = "mock:j-wait"


def _job_context(job_key: str = JOB_KEY) -> dict:
    return JobContext(
        context_id=job_key,
        job_key=job_key,
        platform="mock",
        job_id=job_key.split(":", 1)[-1],
        job_url=f"https://mock.local/{job_key.split(':', 1)[-1]}",
        title=ROLE,
        company="支付科技",
        job_listing={
            "platform": "mock",
            "job_id": job_key.split(":", 1)[-1],
            "job_url": f"https://mock.local/{job_key.split(':', 1)[-1]}",
            "job_title": ROLE,
            "company_name": "支付科技",
            "job_description": "负责支付产品规划。",
        },
        job_profile={"analysis_status": "ok", "job_summary": "支付产品"},
        match_result={
            "analysis_status": "ok",
            "recommendation": "yes",
            "hard_requirements_met": True,
        },
        stage="matched",
    ).to_dict()


def _waiting_task(store: JobSearchTaskStore, *, conversation_id: str, job_key: str = JOB_KEY):
    task = new_job_search_task(
        user_goal={"target_roles": [ROLE]},
        conversation_id=conversation_id,
    )
    set_task_status(task, "WAITING_USER")
    task.current_job_context_id = job_key
    task.explored_jobs = [
        TaskJobProgress(
            job_context_id=job_key,
            job_key=job_key,
            status="WAITING_USER",
            job_id=job_key.split(":", 1)[-1],
            title=ROLE,
            company="支付科技",
            platform="mock",
            listed_order=0,
        ),
        TaskJobProgress(
            job_context_id="mock:other",
            job_key="mock:other",
            status="MATCHED",
            job_id="other",
            title="另一岗",
            company="别的公司",
            platform="mock",
            listed_order=1,
        ),
    ]
    store.save(task)
    return task


def test_waiting_user_continuity_restores_only_current_job():
    store = JobSearchTaskStore()
    cid = "conv-restore-1"
    _waiting_task(store, conversation_id=cid)
    other = _job_context("mock:other")
    session = {
        "job_contexts": [_job_context(JOB_KEY), other],
        "task_ids": [],
    }
    state = new_agent_state(
        goal_input="继续看看",
        resume=None,
        data_source="mock",
        session_context=session,
        conversation_id=cid,
    )
    report = restore_continuity_world(state, task_store=store)
    assert report["reason"] == "waiting_user_continuity"
    assert report["restored_job_keys"] == [JOB_KEY]
    assert JOB_KEY in state.jobs
    assert "mock:other" not in state.jobs
    assert state.jobs[JOB_KEY].match_result is not None
    assert state.jobs[JOB_KEY].stage == "opened"
    assert state.jobs[JOB_KEY].stage != "matched"
    from agent.epistemic import is_restored

    assert is_restored(state.jobs[JOB_KEY].match_result)
    assert state.job_search_task_id is None
    assert any(item.get("task_status") == "WAITING_USER" for item in state.active_tasks)


def test_ambiguous_waiting_tasks_do_not_restore_jobs():
    store = JobSearchTaskStore()
    cid = "conv-restore-2"
    _waiting_task(store, conversation_id=cid, job_key="mock:a")
    _waiting_task(store, conversation_id=cid, job_key="mock:b")
    state = new_agent_state(
        goal_input="继续",
        data_source="mock",
        session_context={"job_contexts": [_job_context("mock:a"), _job_context("mock:b")]},
        conversation_id=cid,
    )
    report = restore_continuity_world(state, task_store=store)
    assert report["reason"] == "ambiguous_waiting_tasks"
    assert state.jobs == {}


def test_reasoner_context_does_not_dump_full_job_contexts_as_live_world():
    state = new_agent_state(
        goal_input="你好",
        data_source="mock",
        session_context={
            "previous_goal": {"target_roles": [ROLE]},
            "job_contexts": [_job_context(JOB_KEY)],
            "task_ids": ["t1"],
        },
        conversation_id="conv-ctx",
    )
    payload = build_reasoner_payload(state, build_registry(data_source="mock"))
    assert "session_context" not in payload["context"]
    assert "job_contexts" not in (payload["context"].get("persistence") or {})
    persistence = payload["context"]["persistence"]
    assert persistence["previous_goal"]["target_roles"] == [ROLE]
    assert persistence["history_jobs"][0]["kind"] == "history_index"
    assert persistence["history_jobs"][0]["job_key"] == JOB_KEY
    assert persistence["history_jobs"][0]["live"] is False
    assert payload["jobs"] == []


def test_restore_does_not_auto_analyze_or_match():
    store = JobSearchTaskStore()
    cid = "conv-restore-3"
    _waiting_task(store, conversation_id=cid)
    registry = build_registry(data_source="mock")
    state = run_agent(
        message="先这样",
        data_source="mock",
        conversation_id=cid,
        session_context={"job_contexts": [_job_context(JOB_KEY)]},
        task_store=store,
        registry=registry,
        llm_provider=ScriptedReasonerLLM([{"action_type": "finish", "reason": "stop"}]),
    )
    assert JOB_KEY in state.jobs
    assert "analyze_job" not in registry.invocations
    assert "match_job" not in registry.invocations
    assert "open_job" not in registry.invocations
    assert "search_jobs" not in registry.invocations
    assert state.session.status == "DONE"
