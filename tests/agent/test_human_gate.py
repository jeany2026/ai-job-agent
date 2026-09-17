"""Human Gate: login / captcha / access / browser. Never 'please open the JD'."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.human_gate import (
    FORBIDDEN_HUMAN_GATE_REASONS,
    HUMAN_GATE_REASONS,
    HumanGateError,
    apply_human_gate,
    classify_exception,
    human_gate_payload,
)
from agent.orchestrator import run_agent
from agent.state import JobRecord, SearchPlan, new_agent_state
from browser.edge_session import EdgeNotConnected, HumanVerificationStopped
from tests.mock_llm import AgentRoutingLLM
from tools.registry import build_registry

RESUME_PATH = ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")
GOAL_TEXT = "帮我找深圳高级产品经理，重点金融科技/支付/复杂业务系统，薪资符合目标"


def test_human_gate_reason_catalog():
    assert HUMAN_GATE_REASONS == (
        "login_required",
        "captcha",
        "access_blocked",
        "browser_unavailable",
        "cdp_unavailable",
        "site_blocked_client",
    )
    for forbidden in FORBIDDEN_HUMAN_GATE_REASONS:
        assert forbidden not in HUMAN_GATE_REASONS


def test_classify_edge_not_connected_is_cdp_fault():
    gate = classify_exception(EdgeNotConnected("无法建立 CDP"), source="search_boss_jobs")
    assert gate["reason"] == "cdp_unavailable"
    assert gate["source"] == "search_boss_jobs"
    assert gate.get("fault") == "cdp_connect_failed"


def test_classify_agent_edge_not_ready_is_browser_unavailable():
    from browser.edge_launcher import AgentEdgeNotReady

    gate = classify_exception(AgentEdgeNotReady("端口被占用"), source="search_boss_jobs")
    assert gate["reason"] == "browser_unavailable"
    assert gate.get("fault") == "agent_edge_not_ready"


def test_classify_captcha_and_access_block():
    captcha = classify_exception(HumanVerificationStopped("拖动滑块"), source="open_boss_job")
    assert captcha["reason"] == "captcha"
    blocked = classify_exception(HumanVerificationStopped("access denied"), source="search_boss_jobs")
    assert blocked["reason"] == "access_blocked"
    site = classify_exception(HumanVerificationStopped("网络环境异常"), source="search_boss_jobs")
    assert site["reason"] == "site_blocked_client"


def test_classify_login_required():
    gate = classify_exception(
        RuntimeError("当前 Edge 未检测到 BOSS 登录态。请先在 Edge 中手动登录 BOSS。"),
        source="search_boss_jobs",
    )
    assert gate["reason"] == "login_required"


def test_ordinary_errors_are_not_human_gate():
    assert classify_exception(ValueError("需要有效的 BOSS job_url 或 job_id")) is None
    assert classify_exception(RuntimeError("职位列表为空。")) is None


def test_payload_rejects_manual_open_jd():
    try:
        human_gate_payload("open_jd_manually", "请用户手动打开 JD")
    except ValueError:
        return
    raise AssertionError("manual JD open must not be a Human Gate reason")


def test_apply_human_gate_keeps_state():
    state = new_agent_state(resume="resume", goal_input="goal", data_source="boss")
    state.status = "ENRICHING_JOBS"
    state.candidate.profile_status = "ok"
    state.search.plans.append(SearchPlan(plan_id="plan-1", keyword="高级产品经理", city="深圳"))
    state.search.active_plan_id = "plan-1"
    state.jobs["boss:job-1"] = JobRecord(
        job_key="boss:job-1",
        stage="listed",
        listed_order=0,
        listed={"platform": "boss", "job_id": "job-1", "job_title": "高级产品经理"},
    )
    apply_human_gate(
        state,
        {"reason": "captcha", "message": "拖动滑块", "source": "search_boss_jobs"},
    )
    assert state.session.status == "NEEDS_HUMAN"
    assert state.human_gate["reason"] == "captcha"
    assert "boss:job-1" in state.jobs
    assert state.jobs["boss:job-1"].stage == "listed"
    assert state.search.plans[0].plan_id == "plan-1"
    assert state.search.active_plan_id == "plan-1"
    assert state.candidate.profile_status == "ok"
    assert state.output is None
    assert state.last_raw_observation["kind"] == "human_gate"
    assert state.last_raw_observation["reason"] == "captcha"


def _run_boss_search_error(exc: BaseException):
    registry = build_registry(data_source="boss")

    def boom(arguments, **_extra):
        raise exc

    registry._handlers["search_jobs"] = boom
    return run_agent(
        resume=SAMPLE_RESUME,
        goal=GOAL_TEXT,
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="boss",
    )


def test_loop_edge_not_connected_needs_human():
    state = _run_boss_search_error(EdgeNotConnected("Edge CDP not reachable"))
    assert state.session.status == "NEEDS_HUMAN"
    assert state.human_gate["reason"] == "cdp_unavailable"
    assert state.human_gate["source"] == "search_jobs"
    assert state.candidate.profile_status == "ok"
    assert state.output is None


def test_loop_captcha_needs_human():
    state = _run_boss_search_error(HumanVerificationStopped("请完成安全验证"))
    assert state.session.status == "NEEDS_HUMAN"
    assert state.human_gate["reason"] == "captcha"


def test_loop_access_blocked_needs_human():
    state = _run_boss_search_error(HumanVerificationStopped("访问存在风险"))
    assert state.session.status == "NEEDS_HUMAN"
    assert state.human_gate["reason"] == "access_blocked"


def test_loop_login_required_needs_human():
    state = _run_boss_search_error(HumanGateError("login_required", "未检测到 BOSS 登录态", source="search_boss_jobs"))
    assert state.session.status == "NEEDS_HUMAN"
    assert state.human_gate["reason"] == "login_required"


def test_loop_human_gate_dict_result():
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
    assert state.human_gate["reason"] == "browser_unavailable"
    assert state.human_gate["source"] == "search_jobs"


def test_loop_human_gate_does_not_drop_search_state():
    registry = build_registry(data_source="mock")

    def boom(arguments, **_extra):
        raise HumanVerificationStopped("拖动滑块")

    registry._handlers["open_job"] = boom
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal=GOAL_TEXT,
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status == "NEEDS_HUMAN"
    assert state.human_gate["reason"] == "captcha"
    assert state.human_gate["source"] == "open_job"
    assert state.candidate.profile_status == "ok"
    assert state.search.plans
    assert state.search.plans[0].keyword == "高级产品经理"
    assert state.jobs
    assert any(record.stage == "listed" for record in state.jobs.values())
    assert state.output is None
    assert state.decisions
