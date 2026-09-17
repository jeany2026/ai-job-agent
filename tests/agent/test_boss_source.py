"""data_source=boss uses unified search_jobs / open_job. interpret stays in the Loop."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.orchestrator import run_agent
from platforms.mock.jobs import mock_open_job, mock_search_jobs
from tests.mock_llm import AgentRoutingLLM
from tools.registry import FORBIDDEN_LOOP_TOOLS, build_registry

RESUME_PATH = ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")
GOAL_TEXT = "帮我找深圳高级产品经理，重点金融科技/支付/复杂业务系统，薪资符合目标"


def _bossify(job: dict) -> dict:
    out = dict(job)
    out["platform"] = "boss"
    job_id = out.get("job_id")
    if job_id:
        out["job_url"] = f"https://www.zhipin.com/job_detail/{job_id}.html"
    return out


def _fake_search(arguments, **_extra):
    listed = mock_search_jobs(
        keyword=arguments.get("keyword") or "高级产品经理",
        city=arguments.get("city") or "深圳",
        limit=int(arguments.get("limit") or 30),
    )
    jobs = [_bossify(item) for item in listed["jobs"]]
    return {"jobs": jobs, "platform": "boss"}


def _fake_open(arguments, **_extra):
    opened = mock_open_job(job_url=arguments.get("job_url"), job_id=arguments.get("job_id"))
    job = _bossify(opened)
    assert "action_analysis" not in job
    assert isinstance(job.get("raw_actions"), list)
    return job


def _run_boss_fake():
    registry = build_registry(data_source="boss")
    registry._handlers["search_jobs"] = _fake_search
    registry._handlers["open_job"] = _fake_open
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal=GOAL_TEXT,
        constraints={"blacklist": ["黑名单科技"]},
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="boss",
    )
    return state, registry


def test_boss_source_reaches_done_without_live_site():
    state, registry = _run_boss_fake()
    assert state.session.status == "WAITING_USER"
    assert state.constraints.data_source == "boss"
    assert "search_jobs" in registry.invocations
    assert "open_job" in registry.invocations
    assert "search_boss_jobs" not in registry.invocations
    assert "open_boss_job" not in registry.invocations
    assert "mock_search_jobs" not in registry.invocations
    assert "mock_open_job" not in registry.invocations
    assert "browse_boss_jobs" not in registry.invocations
    assert not (FORBIDDEN_LOOP_TOOLS & set(registry.invocations))
    interpret_at = registry.invocations.index("interpret_job_actions")
    open_at = registry.invocations.index("open_job")
    assert open_at < interpret_at
    assert "analyze_job" in registry.invocations
    assert "match_job" in registry.invocations


def test_boss_open_does_not_embed_interpret():
    _, registry = _run_boss_fake()
    assert registry.invocations.count("interpret_job_actions") >= 1
    assert registry.invocations.count("open_job") >= 1


def test_mock_source_still_available():
    registry = build_registry(data_source="mock")
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal=GOAL_TEXT,
        constraints={"blacklist": ["黑名单科技"]},
        llm_provider=AgentRoutingLLM(),
        registry=registry,
        data_source="mock",
    )
    assert state.session.status == "WAITING_USER"
    assert "search_jobs" in registry.invocations
    assert "search_boss_jobs" not in registry.invocations
    recommended_ids = {item["job_id"] for item in state.output["recommended"]}
    assert "mock-direct" in recommended_ids
