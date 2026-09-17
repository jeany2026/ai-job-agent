"""Phase 10: local run/recommendation history IO. No cloud, no guessing corrupt files."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.report import build_report
from agent.state import DECISION_SURFACED, JobRecord, new_agent_state, note_job_decision
from storage.errors import CorruptStorageError, MissingStorageError, StorageError
from storage.history import HistoryStore, snapshot_run


def _done_state():
    state = new_agent_state(
        resume="产品经理简历",
        goal_input="深圳产品经理",
        constraints={"platforms": ["mock"]},
        data_source="mock",
        candidate_id="cand-hist",
    )
    state.goal = {
        "analysis_status": "ok",
        "target_roles": ["产品经理"],
        "cities": ["深圳"],
    }
    state.candidate.profile_status = "ok"
    yes = JobRecord(
        job_key="mock:hist-yes",
        stage="matched",
        listed_order=0,
        listed={
            "platform": "mock",
            "job_id": "hist-yes",
            "job_title": "高级产品经理",
            "company_name": "支付科技",
        },
        match_result={
            "recommendation": "yes",
            "overall_fit": "strong",
            "hard_requirements_met": True,
            "capability_assessments": [],
            "rationale": "岗位与候选人产品规划经验匹配",
        },
    )
    note_job_decision(yes, kind=DECISION_SURFACED, source="reasoner")
    state.jobs["mock:hist-yes"] = yes
    state.jobs["mock:hist-no"] = JobRecord(
        job_key="mock:hist-no",
        stage="listed",
        listed_order=1,
        listed={
            "platform": "mock",
            "job_id": "hist-no",
            "job_title": "运营",
            "company_name": "黑名单科技",
        },
        exclude_reason="blacklist",
        constraint_flags=["blacklist"],
    )
    state.search.stats["recommended"] = 1
    state.search.stats["excluded"] = 1
    state.search.stop_reason = "min_recommend_met"
    state.output = build_report(state)
    state.status = "DONE"
    return state


def test_history_round_trip(tmp_path):
    state = _done_state()
    store = HistoryStore(tmp_path)
    saved = store.save_run(state)
    loaded = store.load_run(state.session.session_id)
    assert loaded == saved
    assert loaded["schema_version"] == 1
    assert loaded["session_id"] == state.session.session_id
    assert loaded["status"] == "DONE"
    assert loaded["candidate_id"] == "cand-hist"
    assert loaded["recommended"][0]["job_id"] == "hist-yes"
    assert loaded["recommended"][0]["recommendation"] == "yes"
    assert loaded["excluded"][0]["reason"] == "blacklist"
    assert loaded["output"]["recommended"] == loaded["recommended"]
    again = store.load_run(state.session.session_id)
    assert again == loaded


def test_snapshot_run_matches_saved_record(tmp_path):
    state = _done_state()
    store = HistoryStore(tmp_path)
    saved = store.save_run(state)
    snapped = snapshot_run(state)
    snapped["saved_at"] = saved["saved_at"]
    assert saved == snapped


def test_list_runs_returns_saved_history(tmp_path):
    first = _done_state()
    second = _done_state()
    store = HistoryStore(tmp_path)
    store.save_run(first)
    store.save_run(second)
    runs = store.list_runs()
    ids = {item["session_id"] for item in runs}
    assert ids == {first.session.session_id, second.session.session_id}
    assert all(item["recommended"] for item in runs)


def test_list_runs_empty_directory(tmp_path):
    store = HistoryStore(tmp_path / "missing")
    assert store.list_runs() == []


def test_load_missing_run_fails_closed(tmp_path):
    store = HistoryStore(tmp_path)
    with pytest.raises(MissingStorageError) as exc:
        store.load_run("no-such-session")
    assert exc.value.path
    assert "no-such-session" in str(exc.value)


def test_load_empty_session_id_fails_closed(tmp_path):
    store = HistoryStore(tmp_path)
    with pytest.raises(StorageError):
        store.load_run("  ")


def test_corrupt_json_fails_closed(tmp_path):
    state = _done_state()
    store = HistoryStore(tmp_path)
    store.save_run(state)
    path = tmp_path / f"{state.session.session_id}.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(CorruptStorageError) as exc:
        store.load_run(state.session.session_id)
    assert state.session.session_id in (exc.value.path or "")


def test_empty_history_file_fails_closed(tmp_path):
    state = _done_state()
    path = tmp_path / f"{state.session.session_id}.json"
    path.write_text(" \n", encoding="utf-8")
    store = HistoryStore(tmp_path)
    with pytest.raises(CorruptStorageError):
        store.load_run(state.session.session_id)


def test_history_json_array_fails_closed(tmp_path):
    state = _done_state()
    path = tmp_path / f"{state.session.session_id}.json"
    path.write_text("[]\n", encoding="utf-8")
    store = HistoryStore(tmp_path)
    with pytest.raises(CorruptStorageError):
        store.load_run(state.session.session_id)


def test_history_missing_required_fields_fails_closed(tmp_path):
    state = _done_state()
    path = tmp_path / f"{state.session.session_id}.json"
    path.write_text(json.dumps({"schema_version": 1, "session_id": state.session.session_id}), encoding="utf-8")
    store = HistoryStore(tmp_path)
    with pytest.raises(CorruptStorageError):
        store.load_run(state.session.session_id)


def test_history_unknown_schema_fails_closed(tmp_path):
    state = _done_state()
    path = tmp_path / f"{state.session.session_id}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 99,
                "session_id": state.session.session_id,
                "status": "DONE",
            }
        ),
        encoding="utf-8",
    )
    store = HistoryStore(tmp_path)
    with pytest.raises(CorruptStorageError):
        store.load_run(state.session.session_id)


def test_list_runs_corrupt_file_fails_closed(tmp_path):
    state = _done_state()
    store = HistoryStore(tmp_path)
    store.save_run(state)
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(CorruptStorageError):
        store.list_runs()
