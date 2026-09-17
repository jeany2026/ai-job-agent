"""D.2 JobSearchTask lifecycle: T1–T14 and Fake/Offline E2E."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.decide import Action, tool_action
from agent.loop import LoopContext, reduce
from agent.orchestrator import run_agent
from agent.state import new_agent_state
from api.runs import ConversationStore
from storage.candidate_profile import CandidateProfileStore
from task.schema import new_job_search_task, set_task_status
from task.store import JobSearchTaskStore
from tests.mock_llm import (
    AgentRoutingLLM,
    candidate_profile_response,
    conversation_reference,
    default_reasoner_decision,
    match_result_response,
    understanding_response,
)
from tools.registry import build_registry

RESUME_PATH = ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")
DIRECT_JD = "负责平台产品规划，推动支付、清分、结算等业务建设。"
DIRECT_REQ = (
    "本科及以上学历，5年以上产品经验。必须具备产品规划能力。"
    "熟悉支付业务。有CRM经验优先。有医疗行业经验优先。"
)
TRANSFER_JD = "负责保险产品规划与复杂业务系统设计。"
TRANSFER_REQ = "本科及以上学历，5年以上产品经验。必须具备产品规划能力。保险行业产品经验。"
MOCK_ACTIONS = [
    {"text": "Submit your resume for this role"},
    {"text": "Start a new chat with the recruiter"},
]


def _ok_profile(**overrides) -> dict:
    profile = candidate_profile_response()
    profile["analysis_status"] = "ok"
    profile["error"] = None
    profile["candidate_id"] = "cand-1"
    profile.update(overrides)
    return profile


class _TurnLLM(AgentRoutingLLM):
    """Understanding + optional per-job Reasoner policy. Does not stub decide_next_action.

    surface_job_ids, when set, is a test-double exploration choice: only those jobs
    are opened / asked about. Program still must not auto ask_user after match.
    """

    def __init__(
        self,
        understanding: dict,
        *,
        decisions: dict[str, dict] | None = None,
        surface_job_ids: set[str] | None = None,
    ):
        super().__init__()
        self.understanding = understanding
        self.decisions = decisions or {}
        self.surface_job_ids = surface_job_ids

    def complete_json(self, *, system: str, user: str) -> dict:
        if "UserInputUnderstanding" in system:
            self.calls.append({"system": system, "user": user})
            return self.understanding
        if "你是全局 Agent Reasoner" in system:
            self.calls.append({"system": system, "user": user})
            payload = _json_payload(user)
            observation = payload.get("observation") if isinstance(payload.get("observation"), dict) else {}
            last_tool = observation.get("tool_name")
            result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
            job_id = str(result.get("job_id") or _latest_matched_job_id(payload) or "")
            preferred = self._open_preferred(payload)

            if last_tool == "match_job":
                policy = self.decisions.get(job_id) or {}
                nxt = policy.get("next_action")
                if nxt == "SURFACE_TO_USER" or (self.surface_job_ids is not None and job_id in self.surface_job_ids):
                    return default_reasoner_decision(user, after_match="ask_user")
                if nxt == "COMPLETE_TASK":
                    return default_reasoner_decision(user, after_match="finish")
                if nxt in {"REJECT_JOB", "CONTINUE_EXPLORING"}:
                    return preferred or default_reasoner_decision(user, after_match="finish")
                if self.surface_job_ids is not None:
                    return preferred or default_reasoner_decision(user, after_match="finish")
            if last_tool in {
                "search_jobs",
                "search_jobs",
                "search_boss_jobs",
                "search_liepin_jobs",
                "search_51job_jobs",
            } and preferred is not None:
                return preferred
            if observation.get("kind") == "task_control" and preferred is not None:
                control = (
                    (payload.get("context") or {}).get("task_control")
                    or (payload.get("constraints") or {}).get("task_control")
                    or observation.get("control")
                )
                if control not in {"apply_job", "stop_task"}:
                    return preferred
            return default_reasoner_decision(user)
        return super().complete_json(system=system, user=user)

    def _open_preferred(self, payload: dict) -> dict | None:
        if self.surface_job_ids is None:
            return None
        constraints = payload.get("constraints") if isinstance(payload.get("constraints"), dict) else {}
        jobs = [item for item in (payload.get("jobs") or constraints.get("jobs") or []) if isinstance(item, dict)]
        listed = [item for item in jobs if item.get("stage") == "listed" and item.get("job_id") in self.surface_job_ids]
        listed.sort(key=lambda item: item.get("listed_order") if isinstance(item.get("listed_order"), int) else 10**9)
        if not listed:
            return None
        target = listed[0]
        arguments = {}
        if target.get("job_id"):
            arguments["job_id"] = target["job_id"]
        if target.get("job_url"):
            arguments["job_url"] = target["job_url"]
        open_name = "open_job"
        return {
            "action_type": "tool",
            "tool_name": open_name,
            "arguments": arguments,
            "reason": "test-double exploration: open a chosen job",
        }


def _latest_matched_job_id(payload: dict) -> str:
    constraints = payload.get("constraints") if isinstance(payload.get("constraints"), dict) else {}
    jobs = [item for item in (payload.get("jobs") or constraints.get("jobs") or []) if isinstance(item, dict)]
    matched = [item for item in jobs if item.get("has_match") and item.get("stage") == "matched"]
    matched.sort(key=lambda item: item.get("listed_order") if isinstance(item.get("listed_order"), int) else 10**9)
    if matched:
        return str(matched[-1].get("job_id") or "")
    return ""


def _json_payload(user: str) -> dict:
    try:
        payload = json.loads(user)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _run(
    message: str,
    *,
    store: JobSearchTaskStore,
    conversation_id: str,
    llm=None,
    registry=None,
    session_context=None,
    profile_dir=None,
    candidate_profile=None,
    resume=SAMPLE_RESUME,
):
    return run_agent(
        resume=resume,
        goal=message,
        message=message,
        llm_provider=llm or AgentRoutingLLM(),
        registry=registry or build_registry(data_source="mock"),
        data_source="mock",
        task_store=store,
        conversation_id=conversation_id,
        session_context=session_context,
        candidate_profile=candidate_profile,
        profile_dir=profile_dir,
        candidate_id="cand-1",
    )


def test_t1_natural_language_creates_running_task():
    store = JobSearchTaskStore()
    state = _run("开始帮我找深圳高级产品经理", store=store, conversation_id="conv-t1")
    tasks = store.list_by_conversation("conv-t1")
    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_id == state.job_search_task_id
    assert any(item.get("status") == "RUNNING" for item in task.status_history)
    assert task.task_status in {"RUNNING", "WAITING_USER"}
    assert task.user_goal.get("target_roles")


def test_t2_agent_explores_without_user_continue():
    store = JobSearchTaskStore()
    registry = build_registry(data_source="mock")
    state = _run(
        "开始帮我找深圳高级产品经理",
        store=store,
        conversation_id="conv-t2",
        registry=registry,
    )
    assert "search_jobs" in registry.invocations
    assert "open_job" in registry.invocations
    assert "analyze_job" in registry.invocations
    assert "match_job" in registry.invocations
    assert "decide_next_action" not in registry.invocations
    search_at = registry.invocations.index("search_jobs")
    open_at = registry.invocations.index("open_job")
    analyze_at = registry.invocations.index("analyze_job")
    match_at = registry.invocations.index("match_job")
    assert search_at < open_at < analyze_at < match_at
    assert state.session.status == "WAITING_USER"


def test_t3_unworthy_job_is_rejected_without_human_gate():
    store = JobSearchTaskStore()
    registry = build_registry(data_source="mock")
    llm = _TurnLLM(
        understanding_response(
            task_kind="new_job_search",
            user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]},
        ),
        decisions={
            "mock-direct": {"next_action": "COMPLETE_TASK", "rationale": "不值得占用用户注意力"},
        },
        surface_job_ids=set(),
    )
    state = _run(
        "开始帮我找深圳高级产品经理",
        store=store,
        conversation_id="conv-t3",
        llm=llm,
        registry=registry,
    )
    task = store.load(state.job_search_task_id)
    assert "decide_next_action" not in registry.invocations
    skipped = task.job_progress("mock:mock-direct")
    if skipped is not None:
        assert skipped.status != "WAITING_USER"
        assert skipped.decision != "SURFACE_TO_USER"
    assert state.session.status == "DONE"
    assert (state.output or {}).get("waiting_job") is None or state.output["waiting_job"]["job_id"] != "mock-direct"
    if task.current_job_context_id:
        assert task.current_job_context_id != "mock:mock-direct"


def test_t4_worthy_job_enters_waiting_user():
    store = JobSearchTaskStore()
    state = _run("开始帮我找深圳高级产品经理", store=store, conversation_id="conv-t4")
    task = store.load(state.job_search_task_id)
    assert state.session.status == "WAITING_USER"
    assert task.task_status == "WAITING_USER"
    assert task.current_job_context_id
    waiting = state.output["waiting_job"]
    assert waiting["job_id"]
    assert waiting["job_title"]
    assert waiting["company_name"]
    assert task.current_job_context_id in {waiting["job_key"], f"mock:{waiting['job_id']}"}


def test_t5_waiting_user_survives_http_run():
    store = JobSearchTaskStore()
    state = _run("开始帮我找深圳高级产品经理", store=store, conversation_id="conv-t5")
    task_id = state.job_search_task_id
    del state
    task = store.load(task_id)
    assert task.task_status == "WAITING_USER"
    assert task.current_job_context_id


def test_understanding_skip_does_not_skip_until_reasoner_acts():
    store = JobSearchTaskStore()
    first = _run("开始帮我找深圳高级产品经理", store=store, conversation_id="conv-skip-obs")
    job_key = first.output["waiting_job"]["job_key"]
    task = store.load(first.job_search_task_id)
    assert task.job_progress(job_key).status == "WAITING_USER"

    state = new_agent_state(goal_input="跳过", resume=None, data_source="mock", conversation_id="conv-skip-obs")
    state.status = "RUNNING"
    result = understanding_response(task_kind="skip_job")
    result["analysis_status"] = "ok"
    state = reduce(
        state,
        tool_action("understand_user_input", {"message": "跳过"}),
        result,
        LoopContext(task_store=store, conversation_id="conv-skip-obs"),
    )
    assert state.session.status == "RUNNING"
    assert state.session.status != "DONE"
    assert state.last_raw_observation["kind"] == "user_turn_understood"
    assert state.last_raw_observation["task_kind"] == "skip_job"
    reloaded = store.load(first.job_search_task_id)
    assert reloaded.job_progress(job_key).status == "WAITING_USER"
    assert reloaded.job_progress(job_key).status != "SKIPPED_BY_USER"


def test_t6_skip_continues_without_asking_continue():
    store = JobSearchTaskStore()
    conversations = ConversationStore()
    first = _run("开始帮我找深圳高级产品经理", store=store, conversation_id="conv-t6")
    conversations.remember("conv-t6", first, run_id="run-1")
    first_job = first.output["waiting_job"]["job_key"]
    registry = build_registry(data_source="mock")
    second = _run(
        "跳过",
        store=store,
        conversation_id="conv-t6",
        registry=registry,
        session_context=conversations.session_context("conv-t6"),
        llm=_TurnLLM(understanding_response(task_kind="skip_job")),
        candidate_profile=_ok_profile(),
        resume=None,
    )
    task = store.load(first.job_search_task_id)
    skipped = task.job_progress(first_job)
    assert skipped is not None
    assert skipped.status == "SKIPPED_BY_USER"
    assert second.job_search_task_id == first.job_search_task_id
    assert "search_jobs" not in registry.invocations or registry.invocations.count("search_jobs") == 0
    assert second.session.status in {"WAITING_USER", "DONE"}
    if second.session.status == "WAITING_USER":
        assert second.output["waiting_job"]["job_key"] != first_job


def test_t7_apply_uses_current_job_context_not_search():
    store = JobSearchTaskStore()
    conversations = ConversationStore()
    first = _run("开始帮我找深圳高级产品经理", store=store, conversation_id="conv-t7")
    conversations.remember("conv-t7", first, run_id="run-1")
    waiting = first.output["waiting_job"]
    registry = build_registry(data_source="mock")
    called = []
    original = registry._handlers["execute_action"]

    def wrapped(arguments, **extra):
        called.append(arguments)
        return original(arguments, **extra)

    registry._handlers["execute_action"] = wrapped
    second = _run(
        "投递",
        store=store,
        conversation_id="conv-t7",
        registry=registry,
        session_context=conversations.session_context("conv-t7"),
        llm=_TurnLLM(understanding_response(task_kind="apply_job")),
        candidate_profile=_ok_profile(),
        resume=None,
    )
    assert second.job_search_task_id == first.job_search_task_id
    assert "execute_action" in registry.invocations
    assert "search_jobs" not in registry.invocations
    assert called
    job = called[0].get("job") or {}
    assert job.get("job_id") == waiting["job_id"] or called[0].get("job_id") == waiting["job_id"]
    task = store.load(first.job_search_task_id)
    applied = task.job_progress(waiting["job_key"])
    assert applied is not None
    assert applied.status == "APPLIED"
    assert task.application_history
    assert task.application_history[-1]["result"] == "applied"
    assert second.session.status in {"WAITING_USER", "DONE"}
    assert task.task_status != "WAITING_USER" or (
        task.current_job_context_id != waiting["job_key"]
    )


def test_t8_stop_does_not_search():
    store = JobSearchTaskStore()
    conversations = ConversationStore()
    first = _run("开始帮我找深圳高级产品经理", store=store, conversation_id="conv-t8")
    conversations.remember("conv-t8", first, run_id="run-1")
    registry = build_registry(data_source="mock")
    second = _run(
        "停止",
        store=store,
        conversation_id="conv-t8",
        registry=registry,
        session_context=conversations.session_context("conv-t8"),
        llm=_TurnLLM(understanding_response(task_kind="stop_task")),
        candidate_profile=_ok_profile(),
        resume=None,
    )
    task = store.load(first.job_search_task_id)
    assert task.task_status == "STOPPED"
    assert task.stopping_reason == "user_requested"
    assert "search_jobs" not in registry.invocations
    assert "open_job" not in registry.invocations
    assert second.session.status == "DONE"


def test_t9_resume_does_not_create_new_task():
    store = JobSearchTaskStore()
    conversations = ConversationStore()
    first = _run("开始帮我找深圳高级产品经理", store=store, conversation_id="conv-t9")
    conversations.remember("conv-t9", first, run_id="run-1")
    t1 = first.job_search_task_id
    del first
    second = _run(
        "投递",
        store=store,
        conversation_id="conv-t9",
        session_context=conversations.session_context("conv-t9"),
        llm=_TurnLLM(understanding_response(task_kind="apply_job")),
        candidate_profile=_ok_profile(),
        resume=None,
    )
    assert second.job_search_task_id == t1
    assert len(store.list_by_conversation("conv-t9")) == 1


def test_t10_multiple_active_tasks_do_not_guess():
    store = JobSearchTaskStore()
    first = new_job_search_task(conversation_id="conv-t10", user_goal={"target_roles": ["产品经理"]})
    second = new_job_search_task(conversation_id="conv-t10", user_goal={"target_roles": ["设计师"]})
    set_task_status(first, "WAITING_USER")
    set_task_status(second, "WAITING_USER")
    first.current_job_context_id = "job-a"
    second.current_job_context_id = "job-b"
    store.save(first)
    store.save(second)
    registry = build_registry(data_source="mock")
    state = _run(
        "继续",
        store=store,
        conversation_id="conv-t10",
        registry=registry,
        llm=_TurnLLM(understanding_response(task_kind="continue_task")),
        candidate_profile=_ok_profile(),
        resume=None,
    )
    assert state.session.status == "WAITING_USER"
    assert state.output["clarification_needed"] is True
    assert state.output["error_code"] == "clarification_needed"
    assert "search_jobs" not in registry.invocations
    assert store.load(first.task_id).task_status == "WAITING_USER"
    assert store.load(second.task_id).task_status == "WAITING_USER"


def test_t11_new_search_creates_new_task():
    store = JobSearchTaskStore()
    first = _run("开始帮我找深圳高级产品经理", store=store, conversation_id="conv-t11")
    second = _run(
        "重新帮我找广州的产品经理",
        store=store,
        conversation_id="conv-t11",
        llm=_TurnLLM(
            understanding_response(
                task_kind="new_job_search",
                user_goal={"target_roles": ["产品经理"], "cities": ["广州"]},
            )
        ),
        candidate_profile=_ok_profile(),
    )
    assert second.job_search_task_id != first.job_search_task_id
    ids = {task.task_id for task in store.list_by_conversation("conv-t11")}
    assert first.job_search_task_id in ids
    assert second.job_search_task_id in ids


def test_t12_follow_up_does_not_search():
    store = JobSearchTaskStore()
    conversations = ConversationStore()
    first = _run("开始帮我找深圳高级产品经理", store=store, conversation_id="conv-t12")
    conversations.remember("conv-t12", first, run_id="run-1")
    original = conversations.get_job_context("conv-t12", first.output["waiting_job"]["job_key"])
    registry = build_registry(data_source="mock")
    message = "刚才那个职位，如果没有医疗行业经验，我能不能凭支付结算经验做？"
    state = _run(
        message,
        store=store,
        conversation_id="conv-t12",
        registry=registry,
        session_context={"job_contexts": [original]},
        llm=_TurnLLM(
            understanding_response(
                task_kind="follow_up_job",
                matching_context="不要因为没有医疗行业经验直接排除，重点判断支付结算经验。",
                conversation_reference=conversation_reference(
                    reference_text="刚才那个职位",
                    resolution_hint={"recency": "last"},
                ),
                candidate_supplement={
                    "claimed_capabilities": [
                        {"name": "支付结算", "quote": "支付结算", "source_kind": "user_statement"}
                    ]
                },
            )
        ),
        candidate_profile=_ok_profile(),
        resume=None,
    )
    assert "search_jobs" not in registry.invocations
    assert "open_job" not in registry.invocations
    assert "analyze_job" not in registry.invocations
    assert "match_job" in registry.invocations
    assert state.follow_up_of == original["job_key"]
    task = store.load(first.job_search_task_id)
    assert task.task_status == "WAITING_USER"


def test_t13_profile_stays_isolated(tmp_path):
    profiles = CandidateProfileStore(tmp_path)
    profiles.create(_ok_profile(), candidate_id="cand-1", source_kinds=["resume"])
    store = JobSearchTaskStore()
    conversations = ConversationStore()
    first = _run(
        "开始帮我找深圳高级产品经理",
        store=store,
        conversation_id="conv-t13",
        profile_dir=str(tmp_path),
        candidate_profile=_ok_profile(),
    )
    conversations.remember("conv-t13", first, run_id="run-1")
    version = profiles.load("cand-1")["profile_version"]
    _run(
        "跳过",
        store=store,
        conversation_id="conv-t13",
        session_context=conversations.session_context("conv-t13"),
        llm=_TurnLLM(understanding_response(task_kind="skip_job", persist_requested=False)),
        profile_dir=str(tmp_path),
        candidate_profile=_ok_profile(),
        resume=None,
    )
    _run(
        "投递",
        store=store,
        conversation_id="conv-t13",
        session_context=conversations.session_context("conv-t13"),
        llm=_TurnLLM(understanding_response(task_kind="apply_job", persist_requested=False)),
        profile_dir=str(tmp_path),
        candidate_profile=_ok_profile(),
        resume=None,
    )
    loaded = profiles.load("cand-1")
    assert loaded["profile_version"] == version
    assert "surfaced_jobs" not in json.dumps(loaded["profile"], ensure_ascii=False)


def test_t14_score_is_not_the_only_gate():
    store = JobSearchTaskStore()
    registry = build_registry(data_source="mock")
    jobs = [
        {
            "platform": "mock",
            "job_id": "high-score-low-salary",
            "job_url": "https://mock.local/job/high-score-low-salary",
            "job_title": "高级产品经理",
            "company_name": "低薪公司",
            "city": "深圳",
            "salary": "8-10K",
            "job_description": DIRECT_JD,
            "requirements": DIRECT_REQ,
        },
        {
            "platform": "mock",
            "job_id": "mid-score-transfer",
            "job_url": "https://mock.local/job/mid-score-transfer",
            "job_title": "保险产品经理",
            "company_name": "迁移科技",
            "city": "深圳",
            "salary": "30-40K",
            "job_description": TRANSFER_JD,
            "requirements": TRANSFER_REQ,
        },
    ]

    def fake_search(arguments, **_extra):
        return {"jobs": jobs, "platform": "mock"}

    def fake_open(arguments, **_extra):
        job_id = arguments.get("job_id")
        hit = next(item for item in jobs if item["job_id"] == job_id)
        opened = dict(hit)
        opened["raw_actions"] = list(MOCK_ACTIONS)
        return opened

    def fake_match(arguments, **_extra):
        profile = arguments.get("job_profile") or {}
        summary = profile.get("job_summary") or ""
        if "保险" in summary or "mid-score" in json.dumps(arguments, ensure_ascii=False):
            payload = match_result_response(
                overall_fit="moderate",
                recommendation="weak",
                capability_assessments=[
                    {
                        "dimension": "产品规划",
                        "outcome": "transferable",
                        "job_requirement_ref": "产品规划",
                        "candidate_capability_ref": "支付中台",
                        "transfer_rationale": "支付中台经验可迁移到复杂业务系统。",
                        "evidence": [{"quote": "产品规划", "location_hint": None, "field": None}],
                    }
                ],
                rationale="中等匹配，但核心能力高度可迁移。",
            )
        else:
            payload = match_result_response(
                overall_fit="strong",
                recommendation="yes",
                rationale="匹配分数高，但薪资明显低于目标。",
            )
        payload["analysis_status"] = "ok"
        payload["error"] = None
        return payload

    registry._handlers["search_jobs"] = fake_search
    registry._handlers["open_job"] = fake_open
    registry._handlers["match_job"] = fake_match
    state = _run(
        "开始帮我找深圳高级产品经理，35K左右。",
        store=store,
        conversation_id="conv-t14",
        registry=registry,
        llm=_TurnLLM(
            understanding_response(
                task_kind="new_job_search",
                user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]},
                preferences=["期望薪资35K左右"],
            ),
            decisions={
                "high-score-low-salary": {
                    "next_action": "CONTINUE_EXPLORING",
                    "rationale": "高匹配也不能覆盖明显过低的薪资约束。",
                },
                "mid-score-transfer": {
                    "next_action": "SURFACE_TO_USER",
                    "rationale": "中等匹配但可迁移能力与目标契合，值得用户决定。",
                },
            },
            surface_job_ids={"mid-score-transfer"},
        ),
        candidate_profile=_ok_profile(),
        resume=None,
    )
    task = store.load(state.job_search_task_id)
    high = task.job_progress("mock:high-score-low-salary")
    mid = task.job_progress("mock:mid-score-transfer")
    assert "decide_next_action" not in registry.invocations
    if high is not None:
        assert high.status != "WAITING_USER"
        assert high.decision != "SURFACE_TO_USER"
    assert state.session.status == "WAITING_USER"
    assert state.output["waiting_job"]["job_id"] == "mid-score-transfer"
    assert mid is not None and mid.status == "WAITING_USER"


def test_fake_e2e_search_skip_apply_stop():
    store = JobSearchTaskStore()
    conversations = ConversationStore()
    catalog = [
        _listed("job-a", "不匹配岗位"),
        _listed("job-b", "信息不足岗位"),
        _listed("job-c", "一般匹配岗位"),
        _listed("job-d", "可迁移岗位"),
        _listed("job-e", "继续淘汰岗位"),
        _listed("job-f", "值得投递岗位"),
        _listed("job-g", "后续岗位"),
    ]
    decisions = {
        "job-a": {"next_action": "REJECT_JOB", "rationale": "明显不匹配"},
        "job-b": {"next_action": "CONTINUE_EXPLORING", "rationale": "信息不足，继续处理其他职位"},
        "job-c": {"next_action": "REJECT_JOB", "rationale": "匹配一般"},
        "job-d": {"next_action": "SURFACE_TO_USER", "rationale": "高度可迁移，值得用户决定"},
        "job-e": {"next_action": "REJECT_JOB", "rationale": "继续淘汰"},
        "job-f": {"next_action": "SURFACE_TO_USER", "rationale": "值得用户决定"},
        "job-g": {"next_action": "SURFACE_TO_USER", "rationale": "下一个值得用户决定的职位"},
    }
    registry = _scripted_registry(catalog)
    first = _run(
        "开始帮我找深圳高级产品经理，35K左右。",
        store=store,
        conversation_id="conv-e2e",
        registry=registry,
        llm=_TurnLLM(
            understanding_response(
                task_kind="new_job_search",
                user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"], "salary_min": 35000},
            ),
            decisions=decisions,
            surface_job_ids={"job-d", "job-f", "job-g"},
        ),
        candidate_profile=_ok_profile(),
        resume=None,
    )
    assert first.session.status == "WAITING_USER"
    assert first.output["waiting_job"]["job_id"] == "job-d"
    task = store.load(first.job_search_task_id)
    first_a = task.job_progress("mock:job-a")
    first_c = task.job_progress("mock:job-c")
    assert first_a is not None and first_a.status != "WAITING_USER"
    assert first_c is not None and first_c.status != "WAITING_USER"
    conversations.remember("conv-e2e", first, run_id="run-1")

    skip_registry = _scripted_registry(catalog)
    skipped = _run(
        "跳过。",
        store=store,
        conversation_id="conv-e2e",
        registry=skip_registry,
        session_context=conversations.session_context("conv-e2e"),
        llm=_TurnLLM(
            understanding_response(task_kind="skip_job"),
            decisions=decisions,
            surface_job_ids={"job-d", "job-f", "job-g"},
        ),
        candidate_profile=_ok_profile(),
        resume=None,
    )
    assert skipped.job_search_task_id == first.job_search_task_id
    assert skipped.session.status == "WAITING_USER"
    assert skipped.output["waiting_job"]["job_id"] == "job-f"
    task = store.load(first.job_search_task_id)
    assert task.job_progress("mock:job-d").status == "SKIPPED_BY_USER"
    conversations.remember("conv-e2e", skipped, run_id="run-2")

    apply_registry = _scripted_registry(catalog)
    applied = _run(
        "投递。",
        store=store,
        conversation_id="conv-e2e",
        registry=apply_registry,
        session_context=conversations.session_context("conv-e2e"),
        llm=_TurnLLM(
            understanding_response(task_kind="apply_job"),
            decisions=decisions,
            surface_job_ids={"job-d", "job-f", "job-g"},
        ),
        candidate_profile=_ok_profile(),
        resume=None,
    )
    assert applied.job_search_task_id == first.job_search_task_id
    assert "execute_action" in apply_registry.invocations
    task = store.load(first.job_search_task_id)
    assert task.job_progress("mock:job-f").status == "APPLIED"
    conversations.remember("conv-e2e", applied, run_id="run-3")

    stop_registry = _scripted_registry(catalog)
    stopped = _run(
        "停止。",
        store=store,
        conversation_id="conv-e2e",
        registry=stop_registry,
        session_context=conversations.session_context("conv-e2e"),
        llm=_TurnLLM(understanding_response(task_kind="stop_task"), decisions=decisions),
        candidate_profile=_ok_profile(),
        resume=None,
    )
    task = store.load(first.job_search_task_id)
    assert task.task_status == "STOPPED"
    assert task.stopping_reason == "user_requested"
    assert "search_jobs" not in stop_registry.invocations
    assert stopped.session.status == "DONE"


def _listed(job_id: str, title: str) -> dict:
    return {
        "platform": "mock",
        "job_id": job_id,
        "job_url": f"https://mock.local/job/{job_id}",
        "job_title": title,
        "company_name": f"{title}公司",
        "city": "深圳",
        "salary": "30-40K",
        "job_description": DIRECT_JD,
        "requirements": DIRECT_REQ,
        "raw_actions": list(MOCK_ACTIONS),
    }


def _scripted_registry(catalog: list[dict]):
    registry = build_registry(data_source="mock")

    def fake_search(arguments, **_extra):
        return {"jobs": [{k: item.get(k) for k in (
            "platform", "job_id", "job_url", "job_title", "company_name", "city", "salary"
        )} for item in catalog], "platform": "mock"}

    def fake_open(arguments, **_extra):
        job_id = arguments.get("job_id")
        return dict(next(item for item in catalog if item["job_id"] == job_id))

    registry._handlers["search_jobs"] = fake_search
    registry._handlers["open_job"] = fake_open
    return registry
