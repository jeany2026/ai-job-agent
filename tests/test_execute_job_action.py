"""D.3 execute_job_action: live observe → interpret → bind → click → verify."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.orchestrator import run_agent
from apply.live_execute import FixtureJobPage, click_stamped_action
from tests.mock_llm import (
    AgentRoutingLLM,
    candidate_profile_response,
    intents_response,
    understanding_response,
)
from tools.execute_job_action import execute_job_action
from tools.registry import build_registry

RESUME = (ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt").read_text(encoding="utf-8")

IDENTITY = {
    "platform": "mock",
    "job_id": "job-d3",
    "job_url": "https://mock.local/job/job-d3",
    "job_title": "高级产品经理",
    "company_name": "支付科技有限公司",
    "job_key": "mock:job-d3",
}


class InterpretSequenceLLM:
    def __init__(self, batches: list[list[str]]):
        self.batches = [list(item) for item in batches]
        self.calls: list[dict] = []

    def complete_json(self, *, system: str, user: str) -> dict:
        self.calls.append({"system": system, "user": user})
        if "semantic_intent" not in system:
            raise RuntimeError("InterpretSequenceLLM only answers interpret prompts")
        if not self.batches:
            raise RuntimeError("InterpretSequenceLLM has no remaining interpret batch")
        return intents_response(*self.batches.pop(0))


def _load(*, buttons, page_kind="open", banner="", swap=True, identity=None):
    target = dict(IDENTITY)
    if identity:
        target.update(identity)
    return FixtureJobPage(
        target,
        buttons=buttons,
        page_kind=page_kind,
        banner=banner,
        swap_on_click=swap,
        url=target.get("job_url"),
    )


def _button(intent: str, text: str | None = None) -> dict:
    return {"text": text or intent.replace("_", " "), "aria_label": intent, "tag": "button"}


def _job_payload(**overrides) -> dict:
    job = dict(IDENTITY)
    job.update(overrides)
    return {
        "job": job,
        "job_key": job.get("job_key") or IDENTITY["job_key"],
        "job_id": job.get("job_id"),
        "job_url": job.get("job_url"),
        "user_authorization": "apply_job",
        "job_context_id": job.get("job_key") or IDENTITY["job_key"],
        "task_id": "task-d3",
        "application_history": [],
    }


def _run_execute(page, llm, payload, click_log=None):
    return execute_job_action(payload, page=page, llm_provider=llm, click_log=click_log)


def test_no_hardcoded_page_intent_or_button_copy():
    decide_src = (ROOT_DIR / "agent" / "validate_action.py").read_text(encoding="utf-8")
    execute_src = (ROOT_DIR / "tools" / "execute_job_action.py").read_text(encoding="utf-8")
    live_src = (ROOT_DIR / "apply" / "live_execute.py").read_text(encoding="utf-8")
    boss_src = (ROOT_DIR / "platforms" / "boss" / "execute.py").read_text(encoding="utf-8")
    assert 'authorized_action": "apply_resume"' not in decide_src
    assert "立即沟通" not in execute_src
    assert "立即沟通" not in live_src
    assert "立即沟通" not in boss_src
    assert "投递简历" not in live_src
    assert "get_by_text" not in live_src
    assert "locator.click" in live_src.replace(" ", "")
    assert "search_boss_jobs" not in execute_src
    assert "search_boss_jobs" not in live_src
    assert "search_boss_jobs" not in boss_src
    assert "user_authorization" in decide_src
    assert "apply_job" in decide_src
    assert "execute_action requires user_authorization=apply_job" in decide_src


def test_t1_apply_resume_clicks_and_reobserves():
    click_log: list[int] = []
    page = _load(buttons=[_button("apply_resume")])
    llm = InterpretSequenceLLM([["apply_resume"], ["continue_contact", "view_progress"]])
    result = _run_execute(page, llm, _job_payload(), click_log=click_log)
    assert result["clicked"] is True
    assert click_log == [0]
    assert result["executed_intent"] == "apply_resume"
    assert result["execution_status"] == "success"
    assert result["verification"] == "confirmed"
    assert result["ok"] is True
    assert result["result"] == "applied"
    assert len(llm.calls) >= 2


def test_t2_start_contact_is_not_disguised():
    click_log: list[int] = []
    page = _load(buttons=[_button("start_contact")])
    llm = InterpretSequenceLLM([["start_contact"], ["continue_contact", "view_progress"]])
    result = _run_execute(page, llm, _job_payload(), click_log=click_log)
    assert result["clicked"] is True
    assert result["executed_intent"] == "start_contact"
    assert result["executed_intent"] != "apply_resume"
    assert result["execution_status"] == "success"
    assert result["ok"] is True


def test_t3_existing_relation_does_not_click():
    click_log: list[int] = []
    page = _load(buttons=[_button("continue_contact"), _button("view_progress")], swap=False)
    llm = InterpretSequenceLLM([["continue_contact", "view_progress"]])
    result = _run_execute(page, llm, _job_payload(), click_log=click_log)
    assert result["clicked"] is False
    assert click_log == []
    assert result["execution_status"] == "already_applied"
    assert result["result"] == "already_applied"
    assert result["ok"] is False


def test_t4_identity_mismatch_does_not_click():
    click_log: list[int] = []
    page = _load(
        buttons=[_button("apply_resume")],
        identity={"job_id": "job-other", "job_title": "其他岗位", "company_name": "其他公司"},
    )
    llm = InterpretSequenceLLM([["apply_resume"]])
    result = _run_execute(page, llm, _job_payload(), click_log=click_log)
    assert result["clicked"] is False
    assert click_log == []
    assert result["execution_status"] == "failed"
    assert result["error_code"] == "identity_mismatch"


def test_t5_closed_job_does_not_click():
    click_log: list[int] = []
    page = _load(buttons=[], page_kind="closed")
    llm = InterpretSequenceLLM([])
    result = _run_execute(page, llm, _job_payload(), click_log=click_log)
    assert result["clicked"] is False
    assert click_log == []
    assert result["ok"] is False
    assert result["error_code"] == "job_closed"
    assert result["result"] != "applied"


def test_t6_login_required_needs_human():
    click_log: list[int] = []
    page = _load(buttons=[_button("apply_resume")], banner="未检测到 BOSS 登录态")
    llm = InterpretSequenceLLM([["apply_resume"]])
    result = _run_execute(page, llm, _job_payload(), click_log=click_log)
    assert result["clicked"] is False
    assert click_log == []
    assert result["execution_status"] == "needs_human"
    assert result["error_code"] == "login_required"
    assert result.get("human_gate", {}).get("reason") == "login_required"


def test_t7_captcha_needs_human():
    click_log: list[int] = []
    page = _load(buttons=[_button("apply_resume")], banner="拖动滑块")
    llm = InterpretSequenceLLM([["apply_resume"]])
    result = _run_execute(page, llm, _job_payload(), click_log=click_log)
    assert result["clicked"] is False
    assert click_log == []
    assert result["execution_status"] == "needs_human"
    assert result.get("human_gate", {}).get("reason") == "captcha"


def test_t8_click_without_confirmation_is_uncertain():
    click_log: list[int] = []
    page = _load(buttons=[_button("apply_resume")], swap=False)
    llm = InterpretSequenceLLM([["apply_resume"], ["apply_resume"]])
    result = _run_execute(page, llm, _job_payload(), click_log=click_log)
    assert result["clicked"] is True
    assert click_log == [0]
    assert result["execution_status"] == "uncertain"
    assert result["verification"] == "uncertain"
    assert result["ok"] is False
    assert result["result"] != "applied"


def test_t9_confirmed_success_payload():
    page = _load(buttons=[_button("apply_resume")])
    llm = InterpretSequenceLLM([["apply_resume"], ["continue_contact", "view_progress"]])
    result = _run_execute(page, llm, _job_payload())
    assert result["ok"] is True
    assert result["result"] == "applied"
    assert result["verification"] == "confirmed"
    assert result["application_evidence"] == "applied"


def test_t10_repeat_does_not_click_again():
    click_log: list[int] = []
    page = _load(buttons=[_button("apply_resume")])
    llm = InterpretSequenceLLM([["apply_resume"], ["continue_contact", "view_progress"]])
    payload = _job_payload()
    first = _run_execute(page, llm, payload, click_log=click_log)
    assert first["clicked"] is True
    payload["application_history"] = [
        {"job_context_id": IDENTITY["job_key"], "result": "applied", "evidence": "applied"}
    ]
    second = _run_execute(page, llm, payload, click_log=click_log)
    assert second["execution_status"] == "already_executed"
    assert second["clicked"] is False
    assert click_log == [0]


def test_t11_execution_does_not_search(monkeypatch):
    calls = {"search": 0}

    def boom(*_args, **_kwargs):
        calls["search"] += 1
        raise AssertionError("search_boss_jobs must not run during execute")

    monkeypatch.setattr("platforms.boss.jobs.search_boss_jobs", boom)
    monkeypatch.setattr("tools.boss_job_search.search_boss_jobs", boom)
    page = _load(buttons=[_button("apply_resume")])
    llm = InterpretSequenceLLM([["apply_resume"], ["continue_contact", "view_progress"]])
    result = _run_execute(page, llm, _job_payload())
    assert calls["search"] == 0
    assert result["ok"] is True
    assert result["job_url"] == IDENTITY["job_url"]


def test_t12_waiting_user_does_not_click_without_authorization():
    registry = build_registry(data_source="mock")
    original = registry._handlers["execute_job_action"]
    clicks = {"n": 0}

    def wrapped(arguments, **extra):
        clicks["n"] += 1
        return original(arguments, **extra)

    registry._handlers["execute_job_action"] = wrapped
    state = run_agent(
        resume=RESUME,
        goal="开始帮我找深圳高级产品经理",
        message="开始帮我找深圳高级产品经理",
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="mock",
        candidate_id="cand-1",
        candidate_profile=_ok_profile(),
    )
    assert state.session.status == "WAITING_USER"
    assert "execute_job_action" not in registry.invocations
    assert clicks["n"] == 0

    click_log: list[int] = []
    result = execute_job_action(
        {
            "job": dict(IDENTITY),
            "job_key": IDENTITY["job_key"],
            "job_id": IDENTITY["job_id"],
            "job_url": IDENTITY["job_url"],
        },
        click_log=click_log,
        llm_provider=InterpretSequenceLLM([["apply_resume"]]),
    )
    assert result["error_code"] == "not_authorized"
    assert click_log == []
    assert result["clicked"] is False


def test_t13_apply_returns_to_running_and_can_continue():
    from api.runs import ConversationStore
    from task.store import JobSearchTaskStore

    store = JobSearchTaskStore()
    conversations = ConversationStore()
    first_registry = build_registry(data_source="mock")
    first = run_agent(
        resume=RESUME,
        goal="开始帮我找深圳高级产品经理",
        message="开始帮我找深圳高级产品经理",
        llm_provider=AgentRoutingLLM(),
        registry=first_registry,
        data_source="mock",
        task_store=store,
        conversation_id="conv-d3-t13",
        candidate_id="cand-1",
        candidate_profile=_ok_profile(),
    )
    assert first.session.status == "WAITING_USER"
    conversations.remember("conv-d3-t13", first, run_id="run-1")
    apply_registry = build_registry(data_source="mock")
    applied = run_agent(
        resume=None,
        goal="投递",
        message="投递",
        llm_provider=_TurnLLM(understanding_response(task_kind="apply_job")),
        registry=apply_registry,
        data_source="mock",
        task_store=store,
        conversation_id="conv-d3-t13",
        session_context=conversations.session_context("conv-d3-t13"),
        candidate_id="cand-1",
        candidate_profile=_ok_profile(),
    )
    assert "execute_action" in apply_registry.invocations
    assert "search_jobs" not in apply_registry.invocations
    task = store.load(first.job_search_task_id)
    waiting_key = first.output["waiting_job"]["job_key"]
    progress = task.job_progress(waiting_key)
    assert progress is not None
    assert progress.status == "APPLIED"
    assert task.task_status in {"RUNNING", "WAITING_USER", "COMPLETED"}
    assert task.task_status != "EXECUTING"
    assert applied.session.status in {"WAITING_USER", "DONE"}


def test_click_goes_through_locator():
    page = _load(buttons=[_button("apply_resume")])
    llm = InterpretSequenceLLM([["apply_resume"], ["continue_contact", "view_progress"]])
    seen = {"clicked": False}
    original = click_stamped_action

    def wrapped(page, index):
        locator = page.locator(f"[data-job-agent-action='{int(index)}']")
        locator.click(timeout=10_000)
        seen["clicked"] = True

    from apply import live_execute as live_mod

    live_mod.click_stamped_action = wrapped
    try:
        result = _run_execute(page, llm, _job_payload())
    finally:
        live_mod.click_stamped_action = original
    assert seen["clicked"] is True
    assert result["clicked"] is True


def _ok_profile():
    profile = candidate_profile_response()
    profile["analysis_status"] = "ok"
    profile["error"] = None
    profile["candidate_id"] = "cand-1"
    return profile


class _TurnLLM(AgentRoutingLLM):
    def __init__(self, understanding: dict):
        super().__init__()
        self.understanding = understanding

    def complete_json(self, *, system: str, user: str) -> dict:
        if "UserInputUnderstanding" in system:
            self.calls.append({"system": system, "user": user})
            return self.understanding
        return super().complete_json(system=system, user=user)
