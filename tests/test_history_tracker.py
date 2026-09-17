"""Phase 10: already-applied tracker is local JSON and rules read it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.orchestrator import run_agent
from agent.state import Constraints
from rules.application import application_exclude_reason, applied_key_set
from rules.filters import list_constraint_flags, list_exclude_reason
from storage.errors import CorruptStorageError
from storage.history import HistoryStore
from storage.tracker import AppliedTracker, merge_already_applied
from tests.mock_llm import AgentRoutingLLM
from tools.registry import build_registry

RESUME_PATH = ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")
GOAL_TEXT = "帮我找深圳高级产品经理，重点金融科技/支付/复杂业务系统，薪资符合目标"


def test_tracker_round_trip(tmp_path):
    path = tmp_path / "applied.json"
    tracker = AppliedTracker(path)
    assert tracker.applied_keys() == []
    tracker.mark_applied("mock:job-a")
    tracker.mark_applied("mock:job-a")
    tracker.record_applied(["mock:job-b", "mock:job-a", ""])
    assert tracker.applied_keys() == ["mock:job-a", "mock:job-b"]
    reloaded = AppliedTracker(path)
    assert reloaded.applied_keys() == ["mock:job-a", "mock:job-b"]
    assert reloaded.contains("mock:job-a")
    assert not reloaded.contains("mock:job-c")


def test_tracker_missing_file_is_empty(tmp_path):
    tracker = AppliedTracker(tmp_path / "absent.json")
    assert tracker.applied_keys() == []
    assert not tracker.contains("mock:x")


def test_tracker_corrupt_json_fails_closed(tmp_path):
    path = tmp_path / "applied.json"
    path.write_text("{nope", encoding="utf-8")
    tracker = AppliedTracker(path)
    with pytest.raises(CorruptStorageError):
        tracker.applied_keys()


def test_tracker_wrong_already_applied_type_fails_closed(tmp_path):
    path = tmp_path / "applied.json"
    path.write_text(json.dumps({"schema_version": 1, "already_applied": "mock:x"}), encoding="utf-8")
    tracker = AppliedTracker(path)
    with pytest.raises(CorruptStorageError):
        tracker.applied_keys()


def test_rules_read_tracker_keys():
    tracker = _memory_tracker(["mock:tracked"])
    constraints = Constraints(already_applied=["mock:from-state"])
    tracked = {
        "platform": "mock",
        "job_id": "tracked",
        "company_name": "支付科技",
        "city": "深圳",
    }
    from_state = {
        "platform": "mock",
        "job_id": "from-state",
        "company_name": "支付科技",
        "city": "深圳",
    }
    other = {
        "platform": "mock",
        "job_id": "other",
        "company_name": "支付科技",
        "city": "深圳",
    }
    assert list_exclude_reason(tracked, constraints, tracker=tracker) is None
    assert "already_applied" in list_constraint_flags(tracked, constraints, tracker=tracker)
    assert list_exclude_reason(from_state, constraints, tracker=tracker) is None
    assert "already_applied" in list_constraint_flags(from_state, constraints, tracker=tracker)
    assert list_exclude_reason(other, constraints, tracker=tracker) is None
    assert "already_applied" not in list_constraint_flags(other, constraints, tracker=tracker)
    assert application_exclude_reason(
        job_key="mock:tracked",
        already_applied_keys=[],
        interpret_result=None,
        tracker=tracker,
    ) == "already_applied"
    assert applied_key_set(["mock:from-state"], tracker) == {"mock:from-state", "mock:tracked"}


def test_merge_already_applied_unions_state_and_tracker():
    tracker = _memory_tracker(["mock:b"])
    assert merge_already_applied(["mock:a", "mock:b"], tracker) == ["mock:a", "mock:b"]
    assert merge_already_applied(None, None) == []


def test_run_agent_tracker_marks_listed_job_without_excluding(tmp_path):
    tracker_path = tmp_path / "applied.json"
    AppliedTracker(tracker_path).mark_applied("mock:mock-direct")
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal=GOAL_TEXT,
        constraints={"blacklist": ["黑名单科技"]},
        llm_provider=AgentRoutingLLM(),
        registry=build_registry(data_source="mock"),
        data_source="mock",
        tracker_path=str(tracker_path),
    )
    assert "mock:mock-direct" in state.constraints.already_applied
    record = state.jobs["mock:mock-direct"]
    assert record.stage != "excluded"
    assert record.exclude_reason != "already_applied"
    assert "already_applied" in record.constraint_flags
    persisted = AppliedTracker(tracker_path)
    assert persisted.contains("mock:mock-direct")


def test_run_agent_saves_history_and_recommendations(tmp_path):
    history_dir = tmp_path / "history"
    state = run_agent(
        resume=SAMPLE_RESUME,
        goal=GOAL_TEXT,
        constraints={"blacklist": ["黑名单科技"]},
        llm_provider=AgentRoutingLLM(),
        registry=build_registry(data_source="mock"),
        data_source="mock",
        history_dir=str(history_dir),
    )
    assert state.session.status == "WAITING_USER"
    loaded = HistoryStore(history_dir).load_run(state.session.session_id)
    assert loaded["status"] == "WAITING_USER"
    assert loaded["recommended"] == state.output["recommended"]
    assert loaded["recommended"]
    assert loaded["session_id"] == state.session.session_id


def test_run_agent_corrupt_tracker_fails_closed(tmp_path):
    tracker_path = tmp_path / "applied.json"
    tracker_path.write_text("{", encoding="utf-8")
    with pytest.raises(CorruptStorageError):
        run_agent(
            resume=SAMPLE_RESUME,
            goal=GOAL_TEXT,
            llm_provider=AgentRoutingLLM(),
            registry=build_registry(data_source="mock"),
            data_source="mock",
            tracker_path=str(tracker_path),
        )


class _memory_tracker:
    def __init__(self, keys):
        self._keys = list(keys)

    def applied_keys(self):
        return list(self._keys)
